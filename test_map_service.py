"""Real detached map lifetime and authenticated recovery in checkout-local fixtures."""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


class MapServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="map service ", dir=ROOT)
        self.root = Path(self.temporary.name)
        self.trusted = self.root / "trusted code"
        self.trusted.mkdir()
        for source in ROOT.glob("archctx*.py"):
            shutil.copy2(source, self.trusted / source.name)
        self.repo = self.root / "consumer project"
        self.repo.mkdir()
        self.config = self.repo / "architecture.json"
        self.config.write_text(json.dumps({"version": 1, "repo": ".", "components": [], "relations": []}), encoding="utf-8")
        self.state = self.repo / "selected state"
        self.wrapper = self.repo / "lac wrapper.py"
        self.wrapper.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {str(self.trusted)!r})\n"
            "import archctx, archctx_development\n"
            "if '--_serve' in sys.argv:\n"
            "    original = archctx_development.DevelopmentObserver.start\n"
            "    def slow_start(self):\n"
            "        time.sleep(2)\n"
            "        return original(self)\n"
            "    archctx_development.DevelopmentObserver.start = slow_start\n"
            "raise SystemExit(archctx.main())\n", encoding="utf-8")
        self.children = []

    def call(self, *flags):
        result = subprocess.run([sys.executable, "-I", str(self.wrapper), "--config", str(self.config),
                                 "--state-dir", str(self.state), "map", *flags], cwd=self.repo,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertIn(result.returncode, (0, 2), result.stderr)
        value = json.loads(result.stdout)
        if value.get("status") == "STARTED":
            self.children.append(value)
        return result.returncode, value

    def health(self, child, path="/api/runtime"):
        connection = http.client.HTTPConnection("127.0.0.1", urlsplit(child["url"]).port, timeout=.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def stop(self, child):
        # Only a child started by this test and still proving its nonce can be terminated.
        try:
            health = self.health(child)
        except (OSError, http.client.HTTPException):
            return
        self.assertEqual((health["pid"], health["instance_id"]), (child["pid"], child["instance_id"]))
        os.kill(child["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.health(child)
            except (OSError, http.client.HTTPException):
                return
            time.sleep(.05)
        self.fail("owned map child did not stop")

    def tearDown(self):
        for child in self.children:
            self.stop(child)
        self.temporary.cleanup()

    def test_detached_lifetime_identity_reuse_and_recovery(self):
        code, absent = self.call("--status", "--read-only")
        self.assertEqual((code, absent["status"], self.state.exists()), (0, "MISSING", False))
        (self.repo / "owner.py").write_text("OWNER = 1\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("selected state/\n", encoding="utf-8")
        self.config.write_text(json.dumps({"version": 1, "repo": ".", "components": [
            {"id": "owner", "evidence": [{"path": "owner.py", "contains": "OWNER = 1"}]}], "relations": []}), encoding="utf-8")
        for arguments in (["init", "-q"], ["add", "--", "owner.py", "architecture.json", ".gitignore"],
                          ["-c", "user.name=LAC Fixture", "-c", "user.email=fixture@example.invalid",
                           "commit", "--no-gpg-sign", "-qm", "Synthetic accepted owner"]):
            result = subprocess.run(["git", *arguments], cwd=self.repo, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        refreshed = subprocess.run([sys.executable, "-I", str(self.wrapper), "--config", str(self.config),
                                    "--state-dir", str(self.state), "refresh"], cwd=self.repo,
                                   capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr + refreshed.stdout)
        accepted_hash = json.loads(refreshed.stdout)["context_hash"]
        def accepted_bytes():
            return {p.relative_to(self.state).as_posix(): p.read_bytes()
                    for p in [self.state / "last-good.json", *sorted((self.state / "snapshots").glob("*.json"))]}
        accepted_before = accepted_bytes()
        self.assertEqual(len(accepted_before), 2)  # The real refresh published both LKG and its retained snapshot.
        code, failed = self.call("--ensure", "--read-only", "--port", "-1")
        self.assertEqual((code, failed["status"]), (2, "RETRY"))
        self.assertIn("port", failed["reason"].lower(), failed)
        self.assertLessEqual(len(failed["reason"]), 500)
        code, first = self.call("--ensure", "--read-only")
        self.assertEqual((code, first["status"]), (0, "STARTED"), first)
        self.assertEqual(first["identity"]["entry"], [sys.executable, "-I", str(self.wrapper)])
        self.assertEqual(first["identity"]["config"], str(self.config))
        self.assertEqual(first["identity"]["state"], str(self.state))
        # The launcher has already exited, and the HTTP service is ready before the first observer poll.
        self.assertEqual(self.health(first)["state"], "starting")
        code, reused = self.call("--ensure", "--read-only")
        self.assertEqual((code, reused["status"], reused["pid"]), (0, "REUSED", first["pid"]))
        code, mismatch = self.call("--ensure")
        self.assertEqual((code, mismatch["status"], mismatch["pid"]), (2, "MISMATCH", first["pid"]))
        deadline = time.monotonic() + 5
        while self.health(first)["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(self.health(first)["state"], "running")
        current = self.health(first, "/api/current")
        self.assertEqual(current["context_hash"], accepted_hash)
        queries = current["project"]["queries"]
        self.assertTrue(all(str(self.wrapper).replace("\\", "/") in query.replace("\\", "/")
                            and "-I" in query for query in queries.values()), queries)
        def contents():
            return {p.name: (p.stat().st_mtime_ns, p.stat().st_size, None if p.suffix == ".lock" else p.read_bytes())
                    for p in self.state.iterdir() if p.is_file()}
        before = contents()
        self.assertEqual(self.call("--status", "--read-only")[1]["status"], "RUNNING")
        self.assertEqual(before, contents())
        receipt = self.state / "map-service.json"
        saved = receipt.read_bytes()
        for patch in ({"instance_id": "0" * 32}, {"url": {"port": "broken"}}):
            receipt.write_text(json.dumps({**json.loads(saved), **patch}), encoding="utf-8")
            self.assertEqual(self.call("--status", "--read-only")[1]["status"], "RETRY")
            self.assertEqual(self.call("--ensure", "--read-only")[1]["status"], "RETRY")
        receipt.write_bytes(saved)
        self.assertEqual(self.health(first)["pid"], first["pid"])
        for source in (self.wrapper, self.trusted / "archctx_map.py", self.trusted / "archctx_understand.py"):
            with self.subTest(replaced=source.name):
                previous = source.read_bytes()
                source.write_bytes(previous + b"\n# New source must not rename the running code.\n")
                try:
                    self.assertEqual(self.call("--ensure", "--read-only")[1]["status"], "MISMATCH")
                    self.assertEqual(self.health(first)["identity"], first["identity"])
                finally:
                    source.write_bytes(previous)
        self.stop(first)
        _, restored = self.call("--ensure", "--read-only")
        self.assertEqual(restored["status"], "STARTED", restored)
        self.assertNotEqual(restored["instance_id"], first["instance_id"])
        deadline = time.monotonic() + 5
        while self.health(restored)["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(self.health(restored, "/api/current")["context_hash"], accepted_hash)
        self.assertEqual(accepted_bytes(), accepted_before)

    def test_concurrent_ensure_has_one_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.call("--ensure", "--read-only"), range(2)))
        values = [value for _, value in results]
        self.assertEqual(sum(value["status"] == "STARTED" for value in values), 1, values)
        self.assertTrue(all(value["status"] in ("STARTED", "REUSED", "RETRY") for value in values), values)
        owner = next(value for value in values if value["status"] == "STARTED")
        _, reused = self.call("--ensure", "--read-only")
        self.assertEqual((reused["status"], reused["pid"], reused["instance_id"]),
                         ("REUSED", owner["pid"], owner["instance_id"]))

    def test_console_entry_uses_core_and_ignores_consumer_shadow(self):
        import archctx_map
        with patch.object(sys.modules["__main__"], "__spec__", SimpleNamespace(
                name="__main__", origin="missing-launcher.exe/__main__.py")), patch.object(
                    sys, "argv", ["missing-launcher.exe", "map"]):
            command = archctx_map.entry()
            self.assertEqual(command[-1], str(archctx_map.archctx.CORE_SOURCE_PATH))
            source = archctx_map._entry_source(command)
        self.assertEqual(source["path"], str(archctx_map.archctx.CORE_SOURCE_PATH))
        self.assertEqual(source["sha256"], archctx_map.archctx.CORE_SOURCE_SHA256)
        marker = self.repo / "SHADOW_EXECUTED"
        (self.repo / "archctx.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected execution')\n"
            "raise RuntimeError('consumer shadow executed')\n", encoding="utf-8")
        program = (f"import sys; sys.path.insert(0, {str(self.trusted)!r}); import archctx; "
                   "sys.argv = ['archctx', *sys.argv[1:]]; raise SystemExit(archctx.main())")
        result = subprocess.run([sys.executable, "-c", program, "--config", str(self.config),
                                 "--state-dir", str(self.state), "map", "--ensure", "--read-only"],
                                cwd=self.repo, capture_output=True, text=True, encoding="utf-8", timeout=15)
        value = json.loads(result.stdout)
        if value.get("status") == "STARTED":
            self.children.append(value)
        self.assertEqual((result.returncode, value["status"]), (0, "STARTED"), result.stderr + result.stdout)
        self.assertEqual(value["identity"]["entry"], [sys.executable, str(self.trusted / "archctx.py")])
        self.assertFalse(marker.exists(), "consumer module ran before startup authentication")
        self.assertEqual(self.health(value)["identity"]["modules"]["archctx"]["path"], str(self.trusted / "archctx.py"))


if __name__ == "__main__":
    unittest.main()
