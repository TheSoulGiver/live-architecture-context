import json
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
    def test_watcher_skips_unrelated_then_refreshes_only_evidence_owner(self):
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
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["direct_components"], ["owner"])
            (root / "owner.py").rename(root / "owner-renamed.py")
            result = run(config, state, "watch", "--once")
            self.assertEqual(result["status"], "INVALID")
            self.assertTrue(result["last_good_preserved"])

    def test_watcher_never_labels_a_full_graph_refresh_incremental(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "owner.py").write_text("OWNER = 'one'\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}], "watch": {"paths": ["*.py"]}, "code_graph": {"refresh": [sys.executable, "-c", "print('indexed')"]}}))
            self.assertEqual(run(config, state, "refresh")["status"], "PASS")
            run(config, state, "watch", "--once")
            (root / "owner.py").write_text("OWNER = 'two'\n")
            self.assertEqual(run(config, state, "watch", "--once")["graph"]["freshness"], "refreshed")

    def test_continuous_watcher_does_not_emit_no_relevant_change(self):
        values = [{"status": "WATCH_READY"}, {"status": "NO_RELEVANT_CHANGE"}, {"status": "PASS"}]
        with patch.object(archctx, "watch_once", side_effect=values), patch.object(archctx, "dump") as output, patch.object(archctx, "record_usage") as receipt, patch.object(archctx.time, "sleep"):
            self.assertEqual(archctx.watch(Path("context.json"), None, 50, 1), 0)
        self.assertEqual([call.args[0]["status"] for call in output.call_args_list], ["WATCH_READY", "PASS"])
        self.assertEqual([call.args[1] for call in receipt.call_args_list], ["watch", "watch"])

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
            self.assertTrue({"architecture_refresh", "architecture_history", "architecture_usage"}.issubset({item["name"] for item in tools}))

    def test_search_and_codex_install_are_compact_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
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

    def test_telemetry_is_local_compact_and_never_keeps_raw_task_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER\n")
            config, state = root / "context.json", root / "state"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); run(config, state, "search", "--query", "owner private product goal"); run(config, state, "search", "--query", "no-match")
            summary = run(config, state, "telemetry")
            self.assertEqual(summary["events"]["search"], 2); self.assertEqual(summary["outcomes"]["matched"], 1); self.assertEqual(summary["outcomes"]["empty"], 1); self.assertEqual(summary["privacy"], "local aggregate metrics only; no source, evidence, query, or task text")
            self.assertEqual((summary["actionable_result_count"], summary["eligible_result_count"]), (2, 3)); self.assertAlmostEqual(summary["actionable_result_rate"], 2 / 3)
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
            self.assertIn("stdout_tail", refreshed["graph"]); self.assertNotIn("stdout_tail", record["graph"]); self.assertNotIn("command", record["graph"])
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
            self.assertEqual(run(config, state, "changed-since", "--revision", base)["changed_components"], ["owner"])

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
