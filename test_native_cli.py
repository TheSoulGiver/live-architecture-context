"""The native entrypoint routes existing owners, without hidden work."""
import contextlib
import io
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import archctx


class NativeCliTest(unittest.TestCase):
    def test_unique_config_and_read_only_query(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            repo = Path(tmp)
            config = repo / "architecture/architecture.json"
            archctx.atomic(config, {"version": 1, "repo": "..", "components": [], "relations": []})
            self.assertEqual(archctx.native_config(repo, None), config)
            with patch("archctx_understand.native_understand") as work, patch("archctx_understand.discoveries", return_value={"status": "MISSING"}) as query:
                self.assertEqual(archctx.mcp_value(config, None, "architecture_understand", {"show": True})["status"], "MISSING")
                work.assert_not_called()
                query.assert_called_once_with(config, None, details=False, analysis=None, files=None)
                archctx.understand(config, None, {"show": True, "files": ["a.py"]})
                query.assert_called_with(config, None, details=False, analysis=None, files=["a.py"])
                with self.assertRaisesRegex(ValueError, "read-only"):
                    archctx.understand(config, None, {"show": True, "question": "must not start work"})
            self.assertFalse((config.parent / ".archctx").exists())
            archctx.atomic(repo / ".archctx/architecture.json", {})
            with self.assertRaisesRegex(ValueError, "multiple"):
                archctx.native_config(repo, None)
            self.assertEqual(archctx.native_config(repo, str(config)), config)

    def test_map_defaults_live_and_reuses_existing_viewer(self):
        config = Path(__file__).parent / "architecture/architecture.json"
        for flag, live in (([], True), (["--read-only"], False)):
            with self.subTest(live=live), patch("sys.argv", ["archctx", "--config", str(config), "map", "--no-open", *flag]), patch("archctx_blueprint.main", return_value=0) as viewer:
                self.assertEqual(archctx.main(), 0)
                options = viewer.call_args.args[0]
                self.assertEqual("--live" in options, live)
                self.assertIn("--no-open", options)

    def test_explicit_setup_installs_guidance_without_refresh(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            repo = Path(tmp); config = repo / "architecture/architecture.json"
            archctx.atomic(config, {"version": 1, "repo": "..", "components": [], "relations": []})
            with patch("sys.argv", ["archctx", "--config", str(config), "setup"]), patch("archctx_runtime.setup", return_value={"status": "READY"}) as setup, patch("archctx.refresh") as refresh, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(archctx.main(), 0)
                setup.assert_called_once_with(config, None, None, None)
                refresh.assert_not_called()
            self.assertIn("understand", (repo / "AGENTS.md").read_text())
            self.assertFalse((config.parent / ".archctx/last-good.json").exists())

    def test_mcp_analysis_failure_does_not_close_other_tools(self):
        import json
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "architecture_understand", "arguments": {"files": ["a.py"]}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            config = Path(tmp) / "architecture.json"
            for error in (subprocess.TimeoutExpired("synthetic extraction", 1), KeyError("input_hash")):
                output = io.StringIO()
                with patch("sys.stdin", io.StringIO("\n".join(map(json.dumps, requests)))), contextlib.redirect_stdout(output), patch("archctx_understand.native_understand", side_effect=error):
                    self.assertEqual(archctx.serve_mcp(config, None), 0)
                replies = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(len(replies), 2)
                self.assertTrue(replies[0]["result"]["isError"])
                self.assertIn("tools", replies[1]["result"])

    def test_setup_busy_is_a_recoverable_response(self):
        import json
        output = io.StringIO()
        with patch("sys.argv", ["archctx", "setup"]), patch("archctx_runtime.setup", side_effect=archctx.RefreshBusyError("synthetic busy")), contextlib.redirect_stdout(output):
            self.assertEqual(archctx.main(), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "RETRY")


if __name__ == "__main__": unittest.main()
