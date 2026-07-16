from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from . import config
from .browser import context_options, cookies_from_context, page_user_agent
from .challenge import solve_challenge
from .models import (
    ChallengeRequest,
    RecaptchaV2Request,
    SessionCreateRequest,
    SessionRequest,
    TaskResult,
    TurnstileRequest,
)
from .recaptcha import solve_recaptcha
from .turnstile import solve_turnstile


@dataclass
class BrowserSession:
    context: object
    user_agent: str | None


class BrowserRuntime:
    def __init__(self):
        self.manager = None
        self.browser = None
        self.sessions: dict[str, BrowserSession] = {}
        self.settings = config.Settings.from_env()

    def open(self) -> None:
        if self.browser is not None:
            return
        from camoufox.sync_api import Camoufox

        self.manager = Camoufox(
            headless=self.settings.headless,
            humanize=True,
            block_webrtc=True,
        )
        self.browser = self.manager.__enter__()

    def close(self) -> None:
        for session in self.sessions.values():
            try:
                session.context.close()
            except Exception:
                pass
        self.sessions.clear()
        if self.manager:
            self.manager.__exit__(None, None, None)
        self.manager = None
        self.browser = None

    def _browser(self):
        self.open()
        return self.browser

    def create_session(self, payload: dict) -> dict:
        request = SessionCreateRequest.model_validate(payload)
        session_id = str(payload["sessionId"])
        context = self._browser().new_context(**context_options(request))
        page = context.new_page()
        try:
            user_agent = page_user_agent(page)
        finally:
            page.close()
        self.sessions[session_id] = BrowserSession(context=context, user_agent=user_agent)
        return TaskResult(
            status="solved",
            sessionId=session_id,
            cookies=cookies_from_context(context),
            userAgent=user_agent,
            elapsedMs=0,
        ).model_dump(mode="json")

    def session_request(self, payload: dict) -> dict:
        session_id = str(payload.pop("sessionId"))
        request = SessionRequest.model_validate(payload)
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError("session not found")
        response = session.context.request.fetch(
            str(request.url),
            method=request.method,
            headers=request.headers,
            data=request.body,
            timeout=request.timeoutMs,
        )
        return TaskResult(
            status="no_challenge",
            sessionId=session_id,
            finalUrl=str(response.url),
            httpStatus=response.status,
            cookies=cookies_from_context(session.context),
            userAgent=session.user_agent,
            html=response.text() if request.returnHtml else None,
            elapsedMs=0,
        ).model_dump(mode="json")

    def destroy_session(self, payload: dict) -> dict:
        session_id = str(payload["sessionId"])
        session = self.sessions.pop(session_id, None)
        if session:
            session.context.close()
        return {"status": "solved", "sessionId": session_id, "elapsedMs": 0}

    def handle(self, kind: str, payload: dict) -> dict:
        if kind == "health":
            browser = self._browser()
            connected = not hasattr(browser, "is_connected") or browser.is_connected()
            if not connected:
                raise RuntimeError("Camoufox browser is not connected")
            return {
                "status": "ok",
                "sessions": len(self.sessions),
                "browserVersion": getattr(browser, "version", None),
            }
        if kind == "turnstile.solve":
            return solve_turnstile(self._browser(), TurnstileRequest.model_validate(payload)).model_dump(mode="json")
        if kind == "challenge.solve":
            return solve_challenge(self._browser(), ChallengeRequest.model_validate(payload)).model_dump(mode="json")
        if kind == "recaptcha.v2.solve":
            return solve_recaptcha(self._browser(), RecaptchaV2Request.model_validate(payload)).model_dump(mode="json")
        if kind == "session.create":
            return self.create_session(dict(payload))
        if kind == "session.request":
            return self.session_request(dict(payload))
        if kind == "session.destroy":
            return self.destroy_session(payload)
        raise ValueError(f"unsupported job kind: {kind}")


def main() -> None:
    runtime = BrowserRuntime()
    try:
        for line in sys.stdin:
            request_id = None
            try:
                message = json.loads(line)
                request_id = message.get("id")
                result = runtime.handle(str(message.get("kind")), message.get("payload") or {})
                response = {"id": request_id, "result": result}
            except Exception as exc:
                response = {
                    "id": request_id,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
