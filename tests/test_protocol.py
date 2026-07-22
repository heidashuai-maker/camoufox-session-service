import io
import json

from camoufox_service.protocol import run_worker


class FakeRuntime:
    def __init__(self):
        self.closed = False

    def handle(self, kind, payload):
        if kind == "fail":
            raise ValueError("bad job")
        return {"kind": kind, "payload": payload}

    def close(self):
        self.closed = True


def test_worker_protocol_serializes_results_errors_and_closes_runtime():
    source = io.StringIO(
        '{"id":"1","kind":"echo","payload":{"value":1}}\n{"id":"2","kind":"fail","payload":{}}\n'
    )
    target = io.StringIO()
    runtime = FakeRuntime()

    run_worker(runtime, source=source, target=target)

    responses = [json.loads(line) for line in target.getvalue().splitlines()]
    assert responses[0] == {
        "id": "1",
        "result": {"kind": "echo", "payload": {"value": 1}},
    }
    assert responses[1] == {
        "id": "2",
        "error": {"type": "ValueError", "message": "bad job"},
    }
    assert runtime.closed is True
