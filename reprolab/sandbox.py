"""The trusted controller invokes a fixed, disposable gVisor sandbox.

Generated code never runs in the controller process. All process output is
streamed into a shared byte budget, including Docker control commands.
"""
from __future__ import annotations
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .evidence import canonical, sha256_bytes

IMAGE = "python:3.12-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487"
MEMORY_BYTES = 192 * 1024 * 1024
OUTPUT_LIMIT = 64 * 1024
SANDBOX_SECONDS = 8.0


@dataclass(frozen=True)
class Captured:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    stop_reason: str | None
    elapsed_ms: int


def capture(command: list[str], *, input_bytes: bytes = b"", seconds: float = 8.0,
            output_limit: int = OUTPUT_LIMIT) -> Captured:
    """No shell, no inherited credentials, bounded input/output and wall time."""
    if seconds <= 0 or output_limit <= 0 or len(input_bytes) > 128 * 1024:
        raise ValueError("Invalid process limits")
    start = time.monotonic()
    deadline = start + seconds
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    used = 0
    stop_reason = None
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True,
                                cwd="/", env={"PATH": os.defpath, "LANG": "C.UTF-8"})
    except OSError as error:
        return Captured(b"", str(error).encode()[:500], None, "spawn_error", 0)
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    remaining_input = memoryview(input_bytes)
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, name in [(proc.stdout, "stdout"), (proc.stderr, "stderr")]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            if input_bytes:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                proc.stdin.close()
            while selector.get_map():
                available = deadline - time.monotonic()
                if available <= 0:
                    stop_reason = "timeout"
                    break
                for key, _ in selector.select(min(available, 0.1)):
                    pipe, name = key.fileobj, key.data
                    if name == "stdin":
                        try:
                            written = os.write(pipe.fileno(), remaining_input[:8192])
                            remaining_input = remaining_input[written:]
                        except BlockingIOError:
                            continue
                        except (BrokenPipeError, OSError):
                            remaining_input = memoryview(b"")
                        if not remaining_input:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    try:
                        chunk = os.read(pipe.fileno(), min(8192, output_limit - used + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    accepted = chunk[:max(0, output_limit - used)]
                    buffers[name].extend(accepted)
                    used += len(accepted)
                    if len(chunk) > len(accepted):
                        stop_reason = "output_limit"
                        break
                if stop_reason:
                    break
            if not stop_reason:
                try:
                    proc.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    stop_reason = "timeout"
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.close()
    return Captured(bytes(buffers["stdout"]), bytes(buffers["stderr"]), proc.returncode,
                    stop_reason, round((time.monotonic() - start) * 1000))


class SandboxUnavailable(RuntimeError):
    pass


class Sandbox:
    def __init__(self, *, image: str = IMAGE, work_root: Path | None = None,
                 seconds: float = SANDBOX_SECONDS, docker: str = "/usr/bin/docker"):
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
            raise ValueError("An immutable image digest is required")
        if seconds < 1 or seconds > 15:
            raise ValueError("Sandbox deadline must be between 1 and 15 seconds")
        self.image, self.seconds, self.docker = image, seconds, docker
        self.work_root = (work_root or Path(tempfile.gettempdir()) / "reprolab-work").resolve()
        self.work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.instance = sha256_bytes(str(self.work_root).encode())[:24]

    def control(self, *arguments: str, seconds: float = 5.0) -> Captured:
        return capture([self.docker, "--host=unix:///var/run/docker.sock", *arguments],
                       seconds=seconds, output_limit=64 * 1024)

    def ready(self) -> dict[str, Any]:
        runtimes = self.control("info", "--format", "{{json .Runtimes}}")
        if runtimes.stop_reason or runtimes.exit_code != 0:
            raise SandboxUnavailable("The Docker daemon is unavailable to the controller")
        try:
            if "runsc" not in json.loads(runtimes.stdout):
                raise SandboxUnavailable("The required gVisor runsc runtime is not installed")
        except (ValueError, TypeError) as error:
            raise SandboxUnavailable("Docker runtime configuration could not be read") from error
        image = self.control("image", "inspect", self.image, "--format", "{{.Id}}")
        if image.stop_reason or image.exit_code != 0:
            raise SandboxUnavailable("The pinned sandbox image must be pulled by the operator")
        return {"runtime": "runsc", "image_ref": self.image, "image_id": image.stdout.decode().strip()}

    def command(self, name: str, work: Path) -> list[str]:
        if not re.fullmatch(r"reprolab-[a-f0-9]{32}", name) or not work.is_relative_to(self.work_root):
            raise ValueError("Invalid owned sandbox")
        return [self.docker, "--host=unix:///var/run/docker.sock", "run", "--pull=never", "--runtime=runsc",
                "--name", name, "--label", "io.reprolab.owned=true", "--label", "io.reprolab.run=" + name,
                "--label", "io.reprolab.instance=" + self.instance, "--label", "io.reprolab.expires=" + str(int(time.time()) + 30),
                "--network=none", "--read-only", "--cpus=0.5", "--memory=192m", "--memory-swap=192m",
                "--pids-limit=48", "--ulimit=nofile=64:64", "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
                "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--log-driver=none", "--mount", "type=bind,source=" + str(work) + ",target=/work,readonly",
                "-i", self.image, "python", "-I", "-B", "/work/worker.py"]

    @staticmethod
    def policy(inspect: dict[str, Any]) -> dict[str, bool]:
        host, config = inspect.get("HostConfig", {}), inspect.get("Config", {})
        mounts = inspect.get("Mounts", [])
        return {
            "gvisor": host.get("Runtime") == "runsc",
            "no_network": host.get("NetworkMode") == "none",
            "readonly_root": host.get("ReadonlyRootfs") is True,
            "nonroot": config.get("User") == "65532:65532",
            "memory_limit": host.get("Memory") == MEMORY_BYTES and host.get("MemorySwap") == MEMORY_BYTES,
            "cpu_limit": host.get("NanoCpus") == 500_000_000,
            "pid_limit": host.get("PidsLimit") == 48,
            "no_privilege": host.get("Privileged") is False and "ALL" in host.get("CapDrop", [])
                            and any(x.startswith("no-new-privileges") for x in host.get("SecurityOpt", [])),
            "readonly_work": len(mounts) == 1 and mounts[0].get("Destination") == "/work" and mounts[0].get("RW") is False,
            "bounded_tmp": any(x.startswith("rw,noexec,nosuid,size=16m") for x in [host.get("Tmpfs", {}).get("/tmp", "")]),
            "no_docker_logs": host.get("LogConfig", {}).get("Type") == "none",
        }

    def teardown(self, name: str) -> dict[str, Any]:
        removed = self.control("rm", "--force", name)
        absent = self.control("ps", "--all", "--filter", "name=^" + name + "$", "--format", "{{.ID}}")
        confirmed = absent.exit_code == 0 and absent.stop_reason is None and not absent.stdout.strip()
        return {"confirmed": confirmed, "remove_exit_code": removed.exit_code,
                "absence_query_exit_code": absent.exit_code, "reason": None if confirmed else "Container absence could not be confirmed"}

    def collect(self, *, startup: bool = False) -> dict[str, Any]:
        listed = self.control("ps", "--all", "--filter", "label=io.reprolab.instance=" + self.instance,
                              "--filter", "label=io.reprolab.owned=true", "--format", "{{.Names}}")
        if listed.exit_code != 0 or listed.stop_reason:
            return {"confirmed": False, "removed": [], "reason": "Daemon unavailable"}
        removed = []
        for name in listed.stdout.decode().splitlines():
            if not re.fullmatch(r"reprolab-[a-f0-9]{32}", name):
                continue
            inspect = self.control("inspect", name, "--format", "{{json .Config.Labels}}")
            if inspect.exit_code != 0 or inspect.stop_reason:
                continue
            try:
                labels = json.loads(inspect.stdout)
                expired = int(labels.get("io.reprolab.expires", "0")) < int(time.time())
                owned = labels.get("io.reprolab.instance") == self.instance and labels.get("io.reprolab.owned") == "true"
            except (ValueError, TypeError):
                continue
            if owned and (startup or expired):
                removed.append({"sandbox_id": name, "teardown": self.teardown(name)})
        return {"confirmed": all(item["teardown"]["confirmed"] for item in removed), "removed": removed}

    def run(self, code: str, inputs: list[dict[str, Any]], *, seconds: float | None = None) -> dict[str, Any]:
        if not isinstance(code, str) or len(code.encode()) > 24 * 1024 or not code.strip():
            raise ValueError("Candidate source must be between 1 and 24,576 bytes")
        nonce, name = uuid.uuid4().hex, "reprolab-" + uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix="run-", dir=self.work_root))
        work.chmod(0o755)
        result: dict[str, Any] = {"sandbox_id": name, "runtime": None, "image_ref": self.image,
                                  "nonce": nonce, "policy": {}, "policy_verified": False,
                                  "source_sha256": sha256_bytes(code.encode()),
                                  "worker_sha256": sha256_bytes(Path(__file__).with_name("worker.py").read_bytes())}
        try:
            for filename, data in [("solution.py", code), ("worker.py", Path(__file__).with_name("worker.py").read_text())]:
                target = work / filename
                target.write_text(data)
                target.chmod(0o644)
            input_bytes = canonical({"nonce": nonce, "cases": inputs})
            outcome = capture(self.command(name, work), input_bytes=input_bytes,
                              seconds=seconds if seconds is not None else self.seconds)
            result.update({"exit_code": outcome.exit_code, "stop_reason": outcome.stop_reason,
                           "elapsed_ms": outcome.elapsed_ms, "stdout_bytes": len(outcome.stdout),
                           "stderr_bytes": len(outcome.stderr), "stdout_sha256": sha256_bytes(outcome.stdout),
                           "stderr_excerpt": outcome.stderr.decode(errors="replace")[:1500], "stdout": outcome.stdout.decode(errors="replace")})
            inspection = self.control("inspect", name, "--format", "{{json .}}")
            if inspection.exit_code == 0 and inspection.stop_reason is None:
                try:
                    value = json.loads(inspection.stdout)
                    checks = self.policy(value)
                    result.update({"runtime": value["HostConfig"].get("Runtime"), "image_id": value.get("Image"),
                                   "policy": checks, "policy_verified": all(checks.values()),
                                   "oom_killed": value.get("State", {}).get("OOMKilled", False)})
                except (ValueError, TypeError, KeyError):
                    result["inspection_error"] = "Container policy could not be parsed"
        finally:
            result["teardown"] = self.teardown(name)
            shutil.rmtree(work)
        return result
