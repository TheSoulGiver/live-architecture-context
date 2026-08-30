#!/usr/bin/env python3
"""Source-grounded live architecture context; CALM/Archify remain external owners."""
from __future__ import annotations

import argparse, fnmatch, hashlib, json, os, subprocess, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_VERSION, PROTOCOL_VERSION, SERVER_VERSION = 1, "1.0", "0.1.0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def dump(x: Any) -> None: print(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
def sha(x: bytes) -> str: return hashlib.sha256(x).hexdigest()
def load(path: Path) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e: raise ValueError(f"invalid JSON: {path}: {e}") from e
    if not isinstance(value, dict): raise ValueError(f"JSON object required: {path}")
    return value
def git(repo: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), *args], text=True, encoding="utf-8", errors="replace", capture_output=True)
    return r.stdout.strip() if r.returncode == 0 else None
def revision(repo: Path, facts: dict[str, list[dict[str, Any]]] | None = None) -> str:
    """Prefer an immutable commit; read-only/dubious worktrees get an explicit evidence binding."""
    return git(repo, "rev-parse", "HEAD") or "SOURCE_EVIDENCE:" + semantic(facts or {})[:16]
def state(config_path: Path, explicit: str | None) -> Path:
    if explicit: return Path(explicit).resolve()
    if config_path.parent.name != ".archctx": return config_path.parent / ".archctx"
    legacy = config_path.parent / ".archctx"
    return legacy if legacy.exists() else config_path.parent
def last_path(directory: Path) -> Path: return directory / "last-good.json"
def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(temporary, path)
def telemetry(directory: Path, event: str, value: dict[str, Any], elapsed_ms: int, query: str | None = None) -> None:
    """Best-effort local metrics; never retain source, evidence, or raw task text."""
    try:
        record: dict[str, Any] = {"version": 1, "at": datetime.now(timezone.utc).isoformat(), "event": event, "status": value.get("status"), "freshness": value.get("freshness"), "revision": value.get("revision"), "elapsed_ms": elapsed_ms, "response_bytes": len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())}
        if query is not None: record["query"] = {"sha256": sha(query.encode()), "length": len(query)}
        for key in ("matches", "changed_files", "direct_components", "candidates"):
            if isinstance(value.get(key), list): record[f"{key}_count"] = len(value[key])
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "telemetry.jsonl").open("a", encoding="utf-8") as output: output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError: pass
def telemetry_summary(directory: Path) -> dict[str, Any]:
    events, statuses, bytes_total, elapsed_total = Counter(), Counter(), 0, 0
    path = directory / "telemetry.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try: record = json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(record, dict):
                events[str(record.get("event"))] += 1; statuses[str(record.get("status"))] += 1; bytes_total += int(record.get("response_bytes", 0)); elapsed_total += int(record.get("elapsed_ms", 0))
    count = sum(events.values())
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "privacy": "local metrics only; no source, evidence, or raw task text", "events": dict(sorted(events.items())), "statuses": dict(sorted(statuses.items())), "event_count": count, "response_bytes": bytes_total, "average_latency_ms": elapsed_total // count if count else 0}

def repo_for(config_path: Path, config: dict[str, Any]) -> Path:
    raw = config.get("repo", ".")
    if not isinstance(raw, str): raise ValueError("repo must be a string")
    repo = (config_path.parent / raw).resolve()
    if not repo.is_dir(): raise ValueError(f"repo does not exist: {repo}")
    return repo

def components(config: dict[str, Any]) -> list[dict[str, Any]]:
    if config.get("version") != CONFIG_VERSION: raise ValueError(f"unsupported config version {config.get('version')!r}; expected {CONFIG_VERSION}")
    xs = config.get("components")
    if not isinstance(xs, list) or not xs: raise ValueError("components must be a non-empty array")
    ids = []
    for x in xs:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not x["id"]: raise ValueError("every component needs a non-empty id")
        if not isinstance(x.get("evidence"), list) or not x["evidence"]: raise ValueError(f"{x['id']}: evidence is required")
        ids.append(x["id"])
    if len(ids) != len(set(ids)): raise ValueError("component ids must be unique")
    known = set(ids)
    for relation in config.get("relations", []):
        if not isinstance(relation, dict) or relation.get("from") not in known or relation.get("to") not in known: raise ValueError("relations must use declared component ids")
        if not isinstance(relation.get("kind"), str) or not relation["kind"]: raise ValueError("every relation needs explicit kind")
    return xs

