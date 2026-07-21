from dataclasses import dataclass

from camoufox_service.models import ProxyConfig, RecaptchaV2Request
from camoufox_service.recaptcha import (
    RecaptchaAudioSolver,
    RecaptchaError,
    RecaptchaV2Solver,
    build_recaptcha_html,
    choose_fresh_audio_url,
)
from camoufox_service.recaptcha_audio import AudioChallengeProcessor
from camoufox_service.worker import BrowserRuntime


class FakePage:
    def __init__(self):
        self.url = "https://example.test/"

    def on(self, event, handler):
        return None

    def route(self, pattern, handler):
        self.route_handler = handler

    def goto(self, url, **kwargs):
        self.url = str(url)

    def wait_for_selector(self, selector, timeout):
        return None

    def evaluate(self, script):
        return "FakeFox/1.0"


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self, urls=None):
        return [{"name": "sid", "value": "abc", "domain": "example.test", "path": "/"}]

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()

    def new_context(self, **options):
        return self.context


@dataclass
class FakeSolveResult:
    token: str = "recaptcha-token"
    attempts: int = 1


class FakeAudioSolver:
    def __init__(self, page, **kwargs):
        self.page = page

    def solve_recaptcha(self, processor, *, max_attempts):
        return FakeSolveResult()


class FakeProcessor:
    def __init__(self, **kwargs):
        self.closed = False

    def close(self):
        self.closed = True


def test_recaptcha_template_escapes_values():
    html = build_recaptcha_html(
        RecaptchaV2Request(
            url="https://example.test",
            siteKey='bad"<script>',
            query='A"<script>',
        )
    )

    assert 'bad"<script>' not in html
    assert 'A"<script>' not in html
    assert "g-recaptcha-response" in html


def test_audio_transcript_normalization_is_stable():
    assert AudioChallengeProcessor.normalize_transcript("  Seven,  TWO!! ") == "seven two"


def test_audio_fallback_session_uses_task_proxy(monkeypatch):
    monkeypatch.setattr(
        AudioChallengeProcessor,
        "_configure_audio_tools",
        classmethod(lambda cls: None),
    )
    proxy = ProxyConfig(
        protocol="socks5",
        host="127.0.0.1",
        port=8501,
        username="user",
        password="pass",
    )

    processor = AudioChallengeProcessor(
        user_agent="FakeFox/1.0",
        audio_cache={},
        page=object(),
        proxy=proxy,
    )
    try:
        assert processor.session.proxies == {
            "http": "socks5h://user:pass@127.0.0.1:8501",
            "https": "socks5h://user:pass@127.0.0.1:8501",
        }
    finally:
        processor.close()


def test_audio_retry_requires_a_new_url():
    assert choose_fresh_audio_url("https://audio/2", "https://audio/1") == "https://audio/2"
    assert choose_fresh_audio_url("https://audio/1", "https://audio/1") is None


def test_audio_url_poll_tolerates_source_not_ready():
    class Frames:
        def audio_url(self, timeout):
            raise RecaptchaError("not ready")

    solver = object.__new__(RecaptchaAudioSolver)
    solver.frames = Frames()

    assert solver.read_audio_url() == ""


def test_solver_returns_token_session_and_closes_context():
    browser = FakeBrowser()
    solver = RecaptchaV2Solver(
        browser,
        audio_solver_factory=FakeAudioSolver,
        processor_factory=FakeProcessor,
    )

    result = solver.solve(
        RecaptchaV2Request(
            url="https://example.test/captcha",
            sessionUrl="https://example.test/session",
            siteKey="site-key",
        )
    )

    assert result.status == "solved"
    assert result.token == "recaptcha-token"
    assert result.userAgent == "FakeFox/1.0"
    assert result.cookies[0].name == "sid"
    assert browser.context.closed is True


def test_solver_passes_task_proxy_to_audio_processor():
    browser = FakeBrowser()
    processor_options = {}

    def processor_factory(**options):
        processor_options.update(options)
        return FakeProcessor()

    solver = RecaptchaV2Solver(
        browser,
        audio_solver_factory=FakeAudioSolver,
        processor_factory=processor_factory,
    )
    request = RecaptchaV2Request(
        url="https://example.test/captcha",
        siteKey="site-key",
        proxy="socks5h://127.0.0.1:8501",
    )

    result = solver.solve(request)

    assert result.status == "solved"
    assert processor_options["proxy"] == request.proxy


def test_worker_dispatches_recaptcha_job(monkeypatch):
    runtime = BrowserRuntime()
    monkeypatch.setattr(runtime, "_browser", lambda: object())
    monkeypatch.setattr(
        "camoufox_service.worker.solve_recaptcha",
        lambda browser, request: __import__(
            "camoufox_service.models", fromlist=["TaskResult"]
        ).TaskResult(status="solved", token="recaptcha-token", elapsedMs=1),
        raising=False,
    )

    result = runtime.handle(
        "recaptcha.v2.solve",
        {
            "url": "https://example.test",
            "siteKey": "site-key",
        },
    )

    assert result["token"] == "recaptcha-token"
