"""Bounded capture checks using the real optional pinned provider."""
import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import archctx
import archctx_analysis_inputs as inputs


ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "architecture/.archctx/tools/understand/understand-anything-plugin"


class AnalysisInventoryTest(unittest.TestCase):
    def test_readonly_inventory_keeps_git_ignore_semantics_across_owner_tokens(self):
        with tempfile.TemporaryDirectory(prefix="analysis-owner-", dir=ROOT.parent) as temporary:
            repo = Path(temporary).resolve()
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            (repo / ".gitignore").write_text("local-note.txt\n")
            (repo / "source.py").write_text("VALUE = 1\n")
            (repo / "local-note.txt").write_text("ignored local data\n")
            config_before = (repo / ".git/config").read_bytes()
            expected = inputs.inventory(repo)
            with patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
                # CI may already trust a parent/wildcard; isolate the negative oracle.
                blocked = subprocess.run(["git", "-c", "safe.directory=", "-C", str(repo), "ls-files"], capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(b"dubious ownership", blocked.stderr)
                actual = inputs.inventory(repo)
            self.assertEqual(actual, expected)
            self.assertNotIn("local-note.txt", actual["paths"])
            self.assertEqual((repo / ".git/config").read_bytes(), config_before)

    @staticmethod
    def git_process(raw):
        process = Mock(stdout=io.BytesIO(raw), returncode=None)
        process.poll.side_effect = lambda: process.returncode
        process.kill.side_effect = lambda: setattr(process, "returncode", -9)

        def wait(timeout=None):
            if process.returncode is None:
                process.returncode = 0
            return process.returncode

        process.wait.side_effect = wait
        return process

    def test_git_inventory_stops_owned_process_at_name_and_byte_bounds(self):
        with tempfile.TemporaryDirectory(prefix="analysis-inventory-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            (repo / "selected.py").touch()
            for raw, names, byte_limit, expected in (
                    (b"a.py\0b.py\0c.py\0d.py\0", 2, 1024, ["a.py", "selected.py"]),
                    (b"a.py\0" + b"x" * 100, 20, 16, ["a.py", "selected.py"])):
                process = self.git_process(raw)
                reads = []
                stream = process.stdout
                stream.read1 = lambda size: reads.append(size) or io.BytesIO.read1(stream, size)
                with self.subTest(raw=raw), patch.object(inputs.subprocess, "Popen", return_value=process), \
                        patch.object(inputs.threading, "Timer") as timer, \
                        patch.object(inputs, "INVENTORY_LIMIT", names), patch.object(inputs, "INVENTORY_BYTES", byte_limit):
                    listing = inputs.inventory(repo, ["selected.py"])
                self.assertEqual(listing["paths"], expected)
                self.assertFalse(listing["coverage"]["complete"])
                self.assertGreaterEqual(listing["coverage"]["omitted_at_least"], 1)
                self.assertEqual(listing["coverage"]["method"], "git-index-and-untracked")
                self.assertLessEqual(max(reads), byte_limit + 1)
                process.kill.assert_called_once()
                timer.assert_called_once()
                self.assertEqual(timer.call_args.args[0], 30)
                timer.return_value.cancel.assert_called_once()

    def test_git_inventory_timeout_and_success_release_only_owned_process(self):
        successful = self.git_process(b"a.py\0b.py\0")
        with patch.object(inputs.subprocess, "Popen", return_value=successful) as spawn, patch.object(inputs.threading, "Timer"):
            self.assertEqual(inputs._git_inventory(ROOT), (["a.py", "b.py"], True))
        command = spawn.call_args.args[0]
        self.assertEqual(command[:5], ["git", "-c", "safe.directory=" + str(ROOT.resolve()), "-c", "core.fsmonitor=false"])
        self.assertEqual(spawn.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(spawn.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        successful.kill.assert_not_called()
        stalled = self.git_process(b"")
        timer = Mock()

        def timeout_timer(seconds, callback):
            self.assertEqual(seconds, 30)
            timer.start.side_effect = callback
            return timer

        with patch.object(inputs.subprocess, "Popen", return_value=stalled), \
                patch.object(inputs.threading, "Timer", side_effect=timeout_timer):
            self.assertIsNone(inputs._git_inventory(ROOT))
        stalled.kill.assert_called_once()
        timer.cancel.assert_called_once()
        timer.join.assert_called_once()

    def test_fallback_is_bounded_and_excludes_generated_directories(self):
        with tempfile.TemporaryDirectory(prefix="analysis-inventory-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
            (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
            (repo / "node_modules").mkdir()
            (repo / "node_modules/skip.js").write_text("ignored\n", encoding="utf-8")
            with patch.object(inputs.subprocess, "Popen", side_effect=OSError("fixture without Git")):
                listing = inputs.inventory(repo, ["b.py"])
                self.assertEqual(listing["paths"], ["a.py", "b.py"])
                self.assertEqual(listing["coverage"]["method"], "bounded-filesystem")
                self.assertEqual(inputs.verify_inventory(repo, ["b.py"]), listing["hash"])
                with patch.object(inputs, "INVENTORY_LIMIT", 1):
                    limited = inputs.inventory(repo, ["b.py"])
                    self.assertEqual(limited["paths"], ["b.py"])
                    self.assertFalse(limited["coverage"]["complete"])

    def test_fallback_marks_walk_errors_and_skips_windows_junctions(self):
        with tempfile.TemporaryDirectory(prefix="analysis-inventory-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            (repo / "source.py").touch()
            (repo / "junction").mkdir()
            directories = ["junction"]

            def walk(root, *, followlinks, onerror):
                self.assertFalse(followlinks)
                onerror(PermissionError("unreadable fixture directory"))
                yield str(root), directories, ["source.py"]

            original_lstat = Path.lstat

            def lstat(path):
                if path.name == "junction":
                    return Mock(st_mode=0, st_file_attributes=1024)
                return original_lstat(path)

            with patch.object(inputs, "_git_inventory", return_value=None), \
                    patch.object(inputs.os, "walk", side_effect=walk), patch.object(Path, "lstat", lstat):
                listing = inputs.inventory(repo)
            self.assertEqual(directories, [])
            self.assertEqual(listing["paths"], ["source.py"])
            self.assertFalse(listing["coverage"]["complete"])
            self.assertEqual(listing["coverage"]["unreadable_directories"], 1)


@unittest.skipUnless(shutil.which("node") and shutil.which("git") and (PLUGIN / "packages/core/dist/index.js").exists(),
                     "optional pinned Understand provider is not installed")
class AnalysisCaptureTest(unittest.TestCase):
    def test_capture_separates_evidence_from_selected_source_and_detects_concurrent_change(self):
        with tempfile.TemporaryDirectory(prefix="analysis-capture-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
            (repo / ".gitignore").write_text(".archctx/\n", encoding="utf-8")
            (repo / "a.py").write_text("value = 1\n", encoding="utf-8")
            dependency_bytes = (repo / "a.py").read_bytes()
            (repo / "b.py").write_text("import a\n", encoding="utf-8")
            (repo / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"baseUrl": "."}}), encoding="utf-8")
            (repo / "unused.py").write_text("not parsed\n", encoding="utf-8")
            state = repo / ".archctx"
            with inputs.capture(repo, state, PLUGIN, ["b.py"]) as snapshot:
                source = snapshot["source"]
                self.assertEqual(set(snapshot["source_hashes"]), {"b.py"})
                self.assertEqual(snapshot["scan"]["totalFiles"], 1)
                self.assertFalse((source / "a.py").exists())
                self.assertFalse((source / "unused.py").exists())
                self.assertTrue((source / "tsconfig.json").exists())
                self.assertEqual(snapshot["dependency_contents"], {"a.py": dependency_bytes})
                self.assertEqual(snapshot["dependency_hashes"]["a.py"], archctx.sha(dependency_bytes))
                self.assertEqual(snapshot["dependencies"]["b.py"]["coverage"], "resolved")
                self.assertEqual(snapshot["inventory_hash"], inputs.verify_inventory(repo, ["b.py"]))
                self.assertTrue(all(step["exit_code"] == 0 for step in snapshot["steps"]))
            self.assertFalse(source.exists())
            self.assertFalse((state / "understand/runs").exists())
            with self.assertRaisesRegex(ValueError, "evidence moved"):
                with inputs.capture(repo, state, PLUGIN, ["b.py"]):
                    (repo / "a.py").write_text("value = 2\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
