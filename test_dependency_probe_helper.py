"""Relevant probes come from the real pinned resolver, not a second parser."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_analysis_inputs import PLUGIN, ROOT


@unittest.skipUnless(shutil.which("node") and (PLUGIN / "packages/core/dist/index.js").exists(),
                     "optional pinned Understand provider is not installed")
class DependencyProbeHelperTest(unittest.TestCase):
    def test_python_probes_capture_absent_shadow_and_missing_targets_but_not_unrelated_names(self):
        with tempfile.TemporaryDirectory(prefix="dependency-probes-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            (repo / "pkg").mkdir()
            (repo / "pkg/main.py").write_text("import helper\nfrom . import missing\nimport os\n", encoding="utf-8")
            names = ["pkg/main.py", "helper.py", "unrelated.txt"]
            initial = self.run_helper(repo, "pkg/main.py", "python", names)
            proof = initial["resolution"]["files"]["pkg/main.py"]
            self.assertTrue(proof["supported"])
            self.assertTrue(proof["checks"]["helper.py"])
            for absent in ("pkg/helper.py", "pkg/helper/__init__.py", "pkg/missing.py",
                           "pkg/missing/__init__.py", "pkg/os.py", "os.py"):
                self.assertFalse(proof["checks"][absent])
            self.assertEqual(initial["files"]["pkg/main.py"]["dependencies"], ["helper.py"])
            unrelated = self.run_helper(repo, "pkg/main.py", "python", names[:-1] + ["different.md"])
            self.assertEqual(initial["resolution"], unrelated["resolution"])
            self.assertEqual(initial["files"], unrelated["files"])
            changed = self.run_helper(repo, "pkg/main.py", "python", names + ["pkg/helper.py", "pkg/missing.py"])
            self.assertEqual(changed["files"]["pkg/main.py"]["dependencies"], ["pkg/helper.py", "pkg/missing.py"])
            self.assertNotEqual(initial["resolution"], changed["resolution"])

    def test_uninstrumented_javascript_and_probe_overflow_remain_unsupported(self):
        with tempfile.TemporaryDirectory(prefix="dependency-probes-", dir=ROOT.parent) as temporary:
            repo = Path(temporary)
            # A currently missing require yields no edge; equality with imports
            # alone is insufficient because a new file can make it resolvable.
            (repo / "main.js").write_text("const value = require('./missing');\n", encoding="utf-8")
            js = self.run_helper(repo, "main.js", "javascript", ["main.js"])
            self.assertEqual(js["files"]["main.js"]["dependencies"], [])
            self.assertEqual(js["resolution"]["files"]["main.js"], {"checks": {}, "supported": False})
            (repo / "many.py").write_text("\n".join(f"import absent_{n}" for n in range(1100)), encoding="utf-8")
            many = self.run_helper(repo, "many.py", "python", ["many.py"])
            self.assertEqual(many["resolution"]["files"]["many.py"], {"checks": {}, "supported": False})

    @staticmethod
    def run_helper(repo, selected, language, names):
        input_path, output_path = repo / "input.json", repo / "output.json"
        input_path.write_text(json.dumps({"projectRoot": str(repo), "analysisPaths": [selected],
            "files": [{"path": name, **({"language": language} if name == selected else {})} for name in names],
            "externalModules": ["os"]}), encoding="utf-8")
        result = subprocess.run([shutil.which("node"), str(ROOT / "archctx_assets/analysis_dependencies.mjs"),
            str(PLUGIN), str(input_path), str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