def evidence(repo: Path, component: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    facts, failures = [], []
    for item in component["evidence"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("contains"), str) or not item["contains"]:
            failures.append(f"{component['id']}: every evidence item needs path and contains"); continue
        path = (repo / item["path"]).resolve()
        try: path.relative_to(repo)
        except ValueError: failures.append(f"{component['id']}: evidence escapes repo: {item['path']}"); continue
        if not path.is_file(): failures.append(f"{component['id']}: missing {item['path']}"); continue
        text = path.read_text(encoding="utf-8", errors="replace"); offset = text.find(item["contains"])
        if offset < 0: failures.append(f"{component['id']}: {item['path']} no longer contains {item['contains']!r}"); continue
        facts.append({"path": item["path"].replace("\\", "/"), "contains": item["contains"], "line": text.count("\n", 0, offset) + 1, "sha256": sha(text.encode())})
    return facts, failures

def validate(repo: Path, config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    facts, failures = {}, []
    try: xs = components(config)
    except ValueError as e: return facts, [str(e)]
    for x in xs:
        facts[x["id"]], errors = evidence(repo, x); failures.extend(errors)
    return facts, failures

def context(config: dict[str, Any], rev: str, facts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": CONFIG_VERSION, "revision": rev, "components": [], "relations": [{**r, "provenance": "authored_architecture"} for r in config.get("relations", [])]}
    for x in components(config):
        result["components"].append({k: x[k] for k in ("id", "name", "purpose", "truth_sources", "tags", "code_symbol") if k in x} | {"evidence": facts[x["id"]], "confidence": "source_evidence"})
    return result
def semantic(value: dict[str, Any]) -> str: return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
def substitution(command: list[Any], values: dict[str, str]) -> list[str]: return [values.get(str(x), str(x)) for x in command]
def run(command: list[Any], repo: Path, timeout: int, values: dict[str, str], extra_env: dict[str, Any] | None = None) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    argv = substitution(command, values); env = os.environ.copy()
    if extra_env:
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()): raise ValueError("command env must be a string map")
        env.update(extra_env)
    return argv, subprocess.run(argv, cwd=repo, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, env=env)

def graph_refresh(config: dict[str, Any], repo: Path, directory: Path, changed: list[str] | None) -> dict[str, Any]:
    graph = config.get("code_graph")
    if not graph: return {"configured": False, "freshness": "not_configured"}
    if not isinstance(graph, dict): raise ValueError("code_graph must be an object")
    incremental = changed and isinstance(graph.get("incremental"), list)
    command = graph.get("incremental") if incremental else graph.get("refresh")
    if not isinstance(command, list): return {"configured": True, "provider": graph.get("provider", "external"), "endpoint": graph.get("endpoint"), "index": graph.get("index"), "freshness": "external_daemon_unverified"}
    argv, r = run(command, repo, int(graph.get("timeout_seconds", 180)), {"{repo}": str(repo), "{state}": str(directory / "codegraph"), "{changed_files}": json.dumps(changed or [])})
    if r.returncode: raise RuntimeError(f"code graph command failed ({r.returncode}): {r.stderr.strip()[-1000:]}")
    return {"configured": True, "provider": graph.get("provider", "external"), "freshness": "incremental" if incremental else "refreshed", "command": argv, "stdout_tail": r.stdout.strip()[-1000:]}

def gates(config: dict[str, Any], repo: Path, directory: Path) -> list[dict[str, Any]]:
    receipts = []
    for gate in config.get("gates", []):
        if not isinstance(gate, dict) or not isinstance(gate.get("name"), str) or not isinstance(gate.get("command"), list): raise ValueError("every gate needs name and command argv")
        argv, r = run(gate["command"], repo, int(gate.get("timeout_seconds", 180)), {"{repo}": str(repo), "{state}": str(directory)}, gate.get("env"))
        if r.returncode: raise RuntimeError(f"gate {gate['name']} failed ({r.returncode}): {r.stderr.strip()[-1000:]}")
        receipts.append({"name": gate["name"], "command": argv, "stdout_tail": r.stdout.strip()[-1000:]})
    return receipts

def freshness(record: dict[str, Any], status: str, reason: Any = None) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "status": status, "revision": record.get("revision"), "freshness": "fresh" if status in ("FRESH", "PASS") else "stale", "last_good_at": record.get("created_at"), "confidence": "source_evidence", "reason": reason, "next_action": "query normally" if status in ("FRESH", "PASS") else "run refresh after fixing the reported change"}

def status(config_path: Path, explicit: str | None) -> dict[str, Any]:
    config = load(config_path); directory = state(config_path, explicit); old_path = last_path(directory)
    try:
        repo = repo_for(config_path, config); facts, failures = validate(repo, config)
    except ValueError as e: repo, facts, failures = None, {}, [str(e)]
    if not old_path.exists(): return {"protocol_version": PROTOCOL_VERSION, "status": "MISSING", "freshness": "missing", "repo": str(repo) if repo else None, "next_action": "run refresh"}
    old = load(old_path); current = context(config, revision(repo, facts), facts) if repo and not failures else None
    fresh = current is not None and semantic(current) == old.get("context_hash") and semantic(config) == old.get("config_hash")
    return freshness(old, "FRESH" if fresh else "STALE", None if fresh else (failures or ["source revision, config, or architecture context changed"])) | {"last_good": old}

def refresh(config_path: Path, explicit: str | None, changed: list[str] | None = None) -> dict[str, Any]:
    config, directory = load(config_path), state(config_path, explicit)
    try:
        repo = repo_for(config_path, config); facts, failures = validate(repo, config)
    except ValueError as e: repo, facts, failures = None, {}, [str(e)]
    if failures: return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": failures, "last_good_preserved": last_path(directory).exists(), "next_action": "repair evidence/config, then refresh"}
    candidate = context(config, revision(repo, facts), facts)
    try: graph, receipts = graph_refresh(config, repo, directory, changed), gates(config, repo, directory)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as e: return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [str(e)], "last_good_preserved": last_path(directory).exists(), "next_action": "repair external validator, then refresh"}
    record = {"record_version": CONFIG_VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "repo": str(repo), "revision": revision(repo, facts), "config_hash": semantic(config), "context_hash": semantic(candidate), "context": candidate, "graph": graph, "gates": receipts}
    atomic(last_path(directory), record); atomic(directory / "snapshots" / f"{record['context_hash']}.json", record)
    return freshness(record, "PASS") | {"context_hash": record["context_hash"], "graph": graph, "gates": receipts, "changed_files": changed or []}

