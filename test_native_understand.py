"""Deterministic native-flow fixtures, not execution or adoption proof of UA."""
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_runtime
import archctx_understand as ua


class NativeUnderstandTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.files = ["a.py", "b.py"]
        for relative in self.files:
            (self.repo / relative).write_text("value = 1\n", encoding="utf-8")
        self.config = self.repo / "architecture.json"
        archctx.atomic(self.config, {"version": 1, "repo": ".", "components": [
            {"id": "entry", "evidence": [{"path": "a.py", "contains": "value"}]}]})
        self.directory = archctx.state(self.config, None)
        archctx.atomic(archctx.last_path(self.directory), {"context_hash": "old", "context": {"components": []}})
        self.last_good = archctx.last_path(self.directory).read_bytes()
        self.calls = Counter()
        self.fail_extract = None
        self.drop_file = None
        for target, name, kwargs in (
            (ua, "provider_root", {"return_value": self.repo / "fixture-provider/plugin"}),
            (archctx_runtime, "roots", {"return_value": {"analysis": self.repo / "fixture-provider"}}),
            (archctx, "revision", {"return_value": "fixture"}),
            (ua, "run_upstream", {"side_effect": self.upstream}),
            (ua.subprocess, "run", {"side_effect": self.command}),
            (ua, "validate_graph", {"return_value": {"fixture": True}}),
        ):
            context = patch.object(target, name, **kwargs)
            context.start()
            self.addCleanup(context.stop)

    def upstream(self, plugin, script, args, cwd):
        self.calls[script] += 1
        if script == "scan-project.mjs":
            files = [{"path": p.relative_to(cwd).as_posix(), "language": "python"} for p in sorted(cwd.glob("*.py"))]
            archctx.atomic(args[1], {"scriptCompleted": True, "files": files, "totalFiles": len(files),
                "filteredByIgnore": 0, "estimatedComplexity": "small", "stats": {"byLanguage": {"python": len(files)}}})
        elif script == "extract-import-map.mjs":
            archctx.atomic(args[1], {"scriptCompleted": True, "importMap": {}})
        elif script == "extract-structure.mjs":
            relative = ua.local_json(args[0])["batchFiles"][0]["path"]
            self.calls["extract:" + relative] += 1
            if relative == self.fail_extract:
                self.fail_extract = None
                args[1].write_bytes(b'{"interrupted":')
                raise ValueError("fixture interrupted extraction")
            archctx.atomic(args[1], {"scriptCompleted": True, "filesAnalyzed": 1, "filesSkipped": [],
                "analysisOutcomes": {"structure": {"succeeded": 1, "failed": 0}, "callGraph": {"succeeded": 1, "failed": 0}},
                "results": [{"path": relative}]})
        elif script == "build-fingerprints.mjs":
            archctx.atomic(cwd / ".ua/fingerprints.json", {"fixture": True})
        else:
            raise AssertionError(script)
        return {"script": script, "exit_code": 0, "stdout_tail": "Fingerprints baseline: fixture"}

    def command(self, argv, **kwargs):
        if argv[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.assertTrue(str(argv[1]).endswith("merge-batch-graphs.py"), argv)
        self.calls["merge"] += 1
        intermediate = Path(argv[2]) / ".ua/intermediate"
        values = [ua.local_json(p) for p in sorted(intermediate.glob("batch-*.json"))]
        graph = {"nodes": [n for value in values for n in value["nodes"] if n.get("filePath") != self.drop_file],
                 "edges": [e for value in values for e in value["edges"]]}
        archctx.atomic(intermediate / "assembled-graph.json", graph)
        return subprocess.CompletedProcess(argv, 0, "fixture merge", "")

    def advance(self, result=None):
        return ua.native_understand(self.config, None, resume=result["analysis_id"]) if result else ua.native_understand(self.config, None, files=self.files)

    def write_files(self, result):
        for work in result["work"]:
            relative = work["file"]
            archctx.atomic(Path(work["write_result"]), {"input_hash": work["input_hash"], "nodes": [{
                "id": "file:" + relative, "type": "file", "name": relative, "filePath": relative,
                "summary": "Fixture source", "tags": [], "complexity": "simple"}], "edges": []})

    def write_system(self, result):
        ids = [n["id"] for n in ua.local_json(Path(result["graph"]))["nodes"]]
        archctx.atomic(Path(result["write_result"]), {"graph_sha256": result["graph_sha256"],
            "layers": [{"id": "entry", "name": "Entry", "description": "Fixture grouping", "nodeIds": ids}],
            "tour": [{"order": 1, "title": "Entry", "description": "Fixture tour", "nodeIds": ids}]})

    def system_stage(self):
        files = self.advance()
        self.write_files(files)
        return self.advance(files)

    def complete(self):
        system = self.system_stage()
        self.write_system(system)
        self.assertEqual(self.advance(system)["status"], "FINDINGS_READY")
        return system

    def test_interrupt_resume_preserves_units_and_reuses_completed_mechanical_steps(self):
        self.fail_extract = "b.py"
        with self.assertRaisesRegex(ValueError, "interrupted"):
            self.advance()
        run = next((self.directory / "understand/runs").iterdir())
        pending = self.advance({"analysis_id": run.name})
        self.assertEqual(self.calls["extract:a.py"], 1)
        self.assertEqual(self.calls["extract:b.py"], 2)
        self.assertEqual(len(list((run / "source/.ua/tmp").glob("*.invalid.json"))), 1)
        self.write_files(pending)
        system = self.advance(pending)
        before = self.calls.copy()
        self.assertEqual(self.advance(system)["stage"], "system_understanding")
        self.assertEqual(self.calls, before)
        self.write_system(system)
        self.assertEqual(self.advance(system)["status"], "FINDINGS_READY")
        before = self.calls.copy()
        self.assertEqual(self.advance(system)["status"], "REUSED")
        self.assertEqual(self.calls, before)
        with patch.object(ua, "provider_root", side_effect=AssertionError("unneeded runtime read")):
            self.assertEqual(self.advance()["status"], "REUSED")
        self.assertEqual(archctx.last_path(self.directory).read_bytes(), self.last_good)

    def test_changed_file_reuses_unchanged_extraction_and_semantic_baseline(self):
        self.complete()
        before = self.calls.copy()
        (self.repo / "b.py").write_text("value = 2\n", encoding="utf-8")
        pending = self.advance()
        self.assertEqual(pending["reused_files"], ["a.py"])
        self.assertEqual([w["file"] for w in pending["work"]], ["b.py"])
        self.assertEqual(self.calls["extract:a.py"], before["extract:a.py"])
        self.assertEqual(self.calls["extract:b.py"], before["extract:b.py"] + 1)

    def test_resume_completes_an_interrupted_preparation(self):
        with patch.object(ua, "run_upstream", side_effect=ValueError("fixture interrupted preparation")):
            with self.assertRaisesRegex(ValueError, "interrupted preparation"):
                self.advance()
        run = next((self.directory / "understand/runs").iterdir())
        self.assertFalse(ua.local_json(run / "input.json").get("prepared"))
        self.assertEqual(self.advance({"analysis_id": run.name})["stage"], "source_understanding")
        self.assertTrue(ua.local_json(run / "input.json")["prepared"])

    def test_scope_validation_search_and_compact_work_are_provider_independent(self):
        with patch.object(archctx_runtime, "roots", side_effect=AssertionError("invalid scope read runtime")):
            for files in (["a.py", "a.py"], ["../a.py"], ["missing.py"], ["a.py"] * 65):
                with self.subTest(files=files[:2]), self.assertRaises((OSError, ValueError)):
                    ua.native_understand(self.config, None, files=files)
            self.assertEqual(ua.native_understand(self.config, None, files=[])["status"], "NEEDS_SCOPE")
        with patch.object(archctx, "search", return_value={"matches": [{"id": "entry"}]}):
            self.assertEqual(ua.native_understand(self.config, None, question="entry")["source_files"], ["a.py"])
        self.files = [f"unit{i}.py" for i in range(5)]
        for relative in self.files:
            (self.repo / relative).write_bytes(b"value = 1\n")
        pending = self.advance()
        self.assertEqual(len(pending["work"]), 4)
        self.assertEqual(pending["omitted_work_count"], 1)
        self.write_files(pending)
        self.assertEqual(len(self.advance(pending)["work"]), 1)

    def test_partial_file_or_merge_cannot_silently_drop_coverage(self):
        pending = self.advance()
        self.write_files(pending)
        bad_path = Path(pending["work"][1]["write_result"])
        value = ua.local_json(bad_path)
        archctx.atomic(bad_path, {**value, "nodes": []})
        repair = self.advance(pending)
        self.assertEqual([w["file"] for w in repair["work"]], ["b.py"])
        self.assertEqual(self.calls["merge"], 0)
        self.write_files(repair)
        self.drop_file = "b.py"
        with self.assertRaisesRegex(ValueError, "cover every scoped file"):
            self.advance(pending)
        self.assertFalse(ua.current_path(self.directory).exists())
        self.assertEqual(archctx.last_path(self.directory).read_bytes(), self.last_good)

    def test_invalid_extraction_is_bounded_and_changed_input_scope_does_no_provider_work(self):
        pending = self.advance()
        self.write_files(pending)
        extraction = Path(pending["work"][0]["facts"])
        for broken in (b"{", b'{"scriptCompleted":false}'):
            extraction.write_bytes(broken)
            self.advance(pending)
        self.assertEqual(len(list(extraction.parent.glob("*.invalid.json"))), 1)
        self.assertEqual(extraction.with_name(extraction.stem + ".invalid.json").read_bytes(), b'{"scriptCompleted":false}')
        analyzer_input = Path(pending["work"][0]["symbol_identity_input"])
        value = ua.local_json(analyzer_input)
        value["batchFiles"][0]["path"] = "../outside.py"
        archctx.atomic(analyzer_input, value)
        before = self.calls.copy()
        with self.assertRaisesRegex(ValueError, "captured file scope"):
            self.advance(pending)
        self.assertEqual(self.calls, before)

    def test_cache_hashes_repair_outputs_and_require_system_review_for_changed_graph(self):
        system = self.system_stage()
        self.write_system(system)
        assembled = Path(system["graph"])
        assembled.write_bytes(b'{"nodes":[],"edges":[]}')
        self.assertEqual(self.advance(system)["status"], "FINDINGS_READY")
        self.assertEqual(self.calls["merge"], 2)
        run = assembled.parents[3]
        fingerprints = run / "source/.ua/fingerprints.json"
        fingerprints.write_bytes(b"changed")
        before = self.calls["build-fingerprints.mjs"]
        self.advance(system)
        self.assertEqual(self.calls["build-fingerprints.mjs"], before + 1)
        batch_path = assembled.parent / "batch-0.json"
        batch = ua.local_json(batch_path)
        batch["nodes"][0]["summary"] = "Changed understanding"
        archctx.atomic(batch_path, batch)
        pending = self.advance(system)
        self.assertEqual(pending["stage"], "system_understanding")
        self.assertNotEqual(pending["graph_sha256"], system["graph_sha256"])

    def test_writer_busy_and_source_import_race_keep_previous_publication(self):
        with archctx.refresh_lock(self.directory / "understand"):
            self.assertEqual(self.advance()["status"], "RETRY")
        system = self.system_stage()
        self.write_system(system)
        archctx.atomic(ua.current_path(self.directory), {"analysis_id": "c" * 64, "historical": True})
        previous = ua.current_path(self.directory).read_bytes()
        original = ua.graph_candidates
        def moving(*args):
            result = original(*args)
            (self.repo / "b.py").write_bytes(b"value = 3\n")
            return result
        with patch.object(ua, "graph_candidates", side_effect=moving):
            result = self.advance(system)
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["changed_files"], ["b.py"])
        self.assertEqual(ua.current_path(self.directory).read_bytes(), previous)
        self.assertEqual(archctx.last_path(self.directory).read_bytes(), self.last_good)


if __name__ == "__main__":
    unittest.main()
