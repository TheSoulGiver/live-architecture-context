"""Provider coverage failures stay distinct from absent source capabilities."""
import unittest
from unittest.mock import patch

import archctx
import archctx_understand as ua
import test_native_understand as native_fixture


class ExtractionCoverageTest(unittest.TestCase):
    def setUp(self):
        self.flow = native_fixture.NativeUnderstandTest()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    @staticmethod
    def skipped(relative):
        # Actual upstream shape when no language plugin handles the file.
        return {"scriptCompleted": True, "filesAnalyzed": 0, "filesSkipped": [relative],
                "analysisOutcomes": {"structure": {"succeeded": 0, "failed": 0},
                                     "callGraph": {"succeeded": 0, "failed": 0, "skipped": 0}},
                "results": []}

    def test_native_unsupported_scope_is_explicit_and_preserves_lkg(self):
        self.flow.files = ["scene.gd"]
        (self.flow.repo / "scene.gd").write_text("extends Node\n", encoding="utf-8")
        original = self.flow.upstream

        def upstream(plugin, script, args, cwd):
            if script == "scan-project.mjs":
                archctx.atomic(args[1], {"scriptCompleted": True, "files": [{"path": "scene.gd", "language": "gd"}],
                    "totalFiles": 1, "filteredByIgnore": 0, "estimatedComplexity": "small", "stats": {"byLanguage": {"gd": 1}}})
            elif script == "extract-structure.mjs":
                archctx.atomic(args[1], self.skipped("scene.gd"))
            else:
                return original(plugin, script, args, cwd)
            return {"script": script, "exit_code": 0}

        self.flow.upstream = upstream
        with patch.object(ua, "run_upstream", side_effect=upstream):
            result = archctx.understand(self.flow.config, None, {"files": self.flow.files})
        self.assertEqual(result["status"], "INVALID", result)
        for text in ("scene.gd", "skipped", "unsupported", "UNKNOWN", "canonical evidence"):
            self.assertIn(text, result["reason"])
        self.assertNotIn("work", result)
        self.assertEqual(archctx.last_path(self.flow.directory).read_bytes(), self.flow.last_good)

    def test_import_uses_same_check_and_raw_calls_remain_valid(self):
        completed = self.flow.complete()
        run = self.flow.directory / "understand/runs" / completed["analysis_id"]
        path = run / "source/.ua/tmp/ua-file-extract-results-0.json"
        original = ua.local_json(path)
        # An IIFE may have no top-level symbols yet still contain real calls.
        original["results"][0].update(metrics={"functionCount": 0, "classCount": 0},
            callGraph=[{"caller": "observe", "callee": "normalize", "lineNumber": 1}])
        archctx.atomic(path, original)
        self.assertEqual(ua.native_extraction(path, "a.py")["results"][0]["callGraph"],
                         original["results"][0]["callGraph"])
        retained = ua.current_path(self.flow.directory).read_bytes()
        for value in (self.skipped("a.py"), {**original, "analysisOutcomes": {"structure": {"succeeded": 1}, "callGraph": {"failed": 1}}},
                      {**original, "results": [{"path": "wrong.py"}]}):
            archctx.atomic(path, value)
            with self.assertRaisesRegex(ValueError, "UNKNOWN.*not absence"):
                ua.import_graph(self.flow.config, None, run / "input.json")
            self.assertEqual(ua.current_path(self.flow.directory).read_bytes(), retained)
            self.assertEqual(archctx.last_path(self.flow.directory).read_bytes(), self.flow.last_good)


if __name__ == "__main__":
    unittest.main()
