import json
import os
import sys
import time

for line in sys.stdin:
    request = json.loads(line)
    kind = request["kind"]
    if kind == "sleep":
        time.sleep(float(request["payload"]["seconds"]))
    if kind == "large":
        result = {"body": "x" * int(request["payload"]["bytes"])}
    else:
        result = {"echo": request["payload"], "pid": os.getpid()}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
