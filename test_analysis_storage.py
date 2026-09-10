"""Storage policy checks use disposable synthetic runs, never project state."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_analysis_storage as storage


class AnalysisStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()

    def run_snapshot(self, ident, *, complete=True):
        run = self.directory / "understand/runs" / ident
        for relative in ("source/a.py", "source/.git/config", "source/.ua/tmp/scan.json",
                         "source/.ua/tmp/ua-file-extract-results-0.json",
                         "source/.ua/tmp/agent-notes.json", "source/.ua/intermediate/batch-0.json",
                         "source/.ua/intermediate/layers.json", "source/.ua/intermediate/tour.json",
                         "source/.ua/intermediate/assembled-graph.json", "source/.ua/knowledge-graph.json",
                         "system.json", "mechanical.json"):
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"saved original bytes")
        (run / "graph.json").write_bytes(b"{}")
        receipt = {"analysis_id": ident, "graph_sha256": hashlib.sha256(b"{}").hexdigest()}
        archctx.atomic(run / "input.json", receipt)
        if complete:
            archctx.atomic(run / "completed.json", receipt)
        return run

    def used_bytes(self):
        return sum(info.st_size for info in storage.inventory(self.directory / "understand")[0].values())

    def test_ninth_and_later_runs_fit_by_actual_bytes(self):
        for index in range(12):
            storage.ensure_capacity(self.directory, 1024)
            self.run_snapshot(str(index), complete=False)
        result = storage.ensure_capacity(self.directory)
        self.assertEqual(result["used_bytes"], self.used_bytes())
        self.assertEqual(result["reclaimed_bytes"], 0)
        self.assertEqual(len(list((self.directory / "understand/runs").iterdir())), 12)

    def test_completed_cache_reclaimed_without_original_evidence(self):
        run = self.run_snapshot("done")
        protected_names = ("input.json", "completed.json", "graph.json", "source/a.py", "system.json",
                           "source/.ua/intermediate/batch-0.json", "source/.ua/intermediate/layers.json",
                           "source/.ua/intermediate/tour.json", "source/.ua/tmp/agent-notes.json")
        before = {name: (run / name).read_bytes() for name in protected_names}
        initial = self.used_bytes()
        files = storage.inventory(run)[0]
        cache_bytes = sum(info.st_size for path, info in files.items() if storage.mechanical(path, run))
        with patch.object(storage, "CAPACITY_BYTES", initial - cache_bytes + 10):
            result = storage.ensure_capacity(self.directory, 10)
        self.assertEqual(result["reclaimed_bytes"], cache_bytes)
        self.assertEqual(result["used_bytes"], initial - cache_bytes)
        self.assertEqual(before, {name: (run / name).read_bytes() for name in protected_names})
        self.assertFalse((run / "source/.git/config").exists())
        self.assertFalse((run / "source/.ua/tmp/scan.json").exists())
        self.assertTrue(run.is_dir())

    def test_insufficient_capacity_preserves_every_file_and_reports_bytes(self):
        self.run_snapshot("available")
        self.run_snapshot("referenced")
        self.run_snapshot("current")
        self.run_snapshot("active", complete=False)
        keep = self.run_snapshot("kept")
        (keep / ".keep").touch()
        archctx.atomic(self.directory / "understand/current.json", {"analysis_id": "current"})
        before = {path: path.read_bytes() for path in storage.inventory(self.directory / "understand")[0]}
        used = self.used_bytes()
        with patch.object(storage, "CAPACITY_BYTES", used), self.assertRaises(storage.CapacityError) as caught:
            storage.ensure_capacity(self.directory, used, {"referenced"})
        self.assertEqual(caught.exception.metrics["used_bytes"], used)
        self.assertEqual(set(caught.exception.metrics["protected_runs"]), {"referenced", "current", "active", "kept"})
        self.assertIn(f"used={used} bytes", str(caught.exception))
        self.assertEqual(before, {path: path.read_bytes() for path in storage.inventory(self.directory / "understand")[0]})

    def test_reclamation_skips_current_references_active_and_keep_markers(self):
        self.run_snapshot("available")
        protected = [self.run_snapshot("referenced"), self.run_snapshot("current"),
                     self.run_snapshot("active", complete=False), self.run_snapshot("kept")]
        (protected[-1] / "KEEP").touch()
        archctx.atomic(self.directory / "understand/current.json", {"analysis_id": "current"})
        before = {path: path.read_bytes() for run in protected for path in storage.inventory(run)[0]}
        with patch.object(storage, "CAPACITY_BYTES", self.used_bytes()):
            result = storage.ensure_capacity(self.directory, 1, {"referenced"})
        self.assertGreater(result["reclaimed_bytes"], 0)
        self.assertEqual(before, {path: path.read_bytes() for run in protected for path in storage.inventory(run)[0]})

    def test_legacy_and_unverified_completion_are_preserved(self):
        self.run_snapshot("legacy", complete=False)
        invalid = self.run_snapshot("unverified")
        (invalid / "graph.json").write_bytes(b"changed graph")
        with patch.object(storage, "CAPACITY_BYTES", self.used_bytes()), self.assertRaises(storage.CapacityError) as caught:
            storage.ensure_capacity(self.directory, 1)
        self.assertEqual(caught.exception.metrics["reclaimable_bytes"], 0)
        self.assertEqual(set(caught.exception.metrics["protected_runs"]), {"legacy", "unverified"})

    def test_linked_cache_and_storage_roots_are_never_followed(self):
        run = self.run_snapshot("linked")
        external = self.directory / "external"
        external.mkdir()
        target = external / "important.txt"
        target.write_bytes(b"user data outside analysis storage")
        link = run / "source/.git/outside"
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with patch.object(storage, "CAPACITY_BYTES", self.used_bytes()), self.assertRaises(storage.CapacityError):
            storage.ensure_capacity(self.directory, 1)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b"user data outside analysis storage")
        other = self.directory / "other-state"
        other.mkdir()
        (other / "understand").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root is linked"):
            storage.ensure_capacity(other)


if __name__ == "__main__":
    unittest.main()
