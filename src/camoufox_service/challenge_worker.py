"""DrissionPage Challenge Worker 的 JSONL 进程入口。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from .cloudflare import DrissionBrowser, solve_cloudflare_challenge
from .models import ChallengeRequest, TaskResult


class ChallengeRuntime:
    """只处理整页 Cloudflare Challenge，不启动 Camoufox。"""

    def __init__(
        self,
        solver: Callable[[ChallengeRequest], TaskResult] | None = None,
        browser: DrissionBrowser | None = None,
    ):
        self.browser = browser or DrissionBrowser()
        self.solver = solver or (
            lambda request: solve_cloudflare_challenge(
                request,
                browser_factory=self.browser.open,
            )
        )

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
            result = self.solver(request)
            if result.status == "browser_crashed":
                self.browser.close()
            return self.serialize_result(result)
        raise ValueError(f"unsupported job kind: {kind}")

    def close(self) -> None:
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
