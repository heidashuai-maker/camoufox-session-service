import sys
import textwrap

import pytest

from camoufox_service.models import TaskResult
from camoufox_service.supervisor import (
    WorkerError,
    WorkerProcess,
    WorkerSupervisor,
    WorkerTimeout,
)
from camoufox_service.worker import BrowserRuntime


@pytest.fixture
def fake_worker_command(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time

            for line in sys.stdin:
                request = json.loads(line)
                kind = request["kind"]
                if kind == "sleep":
                    time.sleep(float(request["payload"]["seconds"]))
                result = {"echo": request["payload"], "pid": __import__("os").getpid()}
                print(json.dumps({"id": request["id"], "result": result}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    return [sys.executable, "-u", str(script)]


@pytest.mark.asyncio
async def test_timeout_replaces_worker_and_next_request_succeeds(fake_worker_command):
    supervisor = WorkerSupervisor(
        fake_worker_command,
        workers=1,
        queue_size=1,
        task_timeout=0.05,
    )
    await supervisor.start()
    first_pid = supervisor.pids[0]
    first_generation = supervisor.generation(0)
    try:
        with pytest.raises(WorkerTimeout):
            await supervisor.request("sleep", {"seconds": 1})

        assert supervisor.pids[0] != first_pid
        assert supervisor.generation(0) > first_generation
        result = await supervisor.request("echo", {"value": 7}, timeout=1)
        assert result["echo"] == {"value": 7}
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_start_fails_when_worker_health_probe_fails(tmp_path):
    script = tmp_path / "broken_worker.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    supervisor = WorkerSupervisor(
        [sys.executable, "-u", str(script)],
        workers=1,
        queue_size=1,
        task_timeout=1,
    )

    with pytest.raises(WorkerError):
        await supervisor.start()

    assert supervisor.ready() is False


def test_worker_escalates_browser_crash_to_supervisor():
    runtime = BrowserRuntime()

    with pytest.raises(RuntimeError, match="browser process crashed"):
        runtime.serialize_result(TaskResult(status="browser_crashed", elapsedMs=1))


def test_session_creation_seeds_browser_context_cookies():
    class Page:
        def evaluate(self, _):
            return "Test Agent"

        def close(self):
            pass

    class Context:
        def __init__(self):
            self.added_cookies = []

        def add_cookies(self, cookies):
            self.added_cookies.extend(cookies)

        def new_page(self):
            return Page()

        def cookies(self):
            return self.added_cookies

    class Browser:
        def __init__(self):
            self.context = Context()

        def new_context(self, **_):
            return self.context

    runtime = BrowserRuntime()
    runtime.browser = Browser()

    runtime.create_session(
        {
            "sessionId": "s1",
            "cookies": [
                {
                    "name": "cf_clearance",
                    "value": "token",
                    "domain": ".example.test",
                    "path": "/",
                }
            ],
        }
    )

    assert runtime.browser.context.added_cookies[0]["name"] == "cf_clearance"


@pytest.mark.asyncio
async def test_stop_tolerates_process_exit_between_wait_and_kill(monkeypatch):
    class RaceProcess:
        pid = None
        returncode = None

        async def wait(self):
            return 0

        def kill(self):
            raise ProcessLookupError

    async def raise_timeout(awaitable, *, timeout):
        awaitable.close()
        raise TimeoutError

    worker = WorkerProcess(0, ["fake"])
    worker.process = RaceProcess()
    monkeypatch.setattr("camoufox_service.supervisor.asyncio.wait_for", raise_timeout)

    await worker.stop()

    assert worker.process is None


@pytest.mark.asyncio
async def test_stop_falls_back_when_psutil_wait_procs_raises_oserror(monkeypatch):
    class AsyncProcess:
        pid = 43210
        returncode = None

        def __init__(self):
            self.killed = False

        async def wait(self):
            return 0

        def kill(self):
            self.killed = True

    class PsutilProcess:
        def children(self, recursive):
            return []

        def terminate(self):
            pass

    process = AsyncProcess()
    worker = WorkerProcess(0, ["fake"])
    worker.process = process
    monkeypatch.setattr("camoufox_service.supervisor.psutil.Process", lambda _: PsutilProcess())
    monkeypatch.setattr(
        "camoufox_service.supervisor.psutil.wait_procs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(22, "Invalid argument")),
    )

    await worker.stop()

    assert process.killed is True
    assert worker.process is None
