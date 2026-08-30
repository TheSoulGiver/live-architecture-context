#!/usr/bin/env python3
"""Compact read-only CALM callers/callees adapter for archctx."""
from __future__ import annotations

import argparse, json, os, subprocess, sys
from pathlib import Path
from typing import Any

MAX_EDGES = 12


def compact(response: dict[str, Any], direction: str) -> dict[str, Any]:
    result = response.get("result", {})
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        content = result.get("content", []) if isinstance(result, dict) else []
        first = content[0].get("text") if content and isinstance(content[0], dict) else "{}"
        structured = json.loads(first)
    direct = structured.get("direct", [])
    if not isinstance(direct, list):
        direct = []
    edges = [{k: edge[k] for k in ("symbol", "path", "line", "edge_kind", "edge_confidence", "formal_source") if k in edge} for edge in direct[:MAX_EDGES] if isinstance(edge, dict)]
    return {"provider": "CALM", "direction": direction, "edges_ready": structured.get("edges_ready"), "direct_count": structured.get("direct_count", len(direct)), "edges": edges, "truncated": bool(structured.get("direct_truncated")) or len(direct) > MAX_EDGES, "confidence": structured.get("direct_by_confidence"), "caveat": structured.get("caveat")}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo", required=True); parser.add_argument("--symbol", required=True); parser.add_argument("--direction", choices=("upstream", "downstream"), required=True)
    args = parser.parse_args(); repo = Path(args.repo).resolve(); tool = "npx.cmd" if os.name == "nt" else "npx"; method = "callers" if args.direction == "upstream" else "callees"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "archctx", "version": "0.1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": method, "arguments": {"symbol": args.symbol, "transitive": False}}},
    ]
    process = subprocess.run([tool, "-y", "@eilodon/calm-mcp", "serve", "--project-root", str(repo)], input="\n".join(json.dumps(x) for x in requests) + "\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
    replies = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
    response = next((item for item in replies if item.get("id") == 2), None)
    if response is None or "error" in response:
        print(json.dumps({"provider": "CALM", "available": False, "error": (response or {}).get("error") or process.stderr[-1000:]}, separators=(",", ":"))); return 2
    print(json.dumps(compact(response, args.direction), separators=(",", ":"))); return 0


if __name__ == "__main__":
    raise SystemExit(main())
