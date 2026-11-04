import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import archctx
import archctx_blueprint as blueprint


class StaleBlueprintTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "architecture.json"
        self.state = self.root / "state"
        (self.root / "owner.py").write_text("OWNER = 1\n", encoding="utf-8")
        self.definition = {"version": 1, "repo": ".", "components": [
            {"id": "owner", "name": "Accepted owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}]}
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")

    def save(self):
        self.config.write_text(json.dumps(self.definition), encoding="utf-8")

    def fail_new_components(self):
        for i in range(12):
            self.definition["components"].append({"id": f"new-{i}", "name": f"New component {i}",
                "evidence": [{"path": "owner.py", "contains": f"missing-anchor-{i}-" + "x" * 700}]})
        self.save()
        failure = archctx.refresh(self.config, str(self.state))
        self.assertEqual(failure["status"], "INVALID")
        return failure

    def test_real_failed_refresh_preserves_all_causes_in_status_and_updates(self):
        accepted = archctx.load(archctx.last_path(self.state))
        failure = self.fail_new_components()
        stored = archctx.load(archctx.refresh_outcome_path(self.state))
        self.assertEqual(stored["failures"], failure["failures"])
        for value in (archctx.status(self.config, str(self.state)), archctx.updates(self.config, str(self.state))):
            self.assertEqual(value["status"], "STALE")
            self.assertEqual(value["last_refresh"]["failures"], failure["failures"])
            self.assertEqual({item["id"] for item in value["blueprint_omissions"]}, {f"new-{i}" for i in range(12)})
            for i in range(12):
                self.assertIn(f"missing-anchor-{i}-", " ".join(value["reason"]))
            self.assertEqual(value["last_good_context_hash"] if "last_good_context_hash" in value else value["accepted_blueprint"]["context_hash"], accepted["context_hash"])
        self.assertEqual(archctx.load(archctx.last_path(self.state)), accepted)

    def test_actual_external_failure_invalidates_identical_retained_inputs(self):
        script = self.root / "gate.py"
        script.write_text("from pathlib import Path\nimport sys\n"
                          "sys.exit('external renderer unavailable') if Path('broken').exists() else None\n", encoding="utf-8")
        self.definition["gates"] = [{"name": "external", "command": [sys.executable, str(script)]}]
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
        (self.root / "broken").write_text("break gate", encoding="utf-8")
        failed = archctx.refresh(self.config, str(self.state))
        self.assertEqual(failed["status"], "INVALID")
        value = archctx.status(self.config, str(self.state))
        self.assertEqual(value["status"], "STALE")
        self.assertIn("external renderer unavailable", " ".join(value["reason"]))
        (self.root / "broken").unlink()
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
        self.assertEqual(archctx.status(self.config, str(self.state))["status"], "FRESH")

    def test_map_exposes_new_failure_even_before_observer_notices_it(self):
        record = archctx.load(archctx.last_path(self.state))
        observer = SimpleNamespace(bundle=lambda: ({"status": "FRESH", "updates": {"status": "FRESH"},
            "observation_id": "unchanged-observer", "changes": []}, copy.deepcopy(record)))
        server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(self.config, str(self.state), observer))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)

        def get(headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/api/current", headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            result = response.status, response.getheader("ETag"), json.loads(body) if body else None
            connection.close()
            return result

        _, etag, _ = get()
        failure = self.fail_new_components()
        code, new_etag, value = get({"If-None-Match": etag})
        self.assertEqual(code, 200)
        self.assertNotEqual(new_etag, etag)
        self.assertEqual(value["status"], "STALE")
        self.assertEqual(value["last_refresh"]["failures"], failure["failures"])
        self.assertEqual(len(value["blueprint_omissions"]), 12)
        self.assertEqual(value["components"], record["context"]["components"])

    def test_malformed_components_preserves_original_failure_when_repair_is_unavailable(self):
        self.definition["components"] = "unfinished-schema-edit"
        self.save()
        failed = archctx.refresh(self.config, str(self.state))
        self.assertEqual(failed["status"], "INVALID")
        self.assertIn("components", " ".join(failed["failures"]))
        self.assertIn("evidence_diagnostics_error", failed)
        for value in (archctx.status(self.config, str(self.state)), archctx.updates(self.config, str(self.state))):
            self.assertEqual(value["status"], "STALE")
            self.assertEqual(value["last_refresh"]["failures"], failed["failures"])
            self.assertEqual(value["last_refresh"]["evidence_diagnostics_error"], failed["evidence_diagnostics_error"])

    def test_external_errors_keep_both_ends_of_long_stderr(self):
        diagnostic = "first failure\n" + "detailed failure cause\n" * 250 + "last failure"
        command = [sys.executable, "-c", "import sys;sys.stderr.write(" + repr(diagnostic) + ");sys.exit(1)"]
        for setting in ({"gates": [{"name": "long-error", "command": command}]},
                        {"code_graph": {"refresh": command}},
                        {"archify": {"view": "view.json", "output": "diagram.json", "validate": command}}):
            with self.subTest(setting=next(iter(setting))):
                (self.root / "view.json").write_text(json.dumps({"nodes": [{"id": "owner", "pos": [0, 0]}]}), encoding="utf-8")
                base = {key: item for key, item in self.definition.items() if key not in ("gates", "code_graph", "archify")}
                self.definition = base | setting
                self.save()
                failed = archctx.refresh(self.config, str(self.state))
                self.assertEqual(failed["status"], "INVALID")
                self.assertIn(diagnostic, failed["failures"][0])
                self.assertEqual(archctx.status(self.config, str(self.state))["last_refresh"]["failures"], failed["failures"])

    def test_layout_failure_is_visible_in_map_while_source_context_stays_fresh(self):
        diagnostic = "Architecture layout validation failed:\n" + "edge crosses unrelated component\n" * 120 + "final layout cause"
        renderer_reply = json.dumps({"command": "validate", "stage": "render", "error": diagnostic})
        (self.root / "view.json").write_text(json.dumps({"nodes": [{"id": "owner", "pos": [0, 0]}]}), encoding="utf-8")
        self.definition["archify"] = {"view": "view.json", "output": "diagram.json", "validate":
            [sys.executable, "-c", "import sys;sys.stderr.write(" + repr(renderer_reply) + ");sys.exit(1)"]}
        self.save()
        result = archctx.refresh(self.config, str(self.state))
        self.assertEqual(result["status"], "PASS")
        self.assertIn(diagnostic, result["archify"]["layout_failure"])
        self.assertEqual(archctx.status(self.config, str(self.state))["status"], "FRESH")
        observer = SimpleNamespace(bundle=lambda: ({"status": "FRESH", "updates": {"status": "FRESH"},
            "observation_id": "settled-source", "changes": []}, archctx.load(archctx.last_path(self.state))))
        server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(self.config, str(self.state), observer))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/current")
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(value["status"], "STALE")
        self.assertEqual(value["source_status"], "FRESH")
        self.assertEqual(value["blueprint_status"], "UNAVAILABLE")
        self.assertIn(diagnostic, " ".join(value["reason"]))
        self.assertEqual([item["id"] for item in value["blueprint_omissions"]], ["owner"])
        self.assertIn(diagnostic, " ".join(value["blueprint_omissions"][0]["reasons"]))


if __name__ == "__main__":
    unittest.main()
