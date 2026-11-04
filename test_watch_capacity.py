import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOL = Path(__file__).with_name("archctx.py")


class WatchCapacityTest(unittest.TestCase):
    def refresh(self, root, config):
        path = root / "architecture.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run([sys.executable, str(TOOL), "--config", str(path), "refresh"], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_default_refresh_accepts_ling_sized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = []
            for index in range(600):
                path = f"owner-{index}.py"
                (root / path).write_text("OWNER = 1\n" + "# detail\n" * 2500, encoding="utf-8")
                evidence.append({"path": path, "contains": "OWNER = 1"})
            result = self.refresh(root, {"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": evidence}]})
            self.assertEqual(result["status"], "PASS", result)

    def test_configured_file_limit_names_owners_and_largest_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "small.py").write_text("SMALL = 1\n", encoding="utf-8")
            (root / "large.py").write_text("LARGE = 1\n" + "# payload\n" * 100, encoding="utf-8")
            config = {"version": 1, "repo": ".", "components": [
                {"id": "small-owner", "evidence": [{"path": "small.py", "contains": "SMALL"}]},
                {"id": "large-owner", "evidence": [{"path": "large.py", "contains": "LARGE"}]},
            ], "watch": {"max_files": 2}}
            result = self.refresh(root, config)
            self.assertEqual(result["status"], "INVALID", result)
            failure = "\n".join(result["failures"])
            self.assertIn("3 files", failure)
            self.assertIn("large.py", failure)
            self.assertIn("large-owner", failure)
            config["watch"]["max_files"] = 3
            self.assertEqual(self.refresh(root, config)["status"], "PASS")

    def test_configured_byte_limit_reports_size_and_can_be_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.py").write_text("OWNER = 1\n" + "# payload\n" * 200, encoding="utf-8")
            config = {"version": 1, "repo": ".", "components": [{"id": "runtime", "evidence": [{"path": "large.py", "contains": "OWNER"}]}], "watch": {"max_source_bytes": 1000}}
            result = self.refresh(root, config)
            self.assertEqual(result["status"], "INVALID", result)
            self.assertIn("source bytes", result["failures"][0])
            self.assertIn("large.py", result["failures"][0])
            self.assertIn("runtime", result["failures"][0])
            config["watch"]["max_source_bytes"] = 10000
            self.assertEqual(self.refresh(root, config)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
