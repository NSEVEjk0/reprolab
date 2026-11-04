from __future__ import annotations
import hmac
import json
import mimetypes
import os
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any
from .fixtures import FIXTURES
from .pipeline import run_job
from .provider import VultrProvider
from .sandbox import Sandbox, SandboxUnavailable
from .store import AdmissionDenied, Store

WEB = Path(__file__).resolve().parent.parent / "web"


class Controller:
    def __init__(self, state: Path, *, origin: str, access_code: str = "", public_jobs: bool = False, daily_limit: int = 8):
        self.origin = origin.rstrip("/")
        self.access_code, self.public_jobs = access_code, public_jobs
        self.provider = VultrProvider.from_environment()
        self.sandbox = Sandbox(work_root=state / "work")
        self.store = Store(state / "jobs.sqlite3", daily_limit=daily_limit)
        self.store.recover()
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=4)
        self.sandbox_ready = False
        self.readiness = "Connect Vultr inference and enable bounded job access to run a repair"
        try:
            self.sandbox.ready()
            self.sandbox_ready = self.sandbox.collect(startup=True)["confirmed"]
            if not self.sandbox_ready:
                self.readiness = "Previous owned sandbox teardown could not be confirmed"
        except SandboxUnavailable as error:
            self.readiness = str(error)
        threading.Thread(target=self.work, daemon=True, name="reprolab-repairs").start()

    @property
    def available(self) -> bool:
        return self.provider.configured and self.sandbox_ready and bool(self.access_code or self.public_jobs)

    def work(self) -> None:
        while True:
            job_id, fixture_id = self.queue.get()
            try:
                evidence = run_job(fixture_id, sandbox=self.sandbox, provider=self.provider, job_id=job_id,
                                   progress=lambda stage, message: self.store.update(job_id, stage, message))
                payload = evidence["payload"]
                self.store.update(job_id, payload["status"], payload["message"], evidence)
            except Exception:
                self.store.update(job_id, "failed", "The controller could not complete this job. No additional paid retry was started.")
            finally:
                self.queue.task_done()


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address: tuple[str, int], controller: Controller):
        self.controller = controller
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server: BoundedServer
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args) -> None:
        # No capability headers, submitted code, request URLs or IPs in logs.
        pass

    def send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, value: Any) -> None:
        self.send(status, json.dumps(value, ensure_ascii=False).encode())

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        controller = self.server.controller
        if path == "/api/config":
            self.json(200, {"available": controller.available, "deployment": "controller", "model": controller.provider.model,
                            "access_required": bool(controller.access_code), "daily_limit": controller.store.daily_limit,
                            "examples_available": all((WEB / "examples" / ("reference-" + name + ".json")).is_file() for name in FIXTURES),
                            "message": "The bounded Vultr repair worker is connected" if controller.available else controller.readiness,
                            "fixtures": [{"id": f.id, "title": f.title, "description": f.description, "source": f.source, "example": f.example_input} for f in FIXTURES.values()]})
            return
        if path == "/api/health":
            self.json(200, {"ok": True, "sandbox_ready": controller.sandbox_ready, "inference_configured": controller.provider.configured})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(/evidence)?", path)
        if match:
            header = self.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            job = controller.store.read(match[1], token)
            if job is None:
                self.json(404, {"error": "Job not found or access token missing"})
            elif match[2] and job["evidence"] is None:
                self.json(409, {"error": "The job has no completed evidence bundle yet"})
            else:
                self.json(200, job["evidence"] if match[2] else job)
            return
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / relative).resolve()
        if not target.is_relative_to(WEB) or not target.is_file() or target.suffix not in {".html", ".js", ".css", ".svg", ".json"}:
            self.json(404, {"error": "Page not found"})
            return
        self.send(200, target.read_bytes(), mimetypes.guess_type(target)[0] or "application/octet-stream")

    def do_POST(self) -> None:
        controller = self.server.controller
        if urlsplit(self.path).path != "/api/jobs":
            self.json(404, {"error": "Route not found"}); return
        if self.headers.get("Origin", "").rstrip("/") != controller.origin:
            self.json(403, {"error": "Use the configured ReproLab site origin"}); return
        if not controller.available:
            self.json(503, {"error": controller.readiness}); return
        if controller.access_code and not hmac.compare_digest(self.headers.get("X-Reprolab-Access", ""), controller.access_code):
            self.json(403, {"error": "A valid demo access code is required"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2048 or not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Send a bounded JSON request")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict) or set(value) != {"fixture_id"} or value["fixture_id"] not in FIXTURES:
                raise ValueError("Choose one of the supported reproduction fixtures")
            admission = controller.store.create(value["fixture_id"])
            try:
                controller.queue.put_nowait((admission["id"], value["fixture_id"]))
            except queue.Full:
                controller.store.update(admission["id"], "failed", "The bounded repair queue is full")
                raise AdmissionDenied("The bounded repair queue is full")
            self.json(202, admission)
        except (ValueError, TypeError, KeyError):
            self.json(400, {"error": "Choose a valid fixture with a bounded JSON request"})
        except AdmissionDenied as error:
            self.json(429, {"error": str(error)})


def main() -> None:
    state = Path(os.environ.get("REPROLAB_STATE_DIR", "./.local"))
    controller = Controller(state, origin=os.environ.get("REPROLAB_ORIGIN", "http://127.0.0.1:8080"),
                            access_code=os.environ.get("REPROLAB_ACCESS_CODE", ""),
                            public_jobs=os.environ.get("REPROLAB_PUBLIC_JOBS") == "true",
                            daily_limit=int(os.environ.get("REPROLAB_DAILY_JOB_LIMIT", "8")))
    server = BoundedServer((os.environ.get("REPROLAB_HOST", "127.0.0.1"), int(os.environ.get("REPROLAB_PORT", "8080"))), controller)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