def snapshot(config_path: Path, explicit: str | None) -> dict[str, Any]:
    value = status(config_path, explicit); old = value.pop("last_good", None)
    return value if old is None else value | {"context": old["context"], "graph": old.get("graph"), "gates": old.get("gates", [])}
def canonical(config_path: Path, explicit: str | None, ident: str) -> dict[str, Any]:
    value = snapshot(config_path, explicit)
    for x in value.get("context", {}).get("components", []):
        if x["id"] == ident: return {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "confidence", "next_action") if k in value} | {"canonical": x, "warning": value.get("reason")}
    return {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "next_action") if k in value} | {"error": f"unknown component: {ident}", "warning": value.get("reason")}

def search(config_path: Path, explicit: str | None, query: str) -> dict[str, Any]:
    value = snapshot(config_path, explicit)
    terms = [term for term in query.casefold().split() if term]
    matches = []
    for item in value.get("context", {}).get("components", []):
        haystack = json.dumps({key: item.get(key) for key in ("id", "name", "purpose", "tags", "truth_sources")}, ensure_ascii=False).casefold()
        score = sum(term in haystack for term in terms)
        if score:
            matches.append((score, item))
    compact = [{key: item[key] for key in ("id", "name", "purpose", "tags", "truth_sources", "evidence") if key in item} for _, item in sorted(matches, key=lambda row: (-row[0], row[1]["id"]))]
    return {key: value[key] for key in ("protocol_version", "status", "revision", "freshness", "confidence", "next_action") if key in value} | {"query": query, "matches": compact, "warning": value.get("reason")}

