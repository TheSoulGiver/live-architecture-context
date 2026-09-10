#!/usr/bin/env python3
"""Run the reviewed Archify revision; tools and generated data stay local."""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from archctx_runtime import ARCHIFY_REVISION as REVISION, ARCHIFY_URL as REPOSITORY, archify_main


def main() -> int:
    return archify_main(ROOT / "architecture/.archctx", sys.argv[1:])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
