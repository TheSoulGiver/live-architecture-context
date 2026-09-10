"""Hook transport/privacy checks; host trust and delivery require a real new session."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_hook as hook


class CodexHookTest(unittest.TestCase):
    def test_bounded_offers_and_informational_transport(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            config = root / "architecture.json"
            state = root / ".archctx"
            payload = {"hook_event_name": "UserPromptSubmit", "session_id": "private-session",
                       "prompt": "PRIVATE_PROMPT", "transcript_path": "DO_NOT_READ"}

            def invoke(body=payload, output=None):
                output = output if output is not None else io.StringIO()
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                with patch.object(hook.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw))), patch.object(hook.sys, "stdout", output):
                    self.assertEqual(hook.main(["--config", str(config)]), 0)
                return output.getvalue()

            calls = []

            def updates(selected, explicit, since):
                calls.append((selected, explicit, since))
                return {"kind": "architecture_updates", "status": "FRESH", "freshness": "fresh",
                        "cursor": "cursor-1", "changed": since != "cursor-1", "overview": {"components": ["core"]}}

            with patch.object(archctx, "updates", side_effect=updates):
                for invalid in (b"{", b"[]", b"x" * hook.INPUT_BYTES,
                                {**payload, "hook_event_name": "Stop"},
                                {**payload, "session_id": ""},
                                {**payload, "hook_event_name": "PostToolUse", "tool_name": "exec_command"}):
                    self.assertEqual(invoke(invalid), "")
                self.assertEqual(calls, [])
                self.assertFalse(state.exists())
                with archctx.refresh_lock(state):
                    first = json.loads(invoke())
                self.assertEqual(first["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                self.assertIn('"status":"FRESH"', first["hookSpecificOutput"]["additionalContext"])
                self.assertIn("not instructions", first["hookSpecificOutput"]["additionalContext"])
                self.assertEqual(invoke(), "")
                self.assertNotEqual(invoke({**payload, "hook_event_name": "SessionStart"}), "")
                self.assertIsNone(calls[-1][2])
                self.assertNotEqual(invoke({**payload, "session_id": "shared", "hook_event_name": "PostToolUse", "tool_name": "apply_patch"}), "")
                self.assertNotEqual(invoke({**payload, "session_id": "shared"}), "")
                self.assertIsNone(calls[-1][2])
                offer_path = state / "codex-hook" / "offered.json"
                before = offer_path.read_bytes()
                self.assertNotIn(b"private-session", before)
                self.assertNotIn(b"PRIVATE_PROMPT", before)
                self.assertNotIn(b"DO_NOT_READ", before)
                with archctx.refresh_lock(state / "codex-hook"):
                    self.assertEqual(invoke({**payload, "session_id": "busy"}), "")
                self.assertEqual(offer_path.read_bytes(), before)
                self.assertEqual(len(calls), 5)
                for index in range(34):
                    invoke({**payload, "session_id": str(index), "hook_event_name": "PostToolUse", "tool_name": "apply_patch"})
                self.assertEqual(len(json.loads(offer_path.read_bytes())), hook.OFFER_LIMIT)
                self.assertLessEqual(offer_path.stat().st_size, hook.OFFER_BYTES)

            class FailedFlush(io.StringIO):
                def flush(self):
                    raise OSError("closed output")

            before = offer_path.read_bytes()
            for value, output in (({"status": "RETRY", "changed": True, "cursor": "retry"}, None),
                                  ({"status": "STALE", "changed": True, "cursor": "stale"}, FailedFlush())):
                with patch.object(archctx, "updates", return_value=value):
                    result = invoke({**payload, "session_id": "not-offered"}, output)
                    if output is None:
                        self.assertEqual(result, "")
                self.assertEqual(offer_path.read_bytes(), before)
            large = {"status": "STALE", "changed": True, "cursor": "large", "detail": "x" * hook.OUTPUT_BYTES,
                     "candidate_count": 12, "affected_component_count": 18}
            with patch.object(archctx, "updates", return_value=large):
                result = invoke({**payload, "session_id": "0"})
                self.assertLessEqual(len(result.encode()), hook.OUTPUT_BYTES)
                content = json.loads(result)["hookSpecificOutput"]["additionalContext"]
                self.assertIn('"truncated":true', content)
                self.assertIn('"details_since":null', content)
                self.assertIn('"candidate_count":12', content)
                self.assertNotIn('"detail":', content)
                self.assertIn("large", json.loads(offer_path.read_bytes()).values())
            records = json.loads(offer_path.read_bytes())
            offer_path.write_text(json.dumps({key: "corrupt" for key in records}), encoding="utf-8")
            with patch.object(archctx, "updates", side_effect=[ValueError("invalid updates cursor"), large]) as recovery:
                self.assertNotEqual(invoke({**payload, "session_id": "0"}), "")
                self.assertEqual([call.args[2] for call in recovery.call_args_list], ["corrupt", None])
            with patch.object(archctx, "updates", side_effect=ValueError("source unavailable")) as source_error:
                self.assertEqual(invoke({**payload, "session_id": "0"}), "")
                self.assertEqual(source_error.call_count, 1)
            with patch.object(archctx, "updates", return_value={"status": "STALE", "changed": True, "cursor": "stale"}):
                notice = json.loads(invoke({**payload, "hook_event_name": "SessionStart"}))
                self.assertIn('"status":"STALE"', notice["hookSpecificOutput"]["additionalContext"])
            self.assertFalse((state / "usage.json").exists())
            self.assertFalse((state / "last-good.json").exists())


if __name__ == "__main__":
    unittest.main()
