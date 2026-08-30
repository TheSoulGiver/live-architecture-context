import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
            self.assertIn("architecture_refresh", {item["name"] for item in tools})

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
            external = root.parent / "external.json"; external.write_text(json.dumps({"version": 1, "repo": root.name, "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            failed = subprocess.run([PYTHON, str(TOOL), "--config", str(external), "--state-dir", str(state), "install-codex", "--target", str(root / "OTHER.md")], text=True, capture_output=True)
            self.assertEqual(failed.returncode, 2)

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
            installed = (root / "AGENTS.md").read_text(); self.assertIn("# local rules", installed); self.assertIn(str(PYTHON), installed); self.assertIn("--config .archctx/architecture.json", installed)
            self.assertEqual(run_raw("init", "--repo", str(root))["action"], "updated")
            self.assertEqual((root / ".gitignore").read_text().count(".archctx/"), 1)
            removed = run_raw("--config", str(config), "uninstall-codex", "--target", str(root / "AGENTS.md"))
            self.assertEqual(removed["action"], "removed"); self.assertTrue(config.exists())
            self.assertEqual((root / "AGENTS.md").read_text(), "# local rules\n")
            self.assertEqual(run_raw("--config", str(config), "uninstall-codex", "--target", str(root / "AGENTS.md"))["action"], "unchanged")
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
