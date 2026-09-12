"""Semantic corrections on unchanged source; upstream mechanics are fixtures."""
import unittest
from pathlib import Path

import archctx
import archctx_understand as ua
import test_native_understand as native


class NativeRevisionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = native.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_correction_reuses_other_file_and_preserves_original_evidence(self):
        f = self.fixture
        original = f.complete()
        old_run = f.directory / "understand/runs" / original["analysis_id"]
        saved = {p: p.read_bytes() for p in old_run.rglob("*") if p.is_file()}
        self.assertEqual(f.advance()["status"], "REUSED")
        before = f.calls.copy()
        pending = archctx.understand(f.config, None, {"revise": original["analysis_id"], "files": ["a.py"]})
        self.assertEqual(pending["status"], "NEEDS_AGENT", pending)
        self.assertNotEqual(pending["analysis_id"], original["analysis_id"])
        self.assertEqual(pending["reused_files"], ["b.py"])
        self.assertEqual([w["file"] for w in pending["work"]], ["a.py"])
        self.assertEqual(f.calls["extract:a.py"], before["extract:a.py"])
        f.write_files(pending)
        path = Path(pending["work"][0]["write_result"])
        value = ua.local_json(path)
        value["nodes"][0]["summary"] = "Corrected source-grounded responsibility"
        archctx.atomic(path, value)
        system = f.advance(pending)
        self.assertEqual(system["stage"], "system_understanding")
        f.write_system(system)
        ready = f.advance(system)
        self.assertEqual(ready["status"], "FINDINGS_READY", ready)
        _, receipt = ua.analysis_receipt(f.directory)
        self.assertEqual(receipt["revision_of"]["analysis_id"], original["analysis_id"])
        graph = ua.local_json(ua.graph_file(f.directory, receipt))
        self.assertEqual(next(n for n in graph["nodes"] if n["filePath"] == "a.py")["summary"], value["nodes"][0]["summary"])
        self.assertEqual(f.advance(system)["status"], "REUSED")
        self.assertEqual(saved, {p: p.read_bytes() for p in saved})
        self.assertEqual(archctx.last_path(f.directory).read_bytes(), f.last_good)
        stale_request = archctx.understand(f.config, None, {"revise": original["analysis_id"]})
        self.assertEqual(stale_request["status"], "INVALID")

    def test_revision_with_unchanged_graph_still_requests_system_judgment(self):
        f = self.fixture
        old = f.complete()
        pending = archctx.understand(f.config, None, {"revise": old["analysis_id"]})
        self.assertEqual(pending["status"], "NEEDS_AGENT", pending)
        f.write_files(pending)
        system = f.advance(pending)
        self.assertEqual((system["status"], system["stage"]), ("NEEDS_AGENT", "system_understanding"))
        f.write_system(system)
        path = Path(system["write_result"])
        value = ua.local_json(path)
        value["layers"][0]["description"] = "Corrected grouping meaning, identical source and nodes"
        archctx.atomic(path, value)
        self.assertEqual(f.advance(system)["status"], "FINDINGS_READY")
        self.assertEqual(ua.analysis_receipt(f.directory)[1]["layers"][0]["description"], value["layers"][0]["description"])

    def test_two_corrections_cannot_rebase_each_others_handoff(self):
        f = self.fixture
        old = f.complete()
        first = archctx.understand(f.config, None, {"revise": old["analysis_id"], "files": ["a.py"]})
        second = archctx.understand(f.config, None, {"revise": old["analysis_id"], "files": ["b.py"]})
        self.assertNotEqual(first["analysis_id"], second["analysis_id"])
        f.write_files(second)
        system = f.advance(second)
        f.write_system(system)
        self.assertEqual(f.advance(system)["status"], "FINDINGS_READY")
        saved = ua.catalog_path(f.directory).read_bytes()
        f.write_files(first)
        late = f.advance(first)
        f.write_system(late)
        result = archctx.understand(f.config, None, {"resume": first["analysis_id"]})
        self.assertEqual(result["status"], "INVALID", result)
        self.assertIn("scope advanced", result["reason"])
        self.assertEqual(ua.catalog_path(f.directory).read_bytes(), saved)

    def test_revision_rejects_wrong_scope_and_read_only_combinations(self):
        f = self.fixture
        old = f.complete()
        for extra in ({"show": True}, {"resume": old["analysis_id"]}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    archctx.understand(f.config, None, {"revise": old["analysis_id"], **extra})
        result = archctx.understand(f.config, None, {"revise": old["analysis_id"], "files": ["not-in-scope.py"]})
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("subset", result["reason"])


if __name__ == "__main__":
    unittest.main()
