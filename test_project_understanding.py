"""Deterministic provider fixtures exercise real project import/review contracts."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_analysis_inputs as inputs
import archctx_understand as understand


class ProjectUnderstandingTest(unittest.TestCase):
    def setUp(self):
        fixture_root = Path(__file__).resolve().parent / ".archctx"
        fixture_root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=fixture_root)
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.config, self.state = self.repo / "architecture.json", self.repo / ".archctx"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        for name in ("a.py", "b.py", "uncovered.py"):
            (self.repo / name).write_bytes(b"VALUE = 1\n")
        archctx.atomic(self.config, {"version": 1, "repo": ".", "components": [
            {"id": name + "-owner", "purpose": "Declared " + name,
             "evidence": [{"path": name + ".py", "contains": "VALUE"}]}
            for name in ("a", "b")], "relations": []})
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
        self.accepted = archctx.last_path(self.state).read_bytes()
        for name, value in (("provider_root", self.repo / "provider/plugin"),
                            ("validate_graph", {"ok": True})):
            mocked = patch.object(understand, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def capture(self, file, tag, dependencies=(), title=None, external=()):
        """Capture an independent Agent input before any later publication."""
        hashes = understand.content_hashes(understand.source_bytes(self.repo, [file]))
        dependency_hashes = (understand.content_hashes(understand.source_bytes(self.repo, list(dependencies)))
                             if dependencies else {})
        ident = archctx.semantic({"sources": hashes, "dependencies": dependency_hashes, "request": tag})
        run = self.state / "understand/runs" / ident
        source = run / "source"
        for relative in [file, *dependencies]:
            archctx.atomic_bytes(source / relative, (self.repo / relative).read_bytes())
        rows = {relative: {"dependencies": [], "coverage": "resolved", "unknown": [], "unresolvedLocal": []}
                for relative in [file, *dependencies]}
        rows[file]["dependencies"] = list(dependencies)
        rows[file]["external"] = list(external)
        receipt = {
            "analysis_id": ident, "source_root": str(source), "worktree": str(self.repo),
            "source_revision": "deterministic-provider-fixture", "source_hashes": hashes,
            "dependency_hashes": dependency_hashes, "resolver_hashes": {}, "dependencies": rows,
            "inventory_hash": inputs.inventory(self.repo, [file])["hash"],
            "provider": {"name": "understand-anything", "root": str(self.repo / "provider/plugin"),
                         "revision": understand.PROVIDER_REVISION, "url": understand.PROVIDER_URL}}
        scope = understand.scope_id(receipt)
        receipt["scope_base"] = understand.entry_token(understand.analysis_catalog(self.state)["scopes"].get(scope))
        archctx.atomic(run / "input.json", receipt)
        graph = {"nodes": [{"id": "file:" + file, "type": "file", "filePath": file,
                             "name": title or file, "summary": "Deterministic source description",
                             "tags": [], "complexity": "simple"}],
                 "edges": [], "layers": [], "tour": []}
        archctx.atomic(source / ".ua/knowledge-graph.json", graph)
        archctx.atomic(source / ".ua/tmp/ua-file-extract-results-0.json", {
            "scriptCompleted": True, "filesSkipped": [], "filesAnalyzed": 1, "results": [{"path": file}],
            "analysisOutcomes": {"structure": {"succeeded": 1, "failed": 0}, "callGraph": {"failed": 0}}})
        return run / "input.json"

    def publish(self, input_path):
        result = understand.import_graph(self.config, str(self.state), input_path)
        self.assertEqual(result["status"], "FRESH")
        return understand.discoveries(self.config, str(self.state), analysis=input_path.parent.name)

    def show(self, **selection):
        return understand.discoveries(self.config, str(self.state), **selection)

    def scope_states(self, packet):
        return {tuple(row["source_files"]): row["status"] for row in packet["scopes"]}

    def assert_accepted_unchanged(self):
        self.assertEqual(archctx.last_path(self.state).read_bytes(), self.accepted)

    def test_a_then_b_stays_selectable_reviewable_and_reusable_without_a_provider(self):
        a = self.publish(self.capture("a.py", "A"))
        a_path, a_receipt = understand.analysis_receipt(self.state, a["analysis_id"])
        historical_receipt = a_path.read_bytes()
        historical_graph = understand.graph_file(self.state, a_receipt).read_bytes()
        b = self.publish(self.capture("b.py", "B"))
        self.assertEqual(self.show()["analysis_id"], b["analysis_id"])
        with patch.object(understand, "run_upstream", side_effect=AssertionError("query invoked provider")), \
             patch.object(understand, "validate_graph", side_effect=AssertionError("query invoked schema provider")), \
             patch.object(understand, "prepare", side_effect=AssertionError("unchanged scope was prepared again")):
            selected = self.show(files=["a.py"])
            self.assertEqual(selected["analysis_id"], a["analysis_id"])
            self.assertEqual([c["files"] for c in selected["candidates"]], [["a.py"]])
            self.assertEqual(selected["candidates"][0]["analysis_id"], a["analysis_id"])
            reused = understand.native_understand(self.config, str(self.state), files=["a.py"])
            self.assertEqual((reused["status"], reused["analysis_id"]), ("REUSED", a["analysis_id"]))
        self.assertEqual(a_path.read_bytes(), historical_receipt)
        self.assertEqual(understand.graph_file(self.state, a_receipt).read_bytes(), historical_graph)
        self.assert_accepted_unchanged()
        review = understand.review(self.config, str(self.state), a["candidates"][0]["id"],
                                   bindings=["component:a-owner"], analysis=a["analysis_id"])
        self.assertEqual(review["status"], "PASS")
        self.assertEqual(review["source_analysis"]["analysis_id"], a["analysis_id"])
        reviewed = self.show(analysis=a["scope_id"])["candidates"][0]
        self.assertEqual((reviewed["review_state"], reviewed["bindings"]), ("accepted", ["component:a-owner"]))
        self.assertEqual(self.show()["analysis_id"], b["analysis_id"])
        record = archctx.load(archctx.last_path(self.state))
        self.assertEqual({c["id"] for c in record["context"]["components"]}, {"a-owner", "b-owner"})

    def test_independently_captured_scopes_merge_without_losing_the_other_publication(self):
        # Both inputs start against the same empty catalog. This is the
        # deterministic interleaving of concurrent Agent work, without timing.
        input_a, input_b = self.capture("a.py", "parallel-A"), self.capture("b.py", "parallel-B")
        self.assertIsNone(understand.local_json(input_a)["scope_base"])
        self.assertIsNone(understand.local_json(input_b)["scope_base"])
        b = self.publish(input_b)
        a = self.publish(input_a)
        catalog = understand.analysis_catalog(self.state)
        self.assertEqual({entry["analysis_id"] for entry in catalog["scopes"].values()},
                         {a["analysis_id"], b["analysis_id"]})
        self.assertEqual(self.scope_states(self.show()), {("a.py",): "FRESH", ("b.py",): "FRESH"})
        for finding in (a, b):
            self.assertEqual(self.show(analysis=finding["scope_id"])["analysis_id"], finding["analysis_id"])
        self.assert_accepted_unchanged()

    def test_same_scope_late_result_cannot_replace_a_newer_publication_with_identical_source(self):
        baseline = self.publish(self.capture("a.py", "baseline"))
        baseline_path, baseline_receipt = understand.analysis_receipt(self.state, baseline["analysis_id"])
        history = baseline_path.read_bytes(), understand.graph_file(self.state, baseline_receipt).read_bytes()
        late = self.capture("a.py", "late-agent", title="Late interpretation")
        newer_input = self.capture("a.py", "new-agent", title="New interpretation")
        self.assertEqual(understand.local_json(late)["scope_base"], understand.local_json(newer_input)["scope_base"])
        newer = self.publish(newer_input)
        catalog = understand.catalog_path(self.state).read_bytes()
        mirror = understand.current_path(self.state).read_bytes()
        with self.assertRaisesRegex(ValueError, "scope|newer|changed|superseded"):
            understand.import_graph(self.config, str(self.state), late)
        self.assertEqual(understand.catalog_path(self.state).read_bytes(), catalog)
        self.assertEqual(understand.current_path(self.state).read_bytes(), mirror)
        self.assertEqual(self.show(analysis=baseline["scope_id"])["analysis_id"], newer["analysis_id"])
        self.assertEqual((baseline_path.read_bytes(), understand.graph_file(self.state, baseline_receipt).read_bytes()), history)
        self.assert_accepted_unchanged()

    def test_source_change_only_stales_the_scope_that_captured_it(self):
        a = self.publish(self.capture("a.py", "A"))
        b = self.publish(self.capture("b.py", "B"))
        (self.repo / "a.py").write_bytes(b"VALUE = 2\n")
        selected_b = self.show(analysis=b["analysis_id"])
        self.assertEqual(selected_b["status"], "FRESH")
        self.assertEqual(self.scope_states(selected_b), {("a.py",): "STALE", ("b.py",): "FRESH"})
        selected_a = self.show(analysis=a["analysis_id"])
        self.assertEqual((selected_a["status"], selected_a["changed_files"]), ("STALE", ["a.py"]))
        self.assertEqual(selected_a["candidates"][0]["affected_files"], ["a.py"])
        self.assert_accepted_unchanged()

    def test_changed_captured_dependency_invalidates_review_and_cannot_inherit_it_on_reanalysis(self):
        a = self.publish(self.capture("a.py", "A"))
        b = self.publish(self.capture("b.py", "B", dependencies=("a.py",)))
        prior = b["candidates"][0]
        reviewed = understand.review(self.config, str(self.state), prior["id"],
                                     bindings=["component:b-owner"], analysis=b["analysis_id"])
        self.assertEqual(reviewed["status"], "PASS")
        self.assertEqual(self.show(analysis=b["analysis_id"])["candidates"][0]["review_state"], "accepted")
        old_receipt_path, old_receipt = understand.analysis_receipt(self.state, b["analysis_id"])
        evidence = old_receipt_path.read_bytes()
        (self.repo / "a.py").write_bytes(b"VALUE = 2\n")
        changed = self.show(analysis=b["analysis_id"])
        self.assertEqual(self.scope_states(changed), {("a.py",): "STALE", ("b.py",): "STALE"})
        finding = changed["candidates"][0]
        self.assertEqual(finding["affected_files"], ["a.py"])
        self.assertEqual((finding["review_state"], finding["bindings"]), ("unreviewed", []))
        with self.assertRaisesRegex(ValueError, "source|stale|moved"):
            understand.review(self.config, str(self.state), prior["id"],
                              bindings=["component:b-owner"], analysis=b["analysis_id"])
        renewed = self.publish(self.capture("b.py", "B-after-dependency", dependencies=("a.py",)))
        candidate = renewed["candidates"][0]
        self.assertEqual(candidate["id"], prior["id"])
        self.assertEqual(candidate["content_revision"], prior["content_revision"])
        self.assertNotEqual(candidate["evidence_revision"], prior["evidence_revision"])
        self.assertEqual((candidate["review_state"], candidate["bindings"]), ("unreviewed", []))
        self.assertEqual(self.show(analysis=a["scope_id"])["status"], "STALE")
        self.assertEqual(old_receipt_path.read_bytes(), evidence)
        self.assertTrue(understand.graph_file(self.state, old_receipt).is_file())

    def test_rename_and_delete_mark_retained_scopes_stale_and_new_names_uncovered(self):
        a = self.publish(self.capture("a.py", "A"))
        b = self.publish(self.capture("b.py", "B"))
        histories = {}
        for analysis in (a, b):
            path, receipt = understand.analysis_receipt(self.state, analysis["analysis_id"])
            for historical in (path, understand.graph_file(self.state, receipt), Path(receipt["source_root"]) / receipt["candidates"][0]["files"][0]):
                histories[historical] = historical.read_bytes()
        (self.repo / "a.py").rename(self.repo / "renamed.py")
        (self.repo / "b.py").unlink()
        self.assertEqual(self.scope_states(self.show()), {("a.py",): "STALE", ("b.py",): "STALE"})
        self.assertEqual(self.show(files=["a.py"])["changed_files"], ["a.py"])
        self.assertEqual(self.show(files=["b.py"])["changed_files"], ["b.py"])
        renamed = self.show(files=["renamed.py"])
        self.assertEqual(renamed["uncovered_files"], ["renamed.py"])
        self.assertEqual(renamed["candidates"], [])
        self.assertNotEqual(renamed["status"], "FRESH")
        for path, content in histories.items():
            self.assertEqual(path.read_bytes(), content)
        self.assert_accepted_unchanged()

    def test_new_local_shadow_of_external_import_invalidates_inventory_and_prior_review(self):
        (self.repo / "b.py").write_bytes(b"import json\nVALUE = 1\n")
        b = self.publish(self.capture("b.py", "stdlib-json", external=("json",)))
        candidate = b["candidates"][0]
        result = understand.review(self.config, str(self.state), candidate["id"],
                                   bindings=["component:b-owner"], analysis=b["analysis_id"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.show(analysis=b["analysis_id"])["candidates"][0]["review_state"], "accepted")
        receipt_path, receipt = understand.analysis_receipt(self.state, b["analysis_id"])
        retained = receipt_path.read_bytes()
        source_hash = archctx.sha((self.repo / "b.py").read_bytes())
        (self.repo / "json.py").write_bytes(b"VALUE = 'new local module shadows stdlib'\n")
        self.assertNotEqual(inputs.inventory(self.repo, ["b.py"])["hash"], receipt["inventory_hash"])
        self.assertEqual(archctx.sha((self.repo / "b.py").read_bytes()), source_hash)
        with patch.object(understand, "run_upstream", side_effect=AssertionError("query invoked provider")):
            changed = self.show(analysis=b["analysis_id"])
        self.assertEqual(changed["status"], "STALE")
        self.assertIn("@dependency-resolution", changed["changed_files"])
        self.assertEqual((changed["candidates"][0]["review_state"], changed["candidates"][0]["bindings"]),
                         ("unreviewed", []))
        with self.assertRaisesRegex(ValueError, "source|stale|moved"):
            understand.review(self.config, str(self.state), candidate["id"],
                              bindings=["component:b-owner"], analysis=b["analysis_id"])
        self.assertEqual(receipt_path.read_bytes(), retained)

    def test_uncovered_or_invalid_selection_and_one_invalid_receipt_keep_valid_scopes_visible(self):
        a = self.publish(self.capture("a.py", "A"))
        b = self.publish(self.capture("b.py", "B"))
        unknown = self.show(files=["uncovered.py"])
        self.assertEqual(unknown["uncovered_files"], ["uncovered.py"])
        self.assertEqual(unknown["candidates"], [])
        self.assertEqual(self.scope_states(unknown), {("a.py",): "FRESH", ("b.py",): "FRESH"})
        invalid = self.show(analysis="f" * 64)
        self.assertNotEqual(invalid["status"], "FRESH")
        self.assertEqual(invalid["candidates"], [])
        self.assertEqual(self.scope_states(invalid), {("a.py",): "FRESH", ("b.py",): "FRESH"})
        receipt_path, _ = understand.analysis_receipt(self.state, a["analysis_id"])
        receipt_path.write_bytes(b'{"damaged":true}')
        selected_b = self.show(analysis=b["analysis_id"])
        self.assertEqual(selected_b["status"], "FRESH")
        self.assertEqual(self.scope_states(selected_b), {("a.py",): "INVALID", ("b.py",): "FRESH"})
        selected_a = self.show(analysis=a["analysis_id"])
        self.assertEqual(selected_a["status"], "INVALID")
        self.assertEqual(self.scope_states(selected_a)[("b.py",)], "FRESH")
        self.assert_accepted_unchanged()


if __name__ == "__main__":
    unittest.main()
