import os

import pytest

from camoufox_service.models import TurnstileRequest
from camoufox_service.turnstile import solve_turnstile


@pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="browser integration disabled",
)
def test_official_turnstile_dummy_key():
    from camoufox.sync_api import Camoufox

    with Camoufox(headless=True, humanize=True, block_webrtc=True) as browser:
        result = solve_turnstile(
            browser,
            TurnstileRequest(
                url="https://example.test",
                siteKey="1x00000000000000000000AA",
                strategy="minimal",
            ),
        )

    assert result.status == "solved", result.model_dump()
    assert result.token
