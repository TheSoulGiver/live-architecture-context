import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx


ROOT = Path(__file__).parent
TOOL = ROOT / "archctx.py"
PYTHON = sys.executable


def run(config, state, *args):
    result = subprocess.run([PYTHON, str(TOOL), "--config", str(config), "--state-dir", str(state), *args], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def run_raw(*args):
    result = subprocess.run([PYTHON, str(TOOL), *args], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class ArchitectureContextTest(unittest.TestCase):
    @staticmethod
    def receipt_program(fresh=True):
        return "\n".join((
            "import json, sys",
            "mode, context_hash, revision = sys.argv[1:4]",
            f"receipt = {{'receipt_version': 1, 'provider': 'test', 'context_hash': context_hash, 'revision': revision, 'mode': mode, 'fresh': {fresh!r}, 'graph_revision': 'graph-1'}}",
            "if len(sys.argv) > 4:",
            "    receipt['changed_files_sha256'] = sys.argv[4]",
            "    receipt['changed_file_count'] = len(json.loads(sys.argv[5]))",
            "print(json.dumps(receipt))",
        ))

    def test_watcher_skips_unrelated_and_marks_evidence_owner_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "owner.py").write_text("OWNER = 'one'\n"); (root / "other.py").write_text("other\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}], "watch": {"paths": ["*.py"]}}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            self.assertEqual(run(config, state, "watch", "--once")["status"], "WATCH_READY")
            (root / "other.py").write_text("changed unrelated\n")
            self.assertEqual(run(config, state, "watch", "--once")["status"], "NO_RELEVANT_CHANGE")
            (root / "owner.py").write_text("OWNER = 'two'\n")
            result = run(config, state, "watch", "--once")
            self.assertEqual(result["status"], "STALE")
            self.assertEqual(result["direct_components"], ["owner"])
            self.assertEqual(result["event"], "CANONICAL_EVIDENCE_CHANGED")
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            (root / "owner.py").rename(root / "owner-renamed.py")
            result = run(config, state, "watch", "--once")
            self.assertEqual(result["status"], "STALE")
            failed = run(config, state, "refresh")
            self.assertEqual(failed["status"], "INVALID")
            self.assertTrue(failed["last_good_preserved"])

    def test_agent_friendly_query_aliases_preserve_compact_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "store.py"; source.write_text("class Store: pass\n"); (root / "service.py").write_text("def serve(): pass\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "store", "evidence": [{"path": "store.py", "contains": "class Store"}]}, {"id": "service", "evidence": [{"path": "service.py", "contains": "def serve"}]}], "relations": [{"from": "service", "to": "store", "kind": "reads"}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            self.assertEqual(run(config, state, "search", "store")["matches"][0]["id"], "store")
            self.assertEqual(run(config, state, "search", "--query", "store")["matches"][0]["id"], "store")
            self.assertEqual(run(config, state, "canonical", "--component", "store")["canonical"]["id"], "store")
            self.assertEqual(run(config, state, "evidence", "--component", "store")["evidence"][0]["path"], "store.py")
            traced = run(config, state, "trace", "--from", "service", "--to", "store")
            self.assertEqual((traced["target_reachable"], traced["target_direct"]), (True, True))

    def test_watcher_never_labels_a_full_graph_refresh_incremental(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "owner.py").write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}], "watch": {"paths": ["*.py"]}, "code_graph": {"refresh": [sys.executable, "-c", "print('indexed')"]}}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            run(config, state, "watch", "--once")
            (root / "owner.py").write_text("OWNER = 'two'\n")
            watched = run(config, state, "watch", "--once")
            self.assertEqual(watched["status"], "STALE")
            self.assertEqual(watched["graph"]["freshness"], "unverified")
            refreshed = run(config, state, "refresh")["graph"]
            self.assertEqual((refreshed["freshness"], refreshed["confidence"], refreshed["execution"]["requested_mode"]), ("unverified", "provider_unverified", "full"))

    def test_apply_watcher_promotes_declared_context_and_keeps_visual_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "src" / "runtime.py"; source.parent.mkdir(); source.write_text("def run(): return 'one'\n")
            config, state, view, output = root / "architecture.json", root / "state", root / "architecture.view.json", root / "architecture.archify.json"
            view.write_text(json.dumps({"title": "Runtime", "nodes": [{"id": "runtime", "pos": [0, 0]}]}))
            validator = [PYTHON, "-c", "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))", "{archify_output}"]
            value = {"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "src/runtime.py", "contains": "def run"}]}], "watch": {"paths": ["src/*.py"]}, "code_graph": {"provider": "test", "refresh": [PYTHON, "-c", "print('full')"], "incremental": [PYTHON, "-c", "print('incremental')"]}, "archify": {"view": view.name, "output": output.name, "validate": validator}}
            config.write_text(json.dumps(value)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            self.assertTrue(output.is_file()); self.assertEqual(archctx.watch_once(config, str(state), True)["status"], "WATCH_READY")
            source.write_text("def run(): return 'two'\n")
            promoted = archctx.watch_once(config, str(state), True)
            self.assertEqual((promoted["status"], promoted["event"], promoted["graph"]["freshness"], promoted["archify"]["validation"]), ("PASS", "ARCHITECTURE_CONTEXT_REFRESHED", "unverified", "PASS"))
            self.assertEqual(promoted["graph"]["execution"]["requested_mode"], "incremental")
            self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")
            self.assertEqual(archctx.impact(config, str(state), None, ["src/runtime.py"])["next_action"], "inspect affected evidence and test")
            view.write_text(json.dumps({"title": "Runtime v2", "nodes": [{"id": "runtime", "pos": [0, 0]}]}))
            visual = archctx.watch_once(config, str(state), True)
            self.assertEqual((visual["status"], visual["graph"]["freshness"], visual["graph"]["execution"]["requested_mode"]), ("PASS", "unverified", "not_run"))
            last_visual = output.read_bytes()
            value["archify"]["validate"] = [PYTHON, "-c", "raise SystemExit(3)"]; config.write_text(json.dumps(value))
            blocked = archctx.watch_once(config, str(state), True)
            self.assertEqual((blocked["status"], blocked["event"]), ("INVALID", "ARCHITECTURE_REFRESH_FAILED"))
            self.assertTrue(blocked["last_good_preserved"]); self.assertEqual(output.read_bytes(), last_visual)

    def test_graph_receipt_binds_verified_context_and_never_persists_command_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}], "code_graph": {"provider": "test", "receipt": "stdout_json_v1", "refresh": [PYTHON, "-c", self.receipt_program(), "full", "{context_hash}", "{revision}"]}}
            config.write_text(json.dumps(value))
            refreshed = run(config, state, "refresh")
            graph = refreshed["graph"]
            self.assertEqual((refreshed["status"], graph["freshness"], graph["receipt"]["mode"]), ("PASS", "receipt_verified", "full"))
            self.assertEqual(graph["receipt"]["context_hash"], refreshed["context_hash"])
            stored = run(config, state, "snapshot")["graph"]
            self.assertNotIn("command", stored); self.assertNotIn("stdout_tail", stored)
            self.assertEqual(run(config, state, "status")["graph"]["receipt"]["graph_revision"], "graph-1")

    def test_graph_receipt_reports_actual_mode_and_fails_closed_when_not_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            program = self.receipt_program()
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}], "watch": {"paths": ["source.py"]}, "code_graph": {"provider": "test", "receipt": "stdout_json_v1", "refresh": [PYTHON, "-c", program, "full", "{context_hash}", "{revision}"], "incremental": [PYTHON, "-c", program, "full", "{context_hash}", "{revision}", "{changed_files_sha256}", "{changed_files}"]}}
            config.write_text(json.dumps(value)); run(config, state, "refresh")
            self.assertEqual(run(config, state, "watch", "--once")["status"], "WATCH_READY")
            source.write_text("OWNER = 'two'\n")
            promoted = run(config, state, "watch", "--once", "--apply")
            self.assertEqual((promoted["status"], promoted["graph"]["execution"]["requested_mode"], promoted["graph"]["execution"]["actual_mode"]), ("PASS", "incremental", "full"))
            last_good_hash = promoted["context_hash"]
            value["code_graph"]["refresh"] = [PYTHON, "-c", self.receipt_program(False), "full", "{context_hash}", "{revision}"]
            config.write_text(json.dumps(value))
            failed = run(config, state, "refresh")
            self.assertEqual((failed["status"], failed["freshness"], failed["last_good_preserved"]), ("INVALID", "stale", True))
            self.assertEqual(run(config, state, "status")["last_good_context_hash"], last_good_hash)

    def test_graph_contract_change_forces_full_refresh_on_control_only_watch_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER\n")
            config, state = root / "context.json", root / "state"; program = self.receipt_program()
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}], "code_graph": {"provider": "test", "receipt": "stdout_json_v1", "refresh": [PYTHON, "-c", program, "full", "{context_hash}", "{revision}"]}}
            config.write_text(json.dumps(value)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            self.assertEqual(run(config, state, "watch", "--once")["status"], "WATCH_READY")
            value["code_graph"]["timeout_seconds"] = 179
            config.write_text(json.dumps(value))
            refreshed = run(config, state, "watch", "--once", "--apply")
            self.assertEqual((refreshed["status"], refreshed["graph"]["execution"]["requested_mode"], refreshed["graph"]["receipt"]["mode"]), ("PASS", "full", "full"))

    def test_watch_scope_cap_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "a.py").write_text("A\n"); (root / "b.py").write_text("B\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "a", "evidence": [{"path": "a.py", "contains": "A"}]}], "watch": {"paths": ["*.py"]}}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            with patch.object(archctx, "WATCH_FILE_LIMIT", 1):
                failed = archctx.watch_once(config, str(state))
            self.assertEqual(failed["status"], "INVALID"); self.assertTrue(failed["last_good_preserved"])

    def test_continuous_watcher_does_not_emit_no_relevant_change(self):
        values = [{"status": "WATCH_READY"}, {"status": "NO_RELEVANT_CHANGE"}, {"status": "PASS"}]
        with patch.object(archctx, "watch_once", side_effect=values), patch.object(archctx, "dump") as output, patch.object(archctx, "record_usage") as receipt, patch.object(archctx.time, "sleep"):
            self.assertEqual(archctx.watch(Path("context.json"), None, 50, 1), 0)
        self.assertEqual([call.args[0]["status"] for call in output.call_args_list], ["WATCH_READY", "PASS"])
        self.assertEqual([call.args[1] for call in receipt.call_args_list], ["watch"])

    def test_watcher_does_not_rewrite_unchanged_live_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "owner.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}]}))
            with patch.object(archctx, "atomic", wraps=archctx.atomic) as persist:
                self.assertEqual(archctx.watch_once(config, str(state))["status"], "WATCH_READY")
                persist.reset_mock()
                self.assertEqual(archctx.watch_once(config, str(state))["status"], "NO_RELEVANT_CHANGE")
                persist.assert_not_called()

    def test_dirty_candidate_stales_last_good_until_manual_source_bound_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); runtime = root / "runtime.py"; runtime.write_text("def run(): return 'ok'\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "runtime.py"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            config, state, marker = root / "context.json", root / "state", root / "graph-ran"
            graph_program = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
            value = {"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}], "watch": {"paths": ["runtime.py"]}, "drift_rules": [{"id": "provider-boundary", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["def send_to_provider"]}], "code_graph": {"refresh": [sys.executable, "-c", graph_program]}}
            config.write_text(json.dumps(value)); first = run(config, state, "refresh"); self.assertEqual(first["status"], "PASS"); marker.unlink()
            self.assertEqual(run(config, state, "watch", "--once")["status"], "WATCH_READY")
            (root / "provider.py").write_text("def send_to_provider(): return 'ok'\n")
            observed = run(config, state, "watch", "--once", "--apply")
            self.assertEqual((observed["status"], observed["event"], observed["candidate_count"]), ("CANDIDATE_REVIEW_REQUIRED", "ARCHITECTURE_CANDIDATE_OBSERVED", 1))
            self.assertFalse(marker.exists())
            self.assertEqual(run(config, state, "status")["status"], "STALE")
            self.assertEqual(run(config, state, "refresh")["status"], "CANDIDATE_REVIEW_REQUIRED")
            self.assertFalse(marker.exists())
            candidate = run(config, state, "candidates")["candidates"][0]
            self.assertEqual(candidate["evidence"]["path"], "provider.py")
            runtime.write_text("from provider import send_to_provider\n\ndef run(): return send_to_provider()\n")
            value["components"].append({"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send_to_provider"}]})
            value["relations"] = [{"from": "runtime", "to": "provider", "kind": "uses-provider", "evidence": [{"path": "runtime.py", "contains": "return send_to_provider()"}]}]
            config.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "accept it instead"):
                archctx.reject_candidate(config, str(state), candidate["id"], "false_match")
            accepted = run(config, state, "accept", candidate["id"], "--bind", "component:provider", "--bind", "relation:runtime--uses-provider--provider")
            self.assertEqual(accepted["status"], "PASS")
            self.assertTrue(marker.exists())
            self.assertEqual(run(config, state, "status")["status"], "FRESH")
            self.assertEqual(run(config, state, "candidates")["status"], "PASS")
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            self.assertEqual(run(config, state, "history", "--limit", "1")["snapshots"][0]["candidate_decisions"][0]["decision"], "accepted")
            decisions = (state / "candidate-decisions.json").read_text()
            self.assertIn('"decision": "accepted"', decisions); self.assertNotIn("def send_to_provider", decisions)

    def test_multiple_candidates_can_be_reviewed_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "runtime.py").write_text("def run(): pass\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}], "drift_rules": [{"id": "provider-boundary", "kind": "new-provider", "paths": ["provider-*.py"], "added_contains": ["def send"]}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            (root / "provider-a.py").write_text("def send(): pass\n"); (root / "provider-b.py").write_text("def send(): pass\n")
            self.assertEqual(run(config, state, "watch", "--once")["status"], "CANDIDATE_REVIEW_REQUIRED")
            candidates = run(config, state, "candidates")["candidates"]
            self.assertEqual(len(candidates), 2)
            first = run(config, state, "reject", candidates[0]["id"], "--reason", "false_match")
            self.assertEqual((first["status"], first["remaining_candidate_count"]), ("CANDIDATE_REVIEW_REQUIRED", 1))
            second = run(config, state, "reject", candidates[1]["id"], "--reason", "false_match")
            self.assertEqual(second["status"], "PASS")
            self.assertEqual(run(config, state, "status")["status"], "FRESH")

    def test_candidate_acceptance_needs_exact_signal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); provider = root / "provider.py"; provider.write_text("def legacy(): pass\n")
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "provider", "evidence": [{"path": "provider.py", "contains": "def"}]}], "drift_rules": [{"id": "provider-boundary", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["send_to_provider"]}]}
            config.write_text(json.dumps(value)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            provider.write_text("def send_to_provider(): pass\n")
            candidate = run(config, state, "candidates")["candidates"][0]
            value["components"][0]["name"] = "Renamed provider"; config.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "exact candidate signal"):
                archctx.accept_candidate(config, str(state), candidate["id"], ["component:provider"])

    def test_candidate_baseline_reset_is_explicit_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}
            config.write_text(json.dumps(value)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            value["drift_rules"] = [{"id": "owner-rule", "kind": "new-owner", "paths": ["source.py"], "added_contains": ["OWNER"]}]; config.write_text(json.dumps(value))
            self.assertEqual(run(config, state, "refresh")["status"], "CANDIDATE_CHECK_INCOMPLETE")
            reset = run(config, state, "refresh", "--reset-candidate-baseline")
            self.assertEqual((reset["status"], reset["candidate_baseline_reset"]), ("PASS", True))
            self.assertTrue(run(config, state, "history", "--limit", "1")["snapshots"][0]["candidate_baseline_reset"])

    def test_candidate_output_expands_only_on_explicit_request_and_patterns_stay_out_of_baseline(self):
        values = {"candidates": [{"id": str(number)} for number in range(9)]}
        self.assertEqual((len(archctx.candidate_fields(values)["candidates"]), len(archctx.candidate_fields(values, 0)["candidates"])), (8, 9))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "provider.py").write_text("def send_to_provider(): pass\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send_to_provider"}]}], "drift_rules": [{"id": "provider-boundary", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["def send_to_provider"]}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            baseline = json.loads((state / "last-good.json").read_text())["candidate_baseline"]
            self.assertNotIn("def send_to_provider", json.dumps(baseline))

    def test_absolute_drift_rule_is_stale_not_a_windows_glob_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}
            config.write_text(json.dumps(value)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            value["drift_rules"] = [{"id": "bad", "kind": "new-provider", "paths": [str(root / "source.py")], "added_contains": ["OWNER"]}]; config.write_text(json.dumps(value))
            self.assertEqual(run(config, state, "status")["status"], "STALE")
            self.assertEqual(run(config, state, "candidates")["status"], "CANDIDATE_CHECK_INCOMPLETE")

    def test_drift_rule_ids_are_unique(self):
        with self.assertRaisesRegex(ValueError, "ids must be unique"):
            archctx.normalized_drift_rules({"drift_rules": [{"id": "same", "kind": "new-provider", "paths": ["a.py"], "added_contains": ["one"]}, {"id": "same", "kind": "new-provider", "paths": ["a.py"], "added_contains": ["two"]}]})

    def test_candidate_scan_cap_fails_closed_before_reading_a_broad_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "a.py").write_text("def provider(): pass\n"); (root / "b.py").write_text("def provider(): pass\n")
            config = {"version": 1, "watch": {"ignore": []}, "drift_rules": [{"id": "provider", "kind": "new-provider", "paths": ["*.py"], "added_contains": ["provider"]}]}
            with patch.object(archctx, "CANDIDATE_FILE_LIMIT", 1):
                baseline = archctx.candidate_baseline(config, root)
            self.assertFalse(baseline["complete"]); self.assertEqual(baseline["entries"], [])

    def test_corrupt_candidate_decision_store_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "runtime.py").write_text("def run(): pass\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}], "drift_rules": [{"id": "provider", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["def send"]}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            (root / "provider.py").write_text("def send(): pass\n"); candidate = run(config, state, "candidates")["candidates"][0]
            (state / "candidate-decisions.json").write_text("not json")
            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                archctx.reject_candidate(config, str(state), candidate["id"], "false_match")

    def test_failed_gate_preserves_last_good_and_mcp_lists_live_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            base = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}
            config.write_text(json.dumps(base)); self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            base["gates"] = [{"name": "fail", "command": [sys.executable, "-c", "raise SystemExit(1)"]}]
            config.write_text(json.dumps(base)); self.assertTrue(run(config, state, "refresh")["last_good_preserved"])
            self.assertEqual(run(config, state, "status")["status"], "STALE")
            process = subprocess.run([PYTHON, str(TOOL), "--config", str(config), "--state-dir", str(state), "mcp"], input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n", text=True, capture_output=True, check=True)
            tools = json.loads(process.stdout)["result"]["tools"]
            self.assertTrue({"architecture_refresh", "architecture_history", "architecture_usage", "architecture_candidates", "architecture_accept_candidate", "architecture_reject_candidate"}.issubset({item["name"] for item in tools}))

    def test_search_and_codex_install_are_compact_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"; root.mkdir(); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "name": "Owner service", "tags": ["identity"], "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh")
            self.assertEqual(run(config, state, "search", "--query", "identity")["matches"][0]["id"], "owner")
            self.assertEqual(run(config, state, "install-codex", "--target", str(root / "AGENTS.md"))["action"], "created")
            self.assertEqual(run(config, state, "install-codex", "--target", str(root / "AGENTS.md"))["action"], "unchanged")
            installed = (root / "AGENTS.md").read_text()
            self.assertTrue(installed.startswith("<!-- archctx:begin -->"))
            self.assertIn("archctx:begin", installed)
            self.assertIn("shrinks the next broad source read", installed)
            self.assertIn("`canonical`/`evidence` for a known component", installed)
            self.assertIn("`changed-since`/`drift` only with a supplied base revision", installed)
            self.assertIn("not unrelated Git dirtiness", installed)
            self.assertIn("Read only returned evidence", installed)
            self.assertIn("opt-in `watch --apply`", installed)
            external = root.parent / "external.json"; external.write_text(json.dumps({"version": 1, "repo": root.name, "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            failed = subprocess.run([PYTHON, str(TOOL), "--config", str(external), "--state-dir", str(state), "install-codex", "--target", str(root / "OTHER.md")], text=True, capture_output=True)
            self.assertEqual(failed.returncode, 2)

    def test_status_is_metadata_only_and_search_discloses_omissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config, state = root / "context.json", root / "state"
            components = []
            for number in range(4):
                path = root / f"service-{number}.py"; marker = f"SERVICE_{number}"; path.write_text(marker)
                components.append({"id": f"service-{number}", "tags": ["service"], "evidence": [{"path": path.name, "contains": marker}]})
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": components}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            compact = run(config, state, "status")
            self.assertTrue(compact["last_good_available"]); self.assertNotIn("context", compact); self.assertNotIn("last_good", compact)
            self.assertNotIn("status", run(config, state, "telemetry")["events"])
            self.assertEqual(len(run(config, state, "snapshot")["context"]["components"]), 4)
            limited = run(config, state, "search", "--query", "service")
            self.assertEqual((limited["match_count"], len(limited["matches"]), limited["omitted_match_count"]), (4, 3, 1))
            self.assertEqual(run(config, state, "search", "--query", "service", "--limit", "0")["omitted_match_count"], 0)
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "architecture_search", "arguments": {"query": "service"}}}
            process = subprocess.run([PYTHON, str(TOOL), "--config", str(config), "--state-dir", str(state), "mcp"], input=json.dumps(request) + "\n", text=True, capture_output=True, check=True)
            mcp_value = json.loads(json.loads(process.stdout)["result"]["content"][0]["text"])
            self.assertEqual((len(mcp_value["matches"]), mcp_value["omitted_match_count"]), (3, 1))
            self.assertEqual(run(config, state, "telemetry")["outcomes"]["matched"], 3)

    def test_status_diagnostics_are_opt_in_and_read_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config = root / ".archctx" / "architecture.json"
            state = config.parent / ".archctx"; state.mkdir(parents=True)
            config_text = json.dumps({"version": 1, "repo": "..", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}], "drift_rules": [{"id": "owner-rule", "kind": "new-owner", "paths": ["source.py"], "added_contains": ["OWNER"]}]})
            config.write_text(config_text)
            self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
            last_good = state / "last-good.json"
            legacy = json.loads(last_good.read_text()); legacy.pop("candidate_baseline")
            legacy_text = json.dumps(legacy); last_good.write_text(legacy_text)

            def contents():
                return {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}

            before = contents()
            result = archctx.diagnose_status(config, None)
            diagnostics = result["diagnostics"]
            self.assertEqual((result["status"], result["candidate_state"]), ("STALE", "baseline_missing"))
            self.assertTrue(result["last_good_available"])
            self.assertIn("candidate baseline missing", " ".join(result["reason"]))
            self.assertEqual(diagnostics["config"], {"path": str(config.resolve()), "exists": True})
            self.assertEqual(diagnostics["state"], {"path": str(state.resolve()), "selection": "legacy_nested"})
            self.assertEqual(diagnostics["last_good"], {"path": str(last_good.resolve()), "exists": True})
            self.assertEqual(diagnostics["tool"]["version"], archctx.SERVER_VERSION)
            self.assertEqual(Path(diagnostics["tool"]["module"]), TOOL.resolve())
            self.assertEqual(Path(diagnostics["tool"]["python"]).resolve(), Path(PYTHON).resolve())
            self.assertEqual(diagnostics["tool"]["core_source_sha256"], hashlib.sha256(TOOL.read_bytes()).hexdigest())
            self.assertEqual({key: value for key, value in result.items() if key != "diagnostics"}, archctx.status(config, None))
            self.assertEqual(run_raw("--config", str(config), "status", "--diagnose"), result)
            self.assertNotIn("diagnostics", run_raw("--config", str(config), "status"))
            for name in ("architecture_status", "architecture_stale"):
                with self.subTest(mcp=name):
                    self.assertEqual(archctx.mcp_value(config, None, name, {"diagnose": True}), result)
                    self.assertNotIn("diagnostics", archctx.mcp_value(config, None, name, {}))
                    self.assertNotIn("diagnostics", archctx.mcp_value(config, None, name, {"diagnose": False}))
                    for invalid in ("true", 1):
                        with self.assertRaises(ValueError):
                            archctx.mcp_value(config, None, name, {"diagnose": invalid})
            self.assertEqual(contents(), before)

            explicit = root / "explicit-state"
            missing = root / "absent" / "architecture.json"
            for label, path, override, expected_state, selection in (
                ("explicit wins over legacy", config, str(explicit), explicit, "explicit"),
                ("missing config", missing, None, missing.parent / ".archctx", "config_adjacent"),
            ):
                with self.subTest(case=label):
                    before = contents()
                    value = archctx.diagnose_status(path, override)
                    self.assertEqual(value["diagnostics"]["config"], {"path": str(path.resolve()), "exists": path.exists()})
                    self.assertEqual(value["diagnostics"]["state"], {"path": str(expected_state.resolve()), "selection": selection})
                    self.assertEqual(value["diagnostics"]["last_good"], {"path": str((expected_state / "last-good.json").resolve()), "exists": False})
                    arguments = ["--config", str(path)] + (["--state-dir", override] if override else [])
                    self.assertEqual(run_raw(*arguments, "status", "--diagnose"), value)
                    self.assertFalse(expected_state.exists())
                    self.assertEqual(contents(), before)

            for label, config_data, last_good_data in (
                ("bad last-good JSON", config_text, "not json"),
                ("bad last-good shape", config_text, "{}"),
                ("null last-good context", config_text, '{"context":null}'),
                ("malformed config", "not json", legacy_text),
            ):
                with self.subTest(case=label):
                    config.write_text(config_data); last_good.write_text(last_good_data)
                    before = contents()
                    value = archctx.diagnose_status(config, None)
                    self.assertIn(value["status"], ("INVALID", "STALE"))
                    self.assertEqual(value["diagnostics"], diagnostics)
                    self.assertEqual(run_raw("--config", str(config), "status", "--diagnose"), value)
                    for name in ("architecture_status", "architecture_stale"):
                        self.assertEqual(archctx.mcp_value(config, None, name, {"diagnose": True}), value)
                    self.assertEqual(contents(), before)

    def test_init_is_one_time_evidence_bound_and_uninstall_only_removes_managed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "src").mkdir(); (root / "src" / "service.py").write_text("def serve(): pass\n")
            (root / ".gitignore").write_text(".venv/\n"); (root / "AGENTS.md").write_text("# local rules\n")
            first = run_raw("init", "--repo", str(root), "--component", "service", "--truth-source", "src/service.py", "--evidence", "src/service.py::def serve")
            config = root / ".archctx" / "architecture.json"
            self.assertEqual(first["status"], "PASS"); self.assertTrue(config.exists())
            self.assertTrue((root / ".archctx" / "last-good.json").exists())
            self.assertEqual(json.loads(config.read_text())["repo"], "..")
            self.assertEqual((root / ".gitignore").read_text().count(".archctx/"), 1)
            installed = (root / "AGENTS.md").read_text(); self.assertIn("# local rules", installed); self.assertIn("archctx --config .archctx/architecture.json status", installed); self.assertIn("skip obvious local work", installed); self.assertNotIn(str(TOOL.resolve()), installed)
            self.assertEqual(run_raw("init", "--repo", str(root))["action"], "updated")
            self.assertEqual((root / ".gitignore").read_text().count(".archctx/"), 1)
            removed = run_raw("--config", str(config), "uninstall-codex", "--target", str(root / "AGENTS.md"))
            self.assertEqual(removed["action"], "removed"); self.assertTrue(config.exists())
            self.assertEqual((root / "AGENTS.md").read_text(), "# local rules\n")
            self.assertEqual(run_raw("--config", str(config), "uninstall-codex", "--target", str(root / "AGENTS.md"))["action"], "unchanged")

    def test_cli_uses_only_standard_repo_config_when_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config = root / ".archctx" / "architecture.json"; config.parent.mkdir()
            config.write_text(json.dumps({"version": 1, "repo": "..", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            process = subprocess.run([PYTHON, str(TOOL), "refresh"], cwd=root, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(process.stdout)["status"], "PASS")
            missing = subprocess.run([PYTHON, str(TOOL), "status"], cwd=root.parent, text=True, capture_output=True)
            self.assertEqual(missing.returncode, 2); self.assertIn(".archctx/architecture.json", missing.stdout)

    def test_shared_init_preserves_reviewed_config_and_isolates_selected_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("def serve(): return 'first'\n")
            config = root / "architecture" / "shared context.json"; config.parent.mkdir()
            config.write_text(json.dumps({"version": 1, "repo": "..", "components": [{"id": "service", "evidence": [{"path": source.name, "contains": "def serve"}]}], "gates": [{"name": "validator", "command": [PYTHON, "-c", "from pathlib import Path; Path('validator-ran').write_text('ran')"]}]}, indent=3) + "\n")
            reviewed = config.read_bytes(); agents = root / "AGENTS.md"; agents.write_text("# local rules\n")
            (root / ".gitignore").write_text(".venv/\n")
            command = ".venv/Scripts/python.exe -m archctx"
            arguments = ["--config", str(config), "init", "--repo", str(root), "--command", command]

            before = {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}
            self.assertEqual(run_raw(*arguments, "--check")["status"], "CHECK")
            self.assertEqual({str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}, before)
            self.assertFalse((root / "validator-ran").exists())

            first = run_raw(*arguments)
            self.assertEqual((first["status"], first["action"]), ("PASS", "updated"))
            self.assertEqual(config.read_bytes(), reviewed)
            self.assertFalse((root / ".archctx" / "architecture.json").exists())
            self.assertTrue((root / "validator-ran").exists())
            self.assertFalse((root / ".venv").exists())  # The instruction prefix is never executed.
            self.assertEqual((root / ".gitignore").read_text().count(".archctx/"), 1)
            self.assertIn(f"{command} --config 'architecture/shared context.json' status", agents.read_text())
            self.assertIn("# local rules\n", agents.read_text())
            default_last = config.parent / ".archctx" / "last-good.json"; first_good = default_last.read_bytes()
            self.assertEqual(run_raw("--config", str(config), "install-codex", "--command", command)["action"], "unchanged")

            selected = root / "local state" / "run one's"
            command = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in (PYTHON, str(TOOL))) if sys.platform == "win32" else shlex.join((PYTHON, str(TOOL)))
            source.write_text("def serve(): return 'second'\n")
            selected_arguments = ["--config", str(config), "--state-dir", str(selected), "init", "--repo", str(root), "--command", command]
            self.assertEqual(run_raw(*selected_arguments)["status"], "PASS")
            ignored = "/local\\ state/run\\ one's/"
            self.assertEqual((root / ".gitignore").read_text().splitlines().count(ignored), 1)
            repeated = run_raw(*selected_arguments)
            self.assertFalse(repeated["gitignore_updated"]); self.assertEqual(repeated["codex"]["action"], "unchanged")
            self.assertEqual((root / ".gitignore").read_text().splitlines().count(ignored), 1)
            selected_last = selected / "last-good.json"; second_good = selected_last.read_bytes()
            self.assertNotEqual(json.loads(first_good)["context_hash"], json.loads(second_good)["context_hash"])
            self.assertEqual(default_last.read_bytes(), first_good)
            self.assertFalse((root / ".archctx" / "architecture.json").exists())
            self.assertEqual(run_raw("--config", str(config), "--state-dir", str(selected), "install-codex", "--command", command)["action"], "unchanged")
            managed = agents.read_text().split("From the repository root, run `", 1)[1].split("`", 1)[0]
            shell = ["powershell", "-NoProfile", "-NonInteractive", "-Command"] if sys.platform == "win32" else ["/bin/sh", "-c"]
            actual = subprocess.run([*shell, managed + " --diagnose"], cwd=root, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(actual.stdout)["status"], "FRESH")
            diagnostics = json.loads(actual.stdout)["diagnostics"]
            self.assertEqual(diagnostics["config"], {"path": str(config.resolve()), "exists": True})
            self.assertEqual(diagnostics["state"], {"path": str(selected.resolve()), "selection": "explicit"})
            self.assertEqual(Path(diagnostics["tool"]["python"]).resolve(), Path(PYTHON).resolve())
            self.assertEqual(Path(diagnostics["tool"]["module"]), TOOL.resolve())
            self.assertEqual(diagnostics["tool"]["core_source_sha256"], hashlib.sha256(TOOL.read_bytes()).hexdigest())

            source.write_text("def renamed(): pass\n")
            invalid = run_raw(*selected_arguments)
            self.assertEqual(invalid["status"], "INVALID")
            self.assertTrue(invalid["refresh"]["last_good_preserved"])
            self.assertEqual(selected_last.read_bytes(), second_good)
            self.assertEqual(default_last.read_bytes(), first_good)
            self.assertEqual(config.read_bytes(), reviewed)

            private = root / ".archctx" / "architecture.json"; private.parent.mkdir(); private.write_bytes(reviewed)
            (config.parent / "architecture.json").write_bytes(reviewed)
            before = {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}
            for invocation in (("status",), ("init", "--repo", str(root), "--check")):
                result = subprocess.run([PYTHON, str(TOOL), *invocation], cwd=root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 2); self.assertIn("--config", result.stdout)
            self.assertEqual(run_raw(*selected_arguments, "--check")["status"], "CHECK")
            self.assertEqual({str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}, before)

            source.write_text("def serve(): pass\n")
            nested_agents = root / "instructions" / "AGENTS.md"; nested_agents.parent.mkdir(); nested_agents.write_text("# retained rules\n")
            tracked_state = root / "tracked state"; tracked_state.mkdir(); (tracked_state / "retained.txt").write_text("tracked content\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "--", "tracked state/retained.txt"], check=True)
            ignored_paths = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin", "-z"], input="local state/run one's/last-good.json\0local state/run two/last-good.json\0", text=True, capture_output=True, check=True)
            self.assertEqual(ignored_paths.stdout, "local state/run one's/last-good.json\0")
            before = {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}
            for unsafe_state, target in ((config.parent, agents), (nested_agents.parent, nested_agents), (tracked_state, agents)):
                for operation in ("init", "install-codex"):
                    with self.subTest(state=unsafe_state.name, operation=operation):
                        invocation = [PYTHON, str(TOOL), "--config", str(config), "--state-dir", str(unsafe_state), operation, "--target", str(target)]
                        if operation == "init": invocation.extend(("--repo", str(root)))
                        rejected = subprocess.run(invocation, cwd=root, text=True, capture_output=True)
                        self.assertEqual(rejected.returncode, 2); self.assertIn("state", json.loads(rejected.stdout)["error"])
                        self.assertEqual({str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in (root, *root.rglob("*"))}, before)

            new_config = root / "reviewed" / "nested" / "architecture.json"
            created = run_raw("--config", str(new_config), "init", "--repo", str(root), "--component", "service", "--evidence", "source.py::def serve", "--command", command)
            self.assertEqual((created["status"], created["action"]), ("PASS", "created"))
            self.assertEqual(json.loads(new_config.read_text())["repo"], "../..")
            self.assertTrue((new_config.parent / ".archctx" / "last-good.json").exists())
            self.assertEqual(config.read_bytes(), reviewed); self.assertEqual(private.read_bytes(), reviewed)

    def test_telemetry_is_local_compact_and_never_keeps_raw_task_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); run(config, state, "search", "--query", "owner private product goal"); run(config, state, "search", "--query", "no-match")
            run(config, state, "install-codex", "--target", str(root / "AGENTS.md"))
            summary = run(config, state, "telemetry")
            self.assertEqual(summary["events"]["search"], 2); self.assertEqual(summary["outcomes"]["matched"], 1); self.assertEqual(summary["outcomes"]["empty"], 1); self.assertEqual(summary["privacy"], "local aggregate metrics only; no source, evidence, query, or task text")
            self.assertEqual((summary["actionable_result_count"], summary["eligible_result_count"]), (2, 3)); self.assertAlmostEqual(summary["actionable_result_rate"], 2 / 3)
            self.assertNotIn("install-codex", summary["events"])
            self.assertEqual(summary["retention"], "fixed-size aggregate"); self.assertNotIn("private product goal", (state / "telemetry.json").read_text())

    def test_history_lists_retained_snapshots_and_reads_one_as_historical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}
            config.write_text(json.dumps(value)); first = run(config, state, "refresh")
            value["components"][0]["name"] = "Renamed owner"; config.write_text(json.dumps(value)); second = run(config, state, "refresh")
            listed = run(config, state, "history", "--limit", "1")
            self.assertEqual((listed["snapshot_count"], len(listed["snapshots"]), listed["omitted_snapshot_count"]), (2, 1, 1))
            self.assertEqual(listed["snapshots"][0]["context_hash"], second["context_hash"])
            historical = run(config, state, "history", "--context-hash", first["context_hash"])
            self.assertTrue(historical["not_current_authority"]); self.assertEqual(historical["freshness"], "historical")
            self.assertEqual(historical["context"]["components"][0]["id"], "owner")

    def test_usage_is_bounded_and_does_not_keep_query_or_source_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); run(config, state, "search", "--query", "owner private product goal")
            before = (state / "usage.json").read_text(); receipt = run(config, state, "usage", "--operation", "search")
            self.assertEqual(receipt["records"][0]["result"]["component_ids"], ["owner"])
            self.assertEqual(receipt["records"][0]["result"]["outcome"], "matched")
            self.assertNotIn("private product goal", before); self.assertNotIn("source.py", before)
            run(config, state, "status"); run(config, state, "history")
            self.assertEqual((state / "usage.json").read_text(), before)
            with patch.object(archctx, "USAGE_LIMIT", 2):
                for value in ("a", "b", "c"):
                    archctx.record_usage(state, "canonical", {"status": "FRESH", "canonical": {"id": value}}, 1)
            self.assertEqual(len(archctx.usage_store(state)["records"]), 2)

    def test_legacy_usage_import_and_telemetry_keys_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"; state.mkdir()
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            (state / "telemetry.jsonl").write_text("\n".join(json.dumps(value) for value in ({"at": "2026-01-01T00:00:00Z", "event": "search", "status": "FRESH", "elapsed_ms": 1}, {"at": "2026-01-01T00:01:00Z", "event": "status", "status": "FRESH", "elapsed_ms": 1})) + "\n")
            self.assertEqual(run(config, state, "usage", "--import-legacy")["imported"], 1)
            self.assertEqual(run(config, state, "usage")["records"][0]["origin"], "legacy")
            self.assertEqual(archctx.telemetry_event("mcp:arbitrary-client-key"), "mcp:unknown")

    def test_persisted_snapshots_exclude_validator_diagnostic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}], "code_graph": {"provider": "demo", "refresh": [sys.executable, "-c", "print('graph diagnostics')"]}, "gates": [{"name": "pass", "command": [sys.executable, "-c", "print('gate diagnostics')"]}]}))
            refreshed = run(config, state, "refresh"); record = json.loads((state / "last-good.json").read_text())
            self.assertNotIn("stdout_tail", refreshed["graph"]); self.assertNotIn("command", refreshed["graph"]); self.assertNotIn("stdout_tail", record["graph"]); self.assertNotIn("command", record["graph"])
            self.assertEqual(record["gates"], [{"name": "pass", "status": "PASS"}])
            self.assertNotIn("stdout_tail", run(config, state, "snapshot")["graph"])

    def test_snapshots_are_bounded_without_losing_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            with patch.object(archctx, "SNAPSHOT_LIMIT", 2):
                for value in ("one", "two", "three"):
                    source.write_text(f"OWNER = '{value}'\n")
                    self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
            self.assertEqual(len(archctx.snapshot_files(state)), 2)
            self.assertTrue((state / "last-good.json").exists())
    def test_last_good_is_preserved_and_marked_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER = 'one'\n")
            config = root / "context.json"; state = root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER = 'one'"}]}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            (root / "source.py").write_text("OWNER = 'two'\n")
            value = run(config, state, "canonical", "owner")
            self.assertEqual(value["status"], "STALE")
            self.assertEqual(value["canonical"]["id"], "owner")

    def test_status_uses_evidence_not_git_availability_or_unrelated_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
            with patch.object(archctx, "git", return_value=None): self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")
            (root / "unrelated.txt").write_text("unrelated\n"); subprocess.run(["git", "-C", str(root), "add", "unrelated.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "unrelated"], check=True)
            self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")

    def test_trace_and_impact_are_authored_not_call_graph_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "a.py").write_text("a\n"); (root / "b.py").write_text("b\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            config = root / "context.json"; state = root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "a", "evidence": [{"path": "a.py", "contains": "a"}]}, {"id": "b", "evidence": [{"path": "b.py", "contains": "b"}]}], "relations": [{"from": "a", "to": "b", "kind": "calls"}]}))
            run(config, state, "refresh"); (root / "a.py").write_text("aa\n"); subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True); subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "change"], check=True)
            self.assertEqual(run(config, state, "trace", "a")["kind"], "authored_architecture_trace")
            self.assertEqual(run(config, state, "impact", "--base", base)["kind"], "authored_architecture_impact")

    def test_relation_only_evidence_drives_impact_and_watch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
            (root / "b.py").write_text("def b(): pass\n", encoding="utf-8")
            wiring, unrelated = root / "wiring.py", root / "other.py"
            anchor = "ROUTE = (a, b)\n"
            wiring.write_text(anchor, encoding="utf-8")
            unrelated.write_text("# unrelated\n", encoding="utf-8")
            config, state = root / "architecture.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [
                {"id": "b", "evidence": [{"path": "b.py", "contains": "def b"}]},
                {"id": "a", "evidence": [{"path": "a.py", "contains": "def a"}]}],
                "relations": [{"id": "a-to-b", "from": "a", "to": "b", "kind": "calls",
                               "evidence": [{"path": "wiring.py", "contains": anchor.strip()}]}],
                "watch": {"paths": ["other.py"]}}
            config.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
            before = archctx.load(archctx.last_path(state))
            self.assertEqual(archctx.watch_once(config, str(state))["status"], "WATCH_READY")
            self.assertIn("wiring.py", archctx.load(state / "live-state.json")["manifest"])

            impact = archctx.impact(config, str(state), None, ["wiring.py"])
            self.assertEqual(impact["direct_components"], ["b", "a"])
            self.assertEqual(impact["reachable_components"], ["a", "b"])
            self.assertEqual(impact["kind"], "authored_architecture_impact")
            with patch.object(archctx, "refresh", wraps=archctx.refresh) as refresh:
                unrelated.write_text("# changed unrelated implementation\n", encoding="utf-8")
                self.assertEqual(archctx.impact(config, str(state), None, ["other.py"])["direct_components"], [])
                self.assertEqual(archctx.watch_once(config, str(state), apply=True)["status"], "NO_RELEVANT_CHANGE")
                refresh.assert_not_called()

                wiring.write_text("# moved evidence\n" + anchor, encoding="utf-8")
                observed = archctx.watch_once(config, str(state))
                self.assertEqual((observed["status"], observed["event"]), ("STALE", "CANONICAL_EVIDENCE_CHANGED"))
                self.assertEqual(observed["direct_components"], ["b", "a"])
                refresh.assert_not_called()

                # Observation consumes its manifest; the next edit exercises --apply.
                updated_source = "# moved evidence\n# ordinary implementation edit\n" + anchor
                wiring.write_text(updated_source, encoding="utf-8")
                refreshed = archctx.watch_once(config, str(state), apply=True)
                self.assertEqual((refreshed["status"], refreshed["event"]), ("PASS", "ARCHITECTURE_CONTEXT_REFRESHED"))
                self.assertEqual(refreshed["direct_components"], ["b", "a"])
                self.assertEqual(refreshed["changed_files"], ["wiring.py"])
                accepted = archctx.load(archctx.last_path(state))
                evidence = accepted["context"]["relations"][0]["evidence"][0]
                self.assertEqual((evidence["sha256"], evidence["line"]), (archctx.sha(updated_source.encode()), 3))
                delta = archctx.record_diff(before, accepted)
                self.assertEqual(delta["evidence_changed_relations"], ["a-to-b"])
                self.assertEqual(delta["changed_components"], [])
                self.assertEqual(delta["changed_relations"], [])
                self.assertEqual(archctx.candidates(config, str(state))["candidate_count"], 0)
                self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")

                last_good = archctx.last_path(state).read_bytes()
                wiring.write_text("# relation anchor removed\n", encoding="utf-8")
                failed = archctx.watch_once(config, str(state), apply=True)
                self.assertEqual((failed["status"], failed["event"]), ("INVALID", "ARCHITECTURE_REFRESH_FAILED"))
                self.assertTrue(failed["last_good_preserved"])
                self.assertIn("wiring.py", " ".join(failed["failures"]))
                self.assertEqual(archctx.last_path(state).read_bytes(), last_good)
                self.assertEqual(archctx.status(config, str(state))["status"], "STALE")
                self.assertEqual(archctx.watch_once(config, str(state), apply=True)["status"], "NO_RELEVANT_CHANGE")
                self.assertEqual(refresh.call_count, 2)

            # Malformed relation paths remain validation failures, not relevance crashes.
            value["relations"][0]["evidence"][0]["path"] = None
            config.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(archctx.watch_once(config, str(state), apply=True)["status"], "INVALID")
            self.assertEqual(archctx.last_path(state).read_bytes(), last_good)

    def test_relation_evidence_is_validated_and_traceable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text("from provider import send\n\ndef run(): return send()\n")
            (root / "provider.py").write_text("def send(): return 'ok'\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}, {"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send"}]}], "relations": [{"from": "runtime", "to": "provider", "kind": "uses-provider", "evidence": [{"path": "runtime.py", "contains": "from provider import send"}]}]}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            relation = run(config, state, "trace", "runtime")["relations"][0]
            self.assertEqual(relation["confidence"], "source_evidence")
            self.assertEqual(relation["evidence"][0]["line"], 1)
            (root / "runtime.py").write_text("def run(): return 'not wired'\n")
            failed = run(config, state, "refresh")
            self.assertEqual(failed["status"], "INVALID")
            self.assertTrue(failed["last_good_preserved"])

    def test_relation_evidence_refresh_is_not_a_topology_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.py"; runtime.write_text("from provider import send\n\ndef run(): return send()\n")
            (root / "provider.py").write_text("def send(): return 'ok'\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}, {"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send"}]}], "relations": [{"from": "runtime", "to": "provider", "kind": "uses-provider", "evidence": [{"path": "runtime.py", "contains": "from provider import send"}]}]}))
            run(config, state, "refresh"); runtime.write_text("from provider import send\n\n# implementation detail\ndef run(): return send()\n"); run(config, state, "refresh")
            delta = run(config, state, "changed-since", "--revision", base)
            self.assertEqual(delta["added_relations"], [])
            self.assertEqual(delta["removed_relations"], [])
            self.assertEqual(delta["changed_relations"], [])
            self.assertEqual(delta["evidence_changed_relations"], ["runtime--uses-provider--provider"])

    def test_changed_since_compares_retained_source_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER = 'one'\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            config = root / "context.json"; state = root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); (root / "source.py").write_text("OWNER = 'two'\n")
            subprocess.run(["git", "-C", str(root), "add", "source.py"], check=True); subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "change"], check=True)
            run(config, state, "refresh")
            delta = run(config, state, "changed-since", "--revision", base)
            self.assertEqual(delta["changed_components"], [])
            self.assertEqual(delta["evidence_changed_components"], ["owner"])

    def test_evidence_only_refresh_is_not_an_architecture_component_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.py"; source.write_text("OWNER\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); source.write_text("OWNER\n# implementation detail\n"); run(config, state, "refresh")
            delta = run(config, state, "changed-since", "--revision", base)
            self.assertEqual(delta["changed_components"], [])
            self.assertEqual(delta["evidence_changed_components"], ["owner"])
            self.assertEqual(run(config, state, "history")["snapshots"][0]["delta_from_previous"]["changed_components"], [])

    def test_changed_since_uses_first_snapshot_for_an_unchanged_git_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True); subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            config, state = root / "context.json", root / "state"
            value = {"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}
            config.write_text(json.dumps(value)); run(config, state, "refresh")
            value["components"][0]["name"] = "Renamed owner"; config.write_text(json.dumps(value)); run(config, state, "refresh")
            self.assertEqual(run(config, state, "changed-since", "--revision", base)["changed_components"], ["owner"])


if __name__ == "__main__":
    unittest.main()
