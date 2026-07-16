from camoufox_service.models import TurnstileRequest
from camoufox_service.turnstile import build_turnstile_html, solve_turnstile


class FakeResponse:
    status = 200


class FakePage:
    def __init__(self):
        self.url = "https://example.test/"
        self.route_handler = None

    def route(self, pattern, handler):
        self.route_handler = handler

    def goto(self, url, **kwargs):
        self.url = str(url)
        return FakeResponse()

    def wait_for_function(self, script, timeout):
        return None

    def evaluate(self, script):
        if "navigator.userAgent" in script:
            return "FakeFox/1.0"
        if "cf-turnstile-response" in script:
            return "dummy-token"
        return None

    def content(self):
        return "<html>done</html>"


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self):
        return [{"name": "sid", "value": "1", "domain": "example.test", "path": "/"}]

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()

    def new_context(self, **options):
        self.options = options
        return self.context


def test_turnstile_template_escapes_script_boundary_and_includes_options():
    html = build_turnstile_html(
        TurnstileRequest(
            url="https://example.test",
            siteKey="key</script>",
            strategy="minimal",
            action="search",
            cData="request-1",
        )
    )

    assert "key</script>" not in html
    assert "key<\\/script>" in html
    assert '"action": "search"' in html
    assert "turnstile.render" in html


def test_minimal_turnstile_returns_token_and_closes_context():
    browser = FakeBrowser()

    result = solve_turnstile(
        browser,
        TurnstileRequest(
            url="https://example.test",
            siteKey="1x00000000000000000000AA",
            strategy="minimal",
        ),
    )

    assert result.status == "solved"
    assert result.token == "dummy-token"
    assert result.userAgent == "FakeFox/1.0"
    assert result.cookies[0].name == "sid"
    assert browser.context.closed is True

