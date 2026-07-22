from fastapi.testclient import TestClient

from camoufox_service.app import create_app
from camoufox_service.config import Settings


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self.timeouts = []
        self.pids = [123]
        self.generations = {0: 1}

    async def start(self):
        return None

    async def stop(self):
        return None

    async def request(self, kind, payload, timeout=None, worker_id=None):
        self.calls.append((kind, payload, worker_id))
        self.timeouts.append(timeout)
        return {"status": "solved", "token": "dummy", "elapsedMs": 1}

    async def request_with_worker(self, kind, payload, timeout=None):
        self.calls.append((kind, payload, None))
        self.timeouts.append(timeout)
        if kind == "challenge.solve":
            return {
                "status": "no_challenge",
                "sessionId": "challenge-session-1",
                "httpStatus": 200,
                "elapsedMs": 1,
            }, 0
        return {"status": "solved", "sessionId": payload["sessionId"], "elapsedMs": 1}, 0

    def ready(self):
        return True

    def generation(self, worker_id):
        return self.generations[worker_id]

    def metrics(self):
        return {"workers": 1, "readyWorkers": 1}


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
    with TestClient(
        create_app(
            settings=settings(),
            supervisor=supervisor,
            challenge_supervisor=FakeSupervisor(),
        )
    ) as client:
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


def test_challenge_route_uses_dedicated_supervisor():
    camoufox_supervisor = FakeSupervisor()
    challenge_supervisor = FakeSupervisor()
    with TestClient(
        create_app(
            settings=settings(),
            supervisor=camoufox_supervisor,
            challenge_supervisor=challenge_supervisor,
        )
    ) as client:
        response = client.post(
            "/v1/challenge/solve",
            json={"url": "https://example.test", "timeoutMs": 45_000},
        )

    assert response.status_code == 200
    assert camoufox_supervisor.calls == []
    assert challenge_supervisor.calls[0][0] == "challenge.solve"
    assert challenge_supervisor.timeouts == [55.0]


def test_retained_challenge_session_keeps_challenge_worker_affinity():
    camoufox_supervisor = FakeSupervisor()
    challenge_supervisor = FakeSupervisor()
    with TestClient(
        create_app(
            settings=settings(),
            supervisor=camoufox_supervisor,
            challenge_supervisor=challenge_supervisor,
        )
    ) as client:
        solved = client.post(
            "/v1/challenge/solve",
            json={
                "url": "https://example.test",
                "retainSession": True,
                "ttlSeconds": 300,
            },
        )
        session_id = solved.json()["sessionId"]
        requested = client.post(
            f"/v1/sessions/{session_id}/request",
            json={"url": "https://example.test/account"},
        )
        deleted = client.delete(f"/v1/sessions/{session_id}")

    assert requested.status_code == 200
    assert deleted.status_code == 204
    assert camoufox_supervisor.calls == []
    assert [call[0] for call in challenge_supervisor.calls] == [
        "challenge.solve",
        "challenge.session.request",
        "challenge.session.destroy",
    ]
    assert challenge_supervisor.calls[1][2] == 0
    assert challenge_supervisor.calls[2][2] == 0


def test_session_lifecycle_keeps_worker_affinity():
    supervisor = FakeSupervisor()
    with TestClient(
        create_app(
            settings=settings(),
            supervisor=supervisor,
            challenge_supervisor=FakeSupervisor(),
        )
    ) as client:
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
    app = create_app(
        settings=settings(),
        supervisor=supervisor,
        challenge_supervisor=FakeSupervisor(),
    )
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


def test_session_is_invalidated_after_its_worker_restarts():
    supervisor = FakeSupervisor()
    app = create_app(
        settings=settings(),
        supervisor=supervisor,
        challenge_supervisor=FakeSupervisor(),
    )
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={})
        session_id = created.json()["sessionId"]
        supervisor.generations[0] += 1

        response = client.post(
            f"/v1/sessions/{session_id}/request",
            json={"url": "https://example.test"},
        )

    assert response.status_code == 410
    assert app.state.sessions.get(session_id) is None
