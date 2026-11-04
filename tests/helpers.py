import json
from reprolab.fixtures import expected

def execution(fixture_id, inputs):
    return {"nonce": "test", "exit_code": 0, "stop_reason": None, "runtime": "runsc",
            "policy_verified": True, "policy": {"gvisor": True}, "teardown": {"confirmed": True},
            "stdout": json.dumps({"nonce": "test", "results": [{"index": i, "result": expected(fixture_id, args), "error": None} for i, args in enumerate(inputs)]})}