CODEX_BEGIN, CODEX_END = "<!-- archctx:begin -->", "<!-- archctx:end -->"

def codex_block(relative_config: str, command: str) -> str:
    return f'''{CODEX_BEGIN}
## Architecture context

For substantive work, first run `{command} --config {relative_config} status`.
If `FRESH`, use search or matching canonical/trace only when it narrows the task, then read only returned evidence; otherwise do normal targeted discovery.
If `STALE`, verify cited source before using last-good; if unavailable, develop normally without architecture claims.
Before architecture-relevant edits run `impact --files <paths>`; checkpoint with `refresh` and `changed-since` when relevant.
This narrows first reads only: it never bypasses repo safety, tests, or release rules. Authored traces are not CALM code edges.
{CODEX_END}
'''

def install_codex(config_path: Path, target: Path, check: bool) -> dict[str, Any]:
    config, repo = load(config_path), repo_for(config_path, load(config_path))
    components(config)
    try: relative = config_path.relative_to(repo).as_posix()
    except ValueError as error: raise ValueError("Codex config must live inside its repository (normally .archctx/architecture.json)") from error
    block = codex_block(relative, subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve())]))
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    start, end = before.find(CODEX_BEGIN), before.find(CODEX_END)
    if (start < 0) != (end < 0): raise ValueError(f"unbalanced archctx markers in {target}")
    if start >= 0:
        end += len(CODEX_END)
        if before[end:end + 1] == "\n": end += 1
        remainder = before[:start] + before[end:]
        after = block + ("\n" if remainder.strip() else "") + remainder.lstrip()
        action = "updated"
    else:
        after = block + ("\n" if before.strip() else "") + before; action = "created"
    if not check and after != before:
        target.write_text(after, encoding="utf-8")
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "unchanged" if after == before else action, "target": str(target), "config": relative, "check": check, "next_action": "start a new Codex session in this repo"}

def uninstall_codex(target: Path, check: bool) -> dict[str, Any]:
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    start, end = before.find(CODEX_BEGIN), before.find(CODEX_END)
    if (start < 0) != (end < 0): raise ValueError(f"unbalanced archctx markers in {target}")
    if start < 0: return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "unchanged", "target": str(target), "config_preserved": True, "check": check}
    end += len(CODEX_END)
    if before[end:end + 1] == "\n": end += 1
    after = before[:start] + before[end:]
    if start == 0 and after.startswith("\n"): after = after[1:]
    if not check: target.write_text(after, encoding="utf-8")
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "removed", "target": str(target), "config_preserved": True, "check": check}

def ensure_ignored(repo: Path, check: bool) -> bool:
    path = repo / ".gitignore"; before = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = {line.strip().replace("\\", "/") for line in before.splitlines() if line.strip() and not line.lstrip().startswith("#")}
    if ".archctx/" in entries: return False
    if not check: path.write_text(before + ("" if not before or before.endswith("\n") else "\n") + ".archctx/\n", encoding="utf-8")
    return True

def parsed_evidence(values: list[str]) -> list[dict[str, str]]:
    result = []
    for value in values:
        path, separator, contains = value.partition("::")
        if not separator or not path or not contains: raise ValueError("--evidence must be PATH::EXACT_SOURCE_TEXT")
        result.append({"path": path.replace("\\", "/"), "contains": contains})
    if not result: raise ValueError("new onboarding needs --evidence PATH::EXACT_SOURCE_TEXT; source evidence is required")
    return result

def init(repo: Path, target: Path, component: str | None, truth_sources: list[str], evidence_values: list[str], check: bool) -> dict[str, Any]:
    if not repo.is_dir(): raise ValueError(f"repo does not exist: {repo}")
    config_path = repo / ".archctx" / "architecture.json"; created = False
    if not config_path.exists():
        if check: raise ValueError("init --check needs an existing .archctx/architecture.json")
        if not component: raise ValueError("new onboarding needs --component and --evidence; no architecture is inferred")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        facts = parsed_evidence(evidence_values)
        config = {"version": CONFIG_VERSION, "repo": "..", "components": [{"id": component, "truth_sources": truth_sources or [x["path"] for x in facts], "evidence": facts}], "relations": []}
        if not check: atomic(config_path, config)
        created = True
    config = load(config_path)
    if repo_for(config_path, config) != repo.resolve(): raise ValueError("onboarding config repo must resolve to --repo")
    ignored_added = ensure_ignored(repo, check)
    install = install_codex(config_path, target, check)
    refreshed = refresh(config_path, None) if not check else {"status": "CHECK"}
    return {"protocol_version": PROTOCOL_VERSION, "status": refreshed["status"], "action": "created" if created else "updated", "config": str(config_path), "gitignore_updated": ignored_added, "codex": install, "refresh": refreshed, "next_action": "start a new Codex session in this repo"}

