from fastapi.testclient import TestClient

from camoufox_service.app import create_app
from camoufox_service.config import Settings


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self.pids = [123]

    async def start(self):
        return None

    async def stop(self):
        return None

    async def request(self, kind, payload, timeout=None, worker_id=None):
        self.calls.append((kind, payload, worker_id))
        return {"status": "solved", "token": "dummy", "elapsedMs": 1}

    async def request_with_worker(self, kind, payload, timeout=None):
        self.calls.append((kind, payload, None))
        return {"status": "solved", "sessionId": payload["sessionId"], "elapsedMs": 1}, 0

    def ready(self):
        return True


def settings():
    return Settings(
        host="127.0.0.1",
        port=3000,
        auth_token=None,
        workers=1,
        queue_size=1,
        task_timeout_seconds=10,
        session_ttl_seconds=60,
        max_jobs_per_worker=10,
        max_worker_lifetime_seconds=600,
        max_worker_rss_mb=512,
        headless=True,
    )


def test_turnstile_route_dispatches_typed_job():
    supervisor = FakeSupervisor()
    with TestClient(create_app(settings=settings(), supervisor=supervisor)) as client:
        response = client.post(
            "/v1/turnstile/solve",
            json={
                "url": "https://example.test",
                "siteKey": "1x00000000000000000000AA",
                "strategy": "minimal",
            },
        )

    assert response.status_code == 200
    assert response.json()["token"] == "dummy"
    assert supervisor.calls[0][0] == "turnstile.solve"


def test_session_lifecycle_keeps_worker_affinity():
    supervisor = FakeSupervisor()
    with TestClient(create_app(settings=settings(), supervisor=supervisor)) as client:
        created = client.post("/v1/sessions", json={})
        session_id = created.json()["sessionId"]
        requested = client.post(
            f"/v1/sessions/{session_id}/request",
            json={"url": "https://example.test"},
        )
        deleted = client.delete(f"/v1/sessions/{session_id}")

    assert requested.status_code == 200
    assert deleted.status_code == 204
    assert supervisor.calls[1][2] == 0
    assert supervisor.calls[2][2] == 0


def test_expired_session_is_destroyed_on_maintenance_call():
    supervisor = FakeSupervisor()
    app = create_app(settings=settings(), supervisor=supervisor)
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={})
        session_id = created.json()["sessionId"]
        app.state.sessions.list()[0].expires_at = 0

        response = client.get("/v1/sessions")

    assert response.json() == {"sessions": []}
    assert supervisor.calls[-1] == (
        "session.destroy",
        {"sessionId": session_id},
        0,
    )
