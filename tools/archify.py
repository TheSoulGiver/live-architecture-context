#!/usr/bin/env python3
"""Run the reviewed Archify revision; tools and generated data stay local."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REVISION = "5de7275fe87a66a19d52a4d9b0b3a4f2a5a90115"
REPOSITORY = "https://github.com/tt-a1i/archify.git"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    target = Path(os.environ.get("ARCHIFY_HOME", ROOT / "architecture" / ".archctx" / "tools" / "archify")).resolve()
    git = shutil.which("git")
    node = shutil.which("node")
    if not git or not node:
        raise RuntimeError("Git and Node.js 18+ are required for the pinned Archify renderer")
    version = subprocess.run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 18:
        raise RuntimeError(f"Archify requires Node.js 18+; found {version}")
    args = sys.argv[1:]
    if args == ["setup"]:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([git, "clone", "--no-checkout", "--filter=blob:none", REPOSITORY, str(target)], check=True)
            subprocess.run([git, "-C", str(target), "checkout", "--detach", REVISION], check=True)
        # Existing installations are never reset or overwritten.
    if not (target / ".git").exists():
        raise RuntimeError("Run python tools/archify.py setup, or set ARCHIFY_HOME to the pinned Archify checkout")
    revision = subprocess.run([git, "-C", str(target), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    if revision != REVISION:
        raise RuntimeError(f"Archify must be pinned to {REVISION}; use a separate checkout")
    dirty = subprocess.run([git, "-C", str(target), "status", "--porcelain", "--untracked-files=no"], check=True, capture_output=True, text=True).stdout
    if dirty.strip():
        raise RuntimeError("Pinned Archify source has local edits; use a clean separate checkout")
    if args == ["setup"]:
        print(f"Archify 2.16.0 ready at {target}; revision {revision}")
        return 0
    return subprocess.run([node, str(target / "archify" / "bin" / "archify.mjs"), *args]).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
