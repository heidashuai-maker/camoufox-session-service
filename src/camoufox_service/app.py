"""FastAPI 应用与 HTTP 路由。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
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
from .service import BrowserService, SessionNotFound, SessionWorkerRestarted
from .supervisor import QueueFull, WorkerError, WorkerSupervisor, WorkerTimeout

router = APIRouter()


def get_service(request: Request) -> BrowserService:
    return request.app.state.service


Service = Annotated[BrowserService, Depends(get_service)]


def authorize(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = request.app.state.service.settings.auth_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(service: Service):
    if not service.ready():
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {
        "status": "ready",
        "workers": {
            "camoufox": service.camoufox.pids,
            "challenge": service.challenge.pids,
        },
    }


@router.get("/metrics", dependencies=[Depends(authorize)])
async def metrics(service: Service):
    return service.metrics()


@router.post(
    "/v1/turnstile/solve",
    response_model=TaskResult,
    dependencies=[Depends(authorize)],
)
async def turnstile_solve(request: TurnstileRequest, service: Service):
    return await service.solve_turnstile(request)


@router.post(
    "/v1/recaptcha/v2/solve",
    response_model=TaskResult,
    dependencies=[Depends(authorize)],
)
async def recaptcha_solve(request: RecaptchaV2Request, service: Service):
    return await service.solve_recaptcha(request)


@router.post(
    "/v1/challenge/solve",
    response_model=TaskResult,
    dependencies=[Depends(authorize)],
)
async def challenge_solve(request: ChallengeRequest, service: Service):
    return await service.solve_challenge(request)


@router.post("/v1/sessions", response_model=TaskResult, dependencies=[Depends(authorize)])
async def create_session(request: SessionCreateRequest, service: Service):
    return await service.create_session(request)


@router.get("/v1/sessions", dependencies=[Depends(authorize)])
async def list_sessions(service: Service):
    return {"sessions": await service.list_sessions()}


@router.post(
    "/v1/sessions/{session_id}/request",
    response_model=TaskResult,
    dependencies=[Depends(authorize)],
)
async def session_request(session_id: str, request: SessionRequest, service: Service):
    return await service.session_request(session_id, request)


@router.delete("/v1/sessions/{session_id}", status_code=204, dependencies=[Depends(authorize)])
async def delete_session(session_id: str, service: Service):
    await service.delete_session(session_id)
    return Response(status_code=204)


def create_app(
    *,
    settings: Settings | None = None,
    supervisor: WorkerSupervisor | None = None,
    challenge_supervisor: WorkerSupervisor | None = None,
) -> FastAPI:
    service = BrowserService(
        settings or Settings.from_env(),
        supervisor=supervisor,
        challenge_supervisor=challenge_supervisor,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="camoufox-session-service", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.sessions = service.sessions
    app.include_router(router)
    app.add_exception_handler(
        QueueFull,
        lambda _, exc: JSONResponse(status_code=429, content={"detail": str(exc)}),
    )
    app.add_exception_handler(
        WorkerTimeout,
        lambda _, exc: JSONResponse(status_code=504, content={"detail": str(exc)}),
    )
    app.add_exception_handler(
        WorkerError,
        lambda _, exc: JSONResponse(status_code=503, content={"detail": str(exc)}),
    )
    app.add_exception_handler(
        SessionNotFound,
        lambda _, __: JSONResponse(status_code=404, content={"detail": "Session not found"}),
    )
    app.add_exception_handler(
        SessionWorkerRestarted,
        lambda _, __: JSONResponse(
            status_code=410,
            content={"detail": "Session browser worker restarted"},
        ),
    )
    return app


app = create_app()
