"""Agent entrypoint routing and cursor isolation; all state is disposable."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_understand as understand


class AnalysisCliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.state = self.root / "architecture.json", self.root / "state"
        (self.root / "source.py").write_bytes(b"value = 1\n")
        archctx.atomic(self.config, {"version": 1, "repo": ".", "components": [
            {"id": "entry", "evidence": [{"path": "source.py", "contains": "value"}]}]})

    def cli(self, *arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["archctx", "--config", str(self.config), "--state-dir", str(self.state), *arguments]), \
                contextlib.redirect_stdout(output):
            code = archctx.main()
        return code, json.loads(output.getvalue())

    def saved_files(self):
        return {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_show_routes_scope_and_files_without_starting_analysis(self):
        before = self.saved_files()
        with patch.object(understand, "discoveries", return_value={"status": "FINDINGS_READY"}) as query, \
                patch.object(understand, "native_understand", side_effect=AssertionError("read invoked analysis")):
            code, cli = self.cli("understand", "--show", "--analysis", "a" * 64, "--files", "source.py", "--details")
            self.assertEqual(code, 0)
            query.assert_called_once_with(self.config, str(self.state), details=True, analysis="a" * 64, files=["source.py"])
            self.assertEqual(archctx.mcp_value(self.config, str(self.state), "architecture_understand", {
                "show": True, "analysis": "a" * 64, "files": ["source.py"], "details": True}), cli)
        self.assertEqual(self.saved_files(), before)

    def test_active_files_keep_native_behavior_and_invalid_modes_do_not_execute(self):
        with patch.object(understand, "native_understand", return_value={"status": "NEEDS_AGENT"}) as native:
            self.assertEqual(self.cli("understand", "Why?", "--files", "source.py")[0], 0)
            native.assert_called_once_with(self.config, str(self.state), "Why?", ["source.py"], None, None)
            native.reset_mock()
            self.assertEqual(self.cli("understand", "--revise", "b" * 64, "--files", "source.py")[0], 0)
            native.assert_called_once_with(self.config, str(self.state), None, ["source.py"], None, "b" * 64)
            native.reset_mock()
            archctx.mcp_value(self.config, str(self.state), "architecture_understand", {"revise": "b" * 64, "files": ["source.py"]})
            native.assert_called_once_with(self.config, str(self.state), None, ["source.py"], None, "b" * 64)
            native.reset_mock()
            for arguments in (("--analysis", "a" * 64), ("--show", "Why?"), ("--show", "--resume", "a" * 64),
                              ("--show", "--revise", "a" * 64), ("--resume", "a" * 64, "--revise", "a" * 64)):
                with self.subTest(arguments=arguments):
                    self.assertEqual(self.cli("understand", *arguments)[0], 2)
            native.assert_not_called()

    def test_review_routes_explicit_scope_and_keeps_ambiguity_an_error(self):
        with patch.object(understand, "review", return_value={"status": "PASS"}) as review, \
                patch.object(archctx, "telemetry"), patch.object(archctx, "record_usage"):
            self.assertEqual(self.cli("accept", "ua:shared", "--bind", "component:entry", "--analysis", "a" * 64)[0], 0)
            review.assert_called_with(self.config, str(self.state), "ua:shared", bindings=["component:entry"], analysis="a" * 64)
            self.assertEqual(self.cli("reject", "ua:shared", "--reason", "false_match", "--analysis", "b" * 64)[0], 0)
            review.assert_called_with(self.config, str(self.state), "ua:shared", reason="false_match", analysis="b" * 64)
            archctx.mcp_value(self.config, str(self.state), "architecture_accept_candidate", {
                "id": "ua:shared", "bindings": ["component:entry"], "analysis": "b" * 64})
            review.assert_called_with(self.config, str(self.state), "ua:shared", bindings=["component:entry"], analysis="b" * 64)
            archctx.mcp_value(self.config, str(self.state), "architecture_reject_candidate", {
                "id": "ua:shared", "reason": "false_match", "analysis": "a" * 64})
            review.assert_called_with(self.config, str(self.state), "ua:shared", reason="false_match", analysis="a" * 64)
            review.side_effect = ValueError("analysis selection is ambiguous")
            code, result = self.cli("accept", "ua:shared", "--bind", "component:entry")
            self.assertEqual(code, 2)
            self.assertIn("ambiguous", result["error"])
            review.assert_called_with(self.config, str(self.state), "ua:shared", bindings=["component:entry"], analysis=None)
        with self.assertRaisesRegex(ValueError, "source-analysis"):
            archctx.accept_candidate(self.config, str(self.state), "candidate:plain", ["component:entry"], "a" * 64)

    def test_updates_cursor_is_bound_to_selection_and_cli_matches_mcp(self):
        before = self.saved_files()
        with patch.object(archctx, "analysis_receipt_identity", return_value="stable"), \
                patch.object(understand, "discoveries", return_value={"configured": True, "candidates": []}) as query:
            code, first = self.cli("updates", "--analysis", "a" * 64, "--files", "source.py", "other.py")
            self.assertEqual(code, 0)
            query.assert_called_with(self.config, str(self.state), analysis="a" * 64, files=["source.py", "other.py"])
            again = archctx.mcp_value(self.config, str(self.state), "architecture_updates", {
                "since": first["cursor"], "analysis": "a" * 64, "files": ["other.py", "source.py"]})
            self.assertFalse(again["changed"])
            for selected in ({"analysis": "b" * 64, "files": ["source.py", "other.py"]},
                             {"analysis": "a" * 64, "files": ["source.py"]}, {}):
                with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "another config/state/analysis/files"):
                    archctx.updates(self.config, str(self.state), first["cursor"], **selected)
            default = archctx.updates(self.config, str(self.state))
            expected = archctx.semantic({"config": str(self.config), "state": str(self.state)})
            self.assertEqual(default["cursor"].split(":")[1], expected)
        self.assertEqual(self.saved_files(), before)

    def test_catalog_identity_has_expanded_budget_and_legacy_fallback(self):
        archctx.atomic(understand.current_path(self.state), {"legacy": 1})
        legacy = archctx.analysis_receipt_identity(self.state)
        catalog = self.state / "understand/catalog.json"
        archctx.atomic(catalog, {"padding": "x" * (understand.SUMMARY_BYTES + 1), "scope": "a"})
        initial = archctx.analysis_receipt_identity(self.state)
        self.assertNotEqual(initial, legacy)
        archctx.atomic(understand.current_path(self.state), {"legacy": 2})
        self.assertEqual(archctx.analysis_receipt_identity(self.state), initial)
        archctx.atomic(catalog, {"padding": "x" * (understand.SUMMARY_BYTES + 1), "scope": "b"})
        self.assertNotEqual(archctx.analysis_receipt_identity(self.state), initial)

    def test_mcp_exposes_selection_and_rejects_invalid_input_types(self):
        tools = {tool["name"]: tool["inputSchema"]["properties"] for tool in archctx.mcp_tools()}
        for name in ("architecture_understand", "architecture_updates", "architecture_accept_candidate", "architecture_reject_candidate"):
            self.assertIn("analysis", tools[name])
        for name in ("architecture_understand", "architecture_updates"):
            self.assertIn("files", tools[name])
            for invalid in ({"analysis": 7}, {"files": "source.py"}, {"files": [7]}):
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                    archctx.mcp_value(self.config, str(self.state), name, invalid)


if __name__ == "__main__":
    unittest.main()
