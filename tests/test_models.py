import pytest
from pydantic import ValidationError

from camoufox_service.config import Settings
from camoufox_service.models import (
    ChallengeRequest,
    RecaptchaV2Request,
    SessionCreateRequest,
    TaskResult,
    TurnstileRequest,
)


def test_turnstile_rejects_unknown_strategy():
    with pytest.raises(ValidationError):
        TurnstileRequest(
            url="https://example.test",
            siteKey="key",
            strategy="legacy",
        )


def test_minimal_turnstile_requires_site_key():
    with pytest.raises(ValidationError, match="siteKey"):
        TurnstileRequest(url="https://example.test", strategy="minimal")


def test_requests_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ChallengeRequest(url="https://example.test", legacyMode="waf-session")


def test_proxy_string_is_normalized():
    request = RecaptchaV2Request(
        url="https://example.test",
        siteKey="key",
        proxy="socks5h://user:pass@127.0.0.1:8500",
    )

    assert request.proxy is not None
    assert request.proxy.protocol == "socks5"
    assert request.proxy.host == "127.0.0.1"
    assert request.proxy.port == 8500
    assert request.proxy.username == "user"
    assert request.proxy.password == "pass"


def test_task_result_has_stable_envelope():
    result = TaskResult(status="solved", token="abc", elapsedMs=12)

    payload = result.model_dump()
    assert payload["cookies"] == []
    assert payload["error"] is None
    assert payload["sessionId"] is None


def test_session_accepts_cookies_returned_by_solver():
    request = SessionCreateRequest(
        cookies=[
            {
                "name": "cf_clearance",
                "value": "token",
                "domain": ".example.test",
                "path": "/",
            }
        ]
    )

    assert request.cookies[0].name == "cf_clearance"


def test_settings_read_worker_limits(monkeypatch):
    monkeypatch.setenv("CAMOUFOX_WORKERS", "3")
    monkeypatch.setenv("CAMOUFOX_QUEUE_SIZE", "9")

    settings = Settings.from_env()

    assert settings.workers == 3
    assert settings.queue_size == 9


def test_settings_accept_virtual_display(monkeypatch):
    monkeypatch.setenv("CAMOUFOX_HEADLESS", "virtual")

    settings = Settings.from_env()

    assert settings.headless == "virtual"
