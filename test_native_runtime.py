"""Local setup mechanics with synthetic components; no network/provider installation."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_runtime as runtime
import archctx_understand as understand


class NativeRuntimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.config = self.repo / "architecture/architecture.json"
        self.state = self.repo / "architecture/.archctx"

    def git(self, *args):
        return subprocess.run([runtime.executable("git"), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    @staticmethod
    def ready(path, analysis):
        entry = path / ("understand-anything-plugin/packages/core/dist/index.js" if analysis else "archify/bin/archify.mjs")
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("// synthetic component\n", encoding="utf-8")

    def prepare(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        archctx.atomic(self.config, {"version": 1, "repo": "..", "components": [], "relations": []})

    def setup_ready(self, **kwargs):
        def component(path, revision, repository, install=False):
            if install:
                self.ready(path, revision == understand.PROVIDER_REVISION)
            return path

        with patch.object(runtime.Path, "cwd", return_value=self.repo), \
             patch.object(runtime, "node_runtime", return_value="node"), \
             patch.object(runtime, "checkout", side_effect=component) as checkouts, \
             patch.object(understand, "provider_root", side_effect=lambda path: path), \
             patch.object(runtime, "execute", side_effect=AssertionError("ready component ran installation")):
            result = runtime.setup(self.config, str(self.state), **kwargs)
        return result, checkouts.call_args_list

    def test_initial_setup_wires_empty_declarations_and_never_refreshes(self):
        with patch.object(archctx, "refresh", side_effect=AssertionError("setup refreshed architecture")):
            result, calls = self.setup_ready()
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["architecture"], "draft_unreviewed")
        config = archctx.load(self.config)
        self.assertEqual((config["repo"], config["components"], config["relations"]), ("..", [], []))
        self.assertEqual(archctx.load(self.config.with_name("architecture.view.json"))["nodes"], [])
        self.assertEqual(config["archify"]["validate"][:4], ["{python}", "{lac_runtime}", "--state", "{state}"])
        self.assertTrue(all(call.kwargs["install"] for call in calls))
        self.assertFalse(archctx.last_path(self.state).exists())
        with patch.object(runtime, "execute", side_effect=AssertionError("query executed component")), \
             patch.object(runtime, "checkout", side_effect=AssertionError("query installed component")):
            self.assertEqual(runtime.roots(self.state)["analysis"], self.state / "tools/understand")

    def test_external_ready_components_and_custom_archify_are_not_modified(self):
        self.prepare()
        config = archctx.load(self.config)
        config["archify"] = {"custom": "keep caller-owned command configuration"}
        archctx.atomic(self.config, config)
        original = self.config.read_bytes()
        analysis, renderer = self.repo / "external-analysis", self.repo / "external-renderer"
        self.ready(analysis, True)
        self.ready(renderer, False)
        external_before = {str(path): path.read_bytes() for home in (analysis, renderer) for path in home.rglob("*") if path.is_file()}
        result, calls = self.setup_ready(analysis_home=str(analysis), renderer_home=str(renderer))
        self.assertEqual(result["status"], "READY")
        self.assertFalse(any(call.kwargs["install"] for call in calls))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(external_before, {str(path): path.read_bytes() for home in (analysis, renderer) for path in home.rglob("*") if path.is_file()})
        self.assertFalse(self.config.with_name("architecture.view.json").exists())

    def test_node_and_writer_busy_stop_before_setup_mutations(self):
        self.prepare()
        with patch.object(runtime, "executable", return_value="node"), \
             patch.object(runtime, "execute", return_value="v18.20.0"), \
             patch.object(runtime, "checkout", side_effect=AssertionError("old Node entered install")):
            with self.assertRaisesRegex(ValueError, "Node.js 20"):
                runtime.setup(self.config, str(self.state))
        self.assertFalse(self.state.exists())
        with archctx.refresh_lock(self.state), patch.object(runtime, "node_runtime", return_value="node"), \
             patch.object(runtime, "checkout", side_effect=AssertionError("busy writer entered install")):
            with self.assertRaises(archctx.RefreshBusyError):
                runtime.setup(self.config, str(self.state))
        self.assertFalse((self.state / "tools").exists())

    def test_windows_npm_resolves_cmd_instead_of_posix_shim(self):
        with patch.object(runtime.os, "name", "nt"), patch.object(runtime.shutil, "which", return_value="C:/node/npm.cmd") as which:
            self.assertEqual(runtime.executable("npm"), "C:/node/npm.cmd")
        which.assert_called_once_with("npm.cmd")

    def test_failed_setup_ignores_state_and_rejects_repo_root_before_download(self):
        self.prepare()
        with patch.object(runtime, "node_runtime", return_value="node"), patch.object(runtime, "checkout", side_effect=ValueError("synthetic download interruption")):
            with self.assertRaisesRegex(ValueError, "download interruption"):
                runtime.setup(self.config, str(self.state))
        self.assertIn(".archctx/", (self.repo / ".gitignore").read_text())
        self.assertFalse(archctx.last_path(self.state).exists())
        with patch.object(runtime, "checkout", side_effect=AssertionError("unsafe state entered download")):
            with self.assertRaisesRegex(ValueError, "dedicated directory"):
                runtime.setup(self.config, str(self.repo))

    def test_managed_build_uses_pinned_project_package_manager(self):
        self.prepare()
        npm = self.repo / "node" / ("npm.cmd" if os.name == "nt" else "npm")
        npm_script = npm.parent / "node_modules/npm/bin/npm-cli.js"
        npm_script.parent.mkdir(parents=True)
        npm_script.write_text("// synthetic npm", encoding="utf-8")
        calls = []

        def component(path, revision, repository, install=False):
            path.mkdir(parents=True)
            if revision == runtime.ARCHIFY_REVISION:
                self.ready(path, False)
            return path

        def execute(argv, cwd, **kwargs):
            calls.append(argv)
            if "--prefix" in argv:
                pnpm = Path(argv[argv.index("--prefix") + 1]) / "node_modules/pnpm/bin/pnpm.cjs"
                pnpm.parent.mkdir(parents=True)
                pnpm.write_text("// synthetic pnpm", encoding="utf-8")
            if argv[-1] == "build":
                self.ready(self.state / "tools/understand", True)
            return runtime.PNPM_VERSION if argv[-1] == "--version" else ""

        with patch.object(runtime, "node_runtime", return_value="node"), \
             patch.object(runtime, "executable", return_value=str(npm)), \
             patch.object(runtime, "checkout", side_effect=component), \
             patch.object(runtime, "execute", side_effect=execute), \
             patch.object(understand, "provider_root", side_effect=lambda path: path):
            self.assertEqual(runtime.setup(self.config, str(self.state))["status"], "READY")
        self.assertEqual(calls[0][:2] if os.name == "nt" else calls[0][:1], ["node", str(npm_script)] if os.name == "nt" else [str(npm)])
        self.assertIn("pnpm@" + runtime.PNPM_VERSION, calls[0])
        self.assertIn("--frozen-lockfile", calls[2])
        self.assertIn(str(self.state / "tools/store"), calls[2])

    def test_checkout_resumes_interrupted_fetch_and_no_checkout_without_resetting_edits(self):
        source = self.repo / "source"
        source.mkdir()
        self.git("init", "--quiet", str(source))
        (source / "component.txt").write_text("pinned component\n", encoding="utf-8")
        self.git("-C", str(source), "add", "component.txt")
        self.git("-C", str(source), "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
        revision = self.git("-C", str(source), "rev-parse", "HEAD")
        target = self.repo / "managed"
        execute = runtime.execute
        interrupted = False

        def interrupt_fetch(argv, cwd, **kwargs):
            nonlocal interrupted
            if "fetch" in argv and not interrupted:
                interrupted = True
                raise ValueError("synthetic interrupted fetch")
            return execute(argv, cwd, **kwargs)

        with patch.object(runtime, "execute", side_effect=interrupt_fetch):
            with self.assertRaisesRegex(ValueError, "interrupted fetch"):
                runtime.checkout(target, revision, str(source), install=True)
        self.assertTrue((self.repo / ".managed.install.json").exists())
        self.assertEqual(runtime.checkout(target, revision, str(source), install=True), target)
        self.assertEqual((target / "component.txt").read_text(encoding="utf-8"), "pinned component\n")
        self.assertFalse((self.repo / ".managed.install.json").exists())
        (target / "component.txt").write_text("user edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "tracked edits"):
            runtime.checkout(target, revision, str(source), install=True)
        self.assertEqual((target / "component.txt").read_text(encoding="utf-8"), "user edit\n")
        cloned = self.repo / "no-checkout"
        self.git("clone", "--no-checkout", str(source), str(cloned))
        self.assertEqual(runtime.checkout(cloned, revision, str(source), install=True), cloned)
        self.assertTrue((cloned / "component.txt").is_file())
        partial = self.repo / "partial-clone"
        (partial / ".git/objects").mkdir(parents=True)
        self.assertEqual(runtime.checkout(partial, revision, str(source), install=True), partial)
        self.assertTrue((partial / "component.txt").is_file())

    def test_failed_checkout_preserves_unknown_worktree_content(self):
        target = self.repo / "managed"
        target.mkdir()
        unknown = target / "notes.txt"
        unknown.write_text("caller data", encoding="utf-8")
        archctx.atomic(self.repo / ".managed.install.json", {"repository": "synthetic", "revision": "a" * 40})
        with patch.object(runtime, "execute", side_effect=AssertionError("unknown files reached Git mutation")):
            with self.assertRaisesRegex(ValueError, "unknown files"):
                runtime.checkout(target, "a" * 40, "synthetic", install=True)
        self.assertEqual(unknown.read_text(encoding="utf-8"), "caller data")

    def test_legacy_setup_creates_explicit_archify_home_and_reuses_existing_home(self):
        target = self.repo / "explicit-renderer"
        with patch.dict(os.environ, {"ARCHIFY_HOME": str(target)}), \
             patch.object(runtime, "node_runtime", return_value="node"), \
             patch.object(runtime, "checkout", return_value=target) as checkout:
            self.assertEqual(runtime.archify_main(self.state, ["setup"]), 0)
            self.assertTrue(checkout.call_args.kwargs["install"])
            target.mkdir()
            self.assertEqual(runtime.archify_main(self.state, ["setup"]), 0)
            self.assertFalse(checkout.call_args.kwargs["install"])

    def test_execute_retains_bounded_tail_and_reports_failure_and_timeout(self):
        output = runtime.execute([sys.executable, "-c", "print('x' * 100000 + 'tail')"], self.repo)
        self.assertLessEqual(len(output), 16384)
        self.assertTrue(output.endswith("tail"))
        with self.assertRaisesRegex(ValueError, r"failed \(3\).*tail") as caught:
            runtime.execute([sys.executable, "-c", "import sys; print('x' * 100000 + 'tail'); sys.exit(3)"], self.repo)
        self.assertLess(len(str(caught.exception)), 1700)
        with self.assertRaisesRegex(ValueError, "timed out"):
            runtime.execute([sys.executable, "-c", "import time; time.sleep(30)"], self.repo, timeout=0.1)


if __name__ == "__main__":
    unittest.main()
