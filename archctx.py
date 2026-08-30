#!/usr/bin/env python3
"""Live Architecture Context: a small, source-grounded coordinator.

It deliberately does not parse code. A code graph adapter (for example CGC)
does that job. This tool pins the architecture claims that agents need, proves
their source locations, and only promotes a snapshot after every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"config must be an object: {path}")
    return value


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def source_revision(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD") or "NO_GIT_REVISION"


def resolve_repo(config_path: Path, config: dict[str, Any]) -> Path:
    raw = config.get("repo", ".")
    if not isinstance(raw, str):
        raise ValueError("repo must be a string")
    return (config_path.parent / raw).resolve()


def components(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = config.get("components")
    if not isinstance(result, list) or not result:
        raise ValueError("components must be a non-empty array")
    ids = [item.get("id") for item in result if isinstance(item, dict)]
    if len(ids) != len(result) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("every component needs a non-empty id")
    if len(set(ids)) != len(ids):
        raise ValueError("component ids must be unique")
    return result


def evidence(repo: Path, component: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    checked: list[dict[str, Any]] = []
    failures: list[str] = []
    entries = component.get("evidence")
    if not isinstance(entries, list) or not entries:
        return checked, [f"{component['id']}: evidence is required"]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            failures.append(f"{component['id']}: malformed evidence")
            continue
        path = (repo / entry["path"]).resolve()
        try:
            path.relative_to(repo)
        except ValueError:
            failures.append(f"{component['id']}: evidence escapes repo: {entry['path']}")
            continue
        if not path.is_file():
            failures.append(f"{component['id']}: missing {entry['path']}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        needle = entry.get("contains")
        if not isinstance(needle, str) or not needle:
            failures.append(f"{component['id']}: evidence contains is required")
            continue
        offset = text.find(needle)
        if offset < 0:
            failures.append(f"{component['id']}: {entry['path']} no longer contains {needle!r}")
            continue
        checked.append({"path": entry["path"].replace("\\", "/"), "contains": needle,
                        "line": text.count("\n", 0, offset) + 1, "sha256": sha(text.encode())})
    return checked, failures


def validate(repo: Path, config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    facts: dict[str, list[dict[str, Any]]] = {}
    failures: list[str] = []
    for component in components(config):
        checked, errors = evidence(repo, component)
        facts[component["id"]] = checked
        failures.extend(errors)
    known = set(facts)
    for relation in config.get("relations", []):
        if not isinstance(relation, dict) or relation.get("from") not in known or relation.get("to") not in known:
            failures.append("relations must only use declared component ids")
    return facts, failures


def state_dir(config_path: Path, explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else config_path.parent / ".archctx"


def snapshot_path(state: Path) -> Path:
    return state / "last-good.json"


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_suffix(path.suffix + ".tmp")
    candidate.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(candidate, path)


def context(config: dict[str, Any], revision: str, facts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result = {"schema_version": 1, "revision": revision, "components": [], "relations": config.get("relations", [])}
    for component in components(config):
        result["components"].append({key: component[key] for key in ("id", "name", "purpose", "truth_sources", "tags") if key in component} |
                                    {"evidence": facts[component["id"]]})
    return result


def semantic_hash(value: dict[str, Any]) -> str:
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def run_graph(config: dict[str, Any], repo: Path, state: Path) -> dict[str, Any]:
    graph = config.get("code_graph")
    if not graph:
        return {"configured": False}
    if not isinstance(graph, dict) or not isinstance(graph.get("refresh"), list):
        raise ValueError("code_graph.refresh must be an argv array")
    replacements = {"{repo}": str(repo), "{state}": str(state / "codegraph")}
    argv = [replacements.get(str(part), str(part)) for part in graph["refresh"]]
    result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=graph.get("timeout_seconds", 180))
    if result.returncode:
        raise RuntimeError(f"code graph refresh failed ({result.returncode}): {result.stderr.strip()[-1000:]}")
    return {"configured": True, "command": argv, "stdout_tail": result.stdout.strip()[-1000:]}


def run_gates(config: dict[str, Any], repo: Path, state: Path) -> list[dict[str, Any]]:
    """Run optional deterministic validators such as Archify without a shell."""
    receipts = []
    for gate in config.get("gates", []):
        if not isinstance(gate, dict) or not isinstance(gate.get("name"), str) or not isinstance(gate.get("command"), list):
            raise ValueError("every gate needs name and command argv")
        replacements = {"{repo}": str(repo), "{state}": str(state)}
        argv = [replacements.get(str(part), str(part)) for part in gate["command"]]
        result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=gate.get("timeout_seconds", 180))
        if result.returncode:
            raise RuntimeError(f"gate {gate['name']} failed ({result.returncode}): {result.stderr.strip()[-1000:]}")
        receipts.append({"name": gate["name"], "command": argv, "stdout_tail": result.stdout.strip()[-1000:]})
    return receipts


def status(config_path: Path, explicit_state: str | None) -> dict[str, Any]:
    config = load_json(config_path)
    repo = resolve_repo(config_path, config)
    state = state_dir(config_path, explicit_state)
    latest = snapshot_path(state)
    if not latest.exists():
        return {"status": "MISSING", "reason": "no last-known-good snapshot", "repo": str(repo)}
    previous = load_json(latest)
    try:
        facts, failures = validate(repo, config)
    except ValueError as error:
        failures = [str(error)]
        facts = {}
    current = context(config, source_revision(repo), facts) if not failures else None
    fresh = current is not None and semantic_hash(current) == previous.get("context_hash")
    return {"status": "FRESH" if fresh else "STALE", "last_good": previous,
            "reason": None if fresh else (failures or ["source revision, evidence, or architecture context changed"])}


def refresh(config_path: Path, explicit_state: str | None) -> dict[str, Any]:
    config = load_json(config_path)
    repo = resolve_repo(config_path, config)
    state = state_dir(config_path, explicit_state)
    facts, failures = validate(repo, config)
    if failures:
        return {"status": "INVALID", "failures": failures, "last_good_preserved": snapshot_path(state).exists()}
    revision = source_revision(repo)
    candidate = context(config, revision, facts)
    try:
        graph = run_graph(config, repo, state)
        gates = run_gates(config, repo, state)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return {"status": "INVALID", "failures": [str(error)], "last_good_preserved": snapshot_path(state).exists()}
    record = {"created_at": datetime.now(timezone.utc).isoformat(), "repo": str(repo), "revision": revision,
              "context_hash": semantic_hash(candidate), "context": candidate, "graph": graph, "gates": gates}
    atomic_write(snapshot_path(state), record)
    atomic_write(state / "snapshots" / f"{record['context_hash']}.json", record)
    return {"status": "PASS", "revision": revision, "context_hash": record["context_hash"], "graph": graph}


def last_good(config_path: Path, explicit_state: str | None) -> dict[str, Any]:
    result = status(config_path, explicit_state)
    if "last_good" not in result:
        return result
    return {"status": result["status"], "context": result["last_good"]["context"], "warning": result.get("reason")}


def canonical(config_path: Path, explicit_state: str | None, component_id: str) -> dict[str, Any]:
    result = last_good(config_path, explicit_state)
    for component in result.get("context", {}).get("components", []):
        if component["id"] == component_id:
            return {"status": result["status"], "canonical": component, "warning": result.get("warning")}
    return {"status": result["status"], "error": f"unknown component: {component_id}", "warning": result.get("warning")}


def trace(config_path: Path, explicit_state: str | None, component_id: str, direction: str) -> dict[str, Any]:
    result = last_good(config_path, explicit_state)
    relations = result.get("context", {}).get("relations", [])
    seen, frontier = {component_id}, [component_id]
    while frontier:
        current = frontier.pop(0)
        for relation in relations:
            left, right = relation.get("from"), relation.get("to")
            neighbor = right if direction == "downstream" and left == current else left if direction == "upstream" and right == current else None
            if neighbor and neighbor not in seen:
                seen.add(neighbor); frontier.append(neighbor)
    return {"status": result["status"], "kind": "authored_architecture_trace", "origin": component_id,
            "direction": direction, "components": sorted(seen), "warning": result.get("warning")}


def impact(config_path: Path, explicit_state: str | None, base: str) -> dict[str, Any]:
    config = load_json(config_path); repo = resolve_repo(config_path, config)
    changed = (git(repo, "diff", "--name-only", f"{base}..HEAD") or "").splitlines()
    owners = []
    for component in components(config):
        paths = {entry.get("path") for entry in component.get("evidence", []) if isinstance(entry, dict)}
        if paths.intersection(changed): owners.append(component["id"])
    traces = [trace(config_path, explicit_state, owner, "downstream")["components"] for owner in owners]
    return {"kind": "authored_architecture_impact", "base": base, "changed_files": changed,
            "direct_components": owners, "reachable_components": sorted(set().union(*map(set, traces)) if traces else set()),
            "note": "Source evidence files map directly; this is not a compiler call-graph claim."}


def changed_since(config_path: Path, explicit_state: str | None, revision: str) -> dict[str, Any]:
    state = state_dir(config_path, explicit_state)
    latest = load_json(snapshot_path(state))
    baseline = next((load_json(path) for path in (state / "snapshots").glob("*.json")
                     if load_json(path).get("revision") == revision), None)
    if baseline is None:
        return {"status": "ERROR", "error": f"no retained snapshot for revision {revision}"}
    old = {item["id"]: item for item in baseline["context"]["components"]}
    new = {item["id"]: item for item in latest["context"]["components"]}
    changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    return {"status": "PASS", "from_revision": revision, "to_revision": latest["revision"],
            "changed_components": changed, "note": "Changes are source-evidence/config deltas, not inferred runtime behavior."}


def drift(config_path: Path, base: str) -> dict[str, Any]:
    config = load_json(config_path); repo = resolve_repo(config_path, config)
    patch = git(repo, "diff", "--unified=0", f"{base}..HEAD") or ""
    candidates = []
    for rule in config.get("drift_rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("pattern"), str): continue
        if any(line.startswith("+") and rule["pattern"] in line[1:] for line in patch.splitlines()):
            candidates.append({"kind": rule.get("kind", "unspecified"), "pattern": rule["pattern"], "status": "CANDIDATE_REVIEW_REQUIRED"})
    return {"base": base, "candidates": candidates, "note": "Only configured, high-value additions are reported; no architecture fact is inferred."}


def mcp_tools() -> list[dict[str, Any]]:
    return [
        {"name": "architecture_status", "description": "Freshness and last-known-good status.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "architecture_canonical", "description": "Canonical component and source evidence.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        {"name": "architecture_trace", "description": "Authored architecture trace; not a compiler call-graph claim.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "direction": {"enum": ["upstream", "downstream"]}}, "required": ["id"]}},
    ]


def serve_mcp(config_path: Path, explicit_state: str | None) -> int:
    """Small JSON-lines MCP stdio surface; transport adapters stay dependency-free."""
    for line in sys.stdin:
        try:
            request = json.loads(line); method = request.get("method"); params = request.get("params", {}); result: Any = None
            if method == "initialize": result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "live-architecture-context", "version": "0.1.0"}}
            elif method == "tools/list": result = {"tools": mcp_tools()}
            elif method == "tools/call":
                arguments = params.get("arguments", {}); name = params.get("name")
                value = status(config_path, explicit_state) if name == "architecture_status" else canonical(config_path, explicit_state, arguments.get("id", "")) if name == "architecture_canonical" else trace(config_path, explicit_state, arguments.get("id", ""), arguments.get("direction", "downstream")) if name == "architecture_trace" else {"status": "ERROR", "error": f"unknown tool: {name}"}
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}], "isError": value.get("status") == "ERROR"}
            elif method and "id" not in request: continue
            else: result = {"error": "method not found"}
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, OSError) as error:
            if "id" in locals().get("request", {}): print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": str(error)}}), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True); parser.add_argument("--state-dir")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status"); sub.add_parser("refresh"); sub.add_parser("snapshot"); sub.add_parser("mcp")
    item = sub.add_parser("canonical"); item.add_argument("id")
    route = sub.add_parser("trace"); route.add_argument("id"); route.add_argument("--direction", choices=("upstream", "downstream"), default="downstream")
    change = sub.add_parser("impact"); change.add_argument("--base", required=True)
    change = sub.add_parser("changed-since"); change.add_argument("--revision", required=True)
    change = sub.add_parser("drift"); change.add_argument("--base", required=True)
    args = parser.parse_args(); config_path = Path(args.config).resolve()
    try:
        if args.command == "mcp": return serve_mcp(config_path, args.state_dir)
        action = {"status": lambda: status(config_path, args.state_dir), "refresh": lambda: refresh(config_path, args.state_dir),
                  "snapshot": lambda: last_good(config_path, args.state_dir), "canonical": lambda: canonical(config_path, args.state_dir, args.id),
                  "trace": lambda: trace(config_path, args.state_dir, args.id, args.direction),
                  "impact": lambda: impact(config_path, args.state_dir, args.base),
                  "changed-since": lambda: changed_since(config_path, args.state_dir, args.revision),
                  "drift": lambda: drift(config_path, args.base)}[args.command]
        dump(action()); return 0
    except (ValueError, OSError) as error:
        dump({"status": "ERROR", "error": str(error)}); return 2


if __name__ == "__main__":
    raise SystemExit(main())
