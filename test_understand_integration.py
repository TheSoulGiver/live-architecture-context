"""Synthetic boundary regressions, not upstream analysis or consumer-adoption proof."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_understand as understand


class UnderstandIntegrationTest(unittest.TestCase):
    def test_compact_edges_preserve_group_discovery_and_explicit_details(self):
        self.receipt["candidates"][0]["raw_relations"] = [
            {"source": "caller", "target": str(i), "type": "calls", "direction": "forward"} for i in range(8)]
        self.receipt["candidates"][0]["omitted_raw_relations"] = 3
        self.install_analysis()
        compact = archctx.updates(self.config, None)["source_analysis"]["candidates"][0]
        self.assertEqual(len(compact["raw_relations"]), 2)
        self.assertEqual(compact["omitted_raw_relations"], 9)
        detailed = understand.discoveries(self.config, None, details=True)["candidates"][0]
        self.assertEqual(len(detailed["raw_relations"]), 8)
        self.assertEqual(detailed["omitted_raw_relations"], 3)

    def test_accept_new_source_analysis_refreshes_evidence_without_new_architecture(self):
        self.source.write_text("class Store: pass\n# actual implementation edit\n", encoding="utf-8")
        self.receipt["source_hashes"]["source.py"] = archctx.sha(self.source.read_bytes())
        self.install_analysis()
        result = archctx.accept_candidate(self.config, None, self.ident, ["component:store"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(archctx.status(self.config, None)["status"], "FRESH")
        self.assertEqual([c["id"] for c in archctx.load(archctx.last_path(self.directory))["context"]["components"]], ["store"])

    def test_direct_script_busy_analysis_returns_retry_not_exception_class_traceback(self):
        self.install_analysis()
        with archctx.refresh_lock(self.directory):
            result = subprocess.run([sys.executable, "-B", str(Path(archctx.__file__).resolve()),
                "--config", str(self.config), "accept", self.ident, "--bind", "component:store"],
                capture_output=True, text=True, encoding="utf-8", timeout=10)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "RETRY", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_published_review_receipt_failure_does_not_report_false_publication_failure(self):
        self.install_analysis()
        self.value["components"][0]["purpose"] = "reviewed new purpose"
        archctx.atomic(self.config, self.value)
        with patch.object(archctx, "record_decision", side_effect=OSError("synthetic disk error")):
            result = archctx.accept_candidate(self.config, None, self.ident, ["component:store"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decision_receipt_cleanup"], "deferred")
        record = archctx.load(archctx.last_path(self.directory))
        self.assertTrue(record["candidate_decisions"][0]["source_analysis"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "architecture.json"
        self.directory = self.root / ".archctx"
        self.source = self.root / "source.py"
        self.source.write_text("class Store: pass\n", encoding="utf-8")
        self.helper = self.root / "helper.py"
        self.helper.write_text("VERSION = 1\n", encoding="utf-8")
        self.value = {"version": 1, "repo": ".", "components": [{"id": "store", "purpose": "保留既有身份",
                      "evidence": [{"path": "source.py", "contains": "class Store"}]}]}
        archctx.atomic(self.config, self.value)
        self.assertEqual(archctx.refresh(self.config, None)["status"], "PASS")
        self.ident = "ua:" + "a" * 64 + ":storage"
        raw = b'{"synthetic":true}'
        self.receipt = {"analysis_id": "a" * 64, "graph_sha256": archctx.sha(raw),
                        "worktree": str(self.root), "source_revision": "synthetic-source",
                        "source_hashes": understand.content_hashes(understand.source_bytes(self.root, ["source.py", "helper.py"])),
                        "provider": {"name": "understand-anything", "url": understand.PROVIDER_URL, "revision": understand.PROVIDER_REVISION},
                        "node_count": 2, "edge_count": 1, "tour": [],
                        "candidates": [{"id": self.ident, "kind": "source_analysis", "files": ["source.py"],
                                        "title": "存储职责", "summary": "合成来源测试"}]}
        archctx.atomic_bytes(self.directory / "understand/runs" / self.receipt["analysis_id"] / "graph.json", raw)

    def install_analysis(self):
        archctx.atomic(understand.current_path(self.directory), self.receipt)

    def test_optional_analysis_is_nonblocking_and_queries_do_not_execute_provider(self):
        with patch.object(understand, "discoveries", side_effect=AssertionError("absent analysis must not be loaded")):
            self.assertFalse(archctx.updates(self.config, None)["source_analysis"]["configured"])
        self.install_analysis()
        with patch.object(understand, "run_upstream", side_effect=AssertionError("query ran provider")), \
             patch.object(understand, "validate_graph", side_effect=AssertionError("query validated provider")):
            result = archctx.candidates(self.config, None)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["source_analysis"]["candidate_count"], 1)
            self.assertFalse(result["source_analysis"]["blocking"])
            self.helper.write_text("VERSION = 2\n", encoding="utf-8")
            self.assertEqual(archctx.updates(self.config, None)["source_analysis"]["status"], "STALE")
            self.assertEqual(archctx.status(self.config, None)["status"], "FRESH")
            self.assertEqual(archctx.refresh(self.config, None)["status"], "PASS")
            understand.current_path(self.directory).write_text("{broken", encoding="utf-8")
            self.assertEqual(archctx.updates(self.config, None)["source_analysis"]["status"], "INVALID")
            self.assertEqual(archctx.refresh(self.config, None)["status"], "PASS")

    def test_original_rule_candidates_still_need_review(self):
        self.value["drift_rules"] = [{"id": "provider", "kind": "new-provider", "paths": ["source.py"], "added_contains": ["provider()"]}]
        archctx.atomic(self.config, self.value)
        self.assertEqual(archctx.refresh(self.config, None, reset_candidate_baseline=True)["status"], "PASS")
        self.source.write_text("class Store: pass\nprovider()\n", encoding="utf-8")
        self.install_analysis()
        result = archctx.candidates(self.config, None)
        self.assertEqual(result["status"], "CANDIDATE_REVIEW_REQUIRED")
        self.assertEqual(result["candidates"][0]["provenance"], "deterministic_rule_derived_source_fact")
        self.assertEqual(archctx.refresh(self.config, None)["status"], "CANDIDATE_REVIEW_REQUIRED")
        self.assertEqual(archctx.reject_candidate(self.config, None, result["candidates"][0]["id"], "not_architecture")["status"], "PASS")

    def test_cursor_tracks_content_and_decisions_and_rejects_moved_receipt(self):
        self.install_analysis()
        first = archctx.updates(self.config, None)
        self.assertFalse(archctx.updates(self.config, None, first["cursor"])["changed"])
        accepted = archctx.accept_candidate(self.config, None, self.ident, ["component:store"])
        self.assertEqual(accepted["publication"], "existing_canonical_match")
        second = archctx.updates(self.config, None, first["cursor"])
        self.assertTrue(second["changed"])
        self.assertEqual(second["source_analysis"]["candidates"][0]["review_state"], "accepted")
        self.assertTrue(archctx.canonical(self.config, None, "store")["source_analysis"])
        self.helper.write_text("VERSION = 2\n", encoding="utf-8")
        third = archctx.updates(self.config, None, second["cursor"])
        self.assertTrue(third["changed"])
        self.assertEqual(third["source_analysis"]["status"], "STALE")
        original = understand.discoveries
        def moving(*args, **kwargs):
            result = original(*args, **kwargs)
            replacement = {**self.receipt, "imported_at": "changed during query"}
            archctx.atomic(understand.current_path(self.directory), replacement)
            return result
        with patch.object(understand, "discoveries", side_effect=moving):
            self.assertEqual(archctx.updates(self.config, None, third["cursor"])["status"], "RETRY")

    def test_accept_reuses_component_id_and_keeps_historical_provenance(self):
        self.install_analysis()
        self.value["components"][0]["purpose"] = "经过核实的存储职责"
        archctx.atomic(self.config, self.value)
        result = archctx.accept_candidate(self.config, None, self.ident, ["component:store"])
        self.assertEqual(result["status"], "PASS")
        record = archctx.load(archctx.last_path(self.directory))
        self.assertEqual([c["id"] for c in record["context"]["components"]], ["store"])
        decision = record["candidate_decisions"][0]
        self.assertEqual(decision["bindings"], ["component:store"])
        self.assertEqual(decision["source_analysis"]["analysis_id"], self.receipt["analysis_id"])
        evidence = archctx.mcp_value(self.config, None, "architecture_evidence", {"id": "store"})
        self.assertIn("historical", evidence["source_analysis"][0]["meaning"])
        history = archctx.history(self.directory, None, 1)
        self.assertEqual(history["snapshots"][0]["candidate_decisions"][0]["bindings"], ["component:store"])
        self.source.write_text("class Store: pass\n# implementation changed\n", encoding="utf-8")
        self.assertEqual(archctx.refresh(self.config, None)["status"], "PASS")
        self.assertTrue(archctx.canonical(self.config, None, "store")["source_analysis"])
        self.value["components"][0]["purpose"] = "新的未分析职责"
        archctx.atomic(self.config, self.value)
        self.assertEqual(archctx.refresh(self.config, None)["status"], "PASS")
        self.assertEqual(archctx.canonical(self.config, None, "store")["source_analysis"], [])

    def test_analysis_publication_races_preserve_last_good_and_cleanup_visual(self):
        self.install_analysis()
        proof = understand.publication_proof(self.receipt)
        old = archctx.last_path(self.directory).read_bytes()
        for change in ("source", "receipt", "graph"):
            with self.subTest(change=change):
                self.helper.write_text("VERSION = 1\n", encoding="utf-8")
                self.install_analysis()
                graph = self.directory / "understand/runs" / self.receipt["analysis_id"] / "graph.json"
                graph.write_bytes(b'{"synthetic":true}')
                generation = archctx.generation_base(self.directory) / ("f" * 16 + "-" + "e" * 32)
                def render(*args):
                    generation.mkdir(parents=True)
                    if change == "source": self.helper.write_text("VERSION = 3\n", encoding="utf-8")
                    elif change == "receipt": archctx.atomic(understand.current_path(self.directory), {**self.receipt, "graph_sha256": "b" * 64})
                    else: graph.write_bytes(b'{"changed":true}')
                    return {"configured": False, "generation": str(generation)}
                with archctx.refresh_lock(self.directory), patch.object(archctx, "archify_projection", side_effect=render):
                    result = archctx._refresh_locked(self.config, None, self.directory, understand_proof=proof)
                self.assertEqual(result["status"], "RETRY")
                self.assertIn("source_analysis", result["changed_inputs"])
                self.assertEqual(archctx.last_path(self.directory).read_bytes(), old)
                self.assertFalse(generation.exists())

    def test_stale_analysis_cannot_reach_renderer_and_busy_review_uses_retry(self):
        self.install_analysis()
        self.helper.write_text("VERSION = 3\n", encoding="utf-8")
        with patch.object(archctx, "archify_projection") as render:
            result = archctx._refresh_locked(self.config, None, self.directory, understand_proof=understand.publication_proof(self.receipt))
            self.assertEqual(result["status"], "RETRY")
            render.assert_not_called()
        with patch.object(understand, "review", side_effect=archctx.RefreshBusyError("synthetic lock")):
            self.assertEqual(archctx.accept_candidate(self.config, None, self.ident, ["component:store"])["status"], "RETRY")
            self.assertEqual(archctx.reject_candidate(self.config, None, self.ident, "existing_canonical")["status"], "RETRY")

    def test_analysis_notices_share_existing_size_budget(self):
        self.install_analysis()
        packet = archctx.updates(self.config, None)
        packet["source_analysis"]["candidates"] = [{"id": str(i), "summary": "x" * 1000} for i in range(150)]
        packet["source_analysis"]["tour"] = [{"description": "y" * 1000} for _ in range(50)]
        result = archctx.bounded_updates(copy.deepcopy(packet))
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()), archctx.UPDATES_BYTES)
        self.assertTrue(result["more_available"])
        self.assertGreater(result["source_analysis"]["omitted_candidate_count"] + result["source_analysis"]["omitted_tour_steps"], 0)


if __name__ == "__main__":
    unittest.main()
