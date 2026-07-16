from __future__ import annotations

import json
import time

from .browser import context_options, cookies_from_context, page_user_agent, response_status
from .models import ErrorInfo, TaskResult, TurnstileRequest


TURNSTILE_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Turnstile</title></head>
<body>
  <div id="turnstile-widget"></div>
  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=renderWidget" defer></script>
  <script>
    window.__turnstileToken = null;
    window.renderWidget = function () {
      const config = __CONFIG__;
      config.callback = function (token) {
        window.__turnstileToken = token;
        let field = document.querySelector('[name="cf-turnstile-response"]');
        if (!field) {
          field = document.createElement('input');
          field.type = 'hidden';
          field.name = 'cf-turnstile-response';
          document.body.appendChild(field);
        }
        field.value = token;
      };
      turnstile.render('#turnstile-widget', config);
    };
  </script>
</body>
</html>"""


TOKEN_SCRIPT = """() => window.__turnstileToken ||
  document.querySelector('[name="cf-turnstile-response"]')?.value ||
  document.querySelector('[name="cf-response"]')?.value || null"""


def build_turnstile_html(request: TurnstileRequest) -> str:
    config = {
        "sitekey": request.siteKey,
        "action": request.action,
        "cData": request.cData,
        "appearance": request.appearance,
        "execution": request.execution,
        "language": request.language,
    }
    serialized = json.dumps(
        {key: value for key, value in config.items() if value is not None},
        ensure_ascii=True,
    ).replace("</", "<\\/")
    return TURNSTILE_TEMPLATE.replace("__CONFIG__", serialized)


def _install_minimal_route(page, request: TurnstileRequest) -> None:
    target = str(request.url).rstrip("/")
    html = build_turnstile_html(request)

    def route_request(route) -> None:
        current = route.request.url.rstrip("/")
        if current == target and route.request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            return
        route.continue_()

    page.route("**/*", route_request)


def solve_turnstile(browser, request: TurnstileRequest) -> TaskResult:
    started = time.monotonic()
    context = browser.new_context(**context_options(request))
    try:
        page = context.new_page()
        if request.strategy == "minimal":
            _install_minimal_route(page, request)
        response = page.goto(
            str(request.url),
            wait_until="domcontentloaded",
            timeout=request.timeoutMs,
        )
        page.wait_for_function(TOKEN_SCRIPT, timeout=request.timeoutMs)
        token = page.evaluate(TOKEN_SCRIPT)
        if not token:
            raise RuntimeError("Turnstile completed without a token")
        return TaskResult(
            status="solved",
            token=str(token),
            finalUrl=str(page.url),
            httpStatus=response_status(response),
            cookies=cookies_from_context(context),
            userAgent=page_user_agent(page),
            elapsedMs=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        message = str(exc)
        timed_out = "timeout" in message.lower()
        return TaskResult(
            status="timeout" if timed_out else "failed",
            elapsedMs=int((time.monotonic() - started) * 1000),
            error=ErrorInfo(
                type="TURNSTILE_TIMEOUT" if timed_out else "TURNSTILE_FAILED",
                message=message,
                retryable=True,
                stage="turnstile",
            ),
        )
    finally:
        context.close()

