"""Deterministic native-flow fixtures, not execution or adoption proof of UA."""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_runtime
import archctx_understand as ua
from archctx_development import DevelopmentObserver
import test_native_runtime


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
        self.real_run = subprocess.run
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

    def setup_empty_project(self):
        # A separate, genuinely new consumer: setup supplies all config/view
        # bytes. Only downloaded upstream components are deterministic fixtures.
        self.repo = self.repo / "new-project"
        self.repo.mkdir()
        for relative in self.files:
            (self.repo / relative).write_text("value = 1\n", encoding="utf-8")
        self.config = self.repo / "architecture/architecture.json"
        self.directory = archctx.state(self.config, None)
        def checkout(path, revision, repository, install=False):
            test_native_runtime.NativeRuntimeTest.ready(path, revision == ua.PROVIDER_REVISION)
            return path
        with patch.object(archctx_runtime.Path, "cwd", return_value=self.repo), \
             patch.object(archctx_runtime, "node_runtime", return_value="node"), \
             patch.object(archctx_runtime, "checkout", side_effect=checkout), \
             patch.object(archctx_runtime, "execute", side_effect=AssertionError("fixture must not install")):
            self.assertEqual(archctx_runtime.setup(self.config, None)["architecture"], "draft_unreviewed")
        self.assertEqual(archctx.load(self.config)["components"], [])
        self.assertFalse(archctx.last_path(self.directory).exists())

    @staticmethod
    def renderer(command, repo, timeout, values):
        # Renderer transport fixture; projection, evidence validation, receipt
        # binding and canonical acceptance remain real (real Archify has CI).
        result = {"ok": True}
        if command[4] != "validate":
            render = command[4] == "deliver"
            output = Path(values["{archify_html}" if render else "{archify_compare}"])
            output.write_text("<svg>synthetic first map</svg>", encoding="utf-8")
            digest = archctx.sha(Path(values["{archify_output}"]).read_bytes())
            result["artifact"] = {"sha256": archctx.sha(output.read_bytes())}
            if render:
                result["specification"] = {"sha256": digest}
            else:
                result.update(base={"rawSha256": archctx.sha(Path(values["{archify_before}"]).read_bytes())},
                              head={"rawSha256": digest}, validation={"checkCount": 1, "checksPassed": 1})
                archctx.atomic(Path(values["{archify_receipt}"]), result)
        return command, subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    def test_setup_empty_draft_to_readable_findings_and_first_acceptance(self):
        self.setup_empty_project()
        system = self.system_stage()
        self.write_system(system)
        ready = self.advance(system)
        self.assertEqual((ready["status"], ready["analysis"]["status"]), ("FINDINGS_READY", "FRESH"), ready)
        for result in (archctx.understand(self.config, None, {"show": True}),
                       archctx.mcp_value(self.config, None, "architecture_understand", {"show": True, "details": True}),
                       archctx.candidates(self.config, None)["source_analysis"],
                       archctx.updates(self.config, None)["source_analysis"]):
            self.assertEqual(result["status"], "FRESH")
            self.assertIsNone(result["accepted_context_hash"])
            finding = result["candidates"][0]
            self.assertEqual((finding["match"], finding["review_state"], finding["related_components"], finding["bindings"]),
                             ("unmapped", "unreviewed", [], []))
            self.assertEqual(finding["evidence"][0]["sha256"], archctx.sha((self.repo / "a.py").read_bytes()))
        cli = self.real_run([sys.executable, str(Path(archctx.__file__).resolve()), "--config", str(self.config),
                            "understand", "--show"], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["candidates"][0]["match"], "unmapped")
        self.assertEqual(self.advance(system)["status"], "REUSED")
        self.assertEqual(self.advance()["status"], "REUSED")
        self.assertEqual(archctx.status(self.config, None)["status"], "MISSING")
        self.assertEqual(archctx.refresh(self.config, None)["status"], "INVALID")
        ident = finding["id"]
        with self.assertRaisesRegex(ValueError, "components must be a non-empty array"):
            archctx.accept_candidate(self.config, None, ident, ["component:entry"])
        self.assertFalse(archctx.last_path(self.directory).exists())
        value = archctx.load(self.config)
        value["components"] = [{"id": "entry", "purpose": "Owns the selected fixture value",
                                "evidence": [{"path": "a.py", "contains": "value = 1"}]}]
        archctx.atomic(self.config, value)
        archctx.atomic(self.config.with_name("architecture.view.json"),
                       {"title": "Synthetic first map", "nodes": [{"id": "entry", "pos": [40, 60]}]})
        value["components"][0]["evidence"][0]["contains"] = "absent source anchor"
        archctx.atomic(self.config, value)
        with self.assertRaisesRegex(ValueError, "no longer contains"):
            archctx.accept_candidate(self.config, None, ident, ["component:entry"])
        self.assertFalse(archctx.last_path(self.directory).exists())
        value["components"][0]["evidence"][0]["contains"] = "value = 1"
        archctx.atomic(self.config, value)
        with patch.object(archctx, "run", side_effect=self.renderer):
            accepted = archctx.accept_candidate(self.config, None, ident, ["component:entry"])
        self.assertEqual((accepted["status"], accepted["publication"]), ("PASS", "updated_canonical"), accepted)
        record = archctx.load(archctx.last_path(self.directory))
        self.assertEqual(record["archify"]["context_hash"], record["context_hash"])
        self.assertEqual(record["candidate_decisions"][0]["source_analysis"], accepted["source_analysis"])
        self.assertEqual(archctx.status(self.config, None)["status"], "FRESH")
        final = ua.discoveries(self.config, None)
        self.assertEqual(final["accepted_context_hash"], record["context_hash"])
        self.assertEqual((final["candidates"][0]["match"], final["candidates"][0]["review_state"]), ("review_binding", "accepted"))
        previous = archctx.last_path(self.directory).read_bytes()
        archctx.atomic(self.config, {**value, "components": []})
        self.assertEqual(ua.discoveries(self.config, None)["status"], "INVALID")
        self.assertEqual(self.advance()["status"], "INVALID")
        self.assertEqual(archctx.refresh(self.config, None)["status"], "INVALID")
        self.assertEqual(archctx.last_path(self.directory).read_bytes(), previous)

    def test_invalid_drafts_never_report_native_ready_or_reused(self):
        self.setup_empty_project()
        system = self.system_stage()
        self.write_system(system)
        valid = archctx.load(self.config)
        for invalid in ({**valid, "version": 0}, {**valid, "components": None},
                        {**valid, "relations": [{"from": "invented", "to": "invented", "kind": "calls"}]},
                        {**valid, "coverage": {"scope": ""}}):
            with self.subTest(invalid=invalid):
                archctx.atomic(self.config, invalid)
                # First import and later resume/reuse all consume real discoveries.
                for args in ({"resume": system["analysis_id"]}, {"files": self.files}, {"show": True}):
                    result = archctx.understand(self.config, None, args)
                    self.assertEqual(result["status"], "INVALID", result)
                    analysis = result.get("analysis", result)
                    self.assertEqual(analysis["candidates"], [])
                    self.assertEqual(result["next_action"], analysis["next_action"])
                self.assertFalse(archctx.last_path(self.directory).exists())
        archctx.atomic(self.config, valid)
        self.assertEqual(self.advance()["analysis"]["status"], "FRESH")

    def test_first_live_observation_keeps_analysis_separate_and_idle_reads_bounded(self):
        self.setup_empty_project()
        with patch.object(archctx, "git", return_value=None):
            observer = DevelopmentObserver(self.config, live=True, settle_seconds=0)
        self.addCleanup(observer.stop)
        with patch.object(archctx, "refresh", side_effect=AssertionError("empty draft must not publish")), \
             patch.object(archctx, "watch_once", side_effect=AssertionError("empty draft has no canonical watch scope")):
            initial = observer.poll_once()
            self.assertEqual(initial["status"], "MISSING", initial)
            self.assertFalse(initial["updates"]["source_analysis"]["configured"])
            self.complete()
            ready = observer.poll_once()
            self.assertEqual((ready["status"], ready["updates"]["source_analysis"]["status"]), ("MISSING", "FRESH"), ready)
            self.assertTrue(ready["updates"]["source_analysis"]["candidates"])
            self.assertFalse(observer.accepted_record)
            with patch.object(ua, "discoveries", side_effect=AssertionError("idle findings reread")):
                self.assertEqual(observer.poll_once()["observation_id"], ready["observation_id"])
            (self.repo / "a.py").write_text("value = 2\n", encoding="utf-8")
            stale = observer.poll_once()
            self.assertEqual(stale["updates"]["source_analysis"]["status"], "STALE")
            self.assertEqual(stale["updates"]["source_analysis"]["changed_files"], ["a.py"])
            ua.current_path(self.directory).write_bytes(b"{ invalid analysis")
            invalid = observer.poll_once()
            self.assertEqual((invalid["status"], invalid["updates"]["source_analysis"]["status"]), ("MISSING", "INVALID"))

    def test_first_acceptance_during_draft_observation_never_pairs_mixed_versions(self):
        self.setup_empty_project()
        with patch.object(archctx, "git", return_value=None):
            observer = DevelopmentObserver(self.config)
        self.addCleanup(observer.stop)
        original = archctx.updates
        def accepted_during_read(*args):
            packet = original(*args)
            archctx.atomic(archctx.last_path(self.directory), {"context_hash": "concurrent-first-acceptance"})
            return packet
        with patch.object(archctx, "updates", side_effect=accepted_during_read):
            result = observer.poll_once()
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("changed during observation", result["error"])
        self.assertIsNone(result["accepted_context_hash"])

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
