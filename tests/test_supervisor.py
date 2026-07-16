import sys
import textwrap

import pytest

from camoufox_service.supervisor import WorkerSupervisor, WorkerTimeout
from camoufox_service.models import TaskResult
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
    try:
        with pytest.raises(WorkerTimeout):
            await supervisor.request("sleep", {"seconds": 1})

        assert supervisor.pids[0] != first_pid
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

    with pytest.raises(Exception):
        await supervisor.start()

    assert supervisor.ready() is False


def test_worker_escalates_browser_crash_to_supervisor():
    runtime = BrowserRuntime()

    with pytest.raises(RuntimeError, match="browser process crashed"):
        runtime.serialize_result(TaskResult(status="browser_crashed", elapsedMs=1))
