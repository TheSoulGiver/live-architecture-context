#!/usr/bin/env python3
"""Cheap, dependency-free checks for the public README evidence surface."""
from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ASSETS = ("assets/hero.svg", "assets/context-efficiency.svg", "assets/trust-stack.svg")
REQUIRED_README_REFS = (*ASSETS, "benchmarks/observed-context-ab.json", "docs/public-face/claim-evidence.md", "docs/public-face/comparison-sources.md")


def main() -> int:
    subprocess.run([sys.executable, str(ROOT / "tools" / "render_benchmark.py"), "--check"], check=True)
    readme = README.read_text(encoding="utf-8")
    variants = json.loads((ROOT / "benchmarks" / "observed-context-ab.json").read_text(encoding="utf-8"))["variants"]
    context_reduction = round((1 - variants[1]["input_context"] / variants[0]["input_context"]) * 100)
    reads_reduction = round((1 - variants[1]["distinct_file_token_reads"] / variants[0]["distinct_file_token_reads"]) * 100)
    for claim in (f"**{context_reduction}% less input context**", f"**{reads_reduction}% fewer distinct file-token reads**"):
        if claim not in readme:
            raise SystemExit(f"README benchmark claim drift: {claim}")
    for relative in ASSETS:
        content = (ROOT / relative).read_text(encoding="utf-8")
        if "<svg" not in content or "http://" in content.replace("http://www.w3.org/2000/svg", ""):
            raise SystemExit(f"invalid or externally-dependent SVG: {relative}")
        ElementTree.parse(ROOT / relative)
    missing = [relative for relative in REQUIRED_README_REFS if relative not in readme]
    if missing:
        raise SystemExit(f"README public-face references missing: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
