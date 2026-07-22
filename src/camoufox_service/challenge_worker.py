"""DrissionPage Challenge Worker 的 JSONL 进程入口。"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable

from .cloudflare import DrissionBrowser, solve_cloudflare_challenge
from .models import ChallengeRequest, SessionRequest, TaskResult


class ChallengeRuntime:
    """只处理整页 Cloudflare Challenge，不启动 Camoufox。"""

    def __init__(
        self,
        solver: Callable[[ChallengeRequest], TaskResult] | None = None,
        browser: DrissionBrowser | None = None,
    ):
        self.browser = browser or DrissionBrowser()
        self.solver = solver
        self.sessions: dict[str, object] = {}

    def retain(self, context: object) -> str:
        session_id = uuid.uuid4().hex
        self.sessions[session_id] = context
        return session_id

    def session_request(self, payload: dict) -> dict:
        values = dict(payload)
        session_id = str(values.pop("sessionId"))
        request = SessionRequest.model_validate(values)
        context = self.sessions.get(session_id)
        if context is None:
            raise ValueError("session not found")
        return self.serialize_result(context.request(request, session_id))

    def destroy_session(self, payload: dict) -> dict:
        session_id = str(payload["sessionId"])
        context = self.sessions.pop(session_id, None)
        if context is not None:
            context.close()
        return {"status": "solved", "sessionId": session_id, "elapsedMs": 0}

    @staticmethod
    def serialize_result(result: TaskResult) -> dict:
        if result.status == "browser_crashed":
            raise RuntimeError("Chromium browser process crashed")
        return result.model_dump(mode="json")

    def handle(self, kind: str, payload: dict) -> dict:
        if kind == "health":
            return {"status": "ok", "engine": "drissionpage"}
        if kind == "challenge.solve":
            request = ChallengeRequest.model_validate(payload)
            if self.solver is not None:
                result = self.solver(request)
            else:
                result = solve_cloudflare_challenge(
                    request,
                    browser_factory=self.browser.open,
                    retain_browser=self.retain,
                )
            if result.status == "browser_crashed":
                self.sessions.clear()
                self.browser.close()
            return self.serialize_result(result)
        if kind == "challenge.session.request":
            return self.session_request(payload)
        if kind == "challenge.session.destroy":
            return self.destroy_session(payload)
        raise ValueError(f"unsupported job kind: {kind}")

    def close(self) -> None:
        for context in self.sessions.values():
            try:
                context.close()
            except Exception:
                pass
        self.sessions.clear()
        self.browser.close()


def main() -> None:
    """运行与 Supervisor 兼容的逐行 JSON 请求/响应循环。"""

    runtime = ChallengeRuntime()
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
