"""浏览器任务、Worker 池与 Session 的服务级编排。"""

from __future__ import annotations

import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass

from .config import Settings
from .models import (
    ChallengeRequest,
    RecaptchaV2Request,
    SessionCreateRequest,
    SessionRequest,
    TurnstileRequest,
)
from .sessions import SessionRecord, SessionRegistry
from .supervisor import WorkerError, WorkerSupervisor

_CHALLENGE_RESULT_GRACE_SECONDS = 10


class SessionNotFound(LookupError):
    pass


class SessionWorkerRestarted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionBackend:
    """描述一种 Session 引擎对应的 Worker 与协议命令。"""

    supervisor: WorkerSupervisor
    request_kind: str
    destroy_kind: str


class BrowserService:
    """统一编排两类浏览器 Worker，并维护跨请求 Session 的归属关系。"""

    def __init__(
        self,
        settings: Settings,
        *,
        supervisor: WorkerSupervisor | None = None,
        challenge_supervisor: WorkerSupervisor | None = None,
    ):
        self.settings = settings
        self.camoufox = supervisor or WorkerSupervisor(
            [sys.executable, "-u", "-m", "camoufox_service.worker"],
            workers=settings.workers,
            queue_size=settings.queue_size,
            task_timeout=settings.task_timeout_seconds,
            max_jobs=settings.max_jobs_per_worker,
            max_lifetime=settings.max_worker_lifetime_seconds,
            max_rss_mb=settings.max_worker_rss_mb,
            stream_limit_bytes=settings.worker_stream_limit_bytes,
        )
        self.challenge = challenge_supervisor or WorkerSupervisor(
            [sys.executable, "-u", "-m", "camoufox_service.challenge_worker"],
            workers=settings.challenge_workers,
            queue_size=settings.challenge_queue_size,
            task_timeout=settings.challenge_task_timeout_seconds,
            max_jobs=settings.challenge_max_jobs_per_worker,
            max_lifetime=settings.challenge_max_worker_lifetime_seconds,
            max_rss_mb=settings.challenge_max_worker_rss_mb,
            stream_limit_bytes=settings.worker_stream_limit_bytes,
        )
        self.sessions = SessionRegistry(settings.session_ttl_seconds)
        self.backends = {
            "camoufox": SessionBackend(
                self.camoufox,
                request_kind="session.request",
                destroy_kind="session.destroy",
            ),
            "drissionpage": SessionBackend(
                self.challenge,
                request_kind="challenge.session.request",
                destroy_kind="challenge.session.destroy",
            ),
        }

    async def start(self) -> None:
        await self.camoufox.start()
        try:
            await self.challenge.start()
        except Exception:
            await self.camoufox.stop()
            raise

    async def stop(self) -> None:
        try:
            await self.challenge.stop()
        finally:
            await self.camoufox.stop()

    def ready(self) -> bool:
        return self.camoufox.ready() and self.challenge.ready()

    def metrics(self) -> dict:
        return {
            "camoufox": self.camoufox.metrics(),
            "challenge": self.challenge.metrics(),
            "sessions": len(self.sessions.list()),
        }

    def _backend(self, record: SessionRecord) -> SessionBackend:
        return self.backends[record.engine]

    def _current_session(self, session_id: str) -> tuple[SessionRecord, SessionBackend]:
        """取得仍属于当前 Worker 代际的 Session。"""

        record = self.sessions.get(session_id)
        if record is None:
            raise SessionNotFound(session_id)
        backend = self._backend(record)
        if record.worker_generation != backend.supervisor.generation(record.worker_id):
            self.sessions.delete(session_id)
            raise SessionWorkerRestarted(session_id)
        return record, backend

    async def expire_sessions(self) -> None:
        """删除过期记录，并通知所属 Worker 释放浏览器 Context。"""

        for record in self.sessions.expire():
            backend = self._backend(record)
            with suppress(WorkerError):
                await backend.supervisor.request(
                    backend.destroy_kind,
                    {"sessionId": record.session_id},
                    worker_id=record.worker_id,
                )

    async def solve_turnstile(self, request: TurnstileRequest) -> dict:
        return await self.camoufox.request("turnstile.solve", request.model_dump(mode="json"))

    async def solve_recaptcha(self, request: RecaptchaV2Request) -> dict:
        return await self.camoufox.request("recaptcha.v2.solve", request.model_dump(mode="json"))

    async def solve_challenge(self, request: ChallengeRequest) -> dict:
        payload = request.model_dump(mode="json")
        timeout = request.timeoutMs / 1000 + _CHALLENGE_RESULT_GRACE_SECONDS
        if not request.retainSession:
            return await self.challenge.request("challenge.solve", payload, timeout=timeout)

        result, worker_id = await self.challenge.request_with_worker(
            "challenge.solve", payload, timeout=timeout
        )
        if session_id := result.get("sessionId"):
            self.sessions.create(
                worker_id,
                engine="drissionpage",
                worker_generation=self.challenge.generation(worker_id),
                session_id=session_id,
                ttl_seconds=request.ttlSeconds,
            )
        return result

    async def create_session(self, request: SessionCreateRequest) -> dict:
        await self.expire_sessions()
        session_id = uuid.uuid4().hex
        payload = request.model_dump(mode="json")
        payload["sessionId"] = session_id
        result, worker_id = await self.camoufox.request_with_worker("session.create", payload)
        self.sessions.create(
            worker_id,
            engine="camoufox",
            worker_generation=self.camoufox.generation(worker_id),
            session_id=session_id,
            ttl_seconds=request.ttlSeconds,
        )
        return result

    async def list_sessions(self) -> list[dict]:
        await self.expire_sessions()
        for record in self.sessions.list():
            backend = self._backend(record)
            if record.worker_generation != backend.supervisor.generation(record.worker_id):
                self.sessions.delete(record.session_id)
        return [record.as_dict() for record in self.sessions.list()]

    async def session_request(self, session_id: str, request: SessionRequest) -> dict:
        await self.expire_sessions()
        record, backend = self._current_session(session_id)
        payload = request.model_dump(mode="json")
        payload["sessionId"] = session_id
        return await backend.supervisor.request(
            backend.request_kind,
            payload,
            timeout=request.timeoutMs / 1000,
            worker_id=record.worker_id,
        )

    async def delete_session(self, session_id: str) -> None:
        await self.expire_sessions()
        record, backend = self._current_session(session_id)
        self.sessions.delete(session_id)
        await backend.supervisor.request(
            backend.destroy_kind,
            {"sessionId": session_id},
            worker_id=record.worker_id,
        )
