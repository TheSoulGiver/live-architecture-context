"""Focused checks for optional source analysis; provider fixtures are not dogfood."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_understand as ua


class UnderstandTest(unittest.TestCase):
    def test_prepare_recovers_incomplete_capture_and_reuses_only_complete_input(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            repo = Path(directory).resolve()
            (repo / "source.py").write_bytes(b"x = 1\n")
            config = repo / "architecture.json"
            archctx.atomic(config, {"repo": "."})
            state = archctx.state(config, None).resolve()
            def upstream(plugin, script, args, cwd):
                if script == "scan-project.mjs":
                    archctx.atomic(args[1], {"scriptCompleted": True, "files": [{"path": "source.py", "language": "python"}],
                        "totalFiles": 1, "filteredByIgnore": 0, "estimatedComplexity": "small", "stats": {"byLanguage": {"python": 1}}})
                else:
                    archctx.atomic(args[1], {"scriptCompleted": True, "importMap": {"source.py": []}})
                return {"script": script, "exit_code": 0}
            with patch.object(ua, "provider_root", return_value=repo), patch.object(archctx, "revision", return_value="synthetic"):
                with patch.object(ua, "run_upstream", side_effect=ValueError("interrupted")):
                    with self.assertRaisesRegex(ValueError, "interrupted"):
                        ua.prepare(config, None, repo, ["source.py"])
                run = next((state / "understand/runs").iterdir())
                (run / "source/source.py").unlink()  # Synthetic interrupted capture, not product worktree.
                with patch.object(ua, "run_upstream", side_effect=upstream):
                    self.assertEqual(ua.prepare(config, None, repo, ["source.py"])["status"], "NEEDS_SEMANTIC_ANALYSIS")
                with patch.object(ua, "run_upstream", side_effect=AssertionError("complete capture was re-run")):
                    self.assertEqual(ua.prepare(config, None, repo, ["source.py"])["status"], "REUSED_INPUT")
                (run / "source/source.py").write_bytes(b"do not overwrite")
                with self.assertRaisesRegex(ValueError, "snapshot was modified"):
                    ua.prepare(config, None, repo, ["source.py"])

    def test_finding_identity_is_stable_while_source_revision_changes(self):
        graph = {"nodes": [{"id": "file:a.py", "type": "file", "filePath": "a.py", "name": "A", "summary": "scope"}],
                 "layers": [], "edges": []}
        receipt = {"analysis_id": "one", "graph_sha256": "one", "source_hashes": {"a.py": "a" * 64}}
        first = ua.graph_candidates(graph, receipt, {"a.py": b"x=1\n"})[0]
        second = ua.graph_candidates(graph, {**receipt, "analysis_id": "two", "graph_sha256": "two"}, {"a.py": b"x=1\n"})[0]
        self.assertEqual(first["id"], second["id"])
        changed = ua.graph_candidates(graph, {**receipt, "source_hashes": {"a.py": "b" * 64}}, {"a.py": b"x=2\n"})[0]
        self.assertEqual(first["id"], changed["id"])
        self.assertNotEqual(first["evidence_revision"], changed["evidence_revision"])

    def test_reuse_preserves_unchanged_nodes_and_incoming_edges(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source = Path(directory)
            caller = {"id": "function:caller.py:run", "filePath": "caller.py", "summary": "retained"}
            incoming = {"source": caller["id"], "target": "function:target.py:serve", "type": "calls", "direction": "forward"}
            baseline = {"nodes": [caller], "edges": [incoming]}
            archctx.atomic(source / ".ua/intermediate/batch-existing.json", baseline)
            receipt = {"incremental": {"reused_files": ["caller.py"], "baseline_hash": archctx.semantic(baseline)}}
            ua.check_reused_graph(source, receipt, baseline)
            with self.assertRaisesRegex(ValueError, "incoming"):
                ua.check_reused_graph(source, receipt, {"nodes": [caller], "edges": []})
            with self.assertRaisesRegex(ValueError, "unchanged"):
                ua.check_reused_graph(source, receipt, {"nodes": [{**caller, "summary": "rewritten"}], "edges": [incoming]})
            archctx.atomic(source / ".ua/intermediate/batch-existing.json", {"nodes": [], "edges": []})
            with self.assertRaisesRegex(ValueError, "baseline changed"):
                ua.check_reused_graph(source, receipt, baseline)

    def test_saved_bytes_and_worktree_identity_not_git_head(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            receipt = {"worktree": str(root), "source_hashes": ua.content_hashes(ua.source_bytes(root, ["source.py"]))}
            self.assertEqual(ua.verify_sources(root, receipt), [])
            (root / "source.py").write_text("value = 2\n", encoding="utf-8")
            self.assertEqual(ua.verify_sources(root, receipt), ["source.py"])
            with self.assertRaises(ValueError):
                ua.verify_sources(root / "other", receipt)
            with self.assertRaises(ValueError):
                ua.source_bytes(root, ["../outside.py"])
            with self.assertRaises(ValueError):
                ua.source_bytes(root, ["source.py", "source.py"])

    def test_source_receipt_digests_and_scope_read_budget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            (root / "a.py").write_bytes(b"a\n")
            (root / "b.py").write_bytes(b"b\n")
            hashes = ua.content_hashes(ua.source_bytes(root, ["a.py", "b.py"]))
            for relative, digest in (("missing.py", None), ("a.py", "g" * 64),
                                     ("a.py", "a" * 63), ("a.py", "a" * 65), ("a.py", 7)):
                with self.subTest(relative=relative, digest=digest), self.assertRaises(ValueError):
                    ua.verify_sources(root, {"worktree": str(root), "source_hashes": {relative: digest}})
            with patch.object(ua, "SOURCE_BYTES", 3):
                self.assertEqual(ua.verify_sources(root, {
                    "worktree": str(root), "source_hashes": {"a.py": hashes["a.py"]}}), [])
                with self.assertRaises(ValueError):
                    ua.verify_sources(root, {"worktree": str(root), "source_hashes": hashes})

    def test_state_alias_and_case_are_not_source(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            for relative in (".GIT/config", ".ARCHCTX/evidence.json"):
                with self.assertRaises(ValueError):
                    ua.source_bytes(root, [relative])
            with patch.object(archctx, "repo_file", return_value=(root / ".archctx/private.py", "alias.py")):
                with self.assertRaises(ValueError):
                    ua.source_bytes(root, ["alias.py"])

    def test_import_one_byte_identity_and_retain_on_failed_or_stale_analysis(self):
        # Synthetic provider output only: the real upstream pipeline is exercised
        # separately on the repository's saved source, not claimed by this test.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            (root / "source.py").write_bytes(b"value = 1\n")
            config = root / "architecture.json"
            archctx.atomic(config, {"version": 1, "repo": ".", "components": [
                {"id": "existing", "name": "Existing", "purpose": "fixture",
                 "evidence": [{"path": "source.py", "contains": "value"}]}], "relations": []})
            state = archctx.state(config, None)
            run = state / "understand/runs" / ("a" * 64)
            source = run / "source"
            source.mkdir(parents=True)
            (source / "source.py").write_bytes(b"value = 1\n")
            receipt = {"analysis_id": "a" * 64, "worktree": str(root), "source_revision": "fixture",
                "source_root": str(source), "source_hashes": ua.content_hashes(ua.source_bytes(root, ["source.py"])),
                "provider": {"name": "understand-anything", "root": str(root / "provider/plugin"),
                             "revision": ua.PROVIDER_REVISION, "url": ua.PROVIDER_URL}}
            archctx.atomic(run / "input.json", receipt)
            graph_path = source / ".ua/knowledge-graph.json"
            graph = {"nodes": [{"id": "file:source.py", "type": "file", "filePath": "source.py",
                                "name": "A", "summary": "actual fixture A"}], "edges": [], "layers": [], "tour": []}
            archctx.atomic(graph_path, graph)
            original = graph_path.read_bytes()
            extraction_path = source / ".ua/tmp/ua-file-extract-results-0.json"
            extraction = {"scriptCompleted": True, "filesSkipped": [], "filesAnalyzed": 1,
                "results": [{"path": "source.py"}],
                "analysisOutcomes": {"structure": {"succeeded": 1, "failed": 0}, "callGraph": {"failed": 0}}}
            archctx.atomic(extraction_path, extraction)

            def moving_validator(plugin, raw):
                self.assertEqual(raw, original)
                archctx.atomic(graph_path, {**graph, "nodes": [{**graph["nodes"][0], "name": "B"}]})
                return {"ok": True}

            with patch.object(ua, "provider_root", return_value=root), patch.object(ua, "validate_graph", side_effect=moving_validator):
                result = ua.import_graph(config, None, run / "input.json")
            self.assertEqual(result["status"], "FRESH")
            self.assertEqual(result["candidates"][0]["title"], "A")
            self.assertEqual((run / "graph.json").read_bytes(), original)
            retained = ua.current_path(state).read_bytes()
            with patch.object(ua, "provider_root", return_value=root), patch.object(ua, "validate_graph", return_value={"ok": True}):
                extraction["analysisOutcomes"]["callGraph"]["failed"] = 1
                archctx.atomic(extraction_path, extraction)
                with self.assertRaisesRegex(ValueError, "failed"):
                    ua.import_graph(config, None, run / "input.json")
                extraction["analysisOutcomes"]["callGraph"]["failed"] = 0
                extraction["results"][0]["path"] = "wrong.py"
                archctx.atomic(extraction_path, extraction)
                with self.assertRaisesRegex(ValueError, "failed"):
                    ua.import_graph(config, None, run / "input.json")
            self.assertEqual(ua.current_path(state).read_bytes(), retained)
            (root / "source.py").write_bytes(b"value = 2\n")
            stale = ua.discoveries(config, None)
            self.assertEqual(stale["status"], "STALE")
            self.assertEqual(stale["changed_files"], ["source.py"])
            self.assertFalse(stale["blocking"])


if __name__ == "__main__":
    unittest.main()
