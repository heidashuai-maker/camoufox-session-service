"""FastAPI 应用装配、鉴权、异常映射与 HTTP 路由。"""

from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .models import (
    ChallengeRequest,
    RecaptchaV2Request,
    SessionCreateRequest,
    SessionRequest,
    TaskResult,
    TurnstileRequest,
)
from .sessions import SessionRegistry
from .supervisor import QueueFull, WorkerError, WorkerSupervisor, WorkerTimeout

_CHALLENGE_RESULT_GRACE_SECONDS = 10


def create_app(
    *,
    settings: Settings | None = None,
    supervisor: WorkerSupervisor | None = None,
    challenge_supervisor: WorkerSupervisor | None = None,
) -> FastAPI:
    """组装服务依赖、生命周期、异常映射和所有 HTTP 路由。"""

    service_settings = settings or Settings.from_env()
    service_supervisor = supervisor or WorkerSupervisor(
        [sys.executable, "-u", "-m", "camoufox_service.worker"],
        workers=service_settings.workers,
        queue_size=service_settings.queue_size,
        task_timeout=service_settings.task_timeout_seconds,
        max_jobs=service_settings.max_jobs_per_worker,
        max_lifetime=service_settings.max_worker_lifetime_seconds,
        max_rss_mb=service_settings.max_worker_rss_mb,
        stream_limit_bytes=service_settings.worker_stream_limit_bytes,
    )
    service_challenge_supervisor = challenge_supervisor or WorkerSupervisor(
        [sys.executable, "-u", "-m", "camoufox_service.challenge_worker"],
        workers=service_settings.challenge_workers,
        queue_size=service_settings.challenge_queue_size,
        task_timeout=service_settings.challenge_task_timeout_seconds,
        max_jobs=service_settings.challenge_max_jobs_per_worker,
        max_lifetime=service_settings.challenge_max_worker_lifetime_seconds,
        max_rss_mb=service_settings.challenge_max_worker_rss_mb,
        stream_limit_bytes=service_settings.worker_stream_limit_bytes,
    )
    registry = SessionRegistry(service_settings.session_ttl_seconds)
    challenge_registry = SessionRegistry(service_settings.session_ttl_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service_supervisor.start()
        try:
            await service_challenge_supervisor.start()
            try:
                yield
            finally:
                await service_challenge_supervisor.stop()
        finally:
            await service_supervisor.stop()

    app = FastAPI(title="camoufox-session-service", version=__version__, lifespan=lifespan)
    app.state.sessions = registry
    app.state.challenge_sessions = challenge_registry

    async def expire_sessions() -> None:
        """回收 Registry 中已过期的记录及其 Worker 浏览器上下文。"""

        for record in registry.expire():
            try:
                await service_supervisor.request(
                    "session.destroy",
                    {"sessionId": record.session_id},
                    worker_id=record.worker_id,
                )
            except WorkerError:
                pass
        for record in challenge_registry.expire():
            try:
                await service_challenge_supervisor.request(
                    "challenge.session.destroy",
                    {"sessionId": record.session_id},
                    worker_id=record.worker_id,
                )
            except WorkerError:
                pass

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if not service_settings.auth_token:
            return
        expected = f"Bearer {service_settings.auth_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.exception_handler(QueueFull)
    async def queue_full_handler(_, exc: QueueFull):
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(WorkerTimeout)
    async def timeout_handler(_, exc: WorkerTimeout):
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    @app.exception_handler(WorkerError)
    async def worker_handler(_, exc: WorkerError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        if not service_supervisor.ready() or not service_challenge_supervisor.ready():
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {
            "status": "ready",
            "workers": {
                "camoufox": service_supervisor.pids,
                "challenge": service_challenge_supervisor.pids,
            },
        }

    @app.get("/metrics", dependencies=[Depends(authorize)])
    async def metrics():
        camoufox = service_supervisor.metrics() if hasattr(service_supervisor, "metrics") else {}
        challenge = (
            service_challenge_supervisor.metrics()
            if hasattr(service_challenge_supervisor, "metrics")
            else {}
        )
        return {
            "camoufox": camoufox,
            "challenge": challenge,
            "sessions": len(registry.list()) + len(challenge_registry.list()),
        }

    @app.post("/v1/turnstile/solve", response_model=TaskResult, dependencies=[Depends(authorize)])
    async def turnstile_solve(request: TurnstileRequest):
        return await service_supervisor.request("turnstile.solve", request.model_dump(mode="json"))

    @app.post("/v1/challenge/solve", response_model=TaskResult, dependencies=[Depends(authorize)])
    async def challenge_solve(request: ChallengeRequest):
        if request.retainSession:
            result, worker_id = await service_challenge_supervisor.request_with_worker(
                "challenge.solve",
                request.model_dump(mode="json"),
                timeout=request.timeoutMs / 1000 + _CHALLENGE_RESULT_GRACE_SECONDS,
            )
            if session_id := result.get("sessionId"):
                challenge_registry.create(
                    worker_id,
                    worker_generation=service_challenge_supervisor.generation(worker_id),
                    session_id=session_id,
                    ttl_seconds=request.ttlSeconds,
                )
            return result
        return await service_challenge_supervisor.request(
            "challenge.solve",
            request.model_dump(mode="json"),
            timeout=request.timeoutMs / 1000 + _CHALLENGE_RESULT_GRACE_SECONDS,
        )

    @app.post(
        "/v1/recaptcha/v2/solve", response_model=TaskResult, dependencies=[Depends(authorize)]
    )
    async def recaptcha_solve(request: RecaptchaV2Request):
        return await service_supervisor.request(
            "recaptcha.v2.solve", request.model_dump(mode="json")
        )

    @app.post("/v1/sessions", response_model=TaskResult, dependencies=[Depends(authorize)])
    async def create_session(request: SessionCreateRequest):
        """在选定 Worker 中创建持久上下文，并记录其 Worker 代际。"""

        await expire_sessions()
        session_id = uuid.uuid4().hex
        payload = request.model_dump(mode="json")
        payload["sessionId"] = session_id
        result, worker_id = await service_supervisor.request_with_worker("session.create", payload)
        registry.create(
            worker_id,
            worker_generation=service_supervisor.generation(worker_id),
            session_id=session_id,
            ttl_seconds=request.ttlSeconds,
        )
        return result

    @app.get("/v1/sessions", dependencies=[Depends(authorize)])
    async def list_sessions():
        await expire_sessions()
        for record in registry.list():
            if record.worker_generation != service_supervisor.generation(record.worker_id):
                registry.delete(record.session_id)
        for record in challenge_registry.list():
            if record.worker_generation != service_challenge_supervisor.generation(
                record.worker_id
            ):
                challenge_registry.delete(record.session_id)
        sessions = [{**record.as_dict(), "engine": "camoufox"} for record in registry.list()]
        sessions.extend(
            {**record.as_dict(), "engine": "drissionpage"} for record in challenge_registry.list()
        )
        return {"sessions": sessions}

    @app.post(
        "/v1/sessions/{session_id}/request",
        response_model=TaskResult,
        dependencies=[Depends(authorize)],
    )
    async def session_request(session_id: str, request: SessionRequest):
        """把请求发回 Session 所绑定的 Worker，并拒绝失效代际。"""

        await expire_sessions()
        record = registry.get(session_id)
        payload = request.model_dump(mode="json")
        payload["sessionId"] = session_id
        if record:
            if record.worker_generation != service_supervisor.generation(record.worker_id):
                registry.delete(session_id)
                raise HTTPException(status_code=410, detail="Session browser worker restarted")
            return await service_supervisor.request(
                "session.request",
                payload,
                timeout=request.timeoutMs / 1000,
                worker_id=record.worker_id,
            )
        challenge_record = challenge_registry.get(session_id)
        if not challenge_record:
            raise HTTPException(status_code=404, detail="Session not found")
        if challenge_record.worker_generation != service_challenge_supervisor.generation(
            challenge_record.worker_id
        ):
            challenge_registry.delete(session_id)
            raise HTTPException(status_code=410, detail="Session browser worker restarted")
        return await service_challenge_supervisor.request(
            "challenge.session.request",
            payload,
            timeout=request.timeoutMs / 1000,
            worker_id=challenge_record.worker_id,
        )

    @app.delete("/v1/sessions/{session_id}", status_code=204, dependencies=[Depends(authorize)])
    async def delete_session(session_id: str):
        await expire_sessions()
        record = registry.delete(session_id)
        if record:
            if record.worker_generation != service_supervisor.generation(record.worker_id):
                raise HTTPException(status_code=410, detail="Session browser worker restarted")
            await service_supervisor.request(
                "session.destroy",
                {"sessionId": session_id},
                worker_id=record.worker_id,
            )
            return Response(status_code=204)
        challenge_record = challenge_registry.delete(session_id)
        if not challenge_record:
            raise HTTPException(status_code=404, detail="Session not found")
        if challenge_record.worker_generation != service_challenge_supervisor.generation(
            challenge_record.worker_id
        ):
            raise HTTPException(status_code=410, detail="Session browser worker restarted")
        await service_challenge_supervisor.request(
            "challenge.session.destroy",
            {"sessionId": session_id},
            worker_id=challenge_record.worker_id,
        )
        return Response(status_code=204)

    return app


app = create_app()
