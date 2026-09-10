"""Native storage boundaries using disposable mechanical fixtures, never a model."""
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_analysis_storage as storage
import archctx_understand as understand
import test_native_understand as native_fixture


class NativeAnalysisCapacityTest(unittest.TestCase):
    def setUp(self):
        # Compose only the fixture and helpers; inheriting its TestCase would
        # run every native integration test a second time during discovery.
        self.fixture = native_fixture.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def one_completed_unit(self):
        fixture = self.fixture
        pending = fixture.advance()
        fixture.write_files({"work": [pending["work"][0]]})
        protected = [Path(pending["work"][0]["facts"]), Path(pending["work"][0]["write_result"]),
                     archctx.last_path(fixture.directory)]
        saved = {path: path.read_bytes() for path in protected}
        missing = Path(pending["work"][1]["facts"])
        missing.unlink()  # Reconstructible cache in this disposable test run.
        return pending, saved, missing

    def test_ninth_changed_source_scope_reaches_agent_boundary(self):
        fixture = self.fixture
        fixture.files = ["a.py"]
        analyses = []
        for version in range(9):
            (fixture.repo / "a.py").write_bytes(f"value = {version}\n".encode("utf-8"))
            result = fixture.advance()
            self.assertEqual((result["status"], result["stage"]), ("NEEDS_AGENT", "source_understanding"))
            analyses.append(result["analysis_id"])
        self.assertEqual(len(set(analyses)), 9)
        runs = fixture.directory / "understand/runs"
        self.assertEqual(len(list(runs.iterdir())), 9)
        self.assertEqual((runs / analyses[0] / "source/a.py").read_bytes(), b"value = 0\n")
        self.assertEqual(fixture.calls["extract:a.py"], 9)
        self.assertEqual(archctx.last_path(fixture.directory).read_bytes(), fixture.last_good)

    def test_oversized_extraction_removes_pending_and_preserves_completed_unit(self):
        fixture = self.fixture
        pending, saved, missing = self.one_completed_unit()
        attempted = []

        def oversized(plugin, script, args, cwd):
            self.assertEqual(script, "extract-structure.mjs")
            self.assertEqual(understand.local_json(args[0])["batchFiles"][0]["path"], "b.py")
            attempted.append(args[1])
            args[1].write_bytes(b'{"padding":"' + b"x" * understand.GRAPH_BYTES + b'"}')
            return {"script": script, "exit_code": 0}

        with patch.object(understand, "run_upstream", side_effect=oversized):
            with self.assertRaisesRegex(ValueError, "exceeds .* bytes"):
                fixture.advance(pending)
        self.assertEqual(len(attempted), 1)
        self.assertTrue(attempted[0].name.endswith(".pending.json"))
        self.assertFalse(attempted[0].exists())
        self.assertFalse(missing.exists())
        self.assertFalse(list(missing.parent.glob("*.pending.json")))
        self.assertEqual(saved, {path: path.read_bytes() for path in saved})

    def test_next_extraction_capacity_blocks_provider_then_resumes_after_cache_release(self):
        fixture = self.fixture
        pending, saved, missing = self.one_completed_unit()
        run = fixture.directory / "understand/runs" / pending["analysis_id"]
        receipt = understand.local_json(run / "input.json")
        cache = run / "source/.ua/tmp/import-output.json"
        cache.write_bytes(b"x" * (2 * understand.GRAPH_BYTES))
        used = sum(info.st_size for info in storage.inventory(fixture.directory / "understand")[0].values())
        calls = fixture.calls.copy()
        with patch.object(storage, "CAPACITY_BYTES", used + understand.GRAPH_BYTES - 1):
            with archctx.refresh_lock(fixture.directory / "understand"), \
                    patch.object(understand, "run_upstream", side_effect=AssertionError("provider ran without extraction capacity")):
                with self.assertRaises(storage.CapacityError) as blocked:
                    understand.native_advance(fixture.config, None, fixture.directory, run / "input.json", receipt)
            self.assertEqual(blocked.exception.metrics["reserve_bytes"], understand.GRAPH_BYTES)
            self.assertEqual(saved, {path: path.read_bytes() for path in saved})
            self.assertFalse(missing.exists())
            self.assertFalse(list(missing.parent.glob("*.pending.json")))
            self.assertEqual(fixture.calls, calls)
            self.assertTrue(cache.exists())
            cache.unlink()  # Explicitly release only this synthetic mechanical cache.
            resumed = fixture.advance(pending)
        self.assertEqual((resumed["status"], resumed["stage"]), ("NEEDS_AGENT", "source_understanding"))
        self.assertEqual([work["file"] for work in resumed["work"]], ["b.py"])
        self.assertEqual(fixture.calls["extract:a.py"], calls["extract:a.py"])
        self.assertEqual(fixture.calls["extract:b.py"], calls["extract:b.py"] + 1)
        self.assertEqual(saved, {path: path.read_bytes() for path in saved})


if __name__ == "__main__":
    unittest.main()
