from __future__ import annotations
import json
import secrets
from pathlib import Path
from typing import Any
from .sandbox import Sandbox


def containment(sandbox: Sandbox, host_root: Path) -> dict[str, Any]:
    """Probe only a controller-owned dummy file and credential absence."""
    host_root.mkdir(parents=True, exist_ok=True)
    target = (host_root / "dummy-host-secret.txt").resolve()
    sentinel = secrets.token_hex(24)
    target.write_text(sentinel)
    target.chmod(0o600)
    code = """import os, socket
def solve(args):
    try:
        with open(args['host_path']) as handle: content = handle.read()
        host_read = True
    except OSError:
        host_read = False
    try:
        connection = socket.create_connection(('1.1.1.1', 443), timeout=1)
        connection.close()
        network = True
    except OSError:
        network = False
    try:
        with open('/work/solution.py', 'w') as handle: handle.write('changed')
        work_write = True
    except OSError:
        work_write = False
    try:
        with open('/reprolab-root-write', 'w') as handle: handle.write('changed')
        root_write = True
    except OSError:
        root_write = False
    return {'host_read': host_read, 'network': network, 'work_write': work_write, 'root_write': root_write, 'provider_key_present': bool(os.getenv('VULTR_INFERENCE_API_KEY')), 'uid': os.getuid()}
"""
    try:
        normal = sandbox.run(code, [{"host_path": str(target)}])
        output = json.loads(normal["stdout"])
        observed = output["results"][0]["result"]
        wanted = {"host_read": False, "network": False, "work_write": False, "root_write": False, "provider_key_present": False, "uid": 65532}
        isolation_ok = observed == wanted and normal["exit_code"] == 0 and normal["policy_verified"] and normal["teardown"]["confirmed"] and sentinel not in normal["stdout"]
        timeout = sandbox.run("def solve(args):\n    while True: pass\n", [{}], seconds=2)
        flood = sandbox.run("def solve(args):\n    while True: print('x' * 8192, flush=True)\n", [{}])
        return {"isolation": {"ok": isolation_ok, "observed": observed, "runtime": normal["runtime"], "policy": normal["policy"], "teardown": normal["teardown"]},
                "timeout": {"ok": timeout["stop_reason"] == "timeout" and timeout["teardown"]["confirmed"], "stop_reason": timeout["stop_reason"], "elapsed_ms": timeout["elapsed_ms"], "teardown": timeout["teardown"]},
                "output_limit": {"ok": flood["stop_reason"] == "output_limit" and flood["stdout_bytes"] + flood["stderr_bytes"] <= 65536 and flood["teardown"]["confirmed"], "stop_reason": flood["stop_reason"], "captured_bytes": flood["stdout_bytes"] + flood["stderr_bytes"], "teardown": flood["teardown"]}}
    finally:
        target.unlink(missing_ok=True)
