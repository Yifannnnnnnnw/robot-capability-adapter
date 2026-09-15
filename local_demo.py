#!/usr/bin/env python3
"""Local-only presentation bridge for one existing Panda task. No AA1 edits.

Run with AA1/.venv/bin/python website/local_demo.py from the project checkout.
The worker reuses the saved request, real model, ReCAP, MCP, generated driver,
MuJoCo and physical scorer. All fresh demo output stays in a temporary directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
AA1 = ROOT / "AA1"
REQUEST = ROOT / "expriment/chapter5_cross_robot/data/runs/franka_single_20260915_01/tasks_astra_budget48/requests/mw_pick_place_central.json"
PAGES_ORIGIN = "https://yifannnnnnnnw.github.io"


def emit(directory, event):
    """Presentation output must not alter a driver return or task verdict."""
    try:
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, allow_nan=False) + "\n")
    except (OSError, ValueError):
        pass


def install_capture(directory):
    """Publish actual recorder frames in the MCP child; never step the world."""
    from PIL import Image
    from auto_adapter import export_runtime

    class LiveRecorder(export_runtime._TaskRecorder):
        last_published = 0.0

        def snapshot(self):
            previous = len(self.frames)
            super().snapshot()
            if len(self.frames) == previous or time.monotonic() - self.last_published < 0.08:
                return
            try:
                temporary = directory / "frame.next.jpg"
                Image.fromarray(self.frames[-1]).save(temporary, quality=82)
                temporary.replace(directory / "frame.jpg")
                self.last_published = time.monotonic()
            except (OSError, ValueError):
                pass

    export_runtime._TaskRecorder = LiveRecorder


def worker(directory):
    sys.path[:0] = [str(AA1), str(ROOT / "expriment/chapter5_cross_robot")]
    from auto_adapter.agent.recap import AA1CapabilityAdapter, AA1RecapModel
    from auto_adapter.agent.task_execution import run_task
    from robots.franka.task_evaluation import evaluate_franka_task

    payload = json.loads((directory / "request.json").read_text())
    original_generate = AA1RecapModel.generate_json
    original_execute = AA1CapabilityAdapter.execute
    call_count = 0

    def generate(model, *, messages):
        emit(directory, {"type": "model_start"})
        result = original_generate(model, messages=messages)
        try:
            text = result.strip() if isinstance(result, str) else result
            if isinstance(text, str) and text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            plan = json.loads(text) if isinstance(text, str) else text
            if isinstance(plan, dict):
                emit(directory, {"type": "plan", "summary": plan.get("think", ""),
                                 "subtasks": plan.get("subtasks", [])})
        except (ValueError, TypeError):
            pass  # ReCAP still receives the original response and handles it.
        return result

    def execute(adapter, name, request):
        nonlocal call_count
        call_count += 1
        emit(directory, {"type": "call", "index": call_count, "name": name, "request": request})
        feedback = original_execute(adapter, name, request)
        operation = feedback.get("operation", {})
        result = operation.get("return_value")
        result = result if isinstance(result, dict) else {}
        emit(directory, {"type": "result", "index": call_count, "name": name,
                         "driverResult": {"success": result.get("success"),
                                          "reason": result.get("reason") or operation.get("error_type"),
                                          "operationStatus": operation.get("status")},
                         "simTime": feedback.get("observations", {}).get("sim_time_s")})
        return feedback

    AA1RecapModel.generate_json = generate
    AA1CapabilityAdapter.execute = execute
    try:
        report = run_task(**payload, task_evaluator=evaluate_franka_task)
        return 0 if report.get("ok") is True else 1
    except BaseException as error:
        emit(directory, {"type": "worker_error", "errorType": type(error).__name__})
        raise


class Bridge:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.process = None
        self.directory = None
        self.started = None
        self.stopped = False
        self.lock = threading.Lock()

    def missing_inputs(self):
        missing = []
        if not REQUEST.is_file():
            return ["saved Panda task request"]
        payload = json.loads(REQUEST.read_text())
        for key in ("driver_path", "export_server_path", "scene_path"):
            if not Path(payload[key]).is_file():
                missing.append(key)
        if not (AA1 / ".venv/bin/python").is_file():
            missing.append("AA1 Python environment")
        return missing

    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        with self.lock:
            if self.running():
                raise ValueError("A local task is already running.")
            missing = self.missing_inputs()
            if missing:
                raise ValueError("Missing local inputs: " + ", ".join(missing))
            self.directory = Path(tempfile.mkdtemp(prefix="autoadapter-web-demo-"))
            payload = json.loads(REQUEST.read_text())
            original_server = payload["export_server_path"]
            bootstrap = self.directory / "mcp_bootstrap.py"
            bootstrap.write_text(
                "import sys, runpy\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(SITE)!r})\n"
                "from local_demo import install_capture\n"
                f"install_capture(Path({str(self.directory)!r}))\n"
                f"runpy.run_path({original_server!r}, run_name='__main__')\n"
            )
            payload["export_server_path"] = str(bootstrap)
            payload["output_dir"] = str(self.directory / "run")
            (self.directory / "request.json").write_text(json.dumps(payload, indent=2) + "\n")
            environment = {**os.environ, "PYTHONPATH": str(AA1)}
            with (self.directory / "worker.log").open("w") as log:
                self.process = subprocess.Popen(
                    [str(AA1 / ".venv/bin/python"), str(SITE / "local_demo.py"), "--worker", str(self.directory)],
                    cwd=AA1, env=environment, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            self.started = time.monotonic()
            self.stopped = False
            # One bounded demo uses the saved request's existing budget.
            deadline = threading.Timer(900, self.stop, args=(self.process,))
            deadline.daemon = True
            deadline.start()

    def stop(self, expected=None):
        with self.lock:
            if expected is not None and self.process is not expected:
                return
            if self.running():
                self.stopped = True
                os.killpg(self.process.pid, signal.SIGTERM)

    def events(self, after):
        records = []
        report = None
        frame = None
        if self.directory:
            path = self.directory / "events.jsonl"
            if path.exists():
                for line in path.read_text().splitlines():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        break
            path = self.directory / "frame.jpg"
            if path.exists():
                frame = str(path.stat().st_mtime_ns)
            path = self.directory / "run/task_report.json"
            if not self.running() and path.exists():
                try:
                    full = json.loads(path.read_text())
                    report = {key: full.get(key) for key in ("ok", "execution_ok", "physical_task_success")}
                except json.JSONDecodeError:
                    pass
        error = None
        if self.process and not self.running() and report is None:
            error = "Task stopped by request." if self.stopped else "Worker ended without a final report. Check the local worker log."
        return {"running": self.running(), "events": records[after:], "cursor": len(records),
                "frame": frame, "report": report, "error": error}


def serve(port):
    bridge = Bridge()
    local_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    allowed_origins = local_origins | {PAGES_ORIGIN}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SITE), **kwargs)

        def log_message(self, *_args):
            pass

        def allowed(self):
            return (self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    and self.headers.get("Origin") in allowed_origins | {None})

        def end_headers(self):
            origin = self.headers.get("Origin")
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "X-Demo-Token")
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def respond(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.respond({}, 200 if self.allowed() else 403)

        def do_GET(self):
            if not self.allowed():
                return self.respond({"error": "Origin or host is not allowed."}, 403)
            url = urlsplit(self.path)
            if url.path == "/api/status":
                missing = bridge.missing_inputs()
                return self.respond({"service": "autoadapter-local-demo", "ready": not missing,
                                     "missing": missing, "running": bridge.running(),
                                     "has_run": bridge.directory is not None, "token": bridge.token})
            if url.path == "/api/events":
                try:
                    after = max(0, int(parse_qs(url.query).get("after", ["0"])[0]))
                except ValueError:
                    return self.respond({"error": "Invalid event cursor."}, 400)
                return self.respond(bridge.events(after))
            if url.path == "/api/frame":
                path = bridge.directory / "frame.jpg" if bridge.directory else None
                if not path or not path.exists():
                    return self.respond({"error": "No simulation frame yet."}, 404)
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if url.path == "/assets/panda-pick-place.mp4":
                # Browser video seeking needs byte ranges even for a tiny file.
                path = SITE / "assets/panda-pick-place.mp4"
                size = path.stat().st_size
                start, end = 0, size - 1
                byte_range = self.headers.get("Range")
                if byte_range:
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                    if not match:
                        return self.respond({"error": "Unsupported byte range."}, 416)
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                    if start > end:
                        return self.respond({"error": "Range is outside the video."}, 416)
                self.send_response(206 if byte_range else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if byte_range:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with path.open("rb") as stream:
                    stream.seek(start)
                    self.wfile.write(stream.read(end - start + 1))
                return
            return super().do_GET()

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get("X-Demo-Token", ""), bridge.token):
                return self.respond({"error": "Connect to this bridge before starting or stopping a task."}, 403)
            try:
                if self.path == "/api/run":
                    bridge.start()
                elif self.path == "/api/stop":
                    bridge.stop()
                else:
                    return self.respond({"error": "Unknown demo action."}, 404)
                return self.respond({"running": bridge.running()})
            except ValueError as error:
                return self.respond({"error": str(error)}, 409)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"AutoAdapter local demo: http://127.0.0.1:{port}", flush=True)
    print("No model calls until Run live task is selected. Ctrl+C stops the bridge and its worker.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.worker:
        raise SystemExit(worker(arguments.worker))
    serve(arguments.port)
