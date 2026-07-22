"""内存 Session 元数据、Worker 绑定与过期回收。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

SessionEngine = Literal["camoufox", "drissionpage"]


@dataclass(slots=True)
class SessionRecord:
    """记录 Session 所属 Worker、Worker 代际和有效时间。"""

    session_id: str
    engine: SessionEngine
    worker_id: int
    worker_generation: int
    created_at: float
    last_used_at: float
    expires_at: float

    def as_dict(self) -> dict:
        return {
            "sessionId": self.session_id,
            "engine": self.engine,
            "workerId": self.worker_id,
            "createdAt": self.created_at,
            "lastUsedAt": self.last_used_at,
            "expiresAt": self.expires_at,
        }


class SessionRegistry:
    """管理 Session 元数据的创建、查询、删除和过期回收。"""

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._records: dict[str, SessionRecord] = {}

    def create(
        self,
        worker_id: int,
        *,
        engine: SessionEngine,
        worker_generation: int,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> SessionRecord:
        """创建记录，并把 Session 固定到指定 Worker 及其当前代际。"""

        now = self.clock()
        record = SessionRecord(
            session_id=session_id or uuid.uuid4().hex,
            engine=engine,
            worker_id=worker_id,
            worker_generation=worker_generation,
            created_at=now,
            last_used_at=now,
            expires_at=now + (ttl_seconds or self.ttl_seconds),
        )
        self._records[record.session_id] = record
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        """返回未过期记录并刷新最后使用时间；过期记录直接移除。"""

        record = self._records.get(session_id)
        if record is None:
            return None
        now = self.clock()
        if now >= record.expires_at:
            self._records.pop(session_id, None)
            return None
        record.last_used_at = now
        return record

    def delete(self, session_id: str) -> SessionRecord | None:
        return self._records.pop(session_id, None)

    def expire(self) -> list[SessionRecord]:
        """移除全部过期记录，并返回它们供调用方关闭浏览器上下文。"""

        now = self.clock()
        expired = [record for record in self._records.values() if now >= record.expires_at]
        for record in expired:
            self._records.pop(record.session_id, None)
        return expired

    def list(self) -> list[SessionRecord]:
        return list(self._records.values())
