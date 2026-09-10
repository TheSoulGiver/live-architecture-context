"""One optional real-provider check; no model calls or product-state mutation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "architecture/.archctx/tools/understand/understand-anything-plugin"


@unittest.skipUnless(shutil.which("node") and (PLUGIN / "packages/core/dist/index.js").exists(),
                     "optional pinned Understand provider is not installed")
class DependencyEvidenceTest(unittest.TestCase):
    def test_real_import_resolution_preserves_missing_and_external_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="archctx-dependencies-", dir=ROOT.parent) as temporary:
            run = Path(temporary)
            capture = run / "capture"
            (capture / "pkg").mkdir(parents=True)
            (capture / "client").mkdir()
            (capture / "independent.py").write_text("value = 1\n", encoding="utf-8")
            (capture / "pkg/b.py").write_text(
                "import a\nimport os\nimport unavailable_package\nfrom .missing import value\n", encoding="utf-8")
            (capture / "client/b.ts").write_text(
                "import { value } from './a';\nimport unknown from 'not-a-known-package';\n"
                "import missing from './missing';\nimport fs from 'node:fs';\n", encoding="utf-8")
            (capture / "script.rb").write_text("require_relative './dep'\n", encoding="utf-8")
            # Dependency targets exist only in the inventory, proving there is no whole-repo source read.
            selected = ["independent.py", "pkg/b.py", "client/b.ts", "script.rb"]
            inventory = selected + ["a.py", "client/a.ts", "dep.rb"]
            request = run / "input.json"
            response = run / "output.json"
            request.write_text(json.dumps({"projectRoot": str(capture),
                "files": [{"path": p, "language": {".ts": "typescript", ".rb": "ruby"}.get(Path(p).suffix, "python")} for p in inventory],
                "analysisPaths": selected, "externalModules": sorted(sys.stdlib_module_names)}), encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(ROOT / "archctx_assets/analysis_dependencies.mjs"),
                str(PLUGIN), str(request), str(response)], capture_output=True, text=True, encoding="utf-8", timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(response.read_text(encoding="utf-8"))
            self.assertTrue(output["scriptCompleted"])
            self.assertEqual(output["failures"], [])
            self.assertEqual(output["stats"]["filesScanned"], 4)
            self.assertEqual(output["files"]["independent.py"]["coverage"], "resolved")
            python = output["files"]["pkg/b.py"]
            self.assertEqual(python["dependencies"], ["a.py"])
            self.assertEqual(python["unresolvedLocal"], [".missing"])
            self.assertEqual(python["unknown"], ["unavailable_package"])
            self.assertEqual(python["external"], ["os"])
            typescript = output["files"]["client/b.ts"]
            self.assertEqual(typescript["dependencies"], ["client/a.ts"])
            self.assertEqual(typescript["unresolvedLocal"], ["./missing"])
            self.assertEqual(typescript["unknown"], ["not-a-known-package"])
            self.assertEqual(typescript["external"], ["node:fs"])
            self.assertEqual(python["coverage"], typescript["coverage"])
            self.assertEqual(python["coverage"], "partial")
            ruby = output["files"]["script.rb"]
            self.assertEqual(ruby["dependencies"], ["dep.rb"])
            self.assertEqual(ruby["unresolvedLocal"], [])
            self.assertEqual(ruby["unknown"], ["./dep"])


if __name__ == "__main__":
    unittest.main()
