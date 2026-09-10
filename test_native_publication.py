"""Deterministic live-writer races; synthetic evidence, not provider/adoption proof."""
import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_understand as understand
from archctx_development import DevelopmentObserver


class NativePublicationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.config, self.state = self.repo / "architecture.json", self.repo / "state"
        self.source, self.helper = self.repo / "source.py", self.repo / "helper.py"
        self.source.write_text("class Store: pass\n", encoding="utf-8")
        self.helper.write_text("HELPER = 1\n", encoding="utf-8")
        self.view = self.repo / "view.json"
        archctx.atomic(self.view, {"title": "Synthetic publication", "nodes": [
            {"id": "store", "pos": [0, 0]}, {"id": "helper", "pos": [100, 0]}]})
        self.value = {"version": 1, "repo": ".", "components": [
            {"id": "store", "purpose": "original", "evidence": [{"path": "source.py", "contains": "class Store"}]},
            {"id": "helper", "evidence": [{"path": "helper.py", "contains": "HELPER"}]}],
            "relations": [{"id": "uses-helper", "from": "store", "to": "helper", "kind": "uses",
                           "evidence": [{"path": "source.py", "contains": "class Store"}]}],
            "archify": {"view": "view.json", "output": "output.json",
                        "validate": [sys.executable, "-c", "pass", "{archify_output}"]}}
        archctx.atomic(self.config, self.value)
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
        old = archctx.load(archctx.last_path(self.state))
        self.ident = "ua:" + "c" * 64
        raw = b'{"synthetic":true}'
        self.receipt = {"analysis_id": "a" * 64, "graph_sha256": archctx.sha(raw),
            "worktree": str(self.repo), "source_revision": "synthetic",
            "source_hashes": understand.content_hashes(understand.source_bytes(self.repo, ["source.py", "helper.py"])),
            "dependency_hashes": {}, "resolver_hashes": {},
            "dependencies": {path: {"dependencies": [], "unresolvedLocal": [], "unknown": [], "external": [],
                                      "coverage": "resolved"} for path in ("source.py", "helper.py")},
            "provider": {"name": "understand-anything", "url": understand.PROVIDER_URL, "revision": understand.PROVIDER_REVISION},
            "node_count": 2, "edge_count": 1, "tour": [],
            "accepted_object_ids": {"context_hash": old["context_hash"], "components": ["store", "helper"], "relations": ["uses-helper"]},
            "candidates": [{"id": self.ident, "kind": "source_analysis", "title": "Store", "summary": "Synthetic finding", "files": ["source.py"]}]}
        archctx.atomic_bytes(self.state / "understand/runs" / self.receipt["analysis_id"] / "graph.json", raw)
        archctx.atomic(understand.current_path(self.state), self.receipt)
        self.observer = DevelopmentObserver(self.config, str(self.state), live=True, settle_seconds=0)
        self.observer.poll_once()
        self.addCleanup(self.observer.stop)

    def edit_definition(self):
        self.value["components"][0]["purpose"] = "reviewed responsibility"
        archctx.atomic(self.config, self.value)

    def test_first_review_publishes_without_a_manual_seed_refresh(self):
        self.observer.stop()
        archctx.last_path(self.state).unlink()  # Only this test's synthetic baseline.
        self.receipt.pop("accepted_object_ids")
        archctx.atomic(understand.current_path(self.state), self.receipt)
        result = self.accept()
        self.assertEqual((result["status"], result["publication"]), ("PASS", "updated_canonical"))
        self.assert_proof()

    def accept(self):
        return archctx.accept_candidate(self.config, str(self.state), self.ident, ["component:store"])

    def assert_proof(self):
        record = archctx.load(archctx.last_path(self.state))
        decision = next(d for d in record["candidate_decisions"] if d["id"] == self.ident)
        self.assertEqual(decision["source_analysis"], understand.publication_proof(self.receipt))
        self.assertEqual(decision["bindings"], ["component:store"])
        self.assertEqual({key: decision[key] for key in ("content_revision", "evidence_revision")},
                         understand.review_revisions(self.receipt["candidates"][0], self.receipt))
        self.assertEqual(understand.discoveries(self.config, str(self.state))["candidates"][0]["review_state"], "accepted")
        return record

    def race(self, first, second):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        original = archctx.archify_projection

        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("synthetic race timed out")
            return original(*args, **kwargs)

        def worker():
            try:
                results.append(first())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        with patch.object(archctx, "archify_projection", side_effect=paused):
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                blocked = second()
            finally:
                release.set()
                thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        return results[0], blocked

    def test_live_auto_first_busy_retry_binds_same_generation_without_rerender(self):
        self.edit_definition()
        automatic, blocked = self.race(self.observer.poll_once, self.accept)
        self.assertEqual(automatic["refresh"]["status"], "PASS")
        self.assertEqual((blocked["status"], blocked["changed_inputs"]), ("RETRY", ["writer_lock"]))
        before = archctx.load(archctx.last_path(self.state))
        with patch.object(archctx, "archify_projection", side_effect=AssertionError("same inputs rerendered")), \
             patch.object(archctx, "graph_refresh", side_effect=AssertionError("same inputs refreshed graph")), \
             patch.object(archctx, "gates", side_effect=AssertionError("same inputs repeated gates")):
            accepted = self.accept()
        self.assertEqual((accepted["status"], accepted["publication"], accepted["validation"]),
                         ("PASS", "existing_canonical_match", "reused_accepted_inputs"))
        after = self.assert_proof()
        self.assertEqual((after["context_hash"], after["archify"]), (before["context_hash"], before["archify"]))
        self.assertTrue(self.observer.poll_once()["live"])
        self.assertEqual(self.observer.accepted_record["candidate_decisions"], after["candidate_decisions"])

    def test_explicit_accept_first_observer_retries_and_preserves_proof(self):
        self.edit_definition()
        accepted, blocked = self.race(self.accept, self.observer.poll_once)
        self.assertEqual(accepted["status"], "PASS")
        self.assertEqual(blocked["refresh"]["status"], "RETRY")
        record = self.assert_proof()
        self.assertEqual(self.observer.poll_once()["accepted_context_hash"], record["context_hash"])
        self.assertEqual(self.observer.accepted_record["candidate_decisions"], record["candidate_decisions"])

    def test_reviewed_config_source_and_view_cannot_move_before_refresh_read(self):
        original = archctx._refresh_locked
        old = archctx.last_path(self.state).read_bytes()
        for change in ("config", "source", "view"):
            with self.subTest(change=change):
                saved = {path: path.read_bytes() for path in (self.config, self.source, self.view)}

                def moved(*args, **kwargs):
                    if change == "config":
                        self.edit_definition()
                    elif change == "source":
                        self.source.write_text("class Store: pass\n# changed\n", encoding="utf-8")
                    else:
                        view = archctx.load(self.view)
                        archctx.atomic(self.view, {**view, "title": "changed"})
                    return original(*args, **kwargs)

                try:
                    with patch.object(archctx, "_refresh_locked", side_effect=moved):
                        result = self.accept()
                    self.assertEqual((result["status"], result["changed_inputs"]), ("RETRY", ["reviewed_inputs"]))
                    self.assertEqual(archctx.last_path(self.state).read_bytes(), old)
                finally:
                    for path, raw in saved.items():
                        path.write_bytes(raw)

    def test_reused_generation_survives_final_source_race_and_storage_failure(self):
        old = archctx.last_path(self.state).read_bytes()
        generation = Path(archctx.load(archctx.last_path(self.state))["archify"]["generation"])
        original = archctx.final_refresh_check

        def moved(*args):
            result = original(*args)
            self.helper.write_text("HELPER = 2\n", encoding="utf-8")
            return result

        with patch.object(archctx, "final_refresh_check", side_effect=moved):
            result = self.accept()
        self.assertEqual((result["status"], result["changed_inputs"]), ("RETRY", ["source_analysis"]))
        self.assertEqual(archctx.last_path(self.state).read_bytes(), old)
        self.assertTrue(generation.is_dir())
        self.helper.write_text("HELPER = 1\n", encoding="utf-8")
        atomic = archctx.atomic

        def cannot_publish(path, value):
            if path == archctx.last_path(self.state):
                raise OSError("synthetic disk failure")
            return atomic(path, value)

        with patch.object(archctx, "atomic", side_effect=cannot_publish):
            result = self.accept()
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(archctx.last_path(self.state).read_bytes(), old)
        self.assertTrue(generation.is_dir())
        self.assertEqual(self.accept()["status"], "PASS")
        self.assert_proof()

    def test_partial_analysis_blocks_direct_and_auto_first_deletions(self):
        old_value = copy.deepcopy(self.value)
        for change in ("component", "relation"):
            with self.subTest(change=change):
                self.value = copy.deepcopy(old_value)
                self.value["relations"] = []
                if change == "component":
                    self.value["components"].pop()
                archctx.atomic(self.config, self.value)
                archctx.atomic(self.view, {"nodes": [{"id": c["id"], "pos": [0, 0]} for c in self.value["components"]]})
                with self.assertRaisesRegex(ValueError, f"partial source analysis cannot approve canonical {change} deletion"):
                    self.accept()
                self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
                with self.assertRaisesRegex(ValueError, f"partial source analysis cannot approve canonical {change} deletion"):
                    self.accept()
        self.assertEqual(archctx.decision_store(self.state), [])

    def test_reused_artifact_race_blocks_proof_without_deleting_current_generation(self):
        old = archctx.last_path(self.state).read_bytes()
        record = archctx.load(archctx.last_path(self.state))
        ir = Path(record["archify"]["ir"])
        original = archctx.final_refresh_check

        def moved(*args):
            result = original(*args)
            ir.write_text("changed after reuse check", encoding="utf-8")
            return result

        with patch.object(archctx, "final_refresh_check", side_effect=moved), \
             patch.object(archctx, "archify_projection", side_effect=AssertionError("same inputs rerendered")):
            result = self.accept()
        self.assertEqual((result["status"], result["changed_inputs"]), ("RETRY", ["accepted_artifacts"]))
        self.assertEqual(archctx.last_path(self.state).read_bytes(), old)
        self.assertTrue(ir.is_file())

    def test_same_match_receipt_failure_keeps_committed_proof(self):
        with patch.object(archctx, "record_decision", side_effect=OSError("synthetic receipt failure")):
            result = self.accept()
        self.assertEqual((result["status"], result["decision_receipt_cleanup"]), ("PASS", "deferred"))
        self.assert_proof()


if __name__ == "__main__":
    unittest.main()