def authored(context_value: dict[str, Any], ident: str, direction: str) -> list[str]:
    seen, todo = {ident}, [ident]
    while todo:
        current = todo.pop(0)
        for r in context_value.get("relations", []):
            neighbor = r.get("to") if direction == "downstream" and r.get("from") == current else r.get("from") if direction == "upstream" and r.get("to") == current else None
            if neighbor and neighbor not in seen: seen.add(neighbor); todo.append(neighbor)
    return sorted(seen)
def code_query(config: dict[str, Any], repo: Path, directory: Path, symbol: str, direction: str) -> dict[str, Any]:
    graph = config.get("code_graph", {})
    if not isinstance(graph, dict) or not isinstance(graph.get("query"), list): return {"available": False, "reason": "no code_graph.query configured"}
    argv, r = run(graph["query"], repo, int(graph.get("timeout_seconds", 30)), {"{repo}": str(repo), "{state}": str(directory / "codegraph"), "{symbol}": symbol, "{direction}": direction})
    if r.returncode: return {"available": False, "provider": graph.get("provider", "external"), "error": r.stderr.strip()[-1000:], "command": argv}
    try: result: Any = json.loads(r.stdout)
    except json.JSONDecodeError: result = {"stdout_tail": r.stdout.strip()[-1000:]}
    return {"available": True, "kind": "code_graph_query", "provider": graph.get("provider", "external"), "confidence": "provider_reported", "result": result}
def trace(config_path: Path, explicit: str | None, ident: str, direction: str, include_code: bool = False) -> dict[str, Any]:
    value = snapshot(config_path, explicit); ctx = value.get("context", {})
    answer = {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "next_action") if k in value} | {"kind": "authored_architecture_trace", "provenance": "authored_architecture", "origin": ident, "direction": direction, "components": authored(ctx, ident, direction), "warning": value.get("reason")}
    if include_code and ident in {x["id"] for x in ctx.get("components", [])}:
        config = load(config_path); component = next(x for x in ctx["components"] if x["id"] == ident); answer["code_graph"] = code_query(config, repo_for(config_path, config), state(config_path, explicit), component.get("code_symbol", ident), direction)
    return answer

def paths_for(repo: Path, base: str | None, files: list[str] | None) -> list[str]:
    if files: return sorted({x.replace("\\", "/") for x in files})
    if not base: raise ValueError("impact needs --base or --files")
    result = git(repo, "diff", "--name-only", f"{base}..HEAD")
    if result is None: raise ValueError(f"cannot diff base {base}")
    return [x.replace("\\", "/") for x in result.splitlines() if x]
def owners(config: dict[str, Any], paths: list[str]) -> list[str]:
    changed = set(paths); return [x["id"] for x in components(config) if changed.intersection({e.get("path", "").replace("\\", "/") for e in x["evidence"] if isinstance(e, dict)})]
def impact(config_path: Path, explicit: str | None, base: str | None, files: list[str] | None) -> dict[str, Any]:
    config = load(config_path); changed = paths_for(repo_for(config_path, config), base, files); direct = owners(config, changed); ctx = snapshot(config_path, explicit).get("context", {})
    reach = sorted(set().union(*(set(authored(ctx, x, "downstream")) for x in direct))) if direct else []
    return {"protocol_version": PROTOCOL_VERSION, "kind": "authored_architecture_impact", "provenance": "source_evidence_plus_authored_architecture", "base": base, "changed_files": changed, "direct_components": direct, "reachable_components": reach, "code_graph": {"available": isinstance(config.get("code_graph"), dict), "note": "Code edges remain separate provider facts; use trace --code."}, "next_action": "refresh" if direct else "no canonical evidence owner; no architecture refresh needed"}

