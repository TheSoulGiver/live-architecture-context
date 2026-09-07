import copy
import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_blueprint as blueprint
from tools import archify as launcher


class BlueprintTest(unittest.TestCase):
    def test_renderer_receipts_and_missing_before_recovery(self):
        # Synthetic renderer contract, not a claim of real Archify rendering.
        for scenario in ("valid", "wrong_delivery", "wrong_comparison", "missing_before"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary)
                directory = repo / ".archctx"
                generation = directory / "generations" / ("a" * 16 + "-" + "b" * 32)
                generation.mkdir(parents=True)
                ir = generation / "architecture.archify.json"
                ir.write_text("{}", encoding="utf-8")
                (repo / "view.json").write_text("{}", encoding="utf-8")
                config = {"archify": {"view": "view.json",
                          "render": ["render", "{archify_output}", "{archify_html}"],
                          "compare": ["compare", "{archify_before}", "{archify_output}", "{archify_compare}", "{archify_receipt}"]}}
                candidate = {"revision": "revision", "components": [{"id": "service"}], "relations": []}
                previous = None if scenario != "missing_before" else {"context": candidate, "context_hash": "old",
                            "archify": {"generation": str(generation), "ir": str(generation / "missing.json"), "ir_sha256": "old"}}

                def external(command, _repo, _timeout, values):
                    name = "{archify_html}" if command[0] == "render" else "{archify_compare}"
                    output = Path(values[name])
                    output.write_text("<svg>fixture</svg>", encoding="utf-8")
                    digest = archctx.sha(ir.read_bytes())
                    result = {"ok": True, "artifact": {"sha256": archctx.sha(output.read_bytes())}}
                    if command[0] == "render":
                        result["specification"] = {"sha256": "wrong" if scenario == "wrong_delivery" else digest}
                    else:
                        result.update(base={"rawSha256": digest}, head={"rawSha256": "wrong" if scenario == "wrong_comparison" else digest},
                                      validation={"checkCount": 1, "checksPassed": 1})
                        archctx.atomic(Path(values["{archify_receipt}"]), result)
                    return command, subprocess.CompletedProcess(command, 0, json.dumps(result), "")

                with patch.object(archctx, "run", side_effect=external):
                    if scenario.startswith("wrong"):
                        with self.assertRaisesRegex(ValueError, "receipt does not bind"):
                            blueprint.render_bundle(config, repo, directory, generation, ir, candidate, previous)
                    else:
                        receipt = blueprint.render_bundle(config, repo, directory, generation, ir, candidate, previous)
                        self.assertEqual(receipt["context_hash"], archctx.semantic(candidate))
                        self.assertFalse(receipt["before_available"])
                        if scenario == "missing_before":
                            self.assertIn("unavailable", receipt["before_reason"])

    def test_renderer_rejects_unsupported_node_before_setup(self):
        with patch.object(launcher.shutil, "which", side_effect=["git", "node"]), patch.object(
                launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v16.20.2\n", "")):
            with self.assertRaisesRegex(RuntimeError, "Node.js 18"):
                launcher.main()

    def test_definition_identity_separates_implementation_from_architecture(self):
        context = {"revision": "a", "components": [{"id": "service", "truth_sources": ["service.py"], "evidence": [{"line": 2, "sha256": "old"}]}],
                   "relations": [{"from": "service", "to": "service", "kind": "calls", "evidence": [{"sha256": "old"}]}]}
        updated = copy.deepcopy(context)
        updated["revision"] = "b"
        updated["components"][0]["evidence"][0].update(line=9, sha256="new")
        updated["relations"][0]["evidence"][0]["sha256"] = "new"
        self.assertEqual(blueprint.definition_hash(context), blueprint.definition_hash(updated))
        updated["relations"][0]["kind"] = "owns"
        self.assertNotEqual(blueprint.definition_hash(context), blueprint.definition_hash(updated))

    def test_serves_only_accepted_unchanged_artifacts_and_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            source = "def serve():\n    return '<script>not executable</script>'\n"
            (repo / "service.py").write_text(source, encoding="utf-8")
            config = repo / "architecture.json"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "service", "evidence": [{"path": "service.py", "contains": "def serve"}]}]}), encoding="utf-8")
            directory = repo / ".archctx"
            generation_id = "a" * 16 + "-" + "b" * 32
            generation = directory / "generations" / generation_id
            generation.mkdir(parents=True)
            output = generation / "current.html"
            output.write_text("<svg></svg>", encoding="utf-8")
            ir = generation / "architecture.archify.json"
            ir.write_text("{}", encoding="utf-8")
            receipt = {"generation": str(generation), "ir": str(ir), "ir_sha256": archctx.sha(ir.read_bytes()),
                       "artifacts": {"current.html": archctx.sha(output.read_bytes())}}
            value = {"status": "FRESH", "last_good_context_hash": "accepted-context", "archify": receipt,
                     "context": {"components": [{"id": "service", "evidence": [{"path": "service.py", "line": 1, "sha256": archctx.sha(source.encode())}]}]}}
            real_snapshot = archctx.snapshot
            with patch.object(archctx, "snapshot", return_value=value) as snapshot_mock:
                server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(config, None))
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    def get(path, host=None):
                        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                        connection.request("GET", path, headers={"Host": host} if host else {})
                        response = connection.getresponse()
                        result = response.status, response.read().decode()
                        connection.close()
                        return result
                    self.assertEqual(get("/api/current")[0], 200)
                    self.assertEqual(get("/api/current", "evil.example")[0], 403)
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html"), (200, "<svg></svg>"))
                    self.assertEqual(get("/artifact/older/current.html")[0], 409)
                    self.assertEqual(get(f"/artifact/{generation_id}/secrets.json")[0], 409)
                    code, text = get("/source?component=service&item=0&context=accepted-context")
                    self.assertEqual(code, 200)
                    self.assertIn("&lt;script&gt;", text)
                    self.assertNotIn("<script>", text)
                    (repo / "service.py").write_text("changed", encoding="utf-8")
                    self.assertEqual(get("/source?component=service&item=0&context=accepted-context")[0], 409)
                    output.write_text("modified", encoding="utf-8")
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html")[0], 409)
                    output.write_text("<svg></svg>", encoding="utf-8")
                    archctx.atomic(archctx.last_path(directory), {"repo": str(repo), "context": value["context"],
                                   "context_hash": "accepted-context", "archify": receipt})
                    config.write_text("{ unfinished edit", encoding="utf-8")
                    snapshot_mock.side_effect = real_snapshot
                    code, body = get("/api/current")
                    self.assertEqual((code, json.loads(body)["status"]), (200, "STALE"))
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html"), (200, "<svg></svg>"))
                    self.assertEqual(get("/source?component=service&item=0&context=accepted-context")[0], 409)
                    self.assertTrue(blueprint.handler(config, None))  # Restart also works during the invalid edit.
                finally:
                    server.shutdown()
                    worker.join()
                    server.server_close()

    def test_artifact_cannot_escape_generation_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            private = repo / "secret.html"
            private.write_text("private", encoding="utf-8")
            receipt = {"generation": str(repo), "artifacts": {"secret.html": archctx.sha(private.read_bytes())}}
            with self.assertRaises(ValueError):
                blueprint.artifact_path(repo, repo / ".archctx", receipt, "secret.html")


if __name__ == "__main__":
    unittest.main()
