import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
TOOL = ROOT / "archctx.py"


def run(config, state, *args):
    result = subprocess.run(["python", str(TOOL), "--config", str(config), "--state-dir", str(state), *args], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class ArchitectureContextTest(unittest.TestCase):
    def test_last_good_is_preserved_and_marked_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "source.py").write_text("OWNER = 'one'\n")
            config = root / "context.json"; state = root / "state"
            config.write_text(json.dumps({"repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER = 'one'"}]}]}))
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
            config.write_text(json.dumps({"repo": ".", "components": [{"id": "a", "evidence": [{"path": "a.py", "contains": "a"}]}, {"id": "b", "evidence": [{"path": "b.py", "contains": "b"}]}], "relations": [{"from": "a", "to": "b"}]}))
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
            config.write_text(json.dumps({"repo": ".", "components": [{"id": "owner", "evidence": [{"path": "source.py", "contains": "OWNER"}]}]}))
            run(config, state, "refresh"); (root / "source.py").write_text("OWNER = 'two'\n")
            subprocess.run(["git", "-C", str(root), "add", "source.py"], check=True); subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "change"], check=True)
            run(config, state, "refresh")
            self.assertEqual(run(config, state, "changed-since", "--revision", base)["changed_components"], ["owner"])


if __name__ == "__main__":
    unittest.main()