def retained(directory: Path, rev: str) -> dict[str, Any] | None:
    matches = [load(path) for path in (directory / "snapshots").glob("*.json") if load(path).get("revision") == rev]
    return min(matches, key=lambda value: str(value.get("created_at", ""))) if matches else None
def record_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    a, b = {x["id"]: x for x in old["context"]["components"]}, {x["id"]: x for x in new["context"]["components"]}
    ar, br = set(json.dumps(x, sort_keys=True) for x in old["context"].get("relations", [])), set(json.dumps(x, sort_keys=True) for x in new["context"].get("relations", []))
    return {"changed_components": sorted(x for x in a.keys() | b.keys() if a.get(x) != b.get(x)), "added_relations": [json.loads(x) for x in sorted(br-ar)], "removed_relations": [json.loads(x) for x in sorted(ar-br)]}
def changed_since(config_path: Path, explicit: str | None, rev: str) -> dict[str, Any]:
    directory = state(config_path, explicit)
    if not last_path(directory).exists(): return {"status": "MISSING", "next_action": "run refresh"}
    old = retained(directory, rev)
    if old is None: return {"status": "ERROR", "error": f"no retained snapshot for revision {rev}"}
    new = load(last_path(directory)); return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "from_revision": rev, "to_revision": new["revision"]} | record_diff(old, new)
