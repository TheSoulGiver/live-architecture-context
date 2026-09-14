"""Ensure one authenticated, project-local map without owning a terminal."""
from __future__ import annotations

import http.client
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import archctx
import archctx_blueprint as blueprint
import archctx_development as development

CORE_SOURCE_PATH = Path(__file__).resolve()
CORE_SOURCE_SHA256 = archctx.sha(CORE_SOURCE_PATH.read_bytes())
RECEIPT_BYTES = 32 * 1024
START_SECONDS = 8


def entry() -> list[str]:
    """Retain the actual wrapper/module and the interpreter's isolation boundary."""
    prefix = [sys.executable, *(["-I"] if sys.flags.isolated else [])]
    script = Path(sys.argv[0]).resolve()
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if (spec and spec.name.split(".")[0] in ("unittest", "pytest")) or script.name == "archctx_blueprint.py":
        return [*prefix, str(archctx.CORE_SOURCE_PATH)]
    if spec and getattr(main, "__file__", None) and Path(main.__file__).resolve() == script:
        return [*prefix, "-m", spec.name]
    if script.suffix in (".py", ".pyw") and script.is_file():
        return [*prefix, str(script)]
    return [*prefix, str(archctx.CORE_SOURCE_PATH)]


def _fingerprint(module) -> dict:
    path = Path(getattr(module, "CORE_SOURCE_PATH", module.__file__)).resolve()
    captured = hasattr(module, "CORE_SOURCE_SHA256")
    return {"path": str(path), "sha256": module.CORE_SOURCE_SHA256 if captured else archctx.sha(path.read_bytes()),
            "provenance": "module_import" if captured else "map_startup_source_snapshot"}


def _entry_source(command: list[str]) -> dict:
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    source = command[-1] if command[-2] != "-m" else (
        spec.origin if spec and spec.name == command[-1] and spec.origin else archctx.CORE_SOURCE_PATH)
    path = Path(source).resolve()
    return {"path": str(path), "sha256": archctx.sha(path.read_bytes()), "provenance": "map_startup_source_snapshot"}


# Freeze once: a running service never relabels itself with subsequently replaced files.
MODULES = {module.__name__: _fingerprint(module) for module in (
    archctx, blueprint, development, sys.modules[__name__],
    *(importlib.import_module(name) for name in ("archctx_understand", "archctx_analysis_storage",
      "archctx_analysis_inputs", "archctx_runtime", "archctx_to_archify")))}
ENTRY = entry()
ENTRY_SOURCE = _entry_source(ENTRY)


def identity(config_path: Path, explicit: str | None, live: bool) -> dict:
    return {"config": str(config_path.resolve()), "state": str(archctx.state(config_path, explicit).resolve()),
            "mode": "live" if live else "read_only", "python": str(Path(sys.executable).resolve()),
            "entry": ENTRY, "entry_source": ENTRY_SOURCE, "modules": MODULES}


def _receipt(directory: Path) -> dict | None:
    try:
        with (directory / "map-service.json").open("rb") as stream:
            raw = stream.read(RECEIPT_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > RECEIPT_BYTES:
        raise ValueError("map service receipt exceeds local size limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unsupported map service receipt")
    if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("instance_id", ""))):
        raise ValueError("invalid map service identity")
    if value.get("state") == "failed" and isinstance(value.get("reason"), str) and len(value["reason"]) <= 500:
        return value
    if not isinstance(value.get("url"), str):
        raise ValueError("map service URL must be a string")
    url = urlsplit(value["url"])
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username or url.password or url.path != "/" or url.query or url.fragment):
        raise ValueError("map service must use an explicit loopback URL")
    if not isinstance(value.get("identity"), dict) or type(value.get("pid")) is not int or value["pid"] <= 0:
        raise ValueError("incomplete map service receipt")
    return value


def _status(expected: dict) -> dict:
    try:
        receipt = _receipt(Path(expected["state"]))
        if receipt is None:
            return {"status": "MISSING", "reason": "No managed map receipt exists"}
        if receipt.get("state") == "failed":
            return {"status": "RETRY", "reason": receipt["reason"]}
        connection = http.client.HTTPConnection("127.0.0.1", urlsplit(receipt["url"]).port, timeout=.5)
        try:
            connection.request("GET", "/api/runtime")
            response = connection.getresponse()
            raw = response.read(RECEIPT_BYTES + 1)
            if response.status != 200 or len(raw) > RECEIPT_BYTES:
                raise ValueError("map health response is unavailable or oversized")
            health = json.loads(raw)
        finally:
            connection.close()
        if (not isinstance(health, dict) or health.get("instance_id") != receipt["instance_id"]
                or health.get("identity") != receipt["identity"] or health.get("pid") != receipt["pid"]):
            raise ValueError("map endpoint does not match its saved identity")
        result = {key: receipt[key] for key in ("url", "pid", "instance_id", "identity")}
        if health["identity"] != expected:
            return {**result, "status": "MISMATCH", "reason": "Existing map uses different code, entry, scope, or mode; it was preserved"}
        state = health.get("state")
        if state not in ("starting", "running"):
            return {**result, "status": "RETRY", "reason": health.get("reason", "Map observer is not running")}
        return {**result, "status": state.upper(), "state": state}
    except (OSError, ValueError, TypeError, http.client.HTTPException) as error:
        return {"status": "RETRY", "reason": str(error)[:500]}


