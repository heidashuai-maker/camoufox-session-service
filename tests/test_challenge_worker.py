import pytest

from camoufox_service.challenge_worker import ChallengeRuntime
from camoufox_service.models import TaskResult
from camoufox_service.worker import BrowserRuntime


def test_challenge_worker_accepts_only_health_and_challenge_jobs():
    calls = []

    def solver(request):
        calls.append(request)
        return TaskResult(status="solved", elapsedMs=12)

    runtime = ChallengeRuntime(solver=solver)

    assert runtime.handle("health", {}) == {"status": "ok", "engine": "drissionpage"}
    result = runtime.handle("challenge.solve", {"url": "https://example.test"})
    assert result["status"] == "solved"
    assert str(calls[0].url) == "https://example.test/"
    with pytest.raises(ValueError, match="unsupported job kind"):
        runtime.handle("turnstile.solve", {})


def test_camoufox_worker_no_longer_handles_page_challenge(monkeypatch):
    runtime = BrowserRuntime()
    monkeypatch.setattr(runtime, "_browser", lambda: None)

    with pytest.raises(ValueError, match="unsupported job kind"):
        runtime.handle("challenge.solve", {"url": "https://example.test"})


def test_browser_crash_discards_persistent_chromium():
    class Browser:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    browser = Browser()
    runtime = ChallengeRuntime(
        solver=lambda _: TaskResult(status="browser_crashed", elapsedMs=1),
        browser=browser,
    )

    with pytest.raises(RuntimeError, match="browser process crashed"):
        runtime.handle("challenge.solve", {"url": "https://example.test"})

    assert browser.close_calls == 1