def delta(config_path: Path, explicit: str | None, rev: str) -> dict[str, Any]: return changed_since(config_path, explicit, rev) | {"kind": "architecture_delta", "provenance": "retained_source_evidence_snapshots", "archify": "Use configured Archify gate receipts for typed IR/visual delta; no renderer is reimplemented here."}
def drift(config_path: Path, base: str) -> dict[str, Any]:
    config = load(config_path); patch = git(repo_for(config_path, config), "diff", "--unified=0", f"{base}..HEAD")
    if patch is None: raise ValueError(f"cannot diff base {base}")
    found = []
    for rule in config.get("drift_rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("pattern"), str) or not rule["pattern"]: continue
        patterns = rule.get("paths", ["*"])
        if not isinstance(patterns, list): raise ValueError("drift rule paths must be an array")
        path = ""
        for line in patch.splitlines():
            if line.startswith("+++ b/"): path = line[6:]
            elif line.startswith("+") and not line.startswith("+++") and rule["pattern"] in line[1:] and any(fnmatch.fnmatch(path, str(x)) for x in patterns):
                candidate = {"kind": rule.get("kind", "unspecified"), "path": path, "pattern": rule["pattern"], "status": "CANDIDATE_REVIEW_REQUIRED"}
                if candidate not in found: found.append(candidate)
    return {"protocol_version": PROTOCOL_VERSION, "base": base, "candidates": found, "note": "Configured high-value additions only; candidates are not architecture facts.", "next_action": "review candidates, then update canonical config if appropriate" if found else "none"}

def ignored(path: str, patterns: list[Any]) -> bool: return any(fnmatch.fnmatch(path, str(x)) for x in patterns)
def manifest(repo: Path, config: dict[str, Any]) -> dict[str, str | None]:
    paths = {e["path"].replace("\\", "/") for x in components(config) for e in x["evidence"] if isinstance(e, dict) and isinstance(e.get("path"), str)}
    watch = config.get("watch", {})
    if not isinstance(watch, dict): raise ValueError("watch must be an object")
    for pattern in watch.get("paths", []):
        if not isinstance(pattern, str): raise ValueError("watch.paths must contain strings")
        paths.update(x.relative_to(repo).as_posix() for x in repo.glob(pattern) if x.is_file())
    return {p: sha((repo / p).read_bytes()) if (repo / p).is_file() else None for p in sorted(paths) if not ignored(p, watch.get("ignore", []))}
def watch_once(config_path: Path, explicit: str | None) -> dict[str, Any]:
    config = load(config_path); repo, directory = repo_for(config_path, config), state(config_path, explicit); live = directory / "live-state.json"; current = manifest(repo, config)
    previous = load(live).get("manifest", {}) if live.exists() else None; atomic(live, {"manifest": current, "observed_at": datetime.now(timezone.utc).isoformat()})
    if previous is None: return {"protocol_version": PROTOCOL_VERSION, "status": "WATCH_READY", "watched_files": len(current), "next_action": "keep watching"}
    changed = sorted(p for p in set(previous) | set(current) if previous.get(p) != current.get(p)); direct = owners(config, changed)
    if not direct: return {"protocol_version": PROTOCOL_VERSION, "status": "NO_RELEVANT_CHANGE", "changed_files": changed, "direct_components": [], "next_action": "no architecture refresh"}
    return refresh(config_path, explicit, changed) | {"event": "CANONICAL_EVIDENCE_CHANGED", "direct_components": direct, "watch_changed_files": changed}
def watch(config_path: Path, explicit: str | None, poll_ms: int, max_events: int | None) -> int:
    count = 0
    while max_events is None or count < max_events:
        value = watch_once(config_path, explicit); dump(value)
        if value["status"] not in ("WATCH_READY", "NO_RELEVANT_CHANGE"): count += 1
        time.sleep(max(50, poll_ms) / 1000)
    return 0

def mcp_tools() -> list[dict[str, Any]]:
    empty = {"type": "object", "properties": {}}; ident = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    return [{"name": "architecture_status", "description": "Freshness and last-known-good status.", "inputSchema": empty}, {"name": "architecture_refresh", "description": "Validate and atomically promote context.", "inputSchema": empty}, {"name": "architecture_snapshot", "description": "Compact last-known-good context.", "inputSchema": empty}, {"name": "architecture_canonical", "description": "Canonical component and evidence.", "inputSchema": ident}, {"name": "architecture_search", "description": "Match the current task to compact canonical components.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}, {"name": "architecture_evidence", "description": "Source evidence for one component.", "inputSchema": ident}, {"name": "architecture_trace", "description": "Authored relations; optional code graph stays separate.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "direction": {"enum": ["upstream", "downstream"]}, "include_code_edges": {"type": "boolean"}}, "required": ["id"]}}, {"name": "architecture_impact", "description": "Changed files to canonical ownership.", "inputSchema": {"type": "object", "properties": {"base": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}}}, {"name": "architecture_changed_since", "description": "Retained architecture delta by revision.", "inputSchema": {"type": "object", "properties": {"revision": {"type": "string"}}, "required": ["revision"]}}, {"name": "architecture_drift", "description": "Configured high-value drift candidates only.", "inputSchema": {"type": "object", "properties": {"base": {"type": "string"}}, "required": ["base"]}}, {"name": "architecture_stale", "description": "Alias for freshness status.", "inputSchema": empty}]
