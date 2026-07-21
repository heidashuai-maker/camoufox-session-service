import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from camoufox_service.cloudflare import solve_cloudflare_challenge
from camoufox_service.models import ChallengeRequest

FIXTURE = b"""<!doctype html>
<html>
<head><title>Just a moment...</title></head>
<body>
  <div id="turnstile-wrapper">
    <input type="hidden" name="cf-turnstile-response">
  </div>
  <script>
    const wrapper = document.querySelector('#turnstile-wrapper');
    const outer = wrapper.attachShadow({mode: 'closed'});
    const frame = document.createElement('iframe');
    frame.addEventListener('load', () => {
      const inner = frame.contentDocument.body.attachShadow({mode: 'closed'});
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.addEventListener('click', () => {
        document.title = 'Fixture passed';
        document.cookie = 'cf_clearance=fixture; path=/';
        wrapper.remove();
      });
      inner.appendChild(checkbox);
    });
    outer.appendChild(frame);
  </script>
</body>
</html>
"""


@pytest.mark.skipif(
    os.getenv("RUN_CHALLENGE_BROWSER_TESTS") != "1",
    reason="DrissionPage browser integration disabled",
)
def test_drissionpage_traverses_closed_shadow_dom_and_exports_cookie():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FIXTURE)))
            self.end_headers()
            self.wfile.write(FIXTURE)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = solve_cloudflare_challenge(
            ChallengeRequest(
                url=f"http://127.0.0.1:{server.server_port}/",
                timeoutMs=30_000,
                returnHtml=False,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == "solved", result.model_dump()
    assert result.httpStatus == 200
    assert result.html is None
    assert any(cookie.name == "cf_clearance" for cookie in result.cookies)
