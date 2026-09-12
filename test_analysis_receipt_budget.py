"""Bounded persisted metadata is not the compact Agent reply; no model runs here."""
from contextlib import contextmanager
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import archctx
import archctx_analysis_inputs as inputs
import archctx_analysis_storage as storage
import archctx_blueprint as blueprint
import archctx_understand as ua
import test_native_understand as native_fixture


class AnalysisReceiptBudgetTest(unittest.TestCase):
    def setUp(self):
        self.fixture = native_fixture.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_large_native_receipt_recovers_existing_pretty_history_and_stays_queryable(self):
        f = self.fixture

        @contextmanager
        def capture(*args):
            with f.capture(*args) as captured:
                for row in captured["dependencies"].values():
                    row["external"] = ["package-" + "x" * 160 + str(i) for i in range(210)]
                yield captured

        original_retain = ua.retain_analysis
        saved = {}

        def retained_pretty(directory, receipt, graph):
            # The old writer left a valid, but unreadably large, immutable
            # receipt before its catalog publication. Recover, never relabel it.
            path = directory / "understand/runs" / receipt["analysis_id"] / "imports" / (receipt["graph_sha256"] + ".json")
            historical = {**receipt, "imported_at": "2026-01-01T00:00:00+00:00"}
            archctx.atomic(path, historical)
            saved[path] = path.read_bytes()
            self.assertGreater(len(saved[path]), ua.SUMMARY_BYTES)
            return original_retain(directory, receipt, graph)

        with patch.object(inputs, "capture", side_effect=capture):
            system = f.system_stage()
        f.write_system(system)
        with patch.object(ua, "retain_analysis", side_effect=retained_pretty):
            ready = f.advance(system)
        self.assertEqual((ready["status"], ready["analysis"]["status"]), ("FINDINGS_READY", "FRESH"))
        path, receipt = ua.analysis_receipt(f.directory)
        self.assertEqual(path.read_bytes(), saved[path])
        self.assertEqual(receipt["imported_at"], "2026-01-01T00:00:00+00:00")
        self.assertLessEqual(path.stat().st_size, ua.RECEIPT_BYTES)
        self.assertEqual(len(receipt["dependencies"]["a.py"]["external"]), 210)
        self.assertLess(len(json.dumps(ready).encode()), ua.SUMMARY_BYTES)
        catalog_raw = ua.catalog_path(f.directory).read_bytes()
        self.assertEqual(catalog_raw, ua.bounded_json(ua.analysis_catalog(f.directory), len(catalog_raw)))
        self.assertEqual(f.advance(system)["status"], "REUSED")
        page = blueprint.analysis_source(f.repo, f.directory, {"analysis": [receipt["analysis_id"]],
            "path": ["a.py"], "sha": [receipt["source_hashes"]["a.py"]]})
        self.assertIn("value = 1", page)
        run = f.directory / "understand/runs" / receipt["analysis_id"]
        self.assertTrue(storage.completed(run, storage.inventory(run)[0]))
        self.assertEqual(archctx.last_path(f.directory).read_bytes(), f.last_good)

    def test_import_budget_failure_preserves_lkg_and_completed_semantic_work(self):
        f = self.fixture
        system = f.system_stage()
        f.write_system(system)
        run = Path(system["write_result"]).parent
        saved = (run / "system.json").read_bytes()
        with patch.object(ua, "RECEIPT_BYTES", (run / "input.json").stat().st_size):
            with self.assertRaisesRegex(ValueError, "metadata exceeds"):
                f.advance(system)
        self.assertFalse(ua.catalog_path(f.directory).exists())
        self.assertFalse((run / "imports").exists())
        self.assertEqual((run / "system.json").read_bytes(), saved)
        self.assertEqual(archctx.last_path(f.directory).read_bytes(), f.last_good)

    def test_exact_serialized_utf8_boundary_includes_newline_and_preserves_values(self):
        value = {"scope": ["\u4e2d\u6587", "two"], "padding": "x" * 20}
        raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.assertEqual(ua.bounded_json(value, len(raw)), raw)
        self.assertEqual(json.loads(raw), value)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ua.bounded_json(value, len(raw) - 1)

    def test_oversized_preparation_never_issues_unreadable_agent_work(self):
        f = self.fixture
        with patch.object(ua, "RECEIPT_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                f.advance()
        self.assertFalse(list((f.directory / "understand/runs").glob("*/input.json")))
        self.assertEqual(archctx.last_path(f.directory).read_bytes(), f.last_good)


if __name__ == "__main__":
    unittest.main()
