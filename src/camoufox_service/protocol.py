"""Worker 子进程使用的逐行 JSON 协议。"""

from __future__ import annotations

import json
import sys
from typing import TextIO


def run_worker(runtime, *, source: TextIO | None = None, target: TextIO | None = None) -> None:
    """逐行处理 Supervisor 消息，并保证进程退出时关闭运行时资源。"""

    source = source or sys.stdin
    target = target or sys.stdout
    try:
        for line in source:
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
            target.write(json.dumps(response, ensure_ascii=False) + "\n")
            target.flush()
    finally:
        runtime.close()
