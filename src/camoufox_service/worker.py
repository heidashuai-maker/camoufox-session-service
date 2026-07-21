"""Worker 子进程内的 Camoufox 生命周期、Session 和任务派发。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from . import config
from .browser import context_options, cookies_from_context, page_user_agent
from .models import (
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
    """保存持久 Browser Context 及其实际 User-Agent。"""

    context: object
    user_agent: str | None


class BrowserRuntime:
    """在单个 Worker 内独占 Camoufox，并管理全部持久 Session。"""

    def __init__(self):
        self.manager = None
        self.browser = None
        self.sessions: dict[str, BrowserSession] = {}
        self.settings = config.Settings.from_env()

    def open(self) -> None:
        """按需启动 Camoufox；同一 Worker 的后续任务复用该实例。"""

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
        """关闭全部 Session Context，再退出 Camoufox 管理器。"""

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

    @staticmethod
    def serialize_result(result: TaskResult) -> dict:
        if result.status == "browser_crashed":
            # 浏览器断连必须升级为 Worker 协议错误，才能触发 Supervisor 替换进程。
            raise RuntimeError("browser process crashed")
        return result.model_dump(mode="json")

    def create_session(self, payload: dict) -> dict:
        """创建持久 Context，写入 Cookie，并记录浏览器实际 User-Agent。"""

        values = dict(payload)
        session_id = str(values.pop("sessionId"))
        request = SessionCreateRequest.model_validate(values)
        context = self._browser().new_context(**context_options(request))
        if request.cookies:
            context.add_cookies(
                [cookie.model_dump(exclude_none=True) for cookie in request.cookies]
            )
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
        """使用 Session 的 Browser Context 请求 API，保持 Cookie 与代理身份。"""

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
        """校验任务类型，并派发到求解器或 Session 操作。"""

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
            return self.serialize_result(
                solve_turnstile(self._browser(), TurnstileRequest.model_validate(payload))
            )
        if kind == "recaptcha.v2.solve":
            return self.serialize_result(
                solve_recaptcha(self._browser(), RecaptchaV2Request.model_validate(payload))
            )
        if kind == "session.create":
            return self.create_session(dict(payload))
        if kind == "session.request":
            return self.session_request(dict(payload))
        if kind == "session.destroy":
            return self.destroy_session(payload)
        raise ValueError(f"unsupported job kind: {kind}")


def main() -> None:
    """运行 JSONL Worker 协议循环，并保证退出时关闭浏览器。"""

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
