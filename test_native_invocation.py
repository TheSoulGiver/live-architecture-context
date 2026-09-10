"""Trusted native dispatch across a consumer directory and Python isolation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import archctx


class NativeInvocationTest(unittest.TestCase):
    def test_runtime_and_core_resolve_outside_consumer_shadows_and_keep_isolation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            trusted = Path(directory).resolve() / "trusted code"
            consumer = Path(directory).resolve() / "consumer project"
            trusted.mkdir()
            consumer.mkdir()
            core = trusted / "archctx.py"
            core.write_text("TRUSTED = True\n", encoding="utf-8")
            runtime = trusted / "archctx_runtime.py"
            runtime.write_text(
                "import json, sys\nfrom importlib.util import find_spec\nfrom pathlib import Path\n"
                "core = find_spec('archctx')\n"
                "print(json.dumps({'runtime': str(Path(__file__).resolve()), 'isolated': sys.flags.isolated, "
                "'core': core.origin if core else None}))\n", encoding="utf-8")
            for name in ("archctx.py", "archctx_runtime.py"):
                (consumer / name).write_text("raise RuntimeError('consumer module executed')\n", encoding="utf-8")
            for isolated in (0, 1):
                with self.subTest(isolated=isolated), patch.object(archctx, "CORE_SOURCE_PATH", core), patch.object(
                        archctx, "sys", SimpleNamespace(executable=sys.executable, flags=SimpleNamespace(isolated=isolated))):
                    argv, result = archctx.run(["{python}", "{lac_runtime}"], consumer, 10,
                        {"{lac_runtime}": str(consumer / "archctx_runtime.py")}, {"PYTHONPATH": str(consumer)})
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["runtime"], str(runtime))
                self.assertEqual(value["isolated"], isolated)
                self.assertEqual("-I" in argv, bool(isolated))
                self.assertNotEqual(value["core"], str(consumer / "archctx.py"))
                if not isolated:
                    self.assertEqual(value["core"], str(core))


if __name__ == "__main__":
    unittest.main()
