"""使用长期 Chromium 和隔离 Browser Context 处理整页 Cloudflare Challenge。"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from tempfile import TemporaryDirectory
from typing import Any

from .browser import cookie_from_dict, is_browser_crash_error
from .models import ChallengeRequest, ErrorInfo, SessionRequest, TaskResult

_CHALLENGE_TITLES = {"just a moment..."}
_CHALLENGE_SELECTORS = (
    "#cf-challenge-running",
    ".ray_id",
    ".attack-box",
    "#cf-please-wait",
    "#challenge-spinner",
    "#trk_jschal_js",
    "#turnstile-wrapper",
    ".lds-ring",
)
_CHALLENGE_MARKERS = (
    "challenge-form",
    "cf-chl-",
    "cf-turnstile-response",
    "challenge-stage",
)
_BLOCK_MARKERS = (
    "sorry, you have been blocked",
    "you are unable to access",
)

_PATCH_SCRIPT = """(() => {
  const randomInt = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
  const screenX = randomInt(800, 1200);
  const screenY = randomInt(400, 600);
  Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
  Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
})();
"""


def _packet_status(packet: Any) -> int | None:
    response = getattr(packet, "response", None)
    status = getattr(response, "status", None)
    return int(status) if status is not None else None


def _navigation_timeout_seconds(timeout_ms: int) -> int:
    """为求解交互保留至少一半任务预算，避免导航独占整个任务。"""

    return max(1, timeout_ms // 2_000)


class DrissionChallengeBrowser:
    """封装单个隔离 Browser Context 中的 Challenge 页面。"""

    def __init__(self, owner: DrissionBrowser, context_id: str, page, request: ChallengeRequest):
        self._owner = owner
        self._context_id = context_id
        self.page = page
        page.set.timeouts(page_load=_navigation_timeout_seconds(request.timeoutMs))
        page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=_PATCH_SCRIPT)
        page.run_cdp("Emulation.setLocaleOverride", locale=request.locale)
        page.run_cdp("Network.setExtraHTTPHeaders", headers={"Accept-Language": request.locale})
        if request.userAgent:
            page.set.user_agent(ua=request.userAgent)
        if request.timezone:
            page.run_cdp("Emulation.setTimezoneOverride", timezoneId=request.timezone)

    def navigate(self, url: str) -> int | None:
        self.page.listen.start(url)
        loaded = self.page.get(url)
        packet = self.page.listen.wait(count=1, timeout=5)
        self.page.listen.pause()
        status = _packet_status(packet)
        if loaded is False and status is None:
            html = str(self.page.html or "")
            error_codes = sorted(set(re.findall(r"ERR_[A-Z_]+", html)))
            detail = ", ".join(error_codes) if error_codes else "no HTTP response"
            raise RuntimeError(f"navigation failed: {detail}")
        return status

    def challenge_present(self) -> bool:
        title = str(self.page.title or "").strip().lower()
        if title in _CHALLENGE_TITLES:
            return True
        if self.page.wait.eles_loaded(
            locators=_CHALLENGE_SELECTORS,
            timeout=1,
            any_one=True,
        ):
            return True
        html = str(self.page.html or "").lower()
        return any(marker in html for marker in _CHALLENGE_MARKERS)

    def reset(self) -> None:
        self.page.run_js("try { turnstile.reset() } catch (error) {}")

    def click_verify(self) -> int | None:
        """穿透 closed Shadow DOM，点击 Managed Challenge 内的验证控件。"""

        try:
            response = self.page.ele("@name=cf-turnstile-response", timeout=5)
            wrapper = response.parent()
            iframe = wrapper.shadow_root.ele("tag:iframe", timeout=5)
            iframe_body = iframe.ele("tag:body", timeout=5).shadow_root
            checkbox = iframe_body.ele("tag:input", timeout=5)
            checkbox.focus()
            self.page.listen.resume()
            checkbox.click()
            packet = self.page.listen.wait(count=1, timeout=5)
            return _packet_status(packet)
        except Exception:
            return None
        finally:
            self.page.listen.pause()

    def wait(self, milliseconds: int) -> None:
        self.page.wait(milliseconds / 1000)

    def request(self, request: SessionRequest, session_id: str) -> TaskResult:
        """在已通过挑战的 Context 中继续执行同身份 GET 请求。"""

        if request.method != "GET":
            raise ValueError("challenge sessions currently support GET requests only")
        if request.headers:
            self.page.run_cdp("Network.setExtraHTTPHeaders", headers=request.headers)
        self.page.set.timeouts(page_load=_navigation_timeout_seconds(request.timeoutMs))
        started = time.monotonic()
        http_status = self.navigate(str(request.url))
        return TaskResult(
            status="no_challenge",
            sessionId=session_id,
            finalUrl=self.url,
            httpStatus=http_status,
            cookies=[cookie_from_dict(raw) for raw in self.cookies],
            userAgent=self.user_agent,
            html=self.html if request.returnHtml else None,
            elapsedMs=int((time.monotonic() - started) * 1000),
        )

    @property
    def url(self) -> str:
        return str(self.page.url)

    @property
    def html(self) -> str:
        return str(self.page.html or "")

    @property
    def title(self) -> str:
        return str(self.page.title or "")

    @property
    def user_agent(self) -> str | None:
        value = self.page.user_agent
        return str(value) if value else None

    @property
    def cookies(self) -> list[dict[str, Any]]:
        return list(self.page.cookies(all_info=True))

    def close(self) -> None:
        page, self.page = self.page, None
        if page is not None:
            with suppress(Exception):
                page.close()
        context_id, self._context_id = self._context_id, ""
        if context_id:
            self._owner.dispose(context_id)


class DrissionBrowser:
    """为一个 Challenge Worker 延迟启动并复用一个 Chromium。"""

    def __init__(self, launcher: Callable[[], Any] | None = None):
        self._launcher = launcher
        self._browser = None
        self._profile: TemporaryDirectory | None = None

    def _launch(self):
        if self._launcher:
            return self._launcher()

        from DrissionPage import Chromium, ChromiumOptions

        self._profile = TemporaryDirectory(prefix="cf-profile-")
        options = ChromiumOptions()
        for argument in (
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--no-zygote",
            "--disable-gpu-sandbox",
            "--disable-software-rasterizer",
            "--ignore-certificate-errors",
            "--ignore-ssl-errors",
            "--use-gl=swiftshader",
            "--window-size=1920,1080",
        ):
            options.set_argument(argument)
        options.set_user_data_path(self._profile.name)
        options.auto_port(True)
        options.headless(False)
        chromium_path = os.getenv("CHROMIUM_PATH")
        if chromium_path:
            options.set_paths(browser_path=chromium_path)
        return Chromium(options)

    def _get_browser(self):
        if self._browser is None:
            try:
                self._browser = self._launch()
            except Exception:
                self.close()
                raise
        return self._browser

    @staticmethod
    def _wait_for_driver(browser, target_id: str, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while target_id not in browser._drivers:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"DrissionPage driver not registered for target {target_id}")
            time.sleep(0.1)

    def open(self, request: ChallengeRequest) -> DrissionChallengeBrowser:
        """创建带独立 Cookie、存储和代理的任务 Context。"""

        if request.proxy and (request.proxy.username or request.proxy.password):
            raise ValueError("authenticated proxy is not supported by the challenge backend")

        browser = self._get_browser()
        context_options = {}
        if request.proxy:
            context_options["proxyServer"] = request.proxy.server()
        context_id = browser._run_cdp("Target.createBrowserContext", **context_options)[
            "browserContextId"
        ]
        try:
            target_id = browser._run_cdp(
                "Target.createTarget",
                url="about:blank",
                browserContextId=context_id,
            )["targetId"]
            self._wait_for_driver(browser, target_id)
            page = browser.get_tab(target_id)
            return DrissionChallengeBrowser(self, context_id, page, request)
        except Exception:
            self.dispose(context_id)
            raise

    def dispose(self, context_id: str) -> None:
        if self._browser is None:
            return
        try:
            self._browser._run_cdp("Target.disposeBrowserContext", browserContextId=context_id)
        except Exception:
            self.close()

    def close(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            with suppress(Exception):
                browser.quit(force=True)
        if self._profile is not None:
            self._profile.cleanup()
            self._profile = None


def _result(
    browser,
    request: ChallengeRequest,
    *,
    status: str,
    http_status: int | None,
    elapsed_ms: int,
    error: ErrorInfo | None = None,
) -> TaskResult:
    return TaskResult(
        status=status,
        finalUrl=browser.url,
        httpStatus=http_status,
        cookies=[cookie_from_dict(raw) for raw in browser.cookies],
        userAgent=browser.user_agent,
        html=browser.html if request.returnHtml else None,
        elapsedMs=elapsed_ms,
        error=error,
    )


def solve_cloudflare_challenge(
    request: ChallengeRequest,
    *,
    browser_factory: Callable[[ChallengeRequest], Any] | None = None,
    retain_browser: Callable[[Any], str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TaskResult:
    """在隔离 Context 中求解整页 Challenge 并导出可复用浏览器身份。"""

    started = clock()
    browser = None
    owned_browser = None
    retained = False

    def success(status: str, http_status: int | None) -> TaskResult:
        nonlocal retained
        result = _result(
            browser,
            request,
            status=status,
            http_status=http_status,
            elapsed_ms=int((clock() - started) * 1000),
        )
        if request.retainSession and retain_browser is not None:
            result.sessionId = retain_browser(browser)
            retained = True
        return result

    try:
        if browser_factory is None:
            owned_browser = DrissionBrowser()
            browser_factory = owned_browser.open
        browser = browser_factory(request)
        http_status = browser.navigate(str(request.url))
        title = browser.title.strip().lower()
        html = str(browser.html or "").lower()
        if "attention required" in title or any(marker in html for marker in _BLOCK_MARKERS):
            return _result(
                browser,
                request,
                status="blocked",
                http_status=http_status,
                elapsed_ms=int((clock() - started) * 1000),
                error=ErrorInfo(
                    type="CLOUDFLARE_BLOCKED",
                    message="Cloudflare blocked this browser or network identity",
                    retryable=True,
                    stage="challenge",
                ),
            )
        if (
            http_status is not None
            and http_status >= 500
            and ("cloudflare" in html or "web server is returning an unknown error" in title)
        ):
            return _result(
                browser,
                request,
                status="cloudflare_error",
                http_status=http_status,
                elapsed_ms=int((clock() - started) * 1000),
                error=ErrorInfo(
                    type="CLOUDFLARE_UPSTREAM_ERROR",
                    message=f"Cloudflare returned HTTP {http_status}",
                    retryable=True,
                    stage="challenge",
                ),
            )
        challenge_found = browser.challenge_present()
        if not challenge_found:
            return success("no_challenge", http_status)

        browser.reset()
        deadline = started + request.timeoutMs / 1000
        while clock() < deadline:
            response_status = browser.click_verify()
            if response_status is not None:
                http_status = response_status
            if not browser.challenge_present():
                return success("solved", http_status)
            browser.wait(500)

        return _result(
            browser,
            request,
            status="timeout",
            http_status=http_status,
            elapsed_ms=int((clock() - started) * 1000),
            error=ErrorInfo(
                type="CHALLENGE_TIMEOUT",
                message=f"challenge did not clear within {request.timeoutMs} ms",
                retryable=True,
                stage="challenge",
            ),
        )
    except Exception as exc:
        browser_crashed = is_browser_crash_error(exc)
        return TaskResult(
            status="browser_crashed" if browser_crashed else "failed",
            elapsedMs=int((clock() - started) * 1000),
            error=ErrorInfo(
                type="BROWSER_CRASH" if browser_crashed else "CHALLENGE_FAILED",
                message=str(exc),
                retryable=True,
                stage="challenge",
            ),
        )
    finally:
        if browser is not None and not retained:
            browser.close()
        if owned_browser is not None:
            owned_browser.close()
