"""Deterministic finding/review identity checks; no provider or consumer execution."""
import copy
import tempfile
import unittest
from pathlib import Path

import archctx
import archctx_understand as ua


class NativeIdentityTest(unittest.TestCase):
    def setUp(self):
        self.contents = {"a.py": b"def run(): pass\n", "b.py": b"def save(): pass\n", "c.py": b"other = 1\n"}
        self.receipt = {"analysis_id": "a" * 64, "graph_sha256": "b" * 64,
                        "source_hashes": ua.content_hashes(self.contents),
                        "provider": {"name": "understand-anything", "url": ua.PROVIDER_URL, "revision": ua.PROVIDER_REVISION}}
        self.graph = {
            "nodes": [{"id": p, "type": "file", "name": p, "filePath": p, "summary": "source"} for p in self.contents],
            "layers": [{"id": "entry", "name": "Entry", "description": "Runs the request", "nodeIds": ["a.py"]}],
            "edges": [{"source": "a.py", "target": "b.py", "type": "imports"},
                      {"source": "b.py", "target": "a.py", "type": "calls", "metadata": {"steps": ["validate", "save"]}}],
            "tour": [{"title": "Enter", "nodeIds": ["a.py", "b.py"]}, {"title": "Save", "nodeIds": ["b.py"]}]}

    def candidate(self, graph=None, receipt=None):
        return ua.graph_candidates(graph or self.graph, receipt or self.receipt, self.contents)[0]

    def decision(self, candidate, receipt=None):
        return {"id": candidate["id"], "state": "final", "decision": "accepted", "bindings": ["component:entry"],
                **ua.review_revisions(candidate, receipt or self.receipt)}

    def test_collection_order_and_import_recovery_keep_review_and_raw_metadata(self):
        first = self.candidate()
        changed = copy.deepcopy(self.graph)
        changed["nodes"].reverse()
        changed["edges"].reverse()
        changed["edges"][1]["metadata"] = {"recoveredFromImportMap": True}
        changed["edges"][1]["recoveredFromImportMap"] = True
        original = copy.deepcopy(changed)
        second = self.candidate(changed)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(ua.review_revisions(first, self.receipt), ua.review_revisions(second, self.receipt))
        self.assertEqual(changed, original)
        self.assertEqual(second["raw_relations"][0]["metadata"], {"recoveredFromImportMap": True})
        self.assertIsNotNone(ua.reviewed_decision(second, self.receipt, [self.decision(first)], []))
        changed["layers"][0]["nodeIds"] = ["a.py", "b.py"]
        before = self.candidate(changed)
        changed["layers"][0]["nodeIds"].reverse()
        self.assertEqual(before["content_revision"], self.candidate(changed)["content_revision"])

    def test_meaning_changes_require_review_and_ordered_sequences_stay_ordered(self):
        first = self.candidate()
        variants = []
        for mutate in (
            lambda g: g["edges"][0].update(direction="reverse"),
            lambda g: g["edges"][0].update(metadata={"contract": "new"}),
            lambda g: g["edges"][1]["metadata"]["steps"].reverse(),
            lambda g: g["edges"][1].update(metadata={"recoveredFromImportMap": True}),
            lambda g: g["nodes"][0].update(summary="Different responsibility"),
            lambda g: g["layers"][0].update(description="Different layer meaning"),
            lambda g: g["tour"].reverse(),
            lambda g: g["tour"][0]["nodeIds"].reverse(),
        ):
            graph = copy.deepcopy(self.graph)
            mutate(graph)
            variants.append(self.candidate(graph))
        for changed in variants:
            self.assertEqual(first["id"], changed["id"])
            self.assertNotEqual(first["content_revision"], changed["content_revision"])
            self.assertEqual(first["evidence_revision"], changed["evidence_revision"])
            self.assertIsNone(ua.reviewed_decision(changed, self.receipt, [self.decision(first)], []))

    def test_saved_source_and_related_source_change_never_inherit_review(self):
        first = self.candidate()
        for path in ("a.py", "b.py"):
            receipt = copy.deepcopy(self.receipt)
            receipt["source_hashes"][path] = archctx.sha(b"changed saved source")
            changed = self.candidate(receipt=receipt)
            self.assertEqual(first["id"], changed["id"])
            self.assertEqual(first["content_revision"], changed["content_revision"])
            self.assertNotEqual(first["evidence_revision"], changed["evidence_revision"])
            self.assertIsNone(ua.reviewed_decision(changed, receipt, [self.decision(first)], []))
            self.assertIsNone(ua.reviewed_decision(first, self.receipt, [self.decision(first)], [path]))
        unrelated = copy.deepcopy(self.receipt)
        unrelated.update(analysis_id="new run", graph_sha256="new serialization")
        unrelated["source_hashes"]["c.py"] = archctx.sha(b"unrelated edit")
        self.assertIsNotNone(ua.reviewed_decision(self.candidate(receipt=unrelated), unrelated, [self.decision(first)], ["c.py"]))
        graph = copy.deepcopy(self.graph)
        graph["tour"][0]["nodeIds"].append("c.py")
        toured = self.candidate(graph)
        self.assertIn("c.py", toured["related_source_files"])
        self.assertIsNone(ua.reviewed_decision(toured, self.receipt, [self.decision(toured)], ["c.py"]))

    def test_legacy_id_can_be_explicitly_reviewed_but_old_confirmation_is_untrusted(self):
        legacy = {"id": "ua:legacy-existing-id", "files": ["a.py"], "title": "Existing imported candidate"}
        historical = {"id": legacy["id"], "state": "final", "decision": "accepted", "bindings": ["component:entry"]}
        self.assertIsNone(ua.reviewed_decision(legacy, self.receipt, [historical], []))
        reviewed = self.decision(legacy)
        self.assertIsNotNone(ua.reviewed_decision(legacy, self.receipt, [reviewed], []))
        changed = {**self.receipt, "graph_sha256": "different legacy graph"}
        self.assertIsNone(ua.reviewed_decision(legacy, changed, [reviewed], []))
        self.assertIsNone(ua.reviewed_decision(legacy, self.receipt, [reviewed], ["c.py"]))
        self.assertIsNone(ua.reviewed_decision(legacy, self.receipt, [reviewed, historical], []))

    def test_discovery_binds_product_ids_and_keeps_stale_review_historical(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            repo = Path(directory).resolve()
            for path, raw in self.contents.items():
                (repo / path).write_bytes(raw)
            config = repo / "architecture.json"
            archctx.atomic(config, {"version": 1, "repo": ".", "components": [
                {"id": "entry", "evidence": [{"path": "b.py", "contains": "def save"}]},
                {"id": "overlap", "evidence": [{"path": "a.py", "contains": "def run"}]}]})
            state = archctx.state(config, None)
            candidate = self.candidate()
            receipt = {**self.receipt, "worktree": str(repo), "source_revision": "synthetic", "node_count": 3,
                       "edge_count": 2, "tour": self.graph["tour"], "candidates": [candidate]}
            archctx.atomic(ua.current_path(state), receipt)
            decision = self.decision(candidate)
            archctx.record_decision(state, decision)
            result = ua.discoveries(config, None)
            self.assertEqual(result["status"], "FRESH", result)
            finding = result["candidates"][0]
            self.assertEqual(finding["review_state"], "accepted")
            self.assertEqual(finding["bindings"], ["component:entry"])
            self.assertEqual(finding["related_components"], ["entry", "overlap"])
            self.assertEqual(finding["match"], "review_binding")
            archctx.atomic(archctx.last_path(state), {"candidate_decisions": [decision]})
            archctx.save_decisions(state, [{**decision, "content_revision": "older review"}])
            self.assertEqual(ua.discoveries(config, None)["candidates"][0]["review_state"], "accepted")
            rejected = {**decision, "decision": "rejected", "reason": "false_match", "bindings": []}
            archctx.record_decision(state, rejected)
            self.assertEqual(ua.discoveries(config, None)["candidates"][0]["review_state"], "rejected")
            archctx.save_decisions(state, [decision])
            (repo / "b.py").write_bytes(b"def save(): return 1\n")
            result = ua.discoveries(config, None)
            self.assertEqual(result["status"], "STALE")
            self.assertEqual(result["candidates"][0]["review_state"], "unreviewed")
            self.assertEqual(result["candidates"][0]["bindings"], [])
            self.assertEqual(archctx.decision_store(state), [decision])


if __name__ == "__main__":
    unittest.main()
