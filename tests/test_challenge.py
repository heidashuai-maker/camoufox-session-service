import pytest

import camoufox_service.cloudflare as cloudflare
from camoufox_service.cloudflare import (
    DrissionChallengeBrowser,
    _navigation_timeout_seconds,
    solve_cloudflare_challenge,
)
from camoufox_service.models import ChallengeRequest


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeChallengeBrowser:
    def __init__(
        self,
        states,
        *,
        clock=None,
        navigate_error=None,
        http_status=403,
        title="",
        html="<html>passed</html>",
    ):
        self.states = iter(states)
        self.last_state = False
        self.clock = clock
        self.navigate_error = navigate_error
        self.url = "https://example.test/final"
        self.html = html
        self.title = title
        self.http_status = http_status
        self.user_agent = "Chromium/126"
        self.cookies = [
            {
                "name": "cf_clearance",
                "value": "opaque",
                "domain": ".example.test",
                "path": "/",
                "expires": 1_900_000_000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
        self.closed = False
        self.clicks = 0

    def navigate(self, url):
        if self.navigate_error:
            raise self.navigate_error
        self.url = str(url)
        return self.http_status

    def challenge_present(self):
        self.last_state = next(self.states, self.last_state)
        return self.last_state

    def click_verify(self):
        self.clicks += 1
        return 200

    def wait(self, milliseconds):
        if self.clock:
            self.clock.advance(milliseconds / 1000)

    def close(self):
        self.closed = True


def solve(browser, **request_values):
    clock = browser.clock or FakeClock()
    request = ChallengeRequest(url="https://example.test", **request_values)
    return solve_cloudflare_challenge(request, browser_factory=lambda _: browser, clock=clock)


def test_page_without_challenge_returns_no_challenge_and_closes_browser():
    browser = FakeChallengeBrowser([False])

    result = solve(browser, returnHtml=True)

    assert result.status == "no_challenge"
    assert result.httpStatus == 403
    assert result.html == "<html>passed</html>"
    assert browser.closed is True


def test_attention_required_page_returns_blocked():
    browser = FakeChallengeBrowser(
        [False],
        title="Attention Required! | Cloudflare",
        html="<h1>Sorry, you have been blocked</h1>",
    )

    result = solve(browser)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.type == "CLOUDFLARE_BLOCKED"


def test_cloudflare_520_page_returns_cloudflare_error():
    browser = FakeChallengeBrowser(
        [False],
        http_status=520,
        title="example.test | 520: Web server is returning an unknown error",
        html="<footer>Performance &amp; security by Cloudflare</footer>",
    )

    result = solve(browser)

    assert result.status == "cloudflare_error"
    assert result.error is not None
    assert result.error.type == "CLOUDFLARE_UPSTREAM_ERROR"


def test_detected_challenge_is_clicked_until_clearance_is_available():
    browser = FakeChallengeBrowser([True, False])

    result = solve(browser)

    assert result.status == "solved"
    assert result.httpStatus == 200
    assert result.cookies[0].name == "cf_clearance"
    assert result.userAgent == "Chromium/126"
    assert browser.clicks == 1
    assert browser.closed is True


def test_successful_challenge_can_retain_browser_context():
    browser = FakeChallengeBrowser([True, False])

    result = solve_cloudflare_challenge(
        ChallengeRequest(url="https://example.test", retainSession=True),
        browser_factory=lambda _: browser,
        retain_browser=lambda retained: "challenge-session-1",
        clock=FakeClock(),
    )

    assert result.sessionId == "challenge-session-1"
    assert browser.closed is False


def test_challenge_timeout_returns_typed_error_and_closes_browser():
    clock = FakeClock()
    browser = FakeChallengeBrowser([True], clock=clock)

    result = solve(browser, timeoutMs=1_000)

    assert result.status == "timeout"
    assert result.error is not None
    assert result.error.type == "CHALLENGE_TIMEOUT"
    assert result.error.stage == "challenge"
    assert browser.closed is True


def test_challenge_can_omit_html():
    browser = FakeChallengeBrowser([False])

    result = solve(browser, returnHtml=False)

    assert result.html is None


def test_challenge_failure_is_typed_and_browser_is_closed():
    browser = FakeChallengeBrowser([], navigate_error=RuntimeError("navigation failed"))

    result = solve(browser)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.type == "CHALLENGE_FAILED"
    assert browser.closed is True


def test_drission_navigation_failure_is_not_reported_as_no_challenge():
    class Listener:
        def start(self, _url):
            pass

        def wait(self, **_kwargs):
            return None

        def pause(self):
            pass

    class Page:
        listen = Listener()
        html = "<html>ERR_CONNECTION_CLOSED</html>"

        def get(self, _url):
            return False

    browser = object.__new__(DrissionChallengeBrowser)
    browser.page = Page()

    with pytest.raises(RuntimeError, match="ERR_CONNECTION_CLOSED"):
        browser.navigate("https://example.test")


def test_navigation_uses_only_half_of_the_task_budget():
    assert _navigation_timeout_seconds(90_000) == 45
    assert _navigation_timeout_seconds(1_000) == 1


def test_normal_page_script_reference_is_not_a_challenge():
    class Wait:
        def eles_loaded(self, **_kwargs):
            return False

    class Page:
        title = "Business Search"
        wait = Wait()
        html = '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'

    browser = object.__new__(DrissionChallengeBrowser)
    browser.page = Page()

    assert browser.challenge_present() is False


class FakePageSetter:
    def __init__(self, page):
        self.page = page

    def timeouts(self, **values):
        self.page.timeout_values = values

    def user_agent(self, *, ua):
        self.page.user_agent_value = ua


class FakeContextPage:
    def __init__(self):
        self.set = FakePageSetter(self)
        self.cdp_calls = []
        self.closed = False
        self.timeout_values = None
        self.user_agent_value = None

    def run_cdp(self, method, **values):
        self.cdp_calls.append((method, values))

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, *, fail_dispose=False):
        self._drivers = set()
        self.cdp_calls = []
        self.pages = {}
        self.context_count = 0
        self.target_count = 0
        self.quit_calls = 0
        self.fail_dispose = fail_dispose

    def _run_cdp(self, method, **values):
        self.cdp_calls.append((method, values))
        if method == "Target.createBrowserContext":
            self.context_count += 1
            return {"browserContextId": f"context-{self.context_count}"}
        if method == "Target.createTarget":
            self.target_count += 1
            target_id = f"target-{self.target_count}"
            self._drivers.add(target_id)
            self.pages[target_id] = FakeContextPage()
            return {"targetId": target_id}
        if method == "Target.disposeBrowserContext":
            if self.fail_dispose:
                raise RuntimeError("dispose failed")
            return {}
        raise AssertionError(f"unexpected CDP method: {method}")

    def get_tab(self, target_id):
        return self.pages[target_id]

    def quit(self, *, force):
        assert force is True
        self.quit_calls += 1


def test_drission_browser_reuses_chromium_and_sets_proxy_per_context():
    chromium = FakeChromium()
    launches = []
    browser = cloudflare.DrissionBrowser(launcher=lambda: launches.append(True) or chromium)

    first = browser.open(
        ChallengeRequest(
            url="https://example.test",
            proxy="socks5h://proxy.example:8501",
            userAgent="Agent One",
            locale="zh-CN",
            timezone="Asia/Shanghai",
        )
    )
    second = browser.open(
        ChallengeRequest(
            url="https://example.test",
            proxy="socks5h://proxy.example:8502",
        )
    )

    assert launches == [True]
    context_calls = [
        call for call in chromium.cdp_calls if call[0] == "Target.createBrowserContext"
    ]
    assert context_calls == [
        ("Target.createBrowserContext", {"proxyServer": "socks5://proxy.example:8501"}),
        ("Target.createBrowserContext", {"proxyServer": "socks5://proxy.example:8502"}),
    ]
    assert first.page.user_agent_value == "Agent One"
    assert (
        "Emulation.setTimezoneOverride",
        {"timezoneId": "Asia/Shanghai"},
    ) in first.page.cdp_calls
    assert ("Emulation.setLocaleOverride", {"locale": "zh-CN"}) in first.page.cdp_calls

    first.close()
    second.close()
    dispose_calls = [
        call for call in chromium.cdp_calls if call[0] == "Target.disposeBrowserContext"
    ]
    assert dispose_calls == [
        ("Target.disposeBrowserContext", {"browserContextId": "context-1"}),
        ("Target.disposeBrowserContext", {"browserContextId": "context-2"}),
    ]
    assert first.page is None
    assert second.page is None

    browser.close()
    assert chromium.quit_calls == 1


def test_context_dispose_failure_discards_persistent_chromium():
    chromium = FakeChromium(fail_dispose=True)
    browser = cloudflare.DrissionBrowser(launcher=lambda: chromium)
    task = browser.open(ChallengeRequest(url="https://example.test"))

    task.close()

    assert chromium.quit_calls == 1
