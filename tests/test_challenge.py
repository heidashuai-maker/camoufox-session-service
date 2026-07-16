from camoufox_service.challenge import detect_challenge, solve_challenge
from camoufox_service.models import ChallengeRequest


class FakeResponse:
    status = 200


class FakeFrame:
    def __init__(self, url):
        self.url = url


class FakePage:
    def __init__(self, *, title="Example", body="<html>ok</html>"):
        self.url = "https://example.test/"
        self._title = title
        self._body = body
        self.frames = []

    def title(self):
        return self._title

    def content(self):
        return self._body

    def goto(self, url, **kwargs):
        self.url = str(url)
        return FakeResponse()

    def wait_for_timeout(self, milliseconds):
        return None

    def evaluate(self, script):
        return "FakeFox/1.0"


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self):
        return []

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.context = FakeContext(page)

    def new_context(self, **options):
        return self.context


def test_detects_cloudflare_interstitial_from_page_markers():
    page = FakePage(title="Just a moment...", body='<form id="challenge-form"></form>')
    page.frames = [FakeFrame("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/x")]

    evidence = detect_challenge(page)

    assert evidence.detected is True
    assert evidence.vendor == "cloudflare"


def test_detects_cloudflare_challenge_from_redirect_url():
    page = FakePage()
    page.url = "https://example.test/?__cf_chl_rt_tk=opaque"

    evidence = detect_challenge(page)

    assert evidence.detected is True
    assert evidence.vendor == "cloudflare"


def test_challenge_without_markers_returns_no_challenge_and_closes_context(monkeypatch):
    browser = FakeBrowser(FakePage())
    times = iter([0.0, 2.0, 2.1, 2.2])
    monkeypatch.setattr("camoufox_service.challenge.time.monotonic", lambda: next(times, 2.2))

    result = solve_challenge(
        browser,
        ChallengeRequest(url="https://example.test", waitSeconds=1),
    )

    assert result.status == "no_challenge"
    assert result.httpStatus == 200
    assert result.html == "<html>ok</html>"
    assert browser.context.closed is True
