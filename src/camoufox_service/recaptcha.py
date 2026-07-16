"""reCAPTCHA v2 复选框、Challenge Frame 与音频流程编排。"""

from __future__ import annotations

import html
import random
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .browser import (
    context_options,
    cookies_from_context,
    is_browser_crash_error,
    page_user_agent,
)
from .models import ErrorInfo, RecaptchaV2Request, TaskResult
from .recaptcha_audio import AudioChallengeProcessor


class RecaptchaError(RuntimeError):
    pass


class RecaptchaNotFound(RecaptchaError):
    pass


class RecaptchaRateLimited(RecaptchaError):
    pass


@dataclass
class RecaptchaSolveResult:
    token: str
    attempts: int


def redact_url(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if not parsed.scheme or not parsed.netloc:
        return str(value or "")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def choose_fresh_audio_url(current: str, previous: str | None) -> str | None:
    return current if current and current != previous else None


def build_recaptcha_html(request: RecaptchaV2Request) -> str:
    site_key = html.escape(request.siteKey, quote=True)
    language = html.escape(request.locale or "en-US", quote=True)
    query = html.escape(request.query or "AAA", quote=True)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>reCAPTCHA</title></head>
<body>
  <form id="recaptcha-form">
    <input name="search" value="1">
    <input name="corpname" value="{query}">
    <script src="https://www.google.com/recaptcha/api.js?hl={language}" async defer></script>
    <div class="g-recaptcha" data-sitekey="{site_key}" data-callback="onSolved"></div>
    <textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea>
    <script>
      window.onSolved = function (token) {{
        window.__recaptchaToken = token;
        document.querySelector('#g-recaptcha-response').value = token;
      }};
    </script>
  </form>
</body>
</html>"""


class FrameState:
    """集中查找 reCAPTCHA Frame，并读取复选框与音频界面状态。"""

    def __init__(self, page, timeout: float = 15):
        self.page = page
        self.timeout = timeout

    @staticmethod
    def _milliseconds(seconds: float) -> int:
        return int(seconds * 1000)

    def _find_frame(self, marker: str, selectors: tuple[str, ...], timeout: float | None = None):
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            for frame in self.page.frames:
                if marker in str(frame.url or "") and "/recaptcha/" in str(frame.url or ""):
                    return frame
            for selector in selectors:
                try:
                    handle = self.page.query_selector(selector)
                    frame = handle.content_frame() if handle else None
                    if frame:
                        return frame
                except Exception:
                    pass
            time.sleep(0.2)
        raise RecaptchaNotFound(f"reCAPTCHA frame not found: {marker}")

    def anchor(self, timeout: float | None = None):
        return self._find_frame(
            "/anchor",
            ('iframe[src*="/recaptcha/"][src*="/anchor"]', 'iframe[title*="reCAPTCHA"]'),
            timeout,
        )

    def challenge(self, timeout: float | None = None):
        return self._find_frame(
            "/bframe",
            ('iframe[src*="/recaptcha/"][src*="/bframe"]', 'iframe[title*="recaptcha"]'),
            timeout,
        )

    @staticmethod
    def visible(frame, selector: str, timeout: float = 0.3) -> bool:
        try:
            return frame.locator(selector).first.is_visible(timeout=int(timeout * 1000))
        except Exception:
            return False

    def checkbox_visible(self) -> bool:
        try:
            return self.visible(self.anchor(timeout=0.5), "#recaptcha-anchor")
        except RecaptchaError:
            return False

    def checked(self) -> bool:
        try:
            value = (
                self.anchor(timeout=0.5)
                .locator("#recaptcha-anchor")
                .first.get_attribute("aria-checked", timeout=500)
            )
            return str(value).lower() == "true"
        except Exception:
            return False

    def audio_button_visible(self) -> bool:
        try:
            return self.visible(self.challenge(timeout=0.5), "#recaptcha-audio-button")
        except RecaptchaError:
            return False

    def audio_ready(self) -> bool:
        try:
            frame = self.challenge(timeout=0.5)
            return self.visible(frame, "#audio-response") and self.visible(
                frame, "#recaptcha-verify-button"
            )
        except RecaptchaError:
            return False

    def rate_limited(self) -> bool:
        try:
            frame = self.challenge(timeout=0.5)
            text = frame.locator("body").inner_text(timeout=500).lower()
            return (
                "try again later" in text
                or "automated queries" in text
                or self.visible(frame, ".rc-doscaptcha-header")
            )
        except Exception:
            return False

    def audio_url(self, timeout: float | None = None) -> str:
        frame = self.challenge(timeout=timeout)
        selectors = (
            ("#audio-source", "src"),
            ("audio source", "src"),
            ("audio", "src"),
            (".rc-audiochallenge-tdownload-link", "href"),
            ('a[href*="payload"]', "href"),
        )
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            for selector, attribute in selectors:
                try:
                    value = frame.locator(selector).first.get_attribute(attribute, timeout=500)
                    if value:
                        return str(value)
                except Exception:
                    pass
            time.sleep(0.25)
        raise RecaptchaError("audio source not found")

    def audio_language(self) -> str:
        try:
            source = self.page.locator(
                'iframe[src*="/recaptcha/"][src*="/anchor"]'
            ).first.get_attribute("src")
            query = urlsplit(source).query
            for part in query.split("&"):
                if part.startswith("hl="):
                    return AudioChallengeProcessor.normalize_language(part.split("=", 1)[1])
        except Exception:
            pass
        return "en-US"

    def click(self, locator) -> None:
        locator.scroll_into_view_if_needed(timeout=1000)
        box = locator.bounding_box(timeout=1000)
        if not box:
            raise RecaptchaError("click target is not visible")
        x = box["x"] + box["width"] * random.uniform(0.42, 0.58)
        y = box["y"] + box["height"] * random.uniform(0.42, 0.58)
        self.page.mouse.move(x, y, steps=random.randint(8, 18))
        self.page.wait_for_timeout(random.randint(80, 180))
        self.page.mouse.click(x, y)


class RecaptchaAudioSolver:
    """编排复选框点击、音频切换、识别提交和 Token 等待。"""

    def __init__(self, page, timeout: float = 15, token_timeout: float = 25):
        self.page = page
        self.timeout = timeout
        self.token_timeout = token_timeout
        self.frames = FrameState(page, timeout)

    def token(self) -> str:
        try:
            value = self.page.evaluate(
                "() => window.__recaptchaToken || "
                "document.querySelector('#g-recaptcha-response')?.value || ''"
            )
            return str(value or "").strip()
        except Exception:
            return ""

    def _wait(self, predicate, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.25)
        return None

    def read_audio_url(self) -> str:
        try:
            return self.frames.audio_url(timeout=0.5)
        except RecaptchaError:
            return ""

    def _wait_token(self) -> str:
        token = self._wait(self.token, self.token_timeout)
        if not token:
            raise RecaptchaError("waiting for reCAPTCHA token timed out")
        return token

    def _submit_audio(self, text: str) -> None:
        frame = self.frames.challenge()
        field = frame.locator("#audio-response").first
        self.frames.click(field)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.page.keyboard.type(text.lower(), delay=random.randint(35, 90))
        self.frames.click(frame.locator("#recaptcha-verify-button").first)

    def solve_recaptcha(self, processor, *, max_attempts: int = 3, **_) -> RecaptchaSolveResult:
        """优先读取已有 Token，再按复选框、音频 Challenge 顺序求解。"""

        existing = self.token()
        if existing:
            return RecaptchaSolveResult(existing, 0)
        visible = self._wait(
            lambda: (
                self.frames.checkbox_visible()
                or self.frames.audio_button_visible()
                or self.frames.audio_ready()
            ),
            35,
        )
        if not visible:
            raise RecaptchaNotFound("reCAPTCHA widget did not load")
        if self.frames.rate_limited():
            raise RecaptchaRateLimited("reCAPTCHA rate limited")
        if self.frames.checkbox_visible():
            self.frames.click(self.frames.anchor().locator(".rc-anchor-content").first)
            solved = self._wait(
                lambda: self.token() or self.frames.checked() or self.frames.audio_button_visible(),
                self.timeout,
            )
            if solved and (self.token() or self.frames.checked()):
                return RecaptchaSolveResult(self._wait_token(), 0)
        if not self.frames.audio_ready():
            frame = self.frames.challenge()
            self.frames.click(frame.locator("#recaptcha-audio-button").first)
            if not self._wait(self.frames.audio_ready, self.timeout):
                raise RecaptchaError("audio challenge did not become ready")

        previous_url = None
        for attempt in range(1, max_attempts + 1):
            if self.frames.rate_limited():
                raise RecaptchaRateLimited("reCAPTCHA rate limited")
            audio_url = self._wait(
                lambda previous_url=previous_url: choose_fresh_audio_url(
                    self.read_audio_url(), previous_url
                ),
                self.timeout,
            )
            if not audio_url:
                raise RecaptchaError("fresh audio challenge URL did not appear")
            previous_url = audio_url
            text = processor.try_recognize_from_url(
                audio_url,
                language=self.frames.audio_language(),
                timeout=self.timeout,
            )
            if text:
                self._submit_audio(text)
                if self._wait(lambda: self.token() or self.frames.checked(), self.timeout):
                    return RecaptchaSolveResult(self._wait_token(), attempt)
            if attempt < max_attempts:
                self.frames.click(self.frames.challenge().locator("#recaptcha-reload-button").first)
        raise RecaptchaError(f"audio challenge failed after {max_attempts} attempts")


class RecaptchaV2Solver:
    """管理 reCAPTCHA v2 的浏览器上下文、网络拦截和资源清理。"""

    def __init__(
        self,
        browser,
        *,
        audio_solver_factory=RecaptchaAudioSolver,
        processor_factory=AudioChallengeProcessor,
    ):
        self.browser = browser
        self.audio_solver_factory = audio_solver_factory
        self.processor_factory = processor_factory

    @staticmethod
    def _navigate(page, url: str, timeout_ms: int) -> None:
        last_error = None
        for attempt in range(1, 3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                return
            except Exception as exc:
                last_error = exc
                time.sleep(attempt)
        raise last_error or RecaptchaError("navigation failed")

    @staticmethod
    def _setup_network(
        page, request: RecaptchaV2Request, phase: dict, audio_cache: dict[str, bytes]
    ) -> None:
        """缓存音频响应，并在目标 Origin 下提供最小组件页面。"""

        target = str(request.url).rstrip("/")

        def remember_audio(response):
            if "google.com/recaptcha/api2/payload" not in response.url:
                return
            try:
                body = response.body()
                if body:
                    audio_cache[response.url] = body
            except Exception:
                pass

        def route_request(route):
            current = route.request.url.rstrip("/")
            resource_type = route.request.resource_type
            if phase["value"] == "captcha" and current == target and resource_type == "document":
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=build_recaptcha_html(request),
                )
            elif (
                resource_type in {"image", "stylesheet", "font", "media"}
                and "recaptcha" not in route.request.url
            ):
                route.abort()
            else:
                route.continue_()

        page.on("response", remember_audio)
        page.route("**/*", route_request)

    def solve(self, request: RecaptchaV2Request) -> TaskResult:
        """准备 Session 页面和组件页面，执行音频求解并统一归类错误。"""

        started = time.monotonic()
        context = None
        processor = None
        try:
            context = self.browser.new_context(**context_options(request))
            page = context.new_page()
            phase = {"value": "session"}
            audio_cache: dict[str, bytes] = {}
            self._setup_network(page, request, phase, audio_cache)
            self._navigate(page, str(request.sessionUrl or request.url), request.timeoutMs)
            phase["value"] = "captcha"
            self._navigate(page, str(request.url), request.timeoutMs)
            page.wait_for_selector(".g-recaptcha", timeout=min(request.timeoutMs, 15_000))
            user_agent = page_user_agent(page)
            processor = self.processor_factory(
                user_agent=user_agent or "Mozilla/5.0",
                audio_cache=audio_cache,
                page=page,
            )
            solved = self.audio_solver_factory(page, timeout=15, token_timeout=25).solve_recaptcha(
                processor,
                max_attempts=request.maxAudioAttempts,
                wait=True,
                wait_timeout=35,
            )
            if len(solved.token) < 10:
                raise RecaptchaError("reCAPTCHA returned an invalid token")
            return TaskResult(
                status="solved",
                token=solved.token,
                finalUrl=str(page.url),
                cookies=cookies_from_context(context),
                userAgent=user_agent,
                elapsedMs=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            rate_limited = isinstance(exc, RecaptchaRateLimited)
            browser_crashed = is_browser_crash_error(exc)
            if browser_crashed:
                error_type = "BROWSER_CRASH"
            elif rate_limited:
                error_type = "RECAPTCHA_RATE_LIMIT"
            else:
                error_type = "RECAPTCHA_FAILED"
            return TaskResult(
                status="browser_crashed" if browser_crashed else "failed",
                elapsedMs=int((time.monotonic() - started) * 1000),
                error=ErrorInfo(
                    type=error_type,
                    message=str(exc),
                    retryable=True,
                    stage="recaptcha",
                ),
            )
        finally:
            if processor:
                processor.close()
            if context is not None:
                context.close()


def solve_recaptcha(browser, request: RecaptchaV2Request) -> TaskResult:
    """使用默认依赖创建并执行 reCAPTCHA v2 求解器。"""

    return RecaptchaV2Solver(browser).solve(request)