def mcp_value(config_path: Path, explicit: str | None, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in ("architecture_status", "architecture_stale"): return status(config_path, explicit)
    if name == "architecture_refresh": return refresh(config_path, explicit)
    if name == "architecture_snapshot": return snapshot(config_path, explicit)
    if name == "architecture_canonical": return canonical(config_path, explicit, str(args.get("id", "")))
    if name == "architecture_search": return search(config_path, explicit, str(args.get("query", "")))
    if name == "architecture_evidence":
        value = canonical(config_path, explicit, str(args.get("id", ""))); return {k: value[k] for k in value if k != "canonical"} | {"evidence": value.get("canonical", {}).get("evidence", [])}
    if name == "architecture_trace": return trace(config_path, explicit, str(args.get("id", "")), str(args.get("direction", "downstream")), bool(args.get("include_code_edges")))
    if name == "architecture_impact": return impact(config_path, explicit, args.get("base"), args.get("files"))
    if name == "architecture_changed_since": return changed_since(config_path, explicit, str(args.get("revision", "")))
    if name == "architecture_drift": return drift(config_path, str(args.get("base", "")))
    return {"status": "ERROR", "error": f"unknown tool: {name}"}
def serve_mcp(config_path: Path, explicit: str | None) -> int:
    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line); method, params = request.get("method"), request.get("params", {})
            if method == "initialize": result: Any = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "live-architecture-context", "version": SERVER_VERSION}}
            elif method == "tools/list": result = {"tools": mcp_tools()}
            elif method == "tools/call":
                name, arguments = str(params.get("name", "")), params.get("arguments", {})
                value = mcp_value(config_path, explicit, name, arguments); telemetry(state(config_path, explicit), f"mcp:{name}", value, 0, str(arguments.get("query")) if isinstance(arguments.get("query"), str) else None); result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}], "isError": value.get("status") in ("ERROR", "INVALID")}
            elif "id" not in request: continue
            else: raise ValueError("method not found")
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, OSError) as e:
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": str(e)}}, ensure_ascii=False), flush=True)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config"); parser.add_argument("--state-dir"); sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "refresh", "snapshot", "mcp", "telemetry"): sub.add_parser(name)
    x = sub.add_parser("canonical"); x.add_argument("id"); x = sub.add_parser("search"); x.add_argument("--query", required=True); x = sub.add_parser("evidence"); x.add_argument("id")
    x = sub.add_parser("trace"); x.add_argument("id"); x.add_argument("--direction", choices=("upstream", "downstream"), default="downstream"); x.add_argument("--code", action="store_true")
    x = sub.add_parser("impact"); x.add_argument("--base"); x.add_argument("--files", nargs="*")
    x = sub.add_parser("changed-since"); x.add_argument("--revision", required=True); x = sub.add_parser("delta"); x.add_argument("--revision", required=True); x = sub.add_parser("drift"); x.add_argument("--base", required=True)
    x = sub.add_parser("watch"); x.add_argument("--once", action="store_true"); x.add_argument("--poll-ms", type=int, default=500); x.add_argument("--max-events", type=int)
    x = sub.add_parser("install-codex"); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--check", action="store_true")
    x = sub.add_parser("uninstall-codex"); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--check", action="store_true")
    x = sub.add_parser("init"); x.add_argument("--repo", default="."); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--component"); x.add_argument("--truth-source", action="append", default=[]); x.add_argument("--evidence", action="append", default=[]); x.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            repo = Path(args.repo).resolve(); target = Path(args.target); target = target if target.is_absolute() else repo / target
            dump(init(repo, target, args.component, args.truth_source, args.evidence, args.check)); return 0
        if not args.config: raise ValueError("--config is required except for init")
        config_path = Path(args.config).resolve()
        if args.command == "mcp": return serve_mcp(config_path, args.state_dir)
        if args.command == "telemetry": dump(telemetry_summary(state(config_path, args.state_dir))); return 0
        if args.command == "watch":
            if args.once: dump(watch_once(config_path, args.state_dir)); return 0
            return watch(config_path, args.state_dir, args.poll_ms, args.max_events)
        target = Path(args.target) if args.command in ("install-codex", "uninstall-codex") else None
        install_target = target if target and target.is_absolute() else (repo_for(config_path, load(config_path)) / target) if target else None
        actions = {"status": lambda: status(config_path, args.state_dir), "refresh": lambda: refresh(config_path, args.state_dir), "snapshot": lambda: snapshot(config_path, args.state_dir), "canonical": lambda: canonical(config_path, args.state_dir, args.id), "search": lambda: search(config_path, args.state_dir, args.query), "evidence": lambda: mcp_value(config_path, args.state_dir, "architecture_evidence", {"id": args.id}), "trace": lambda: trace(config_path, args.state_dir, args.id, args.direction, args.code), "impact": lambda: impact(config_path, args.state_dir, args.base, args.files), "changed-since": lambda: changed_since(config_path, args.state_dir, args.revision), "delta": lambda: delta(config_path, args.state_dir, args.revision), "drift": lambda: drift(config_path, args.base), "install-codex": lambda: install_codex(config_path, install_target.resolve(), args.check), "uninstall-codex": lambda: uninstall_codex(install_target.resolve(), args.check)}
        started = time.monotonic(); value = actions[args.command](); telemetry(state(config_path, args.state_dir), args.command, value, int((time.monotonic() - started) * 1000), args.query if args.command == "search" else None); dump(value); return 0
    except (ValueError, OSError) as e: dump({"protocol_version": PROTOCOL_VERSION, "status": "ERROR", "error": str(e)}); return 2
if __name__ == "__main__": raise SystemExit(main())
