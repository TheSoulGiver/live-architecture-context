#!/usr/bin/env python3
"""Render the public, anonymized benchmark SVG from its checked-in data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "benchmarks" / "observed-context-ab.json"
OUTPUT = ROOT / "assets" / "context-efficiency.svg"


def percent_change(before: int, after: int) -> int:
    return round((after / before - 1) * 100)


def render(data: dict) -> str:
    before, after = data["variants"]
    context_change = percent_change(before["input_context"], after["input_context"])
    reads_change = percent_change(before["distinct_file_token_reads"], after["distinct_file_token_reads"])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 820" role="img" aria-labelledby="title desc">
  <title id="title">Observed context efficiency in one real dogfood workflow</title>
  <desc id="desc">An anonymized paired comparison shows lower input context and fewer distinct file-token reads for one fresh coding-agent session using Live Architecture Context.</desc>
  <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#08111f"/><stop offset="1" stop-color="#142440"/></linearGradient><linearGradient id="teal" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#28b99d"/><stop offset="1" stop-color="#7debd1"/></linearGradient><style>.k{{font:600 14px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:1px;fill:#a5b6d1}}.h{{font:700 27px Inter,Segoe UI,Arial,sans-serif;fill:#f8fafc}}.l{{font:700 19px Inter,Segoe UI,Arial,sans-serif;fill:#f8fafc}}.v{{font:700 24px ui-monospace,SFMono-Regular,Menlo,monospace;fill:#f8fafc}}.s{{font:500 16px Inter,Segoe UI,Arial,sans-serif;fill:#c6d2e4}}</style></defs>
  <rect width="640" height="820" rx="20" fill="url(#bg)"/><text x="36" y="44" class="k">OBSERVED DOGFOOD / ANONYMIZED PAIRED WORKFLOW</text><text x="36" y="82" class="h">Less context for one fresh Agent session.</text>
  <g transform="translate(36 116)"><rect width="568" height="248" rx="16" fill="#0e1a2d" stroke="#40546f"/><text x="22" y="38" class="l">Input context</text><rect x="392" y="18" width="150" height="48" rx="24" fill="#12443f"/><text x="425" y="50" class="v" fill="#a6f4df">{context_change}%</text><text x="22" y="72" class="k">DIRECT REPOSITORY EXPLORATION</text><rect x="22" y="87" width="390" height="30" rx="6" fill="#485774"/><text x="430" y="111" class="v">{before["input_context"]:,}</text><text x="22" y="158" class="k">WITH LIVE ARCHITECTURE CONTEXT</text><rect x="22" y="173" width="{390 * after["input_context"] / before["input_context"]:.1f}" height="30" rx="6" fill="url(#teal)"/><text x="430" y="197" class="v">{after["input_context"]:,}</text><text x="22" y="229" class="s">tokens supplied across the observed workflow</text></g>
  <g transform="translate(36 392)"><rect width="568" height="248" rx="16" fill="#0e1a2d" stroke="#40546f"/><text x="22" y="38" class="l">Distinct file-token reads</text><rect x="392" y="18" width="150" height="48" rx="24" fill="#12443f"/><text x="425" y="50" class="v" fill="#a6f4df">{reads_change}%</text><text x="22" y="72" class="k">DIRECT REPOSITORY EXPLORATION</text><rect x="22" y="87" width="390" height="30" rx="6" fill="#485774"/><text x="430" y="111" class="v">{before["distinct_file_token_reads"]:,}</text><text x="22" y="158" class="k">WITH LIVE ARCHITECTURE CONTEXT</text><rect x="22" y="173" width="{390 * after["distinct_file_token_reads"] / before["distinct_file_token_reads"]:.1f}" height="30" rx="6" fill="url(#teal)"/><text x="430" y="197" class="v">{after["distinct_file_token_reads"]:,}</text><text x="22" y="229" class="s">unique file-token reads across the workflow</text></g>
  <g transform="translate(36 668)"><rect width="568" height="112" rx="16" fill="#101d32" stroke="#40546f"/><text x="22" y="32" class="k">SCOPE</text><text x="22" y="59" class="s">One observed real workflow. Not a universal token, speed, cost,</text><text x="22" y="82" class="s">or correctness benchmark.</text><text x="22" y="103" class="k">DATA → benchmarks/observed-context-ab.json · RENDER → this SVG</text></g>
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the checked-in SVG differs from rendered data")
    args = parser.parse_args()
    expected = render(json.loads(DATA.read_text(encoding="utf-8")))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("benchmark SVG is stale; run tools/render_benchmark.py")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
