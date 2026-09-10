"""Semantic handoff continuity through native resume, with isolated fixtures."""
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_understand as understand
import test_native_understand as native_fixture


class NativeContinuityTest(unittest.TestCase):
    def setUp(self):
        self.fixture = native_fixture.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def system_result(self, boundary, label):
        ids = [node["id"] for node in understand.local_json(Path(boundary["graph"]))["nodes"]]
        return {"graph_sha256": boundary["graph_sha256"],
                "layers": [{"id": "entry", "name": label, "description": label, "nodeIds": ids}],
                "tour": [{"order": 1, "title": label, "description": label, "nodeIds": ids}]}

    def retained_bytes(self, analysis_id):
        fixture = self.fixture
        receipt_path, receipt = understand.analysis_receipt(fixture.directory, analysis_id)
        run = receipt_path.parent.parent
        paths = [understand.catalog_path(fixture.directory), understand.current_path(fixture.directory),
                 receipt_path, understand.graph_file(fixture.directory, receipt),
                 run / "graph.json", run / "completed.json", archctx.last_path(fixture.directory)]
        return {path: path.read_bytes() for path in paths}

    def assert_retained(self, expected):
        self.assertEqual({str(path): archctx.sha(raw) for path, raw in expected.items()},
                         {str(path): archctx.sha(path.read_bytes()) for path in expected},
                         "late semantic resume replaced a newer retained publication")

    def assert_old_system_handoff_rejected(self, include_previous_publication=False, recover_input=False):
        fixture = self.fixture
        expected_base = None
        if include_previous_publication:
            previous = fixture.complete()
            _, receipt = understand.analysis_receipt(fixture.directory, previous["analysis_id"])
            expected_base = understand.entry_token(receipt)
            # A new source version creates a new handoff based on a real retained
            # predecessor. A and B below still see exactly the same source bytes.
            (fixture.repo / "a.py").write_bytes(b"value = 2\n")
        source_work = fixture.advance()
        fixture.write_files(source_work)
        if include_previous_publication:
            for work in source_work["work"]:
                result_path = Path(work["write_result"])
                result = understand.local_json(result_path)
                result["nodes"][0]["summary"] = "Changed fixture source"
                archctx.atomic(result_path, result)
        first = fixture.advance(source_work)
        self.assertEqual((first["status"], first["stage"]), ("NEEDS_AGENT", "system_understanding"))
        run = fixture.directory / "understand/runs" / first["analysis_id"]
        input_path = run / "input.json"
        original_input = understand.local_json(input_path)
        self.assertIn("scope_base", original_input)
        self.assertEqual(original_input["scope_base"], expected_base)
        source = {path: (fixture.repo / path).read_bytes() for path in fixture.files}
        assembled = Path(first["graph"]).read_bytes()
        first_result = self.system_result(first, "A old interpretation")

        second = fixture.advance(first)
        self.assertEqual((second["status"], second["stage"]), ("NEEDS_AGENT", "system_understanding"))
        self.assertEqual((second["analysis_id"], second["graph_sha256"]), (first["analysis_id"], first["graph_sha256"]))
        archctx.atomic(Path(second["write_result"]), self.system_result(second, "B newer interpretation"))
        self.assertEqual(fixture.advance(second)["status"], "FINDINGS_READY")
        retained = self.retained_bytes(second["analysis_id"])

        # A finishes its old request after B has published, with identical
        # source bytes and assembled graph. Only the system interpretation differs.
        archctx.atomic(Path(first["write_result"]), first_result)
        if recover_input:
            # Only a mechanical input in this isolated fixture is removed.
            # Rebuilding it must not adopt B as the baseline of A's old request.
            (run / "source/.ua/tmp/ua-file-analyzer-input-0.json").unlink()
        with patch.object(archctx, "revision", return_value="later-fixture-revision"):
            late = archctx.understand(fixture.config, None, {"resume": first["analysis_id"]})
        recovered_input = understand.local_json(input_path)
        for key in ("scope_base", "source_revision", "captured_at"):
            self.assertEqual(recovered_input[key], original_input[key], key)
        self.assertEqual(late["status"], "INVALID", late)
        self.assertIn("scope advanced", late["reason"])
        self.assertIn("re-review", late["reason"])
        self.assertTrue(late["last_good_preserved"])
        self.assertEqual(source, {path: (fixture.repo / path).read_bytes() for path in source})
        self.assertEqual(Path(first["graph"]).read_bytes(), assembled)
        self.assert_retained(retained)
        _, current = understand.analysis_receipt(fixture.directory, second["analysis_id"])
        self.assertEqual(current["layers"][0]["name"], "B newer interpretation")

    def test_old_system_handoff_cannot_replace_same_source_newer_semantics(self):
        self.assert_old_system_handoff_rejected()

    def test_existing_predecessor_cannot_rebase_old_system_handoff(self):
        self.assert_old_system_handoff_rejected(include_previous_publication=True)

    def test_missing_mechanical_input_preserves_initial_handoff_baseline(self):
        self.assert_old_system_handoff_rejected(recover_input=True)

    def test_missing_mechanical_input_preserves_existing_handoff_baseline(self):
        self.assert_old_system_handoff_rejected(include_previous_publication=True, recover_input=True)

    def test_identical_system_handoff_is_idempotent_after_publication(self):
        fixture = self.fixture
        first = fixture.system_stage()
        second = fixture.advance(first)
        result = self.system_result(first, "Same interpretation")
        archctx.atomic(Path(second["write_result"]), result)
        self.assertEqual(fixture.advance(second)["status"], "FINDINGS_READY")
        retained = self.retained_bytes(second["analysis_id"])
        calls = fixture.calls.copy()
        archctx.atomic(Path(first["write_result"]), result)
        self.assertEqual(fixture.advance(first)["status"], "REUSED")
        self.assertEqual(fixture.calls, calls)
        self.assert_retained(retained)

    def test_missing_input_cannot_adopt_a_later_overlapping_scope_reuse_plan(self):
        fixture = self.fixture
        first = fixture.system_stage()
        run = fixture.directory / "understand/runs" / first["analysis_id"]
        fixture.files = ["a.py"]
        fixture.complete()  # A different scope, not a same-scope token conflict.
        missing = run / "source/.ua/tmp/ua-file-analyzer-input-0.json"
        missing.unlink()
        originals = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
        result = archctx.understand(fixture.config, None, {"resume": first["analysis_id"]})
        self.assertEqual(result["status"], "INVALID", result)
        self.assertIn("reuse plan", result["reason"])
        self.assertFalse(missing.exists())
        self.assert_retained(originals)

    def test_other_scope_publication_does_not_invalidate_waiting_system_handoff(self):
        fixture = self.fixture
        fixture.files = ["a.py"]
        first = fixture.system_stage()
        first_result = self.system_result(first, "Scope A")
        fixture.files = ["b.py"]
        second = fixture.system_stage()
        self.assertNotEqual(first["analysis_id"], second["analysis_id"])
        archctx.atomic(Path(second["write_result"]), self.system_result(second, "Scope B"))
        self.assertEqual(fixture.advance(second)["status"], "FINDINGS_READY")
        receipt_path, receipt = understand.analysis_receipt(fixture.directory, second["analysis_id"])
        second_scope = understand.scope_id(receipt)
        second_entry = understand.analysis_catalog(fixture.directory)["scopes"][second_scope]
        immutable = {path: path.read_bytes() for path in
                     (receipt_path, understand.graph_file(fixture.directory, receipt), archctx.last_path(fixture.directory))}

        archctx.atomic(Path(first["write_result"]), first_result)
        self.assertEqual(fixture.advance(first)["status"], "FINDINGS_READY")
        catalog = understand.analysis_catalog(fixture.directory)
        self.assertEqual(len(catalog["scopes"]), 2)
        self.assertEqual(catalog["scopes"][second_scope], second_entry)
        self.assert_retained(immutable)
        self.assertEqual(understand.analysis_receipt(fixture.directory, first["analysis_id"])[1]["layers"][0]["name"], "Scope A")
        self.assertEqual(understand.analysis_receipt(fixture.directory, second["analysis_id"])[1]["layers"][0]["name"], "Scope B")


if __name__ == "__main__":
    unittest.main()