def status(config_path: Path, explicit: str | None = None, *, live: bool = True, port: int = 0) -> dict:
    """Read a bounded receipt and verify its loopback endpoint; never create state."""
    return _status(identity(Path(config_path).resolve(), explicit, live))


def ensure(config_path: Path, explicit: str | None = None, *, live: bool = True, port: int = 0) -> dict:
    config_path = Path(config_path).resolve()
    expected = identity(config_path, explicit, live)
    directory = Path(expected["state"])
    try:
        with archctx.refresh_lock(directory, "map-start.lock"):
            current = _status(expected)
            if current["status"] in ("STARTING", "RUNNING", "MISMATCH"):
                return {**current, "status": "REUSED"} if current["status"] != "MISMATCH" else current
            # Only an OS-released owner lock proves that a disconnected receipt is recoverable.
            try:
                with archctx.refresh_lock(directory, "map-owner.lock"):
                    pass
            except archctx.RefreshBusyError:
                return {"status": "RETRY", "reason": "A managed map owns this scope but its identity cannot be confirmed"}
            instance_id = uuid.uuid4().hex
            command = [*expected["entry"], "--config", str(config_path), "--state-dir", str(directory),
                       "map", "--_serve", instance_id, "--port", str(port)]
            if not live:
                command.append("--read-only")
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, shell=False, **options)
            deadline = time.monotonic() + START_SECONDS
            while time.monotonic() < deadline:
                current = _status(expected)
                if current.get("instance_id") == instance_id:
                    if current["status"] in ("STARTING", "RUNNING"):
                        return {**current, "status": "STARTED"}
                    return current
                if process.poll() is not None:
                    failed = _receipt(directory)
                    reason = failed["reason"] if failed and failed.get("instance_id") == instance_id and failed.get("state") == "failed" else "Map child exited before authenticated startup"
                    return {"status": "RETRY", "reason": reason, "exit_code": process.returncode}
                time.sleep(.05)
            return {"status": "RETRY", "pid": process.pid, "reason": "Map startup is still unconfirmed; retry uses the same owner lock"}
    except archctx.RefreshBusyError:
        current = _status(expected)
        return {**current, "status": "REUSED"} if current["status"] in ("STARTING", "RUNNING") else {
            **current, "status": "MISMATCH" if current["status"] == "MISMATCH" else "RETRY"}
    except (OSError, ValueError) as error:
        return {"status": "RETRY", "reason": str(error)[:500]}


@contextmanager
def _owner(directory: Path, instance_id: str):
    with archctx.refresh_lock(directory, "map-owner.lock"):
        try:
            yield
        except Exception as error:
            archctx.atomic(directory / "map-service.json", {"version": 1, "instance_id": instance_id,
                "state": "failed", "reason": f"Map startup failed: {type(error).__name__}: {error}"[:500]})
            raise


def serve(config_path: Path, explicit: str | None = None, *, live: bool = True,
          port: int = 0, instance_id: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{32}", instance_id):
        raise ValueError("managed map requires its launch identity")
    config_path = Path(config_path).resolve()
    expected = identity(config_path, explicit, live)
    directory = Path(expected["state"])
    with _owner(directory, instance_id):
        observer = development.DevelopmentObserver(config_path, str(directory), live=live)
        runtime = {"state": "starting", "instance_id": instance_id, "pid": os.getpid(), "identity": expected}
        base = blueprint.handler(config_path, str(directory), observer)

        class Handler(base):
            def do_GET(self):
                if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                    self.reply(403, "Local blueprint host required", "text/plain")
                elif urlsplit(self.path).path == "/api/runtime":
                    health = dict(runtime)
                    if health["state"] == "running" and not (observer._thread and observer._thread.is_alive()):
                        health.update(state="stopped", reason="Map observer stopped; existing viewer was preserved")
                    self.reply(200, json.dumps(health), "application/json")
                elif runtime["state"] != "running" and urlsplit(self.path).path != "/":
                    self.reply(503, "Map observation is initializing", "text/plain")
                else:
                    super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.daemon_threads = True
        record = {"version": 1, "url": f"http://127.0.0.1:{server.server_port}/",
                  **{key: runtime[key] for key in ("instance_id", "pid", "identity")}}

        def initialize():
            try:
                observer.start()
                runtime["state"] = "running"
            except Exception as error:
                runtime.update(state="failed", reason=f"Observer initialization failed: {type(error).__name__}: {error}"[:500])

        try:
            archctx.atomic(directory / "map-service.json", record)
            threading.Thread(target=initialize, name="archctx-map-start", daemon=True).start()
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            observer.stop()
    return 0
