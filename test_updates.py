import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx


ROOT = Path(__file__).parent


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config, self.state = self.root / "context.json", self.root / "state"
        (self.root / "owner.py").write_text("OWNER = 1\n")
        self.definition = {
            "version": 1, "repo": ".",
            "components": [{"id": "owner", "purpose": "Own the example", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}],
            "watch": {"paths": ["*.py"]},
        }
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")

    def save(self):
        self.config.write_text(json.dumps(self.definition))

    def read(self, cursor=None):
        return archctx.updates(self.config, str(self.state), cursor)

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_read_only_single_observation_and_unchanged_cursor(self):
        before = self.files()
        with patch.object(archctx, "validate", wraps=archctx.validate) as validation, patch.object(archctx, "refresh", side_effect=AssertionError("read must not refresh")), patch.object(archctx, "run", side_effect=AssertionError("read must not invoke external commands")):
            first = self.read()
            self.assertEqual(validation.call_count, 1)
            again = self.read(first["cursor"])
        self.assertEqual(first["overview"][0]["id"], "owner")
        self.assertFalse(again["changed"])
        self.assertNotIn("accepted_delta", again)
        self.assertEqual(self.files(), before)
        (self.root / "unrelated.py").write_text("unrelated edit\n")
        self.assertFalse(self.read(first["cursor"])["changed"])

    def test_dirty_evidence_and_accepted_semantics_are_separate(self):
        first = self.read()
        (self.root / "owner.py").write_text("OWNER = 2\n")
        dirty = self.read(first["cursor"])
        self.assertEqual((dirty["status"], dirty["affected_components"]), ("STALE", ["owner"]))
        self.assertEqual(dirty["accepted_delta"]["changed_components"], [])
        self.assertFalse(self.read(dirty["cursor"])["changed"])
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
        refreshed = self.read(dirty["cursor"])
        self.assertEqual(refreshed["accepted_delta"]["evidence_changed_components"], ["owner"])
        self.assertEqual(refreshed["accepted_delta"]["changed_components"], [])
        self.definition["components"][0]["purpose"] = "Changed ownership description"
        self.definition["coverage"] = {"scope": "Bounded example", "limitations": ["Not runtime proof"]}
        self.save()
        archctx.refresh(self.config, str(self.state))
        semantic = self.read(refreshed["cursor"])
        self.assertEqual(semantic["accepted_delta"]["changed_components"], ["owner"])
        self.assertTrue(semantic["accepted_delta"]["coverage_changed"])

    def test_candidate_dedup_and_incomplete_observation_preserve_last_good(self):
        self.definition["drift_rules"] = [{"id": "provider", "paths": ["provider.py"], "added_contains": ["def send"]}]
        self.save()
        archctx.refresh(self.config, str(self.state), reset_candidate_baseline=True)
        first = self.read()
        accepted = (self.state / "last-good.json").read_bytes()
        (self.root / "provider.py").write_text("def send(): pass\n")
        candidate = self.read(first["cursor"])
        self.assertEqual((candidate["status"], candidate["candidate_count"]), ("STALE", 1))
        self.assertFalse(self.read(candidate["cursor"])["changed"])
        (self.root / "provider.py").write_text("# moved line\ndef send(): pass\n")
        self.assertFalse(self.read(candidate["cursor"])["changed"])
        with patch.object(archctx, "CANDIDATE_FILE_LIMIT", 0):
            incomplete = self.read(candidate["cursor"])
        self.assertEqual((incomplete["status"], incomplete["candidate_state"]), ("STALE", "incomplete"))
        self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)

    def test_missing_retained_baseline_and_cursor_validation(self):
        first = self.read()
        previous_hash = first["accepted_blueprint"]["context_hash"]
        self.definition["components"][0]["name"] = "Changed name"
        self.save()
        archctx.refresh(self.config, str(self.state))
        (self.state / "snapshots" / f"{previous_hash}.json").unlink()
        result = self.read(first["cursor"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["accepted_delta"]["baseline"], "unavailable")
        self.assertNotIn("changed_components", result["accepted_delta"])
        for bad in ("../state", "u1:" + "a" * 500, 12):
            with self.subTest(cursor=bad), self.assertRaisesRegex(ValueError, "invalid updates cursor"):
                self.read(bad)
        with self.assertRaisesRegex(ValueError, "another config/state"):
            archctx.updates(self.config, str(self.root / "other-state"), first["cursor"])

    def test_config_only_change_names_owner_and_missing_config_preserves_identity(self):
        first = self.read()
        self.definition["components"][0]["purpose"] = "New declared responsibility"
        self.save()
        changed = self.read(first["cursor"])
        self.assertEqual(changed["affected_components"], ["owner"])
        self.assertEqual(changed["accepted_delta"]["changed_components"], [])
        self.assertTrue(changed["reason"])
        self.config.unlink()
        missing = self.read(changed["cursor"])
        self.assertEqual(missing["status"], "STALE")
        self.assertEqual(missing["accepted_blueprint"]["context_hash"], first["accepted_blueprint"]["context_hash"])
        self.assertIn("unavailable", missing["reason"][0])

    def test_read_race_retries_without_advancing_cursor(self):
        first = self.read()
        real_status = archctx.status
        def changed_after_read(*args, **kwargs):
            value = real_status(*args, **kwargs)
            record = json.loads((self.state / "last-good.json").read_text())
            record["created_at"] = "changed during read"
            (self.state / "last-good.json").write_text(json.dumps(record))
            return value
        with patch.object(archctx, "status", side_effect=changed_after_read):
            retry = self.read(first["cursor"])
        self.assertEqual((retry["status"], retry["cursor"]), ("RETRY", first["cursor"]))

    def test_view_only_change_has_no_accepted_semantic_delta(self):
        view = self.root / "view.json"
        view.write_text('{"layout":"left"}')
        self.definition["archify"] = {"view": "view.json", "output": "state/view.json", "validate": [sys.executable, "-c", "pass"]}
        self.save()
        # A retained receipt is sufficient: updates never validates or renders a view.
        first = self.read()
        view.write_text('{"layout":"right"}')
        changed = self.read(first["cursor"])
        self.assertTrue(changed["view_changed"])
        self.assertEqual(changed["accepted_delta"]["changed_components"], [])
        self.assertEqual(changed["affected_components"], [])
        self.assertFalse(self.read(changed["cursor"])["changed"])
        real_status = archctx.status
        def moved_view(*args, **kwargs):
            value = real_status(*args, **kwargs)
            view.write_text('{"layout":"raced"}')
            return value
        with patch.object(archctx, "status", side_effect=moved_view):
            retry = self.read(changed["cursor"])
        self.assertEqual((retry["status"], retry["cursor"]), ("RETRY", changed["cursor"]))

    def test_oversized_notices_are_explicitly_bounded(self):
        first = self.read()
        for number in range(12):
            self.definition["components"].append({"id": str(number) + "很长" * 1000, "evidence": [{"path": "owner.py", "contains": "OWNER"}]})
        self.save()
        archctx.refresh(self.config, str(self.state))
        result = self.read(first["cursor"])
        self.assertTrue(result["truncated_strings"])
        self.assertTrue(result["more_available"])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()), archctx.UPDATES_BYTES)
        self.assertEqual(result["affected_component_count"], 12)
        self.assertGreater(result["omitted_affected_components"], 0)

    def test_relation_retarget_keeps_both_old_and_new_endpoints(self):
        for name in ("before", "after"):
            self.definition["components"].append({"id": name, "evidence": [{"path": "owner.py", "contains": "OWNER"}]})
        self.definition["relations"] = [{"id": "selected-target", "from": "owner", "to": "before", "kind": "uses"}]
        self.save()
        archctx.refresh(self.config, str(self.state))
        first = self.read()
        self.definition["relations"][0]["to"] = "after"
        self.save()
        self.assertEqual(set(self.read(first["cursor"])["affected_components"]), {"owner", "before", "after"})
        archctx.refresh(self.config, str(self.state))
        self.assertEqual(set(self.read(first["cursor"])["affected_components"]), {"owner", "before", "after"})

    def test_cli_mcp_are_read_only_and_lists_report_omissions(self):
        for number in range(10):
            self.definition["components"].append({"id": f"owner-{number}", "evidence": [{"path": "owner.py", "contains": "OWNER"}]})
        self.save()
        archctx.refresh(self.config, str(self.state))
        first = self.read()
        self.assertEqual((len(first["overview"]), first["component_count"], first["omitted_overview_components"]), (8, 11, 3))
        (self.root / "owner.py").write_text("OWNER = 3\n")
        before = self.files()
        command = [sys.executable, str(ROOT / "archctx.py"), "--config", str(self.config), "--state-dir", str(self.state)]
        cli = json.loads(subprocess.run(command + ["updates", "--since", first["cursor"]], capture_output=True, text=True, check=True).stdout)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "architecture_updates", "arguments": {"since": first["cursor"]}}}
        response = subprocess.run(command + ["mcp"], input=json.dumps(request) + "\n", capture_output=True, text=True, check=True)
        mcp = json.loads(json.loads(response.stdout)["result"]["content"][0]["text"])
        self.assertEqual(cli, mcp)
        self.assertEqual((len(cli["affected_components"]), cli["affected_component_count"], cli["omitted_affected_components"]), (8, 11, 3))
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
