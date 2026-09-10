#!/usr/bin/env python3
"""Source-grounded live architecture context; CALM/Archify remain external owners."""
from __future__ import annotations

import argparse, fnmatch, hashlib, json, os, shlex, shutil, subprocess, sys, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_VERSION, PROTOCOL_VERSION, SERVER_VERSION = 1, "1.0", "0.1.7"
COMPONENT_FIELDS = ("id", "name", "purpose", "truth_sources", "tags", "code_symbol")
# Capture core source identity at import, not after a running MCP's file is replaced.
CORE_SOURCE_PATH = Path(__file__).resolve()
try:
    CORE_SOURCE_SHA256 = hashlib.sha256(CORE_SOURCE_PATH.read_bytes()).hexdigest()
except OSError:
    CORE_SOURCE_SHA256 = None
SNAPSHOT_LIMIT, GENERATION_LIMIT, USAGE_LIMIT, USAGE_BYTES = 32, 4, 128, 64 * 1024
CANDIDATE_ENTRY_LIMIT, CANDIDATE_BYTES, CANDIDATE_OUTPUT_LIMIT = 64, 32 * 1024, 8
CANDIDATE_FILE_LIMIT, CANDIDATE_SOURCE_BYTES = 256, 4 * 1024 * 1024
WATCH_FILE_LIMIT, WATCH_SOURCE_BYTES = 512, 8 * 1024 * 1024
UPDATES_BYTES = 16 * 1024
DECISION_LIMIT, DECISION_BYTES = 64, 64 * 1024
USAGE_OPERATIONS = {"refresh", "snapshot", "canonical", "search", "evidence", "trace", "impact", "changed-since", "delta", "drift", "candidates", "accept", "reject", "watch"}
OBSERVATIONAL_OPERATIONS = {"status", "telemetry", "history", "usage", "updates"}
ACTIONABLE_OUTCOMES = {"promoted", "context_available", "matched", "evidence", "related", "affected", "changed", "candidate", "recovery_required"}
NO_FINDING_OUTCOMES = {"empty", "unrelated", "unaffected", "unchanged", "none"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def dump(x: Any) -> None: print(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
def sha(x: bytes) -> str: return hashlib.sha256(x).hexdigest()
def load(path: Path) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e: raise ValueError(f"invalid JSON: {path}: {e}") from e
    if not isinstance(value, dict): raise ValueError(f"JSON object required: {path}")
    return value
def load_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value, raw
def git(repo: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), *args], text=True, encoding="utf-8", errors="replace", capture_output=True)
    return r.stdout.strip() if r.returncode == 0 else None
def revision(repo: Path, facts: Any = None) -> str:
    """Prefer an immutable commit; read-only/dubious worktrees get an explicit evidence binding."""
    return git(repo, "rev-parse", "HEAD") or "SOURCE_EVIDENCE:" + semantic(facts or {})[:16]
def state(config_path: Path, explicit: str | None) -> Path:
    if explicit: return Path(explicit).resolve()
    if config_path.parent.name != ".archctx": return config_path.parent / ".archctx"
    legacy = config_path.parent / ".archctx"
    return legacy if legacy.exists() else config_path.parent
def last_path(directory: Path) -> Path: return directory / "last-good.json"


class RefreshBusyError(RuntimeError):
    """Another cooperative Archctx writer owns this state directory."""


def atomic_bytes(path: Path, value: bytes) -> None:
    """Replace one file without a shared temporary-name race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def atomic(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


@contextmanager
def refresh_lock(directory: Path):
    """A non-waiting, process-safe writer lock; the OS releases it on exit."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "refresh.lock"
    handle = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RefreshBusyError("another refresh is already validating this architecture context") from error
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RefreshBusyError("another refresh is already validating this architecture context") from error
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        acquired = True
        yield
    finally:
        if acquired:
            try:
                unlock()
            except OSError:
                pass
        handle.close()


def refresh_retry(directory: Path, old: dict[str, Any] | None, reason: str, changed_inputs: list[str] | None = None) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "RETRY",
        "freshness": "stale",
        "failures": [reason],
        "changed_inputs": changed_inputs or [],
        "last_good_preserved": bool(old) or last_path(directory).exists(),
        "next_action": "retry refresh after source, config, and view inputs are stable",
    }
def telemetry_event(event: str) -> str:
    """Keep aggregate telemetry keys bounded even for malformed MCP calls."""
    allowed = USAGE_OPERATIONS | OBSERVATIONAL_OPERATIONS | {"install-codex", "uninstall-codex"}
    if event.startswith("mcp:"):
        name = event.removeprefix("mcp:").removeprefix("architecture_")
        return f"mcp:{name}" if name in allowed else "mcp:unknown"
    return event if event in allowed else "unknown"
def has_items(value: dict[str, Any], key: str) -> bool: return isinstance(value.get(key), list) and bool(value[key])
def result_outcome(event: str, value: dict[str, Any]) -> str | None:
    operation = usage_operation(event)
    if operation is None or operation == "watch": return None
    if str(value.get("status", "")) in ("ERROR", "INVALID", "MISSING", "STALE"): return "recovery_required"
    if operation == "refresh": return "promoted" if value.get("status") == "PASS" else "none"
    if operation == "snapshot": return "context_available" if isinstance(value.get("context"), dict) else "empty"
    if operation == "search": return "matched" if isinstance(value.get("match_count"), int) and value["match_count"] > 0 else "empty"
    if operation == "canonical": return "evidence" if isinstance(value.get("canonical"), dict) else "empty"
    if operation == "evidence": return "evidence" if has_items(value, "evidence") else "empty"
    if operation == "trace": return "related" if isinstance(value.get("components"), list) and len(value["components"]) > 1 else "unrelated"
    if operation == "impact": return "affected" if has_items(value, "direct_components") else "unaffected"
    if operation in ("changed-since", "delta"): return "changed" if any(has_items(value, key) for key in ("changed_components", "added_relations", "removed_relations", "changed_relations")) else "unchanged"
    if operation in ("drift", "candidates"): return "candidate" if has_items(value, "candidates") else "none"
    if operation in ("accept", "reject"): return "promoted" if value.get("status") == "PASS" else "recovery_required"
    return None
def telemetry(directory: Path, event: str, value: dict[str, Any], elapsed_ms: int) -> None:
    """Best-effort fixed-size metrics; never retain source, evidence, query, or task text."""
    try:
        path = directory / "telemetry.json"; before = load(path) if path.exists() else {}
        events = before.get("events") if isinstance(before.get("events"), dict) else {}
        statuses = before.get("statuses") if isinstance(before.get("statuses"), dict) else {}
        counts = before.get("counts") if isinstance(before.get("counts"), dict) else {}
        outcomes = before.get("outcomes") if isinstance(before.get("outcomes"), dict) else {}
        event = telemetry_event(event); events[event] = int(events.get(event, 0)) + 1
        status = str(value.get("status")); statuses[status] = int(statuses.get(status, 0)) + 1
        outcome = result_outcome(event, value)
        if outcome: outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
        for key in ("matches", "changed_files", "direct_components", "candidates"):
            if isinstance(value.get(key), list): counts[key] = int(counts.get(key, 0)) + len(value[key])
        atomic(path, {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "events": events, "statuses": statuses, "counts": counts, "outcomes": outcomes, "event_count": int(before.get("event_count", 0)) + 1, "response_bytes": int(before.get("response_bytes", 0)) + len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()), "latency_ms": int(before.get("latency_ms", 0)) + elapsed_ms})
    except (OSError, ValueError): pass
def telemetry_summary(directory: Path) -> dict[str, Any]:
    try: value = load(directory / "telemetry.json") if (directory / "telemetry.json").exists() else {}
    except ValueError: value = {}
    events = value.get("events") if isinstance(value.get("events"), dict) else {}
    statuses = value.get("statuses") if isinstance(value.get("statuses"), dict) else {}
    counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
    outcomes = value.get("outcomes") if isinstance(value.get("outcomes"), dict) else {}
    count, elapsed_total = int(value.get("event_count", 0)), int(value.get("latency_ms", 0))
    actionable = sum(int(outcomes.get(key, 0)) for key in ACTIONABLE_OUTCOMES); eligible = actionable + sum(int(outcomes.get(key, 0)) for key in NO_FINDING_OUTCOMES)
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "privacy": "local aggregate metrics only; no source, evidence, query, or task text", "measurement": "outcome is a result proxy, not proof an agent used it", "events": dict(sorted(events.items())), "statuses": dict(sorted(statuses.items())), "counts": dict(sorted(counts.items())), "outcomes": dict(sorted(outcomes.items())), "actionable_result_count": actionable, "eligible_result_count": eligible, "actionable_result_rate": actionable / eligible if eligible else None, "event_count": count, "response_bytes": int(value.get("response_bytes", 0)), "average_latency_ms": elapsed_total // count if count else 0, "retention": "fixed-size aggregate"}

def usage_path(directory: Path) -> Path: return directory / "usage.json"
def bounded_strings(values: Any, limit: int = 8) -> list[str]:
    if not isinstance(values, list): return []
    result = []
    for value in values:
        text = str(value)
        if text and text not in result: result.append(text[:160])
        if len(result) == limit: break
    return result
def usage_operation(event: str) -> str | None:
    name = event.removeprefix("mcp:").removeprefix("architecture_")
    return name if name in USAGE_OPERATIONS else None
def usage_result(event: str, value: dict[str, Any]) -> dict[str, Any]:
    result = {key: value[key] for key in ("status", "freshness", "revision", "from_revision", "to_revision", "context_hash", "last_good_context_hash") if isinstance(value.get(key), (str, int, float, bool))}
    component_ids = []
    canonical = value.get("canonical")
    if isinstance(canonical, dict) and isinstance(canonical.get("id"), str): component_ids.append(canonical["id"])
    for key in ("matches",):
        for item in value.get(key, []) if isinstance(value.get(key), list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str): component_ids.append(item["id"])
    for key in ("components", "direct_components", "reachable_components", "changed_components"):
        component_ids.extend(str(item) for item in value.get(key, []) if isinstance(item, str))
    if component_ids: result["component_ids"] = bounded_strings(component_ids)
    for key in ("match_count", "omitted_match_count", "watched_files"):
        if isinstance(value.get(key), int): result[key] = value[key]
    for key, target in (("changed_files", "changed_file_count"), ("watch_changed_files", "watch_changed_file_count"), ("candidates", "candidate_count"), ("failures", "failure_count"), ("evidence", "evidence_count")):
        if isinstance(value.get(key), list): result[target] = len(value[key])
    if isinstance(value.get("graph"), dict): result["graph_freshness"] = value["graph"].get("freshness")
    if outcome := result_outcome(event, value): result["outcome"] = outcome
    return result
def usage_store(directory: Path) -> dict[str, Any]:
    try: value = load(usage_path(directory)) if usage_path(directory).exists() else {}
    except ValueError: value = {}
    records = value.get("records") if isinstance(value.get("records"), list) else []
    return {"legacy_imported": bool(value.get("legacy_imported")), "records": [record for record in records if isinstance(record, dict)]}
def bounded_usage(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retained = records[-USAGE_LIMIT:]
    while retained and len(json.dumps({"version": 1, "records": retained}, ensure_ascii=False, separators=(",", ":")).encode()) > USAGE_BYTES: retained.pop(0)
    return retained
def record_usage(directory: Path, event: str, value: dict[str, Any], elapsed_ms: int, origin: str = "cli", subject_id: str | None = None) -> None:
    operation = usage_operation(event)
    if operation is None: return
    try:
        current = usage_store(directory); last = load(last_path(directory)) if last_path(directory).exists() else {}
        result = usage_result(event, value)
        record = {"at": datetime.now(timezone.utc).isoformat(), "origin": origin, "operation": operation, "elapsed_ms": elapsed_ms, "result": result}
        if isinstance(subject_id, str) and subject_id: record["subject_id"] = subject_id[:160]
        if isinstance(last.get("revision"), str) and "revision" not in result: record["context_revision"] = last["revision"]
        context_hash = result.pop("context_hash", None) or result.pop("last_good_context_hash", None) or last.get("context_hash")
        if isinstance(context_hash, str): record["context_hash"] = context_hash
        atomic(usage_path(directory), {"version": 1, "updated_at": record["at"], "legacy_imported": current["legacy_imported"], "records": bounded_usage(current["records"] + [record])})
    except (OSError, ValueError): pass
def legacy_usage_records(directory: Path) -> list[dict[str, Any]]:
    path = directory / "telemetry.jsonl"
    if not path.is_file(): return []
    records = []
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try: value = json.loads(line)
                except json.JSONDecodeError: continue
                if not isinstance(value, dict): continue
                operation = usage_operation(str(value.get("event", "")))
                if operation is None: continue
                result = {key: value[key] for key in ("status", "freshness", "revision") if isinstance(value.get(key), (str, int, float, bool))}
                record = {"at": str(value.get("at", ""))[:64] or "legacy", "origin": "legacy", "operation": operation, "elapsed_ms": int(value.get("elapsed_ms", 0)) if isinstance(value.get("elapsed_ms"), int) else 0, "result": result}
                records.append(record)
    except OSError: return []
    return bounded_usage(records)
def import_legacy_usage(directory: Path) -> dict[str, Any]:
    current = usage_store(directory)
    if current["legacy_imported"]: return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "unchanged", "imported": 0, "next_action": "query usage"}
    legacy = legacy_usage_records(directory)
    if not legacy: return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "unchanged", "imported": 0, "next_action": "query usage"}
    atomic(usage_path(directory), {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "legacy_imported": True, "records": bounded_usage(legacy + current["records"])})
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "action": "legacy_imported", "imported": len(legacy), "next_action": "query usage"}
def usage(directory: Path, operation: str | None, limit: int) -> dict[str, Any]:
    if limit < 0: raise ValueError("usage limit must be non-negative")
    records = usage_store(directory)["records"]
    matches = [record for record in records if operation is None or record.get("operation") == operation]
    visible = list(reversed(matches)) if limit == 0 else list(reversed(matches[-limit:]))
    retained_hashes = {path.stem for path in snapshot_files(directory)}
    events = [{**record, "context_retained": record.get("context_hash") in retained_hashes} if isinstance(record.get("context_hash"), str) else record for record in visible]
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "kind": "architecture_usage", "privacy": "bounded local operation receipts; no prompt, query, source path, evidence text, command, stdout, or stderr", "records": events, "match_count": len(matches), "omitted_match_count": len(matches) - len(visible), "retention": {"max_records": USAGE_LIMIT, "max_bytes": USAGE_BYTES}}

def repo_for(config_path: Path, config: dict[str, Any]) -> Path:
    raw = config.get("repo", ".")
    if not isinstance(raw, str): raise ValueError("repo must be a string")
    repo = (config_path.parent / raw).resolve()
    if not repo.is_dir(): raise ValueError(f"repo does not exist: {repo}")
    return repo

def components(config: dict[str, Any]) -> list[dict[str, Any]]:
    if config.get("version") != CONFIG_VERSION: raise ValueError(f"unsupported config version {config.get('version')!r}; expected {CONFIG_VERSION}")
    coverage(config)
    xs = config.get("components")
    if not isinstance(xs, list) or not xs: raise ValueError("components must be a non-empty array")
    ids = []
    for x in xs:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not x["id"]: raise ValueError("every component needs a non-empty id")
        if not isinstance(x.get("evidence"), list) or not x["evidence"]: raise ValueError(f"{x['id']}: evidence is required")
        ids.append(x["id"])
    if len(ids) != len(set(ids)): raise ValueError("component ids must be unique")
    known = set(ids)
    relation_ids = set()
    for relation in config.get("relations", []):
        if not isinstance(relation, dict) or relation.get("from") not in known or relation.get("to") not in known: raise ValueError("relations must use declared component ids")
        if not isinstance(relation.get("kind"), str) or not relation["kind"]: raise ValueError("every relation needs explicit kind")
        if "dependency" in relation and relation["dependency"] not in ("from_to", "to_from", "none"):
            raise ValueError("relation.dependency must be from_to, to_from, or none")
        if "evidence" in relation and (not isinstance(relation["evidence"], list) or not relation["evidence"]): raise ValueError("relation evidence must be a non-empty array when supplied")
        ident = relation_id(relation)
        if ident in relation_ids: raise ValueError(f"relation ids must be unique: {ident}")
        relation_ids.add(ident)
    return xs


def coverage(config: dict[str, Any]) -> dict[str, Any] | None:
    """Keep declared blueprint coverage compact and distinct from source facts."""
    value = config.get("coverage")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"scope", "limitations"}:
        raise ValueError("coverage supports only scope and limitations")
    scope = value.get("scope")
    limitations = value.get("limitations", [])
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 1000:
        raise ValueError("coverage.scope must be a non-empty string up to 1000 characters")
    if not isinstance(limitations, list) or len(limitations) > 16 or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in limitations):
        raise ValueError("coverage.limitations must contain at most 16 non-empty strings up to 240 characters")
    return {"scope": scope, "limitations": limitations}

def relation_id(relation: dict[str, Any]) -> str:
    value = relation.get("id")
    if isinstance(value, str) and value: return value
    return f"{relation['from']}--{relation['kind']}--{relation['to']}"

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

def validate(repo: Path, config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[list[dict[str, Any]]], list[str]]:
    facts, relation_facts, failures = {}, [], []
    try: xs = components(config)
    except ValueError as e: return facts, relation_facts, [str(e)]
    for x in xs:
        facts[x["id"]], errors = evidence(repo, x); failures.extend(errors)
    for relation in config.get("relations", []):
        if "evidence" not in relation:
            relation_facts.append([]); continue
        value, errors = evidence(repo, {"id": relation_id(relation), "evidence": relation["evidence"]})
        relation_facts.append(value); failures.extend(errors)
    return facts, relation_facts, failures

def context(config: dict[str, Any], rev: str, facts: dict[str, list[dict[str, Any]]], relation_facts: list[list[dict[str, Any]]]) -> dict[str, Any]:
    relations = []
    for index, relation in enumerate(config.get("relations", [])):
        value = {**relation, "provenance": "authored_architecture"}
        if "evidence" in relation:
            value["evidence"] = relation_facts[index]; value["confidence"] = "source_evidence"
        relations.append(value)
    result: dict[str, Any] = {"schema_version": CONFIG_VERSION, "revision": rev, "components": [], "relations": relations}
    for x in components(config):
        result["components"].append({k: x[k] for k in COMPONENT_FIELDS if k in x} | {"evidence": facts[x["id"]], "confidence": "source_evidence"})
    if value := coverage(config):
        result["coverage"] = value
    return result
def semantic(value: dict[str, Any]) -> str: return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
def architecture_semantic(value: dict[str, Any]) -> str: return semantic({key: item for key, item in value.items() if key != "revision"})
def substitution(command: list[Any], values: dict[str, str]) -> list[str]: return [values.get(str(x), str(x)) for x in command]
def run(command: list[Any], repo: Path, timeout: int, values: dict[str, str], extra_env: dict[str, Any] | None = None) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    argv = substitution(command, {**values, "{python}": sys.executable,
                                 "{lac_runtime}": str(CORE_SOURCE_PATH.with_name("archctx_runtime.py"))})
    if command[:2] == ["{python}", "{lac_runtime}"] and sys.flags.isolated:
        argv.insert(1, "-I")
    env = os.environ.copy()
    if extra_env:
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()): raise ValueError("command env must be a string map")
        env.update(extra_env)
    return argv, subprocess.run(argv, cwd=repo, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, env=env)

def graph_changed_files(changed: list[str] | None) -> list[str]: return sorted({str(path).replace("\\", "/") for path in changed or []})
def graph_changed_files_hash(changed: list[str] | None) -> str: return sha(json.dumps(graph_changed_files(changed), ensure_ascii=False, separators=(",", ":")).encode())
def graph_command(graph: dict[str, Any], name: str) -> list[str] | None:
    command = graph.get(name)
    if command is None: return None
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command): raise ValueError(f"code_graph.{name} must be a non-empty argv string list")
    return command
def graph_provider(graph: dict[str, Any]) -> str:
    provider = graph.get("provider", "external")
    if not isinstance(provider, str) or not provider: raise ValueError("code_graph.provider must be a non-empty string")
    return provider
def graph_receipt_contract(graph: dict[str, Any]) -> str | None:
    contract = graph.get("receipt")
    if contract not in (None, "stdout_json_v1"): raise ValueError("code_graph.receipt must be stdout_json_v1 when configured")
    return contract
def graph_receipt(stdout: str, provider: str, candidate: dict[str, Any], context_hash: str, requested_mode: str, changed: list[str] | None) -> dict[str, Any]:
    try: receipt = json.loads(stdout)
    except json.JSONDecodeError as error: raise ValueError("code graph receipt must be one JSON object") from error
    if not isinstance(receipt, dict): raise ValueError("code graph receipt must be one JSON object")
    required = {"receipt_version": 1, "provider": provider, "context_hash": context_hash, "revision": candidate["revision"]}
    for key, expected in required.items():
        if receipt.get(key) != expected: raise ValueError(f"code graph receipt {key} does not bind this validated context")
    mode = receipt.get("mode")
    if mode not in ("full", "incremental"): raise ValueError("code graph receipt mode must be full or incremental")
    if requested_mode == "full" and mode != "full": raise ValueError("code graph receipt cannot report incremental for a full request")
    if receipt.get("fresh") is not True: raise ValueError("code graph receipt is not fresh")
    graph_revision = receipt.get("graph_revision")
    if not isinstance(graph_revision, str) or not graph_revision or len(graph_revision) > 256: raise ValueError("code graph receipt graph_revision must be a non-empty short string")
    normalized = {"receipt_version": 1, "provider": provider, "context_hash": context_hash, "revision": candidate["revision"], "mode": mode, "fresh": True, "graph_revision": graph_revision}
    if requested_mode == "incremental":
        observed = graph_changed_files(changed); digest = graph_changed_files_hash(observed)
        if receipt.get("changed_files_sha256") != digest or receipt.get("changed_file_count") != len(observed): raise ValueError("code graph receipt does not bind observed changed files")
        normalized |= {"changed_files_sha256": digest, "changed_file_count": len(observed)}
    return normalized
def graph_refresh(config: dict[str, Any], repo: Path, directory: Path, changed: list[str] | None, candidate: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    graph = config.get("code_graph")
    if not graph: return {"configured": False, "freshness": "not_configured"}
    if not isinstance(graph, dict): raise ValueError("code_graph must be an object")
    provider, contract, contract_hash = graph_provider(graph), graph_receipt_contract(graph), semantic(graph)
    refresh_command, incremental_command = graph_command(graph, "refresh"), graph_command(graph, "incremental")
    prior_graph = persistent_graph(previous.get("graph", {})) if isinstance(previous, dict) else {}
    context_hash = semantic(candidate); prior_context_hash = previous.get("context_hash") if isinstance(previous, dict) else None
    observed = graph_changed_files(changed)
    run_required = changed is None or bool(observed) or prior_context_hash != context_hash or prior_graph.get("contract_hash") != contract_hash
    if not run_required: return prior_graph | {"execution": {"requested_mode": "not_run"}}
    requested_mode = "incremental" if observed and incremental_command else "full"
    command = incremental_command if requested_mode == "incremental" else refresh_command
    if command is None:
        if contract: raise ValueError("code_graph.receipt needs a refresh command")
        return {"configured": True, "provider": provider, "contract_hash": contract_hash, "freshness": "external_daemon_unverified", "confidence": "provider_unverified"}
    if contract:
        required = ("{context_hash}", "{revision}") + (("{changed_files_sha256}",) if requested_mode == "incremental" else ())
        if any(token not in command for token in required): raise ValueError("code graph receipt command is missing required binding placeholders")
    values = {"{repo}": str(repo), "{state}": str(directory / "codegraph"), "{changed_files}": json.dumps(observed, ensure_ascii=False, separators=(",", ":")), "{changed_files_sha256}": graph_changed_files_hash(observed), "{context_hash}": context_hash, "{revision}": str(candidate["revision"])}
    _, result = run(command, repo, int(graph.get("timeout_seconds", 180)), values)
    if result.returncode: raise RuntimeError(f"code graph command failed ({result.returncode}): {result.stderr.strip()[-1000:]}")
    execution = {"requested_mode": requested_mode}
    if not contract: return {"configured": True, "provider": provider, "contract_hash": contract_hash, "freshness": "unverified", "confidence": "provider_unverified", "execution": execution}
    receipt = graph_receipt(result.stdout, provider, candidate, context_hash, requested_mode, observed)
    return {"configured": True, "provider": provider, "contract_hash": contract_hash, "freshness": "receipt_verified", "confidence": "provider_reported", "receipt": receipt, "execution": execution | {"actual_mode": receipt["mode"]}}

def gates(config: dict[str, Any], repo: Path, directory: Path) -> list[dict[str, Any]]:
    receipts = []
    for gate in config.get("gates", []):
        if not isinstance(gate, dict) or not isinstance(gate.get("name"), str) or not isinstance(gate.get("command"), list): raise ValueError("every gate needs name and command argv")
        argv, r = run(gate["command"], repo, int(gate.get("timeout_seconds", 180)), {"{repo}": str(repo), "{state}": str(directory)}, gate.get("env"))
        if r.returncode: raise RuntimeError(f"gate {gate['name']} failed ({r.returncode}): {r.stderr.strip()[-1000:]}")
        receipts.append({"name": gate["name"], "command": argv, "stdout_tail": r.stdout.strip()[-1000:]})
    return receipts
def persistent_graph(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict): return {}
    if not value.get("configured"): return {"configured": False} if "configured" in value else {}
    if "confidence" not in value: return {"configured": True, "provider": value.get("provider", "external"), "freshness": "unverified_legacy", "confidence": "provider_unverified"}
    return {key: value[key] for key in ("configured", "provider", "contract_hash", "freshness", "confidence", "receipt") if key in value}
def persistent_gates(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": value["name"], "status": "PASS"} for value in values if isinstance(value, dict) and isinstance(value.get("name"), str)]

def relative_glob(value: str, field: str) -> str:
    path = value.replace("\\", "/")
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":") or ".." in path.split("/"):
        raise ValueError(f"{field} must be repo-relative")
    return path

def repo_file(repo: Path, value: Any, field: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty repo-relative path")
    relative = relative_glob(value, field)
    if any(marker in relative for marker in "*?["):
        raise ValueError(f"{field} must name one file, not a glob")
    path = (repo / relative).resolve()
    try: path.relative_to(repo.resolve())
    except ValueError as error: raise ValueError(f"{field} must stay inside repository") from error
    return path, relative

def archify_config(config: dict[str, Any], repo: Path) -> dict[str, Any] | None:
    value = config.get("archify")
    if value is None: return None
    if not isinstance(value, dict): raise ValueError("archify must be an object")
    extra = sorted(set(value) - {"view", "output", "validate", "render", "compare", "timeout_seconds"})
    if extra: raise ValueError(f"archify has unsupported fields: {', '.join(extra)}")
    view, view_relative = repo_file(repo, value.get("view"), "archify.view")
    output, output_relative = repo_file(repo, value.get("output"), "archify.output")
    if view == output: raise ValueError("archify.view and archify.output must differ")
    command = value.get("validate")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("archify.validate must be a non-empty command argv")
    optional = {}
    for name in ("render", "compare"):
        command = value.get(name)
        if command is not None and (not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command)):
            raise ValueError(f"archify.{name} must be a non-empty command argv when configured")
        optional[name] = command
    if (optional["render"] is None) != (optional["compare"] is None):
        raise ValueError("archify.render and archify.compare must be configured together")
    timeout = value.get("timeout_seconds", 180)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("archify.timeout_seconds must be a positive integer")
    return {"view": view, "view_relative": view_relative, "output": output, "output_relative": output_relative, "validate": value["validate"], "timeout_seconds": timeout, **optional}


def generation_base(directory: Path) -> Path:
    return (directory / "generations").resolve()


def controlled_generation(directory: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (directory / raw).resolve()
    try:
        relative = path.relative_to(generation_base(directory))
    except ValueError:
        return None
    stem, separator, nonce = path.name.partition("-")
    if len(relative.parts) != 1 or not separator or len(stem) != 16 or len(nonce) != 32 or any(char not in "0123456789abcdef" for char in stem + nonce):
        return None
    return path


def cleanup_generation(directory: Path, value: Any) -> None:
    path = controlled_generation(directory, value)
    if path is None or not path.exists():
        return
    try:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError:
        pass


def prune_generations(directory: Path) -> None:
    """Keep the current visual bundle plus a small local before/delta history."""
    base = generation_base(directory)
    if not base.is_dir():
        return
    retained: set[Path] = set()
    records = [last_path(directory), *snapshot_files(directory)]
    for path in records:
        try:
            receipt = load(path).get("archify", {})
            generation = controlled_generation(directory, receipt.get("generation") if isinstance(receipt, dict) else None)
            if generation is not None and len(retained) < GENERATION_LIMIT:
                retained.add(generation)
        except (OSError, ValueError):
            continue
    for path in base.iterdir():
        try:
            resolved = path.resolve()
            if controlled_generation(directory, str(resolved)) != resolved:
                continue
        except (OSError, ValueError):
            continue
        if resolved not in retained and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)


def archify_artifact_reason(directory: Path, receipt: dict[str, Any]) -> str | None:
    generation = controlled_generation(directory, receipt.get("generation"))
    if generation is None or not generation.is_dir():
        return "Archify generation is missing or outside local state; run refresh"
    ir_value = receipt.get("ir")
    ir = Path(ir_value).resolve() if isinstance(ir_value, str) and Path(ir_value).is_absolute() else (generation / str(ir_value or "")).resolve()
    try:
        ir.relative_to(generation)
    except ValueError:
        return "Archify IR is outside its generation; run refresh"
    if not ir.is_file() or sha(ir.read_bytes()) != receipt.get("ir_sha256"):
        return "Archify immutable IR is missing or changed; run refresh"
    artifacts = receipt.get("artifacts", {})
    if not isinstance(artifacts, dict) or any(not isinstance(name, str) or not isinstance(digest, str) for name, digest in artifacts.items()):
        return "Archify render receipt is invalid; run refresh"
    for name, digest in artifacts.items():
        artifact = (generation / name).resolve()
        try:
            artifact.relative_to(generation)
        except ValueError:
            return "Archify render receipt escapes its generation; run refresh"
        if not artifact.is_file() or sha(artifact.read_bytes()) != digest:
            return "Archify rendered artifact is missing or changed; run refresh"
    # The digest proves bytes were not changed after publication.  The binding
    # also proves those intact bytes belong to *this* accepted context rather
    # than to another otherwise-valid generation.
    if receipt.get("render") == "PASS":
        binding_path = generation / "binding.json"
        if "binding.json" not in artifacts or not binding_path.is_file():
            return "Archify render binding is missing; run refresh"
        try:
            binding = load(binding_path)
        except ValueError:
            return "Archify render binding is invalid; run refresh"
        fields = ("context_hash", "revision", "ir_sha256", "view_hash", "delta_kind")
        if any(field not in binding or field not in receipt or binding[field] != receipt[field] for field in fields):
            return "Archify render binding does not match its receipt; run refresh"
        bound_artifacts = binding.get("artifacts")
        expected_artifacts = {name: digest for name, digest in artifacts.items() if name != "binding.json"}
        if not isinstance(bound_artifacts, dict) or bound_artifacts != expected_artifacts:
            return "Archify render binding artifacts do not match its receipt; run refresh"
    return None


def archify_projection(config: dict[str, Any], repo: Path, directory: Path, candidate: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    settings = archify_config(config, repo)
    if settings is None: return {"configured": False}
    from archctx_to_archify import project, repository_evidence

    generation = generation_base(directory) / f"{semantic(candidate)[:16]}-{uuid.uuid4().hex}"
    generation.mkdir(parents=True, exist_ok=False)
    ir = generation / "architecture.archify.json"
    try:
        view_bytes = settings["view"].read_bytes()
        repository = repository_evidence(repo, candidate)
        projected = project(config, json.loads(view_bytes.decode("utf-8")), context_value=candidate, repository=repository)
        atomic(ir, projected)
        _, result = run(settings["validate"], repo, settings["timeout_seconds"], {"{repo}": str(repo), "{state}": str(directory), "{archify_output}": str(ir)})
        if result.returncode:
            diagnostic = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Archify validation failed ({result.returncode}): {diagnostic[-1000:]}")
        receipt: dict[str, Any] = {"configured": True, "view": settings["view_relative"], "view_sha256": sha(view_bytes), "output": settings["output_relative"], "generation": str(generation.resolve()), "ir": str(ir.resolve()), "ir_sha256": sha(ir.read_bytes()), "validation": "PASS", "repository_evidence": repository or {"status": "not_available_for_current_evidence"}}
        if settings["render"] is not None:
            from archctx_blueprint import render_bundle

            rendered = render_bundle(config, repo, directory, generation, ir, candidate, previous)
            if not isinstance(rendered, dict):
                raise ValueError("Archify render bundle must return one object receipt")
            if any(receipt[key] != value for key, value in rendered.items() if key in receipt):
                raise ValueError("Archify render receipt must not replace projection bindings")
            receipt |= rendered
        return receipt
    except BaseException:
        cleanup_generation(directory, str(generation))
        raise


def compatibility_output(settings: dict[str, Any] | None, receipt: dict[str, Any]) -> dict[str, Any]:
    if settings is None or not receipt.get("configured") or not receipt.get("generation"):
        return {"state": "not_configured"}
    ir = Path(str(receipt.get("ir", "")))
    try:
        if not ir.is_file():
            raise OSError("immutable Archify IR is unavailable")
        atomic_bytes(settings["output"], ir.read_bytes())
        return {"state": "synced", "output": settings["output_relative"], "sha256": sha(settings["output"].read_bytes())}
    except OSError as error:
        return {"state": "deferred", "output": settings["output_relative"], "reason": str(error)[:240]}


def archify_stale_reason(config: dict[str, Any], repo: Path, directory: Path, record: dict[str, Any]) -> str | None:
    settings = archify_config(config, repo)
    if settings is None: return None
    receipt = record.get("archify")
    if not isinstance(receipt, dict) or not receipt.get("configured"):
        return "Archify projection is missing; run refresh"
    if receipt.get("view") != settings["view_relative"] or receipt.get("output") != settings["output_relative"]:
        return "Archify projection configuration changed; run refresh"
    # Legacy projections did not bind a visual bundle to the accepted context.
    # New render configurations must: an intact generation from another
    # accepted refresh is still stale if its receipt names a different state.
    if settings["render"] is not None and (receipt.get("context_hash") != record.get("context_hash") or receipt.get("revision") != record.get("revision")):
        return "Archify render receipt does not bind this accepted context; run refresh"
    try:
        if sha(settings["view"].read_bytes()) != receipt.get("view_sha256"):
            return "Archify view changed; run refresh"
    except OSError:
        return "Archify view is missing or unreadable; run refresh"
    if receipt.get("generation"):
        return archify_artifact_reason(directory, receipt)
    try:
        if sha(settings["output"].read_bytes()) != receipt.get("output_sha256"):
            return "Archify legacy projection output is missing or changed; run refresh"
    except OSError:
        return "Archify legacy projection output is missing or unreadable; run refresh"
    return None


def refresh_input_fingerprint(config_path: Path, config: dict[str, Any], config_bytes: bytes, repo: Path, candidate: dict[str, Any]) -> dict[str, str | None]:
    settings = archify_config(config, repo)
    return {
        "config_sha256": sha(config_bytes),
        "config_semantic_hash": semantic(config),
        "context_hash": semantic(candidate),
        "revision": str(candidate["revision"]),
        "view_sha256": sha(settings["view"].read_bytes()) if settings is not None else None,
        "watch_manifest_hash": semantic(manifest(config_path, repo, config)),
    }


def refresh_inputs(config_path: Path) -> tuple[dict[str, Any], Path, dict[str, list[dict[str, Any]]], list[list[dict[str, Any]]], dict[str, Any], dict[str, str | None]]:
    config, config_bytes = load_bytes(config_path)
    repo = repo_for(config_path, config)
    facts, relation_facts, failures = validate(repo, config)
    if failures:
        raise ValueError("; ".join(failures))
    candidate = context(config, revision(repo, {"components": facts, "relations": relation_facts}), facts, relation_facts)
    return config, repo, facts, relation_facts, candidate, refresh_input_fingerprint(config_path, config, config_bytes, repo, candidate)


def final_refresh_check(config_path: Path, expected: dict[str, str | None]) -> tuple[list[str], str | None]:
    """Re-read the declared inputs after external validation, before publication."""
    try:
        _, _, _, _, _, current = refresh_inputs(config_path)
    except (OSError, ValueError) as error:
        return ["config_or_source"], str(error)
    changed = []
    if current["config_sha256"] != expected["config_sha256"] or current["config_semantic_hash"] != expected["config_semantic_hash"]:
        changed.append("config")
    if current["context_hash"] != expected["context_hash"]:
        changed.append("source_evidence")
    if current["revision"] != expected["revision"]:
        changed.append("revision")
    if current["view_sha256"] != expected["view_sha256"]:
        changed.append("view")
    if current["watch_manifest_hash"] != expected["watch_manifest_hash"]:
        changed.append("source_or_watch_scope")
    return changed, None

def normalized_drift_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("drift_rules", [])
    if not isinstance(raw, list): raise ValueError("drift_rules must be an array")
    result = []; seen = set()
    for index, rule in enumerate(raw):
        if not isinstance(rule, dict): raise ValueError("every drift rule must be an object")
        paths = rule.get("paths", ["*"])
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths): raise ValueError("drift rule paths must be a non-empty string array")
        paths = [relative_glob(path, "drift rule paths") for path in paths]
        added = rule.get("added_contains")
        if added is None: added = [rule["pattern"]] if isinstance(rule.get("pattern"), str) and rule["pattern"] else []
        removed = rule.get("removed_contains", [])
        if not isinstance(added, list) or not isinstance(removed, list) or not all(isinstance(value, str) and value for value in added + removed): raise ValueError("drift rule signals must be non-empty string arrays")
        if not added and not removed: raise ValueError("drift rule needs pattern, added_contains, or removed_contains")
        ident = rule.get("id")
        if not isinstance(ident, str) or not ident: ident = f"rule-{index}-{sha(json.dumps({k: rule[k] for k in sorted(rule)}, ensure_ascii=False, separators=(',', ':')).encode())[:12]}"
        if ident in seen: raise ValueError(f"drift rule ids must be unique: {ident}")
        seen.add(ident)
        kind = rule.get("kind", "unspecified")
        if not isinstance(kind, str) or not kind: raise ValueError("drift rule kind must be a non-empty string")
        result.append({"id": ident, "kind": kind, "paths": sorted(set(paths)), "added_contains": sorted(set(added)), "removed_contains": sorted(set(removed))})
    return result

def candidate_files(repo: Path, rules: list[dict[str, Any]], ignore: list[Any] | None = None) -> tuple[list[Path], bool]:
    found: dict[str, Path] = {}
    for rule in rules:
        for pattern in rule["paths"]:
            for path in repo.glob(pattern):
                if not path.is_file(): continue
                try: relative = path.resolve().relative_to(repo.resolve()).as_posix()
                except ValueError: continue
                if ignored(relative, ignore or []): continue
                found[relative] = path
                if len(found) > CANDIDATE_FILE_LIMIT: return [found[key] for key in sorted(found)[:CANDIDATE_FILE_LIMIT]], False
    return [found[key] for key in sorted(found)], True

def signal_signature(text: str, pattern: str) -> dict[str, Any] | None:
    matches = [(number, sha(line.encode())) for number, line in enumerate(text.splitlines(), 1) if pattern in line]
    if not matches: return None
    return {"count": len(matches), "sha256": sha(json.dumps(sorted(value for _, value in matches), separators=(",", ":")).encode()), "first_line": matches[0][0]}

def signal_key(direction: str, pattern: str) -> str:
    return f"{direction}:{sha(pattern.encode())}"

def candidate_baseline(config: dict[str, Any], repo: Path) -> dict[str, Any]:
    rules = normalized_drift_rules(config); rule_hash = semantic(rules); entries = []
    watch = config.get("watch", {})
    if not isinstance(watch, dict): raise ValueError("watch must be an object")
    ignore = watch.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(pattern, str) for pattern in ignore): raise ValueError("watch.ignore must contain strings")
    files, complete = candidate_files(repo, rules, ignore); scanned_bytes = 0
    if not complete: return {"version": 2, "rules_hash": rule_hash, "entries": [], "complete": False, "scanned_file_count": len(files), "scanned_source_bytes": scanned_bytes}
    for path in files:
        scanned_bytes += path.stat().st_size
        if scanned_bytes > CANDIDATE_SOURCE_BYTES: return {"version": 2, "rules_hash": rule_hash, "entries": [], "complete": False, "scanned_file_count": len(files), "scanned_source_bytes": scanned_bytes}
        relative = path.resolve().relative_to(repo.resolve()).as_posix(); text = path.read_text(encoding="utf-8", errors="replace")
        for rule in rules:
            if not any(fnmatch.fnmatch(relative, pattern) for pattern in rule["paths"]): continue
            signals: dict[str, dict[str, Any]] = {}
            for direction, patterns in (("added", rule["added_contains"]), ("removed", rule["removed_contains"])):
                for pattern in patterns:
                    if value := signal_signature(text, pattern): signals[signal_key(direction, pattern)] = value
            if signals: entries.append({"rule_id": rule["id"], "path": relative, "signals": signals})
    complete = complete and len(entries) <= CANDIDATE_ENTRY_LIMIT
    entries = entries[:CANDIDATE_ENTRY_LIMIT]
    value = {"version": 2, "rules_hash": rule_hash, "entries": entries, "complete": complete, "scanned_file_count": len(files), "scanned_source_bytes": scanned_bytes}
    while entries and len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > CANDIDATE_BYTES:
        entries.pop(); complete = False; value["complete"] = False
    return value

def baseline_entries(value: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    entries = value.get("entries") if isinstance(value.get("entries"), list) else []
    return {(entry.get("rule_id"), entry.get("path")): entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("rule_id"), str) and isinstance(entry.get("path"), str) and isinstance(entry.get("signals"), dict)}

def candidate_changes(previous: dict[str, Any], current: dict[str, Any], rules: list[dict[str, Any]], base_context_hash: str) -> list[dict[str, Any]]:
    kinds = {rule["id"]: rule["kind"] for rule in rules}; before, after = baseline_entries(previous), baseline_entries(current); found = []
    for rule_id, path in sorted(set(before) | set(after)):
        left, right = before.get((rule_id, path), {}), after.get((rule_id, path), {})
        for signal in sorted(set(left.get("signals", {})) | set(right.get("signals", {}))):
            old, new = left.get("signals", {}).get(signal), right.get("signals", {}).get(signal)
            # Line numbers locate a finding for review, but a surrounding
            # comment or import must not turn an unchanged signal into a new
            # architecture candidate.  Keep the location as current evidence
            # while binding the candidate identity to the signal itself.
            old_signal = {key: value for key, value in old.items() if key != "first_line"} if isinstance(old, dict) else old
            new_signal = {key: value for key, value in new.items() if key != "first_line"} if isinstance(new, dict) else new
            if old_signal == new_signal: continue
            evidence = new or old or {}; payload = {"base_context_hash": base_context_hash, "rule_id": rule_id, "path": path, "signal": signal, "before": old_signal, "after": new_signal}
            found.append({"id": sha(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), "kind": kinds.get(rule_id, "unspecified"), "rule_id": rule_id, "signal": signal, "status": "CANDIDATE_REVIEW_REQUIRED", "provenance": "deterministic_rule_derived_source_fact", "change": "introduced" if new else "removed", "evidence": {"path": path, "line": evidence.get("first_line"), "sha256": evidence.get("sha256")}})
    return found

def candidate_observation(config: dict[str, Any], repo: Path, record: dict[str, Any] | None) -> dict[str, Any]:
    rules = normalized_drift_rules(config); current = candidate_baseline(config, repo)
    if not current["complete"]: return {"state": "incomplete", "current": current, "candidates": []}
    if not rules: return {"state": "ready", "current": current, "candidates": []}
    previous = record.get("candidate_baseline") if isinstance(record, dict) else None
    if not isinstance(previous, dict) or previous.get("version") != 2: return {"state": "baseline_missing", "current": current, "candidates": []}
    if not previous.get("complete", False): return {"state": "baseline_incomplete", "current": current, "candidates": []}
    if previous.get("rules_hash") != current["rules_hash"]: return {"state": "rules_changed", "current": current, "candidates": []}
    changes = candidate_changes(previous, current, rules, str(record.get("context_hash", "")))
    if len(changes) > DECISION_LIMIT: return {"state": "incomplete", "current": current, "candidates": []}
    return {"state": "ready", "current": current, "candidates": changes}

def candidate_fields(observation: dict[str, Any], limit: int = CANDIDATE_OUTPUT_LIMIT) -> dict[str, Any]:
    if limit < 0: raise ValueError("candidate limit must be non-negative")
    candidates = observation.get("candidates", []) if isinstance(observation.get("candidates"), list) else []
    visible = candidates[:CANDIDATE_ENTRY_LIMIT] if limit == 0 else candidates[:min(limit, CANDIDATE_ENTRY_LIMIT)]
    return {"candidate_state": observation.get("state"), "candidate_count": len(candidates), "candidates": visible, "omitted_candidate_count": len(candidates) - len(visible)}

def candidate_decision_path(directory: Path) -> Path: return directory / "candidate-decisions.json"
def decision_store(directory: Path) -> list[dict[str, Any]]:
    path = candidate_decision_path(directory)
    if not path.exists(): return []
    value = load(path)
    if not isinstance(value.get("records"), list): raise ValueError("candidate decision store records must be an array")
    return [record for record in value["records"] if isinstance(record, dict)]
def bounded_decisions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retained = list(records)
    while len(retained) > DECISION_LIMIT or len(json.dumps({"version": 1, "records": retained}, ensure_ascii=False, separators=(",", ":")).encode()) > DECISION_BYTES:
        index = next((index for index, record in enumerate(retained) if record.get("state") != "pending"), None)
        if index is None: raise ValueError("candidate decision capacity exceeded; narrow drift rules")
        retained.pop(index)
    return retained
def save_decisions(directory: Path, records: list[dict[str, Any]]) -> None:
    retained = bounded_decisions(records)
    atomic(candidate_decision_path(directory), {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "records": retained})
def record_decision(directory: Path, decision: dict[str, Any]) -> None:
    records = decision_store(directory)
    if decision.get("state") == "pending":
        records = [record for record in records if not (record.get("state") == "pending" and record.get("id") == decision.get("id") and record.get("base_context_hash") == decision.get("base_context_hash"))]
    save_decisions(directory, records + [decision])
def pending_decisions(directory: Path, base_context_hash: str, candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = {candidate["id"] for candidate in candidates}
    found: dict[str, dict[str, Any]] = {}
    for record in decision_store(directory):
        if record.get("state") == "pending" and record.get("base_context_hash") == base_context_hash and record.get("id") in ids:
            found[record["id"]] = record
    return found
def finalize_decisions(directory: Path, base_context_hash: str, decisions: dict[str, dict[str, Any]], context_hash: str) -> None:
    ids = set(decisions)
    records = [record for record in decision_store(directory) if not (record.get("state") == "pending" and record.get("base_context_hash") == base_context_hash and record.get("id") in ids)]
    final = [{key: value for key, value in decision.items() if key != "state"} | {"state": "final", "context_hash": context_hash, "finalized_at": datetime.now(timezone.utc).isoformat()} for decision in decisions.values()]
    save_decisions(directory, records + final)

def freshness(record: dict[str, Any], status: str, reason: Any = None) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "status": status, "revision": record.get("revision"), "freshness": "fresh" if status in ("FRESH", "PASS") else "stale", "last_good_at": record.get("created_at"), "confidence": "source_evidence", "reason": reason, "next_action": "query normally" if status in ("FRESH", "PASS") else "run refresh after fixing the reported change"}


def unavailable_status(directory: Path, record: dict[str, Any] | None, error: Exception) -> dict[str, Any]:
    if record is not None:
        return freshness(record, "STALE", [f"architecture config is unavailable: {error}"]) | {
            "last_good_available": True,
            "last_good_context_hash": record.get("context_hash"),
            "graph": persistent_graph(record.get("graph", {})),
            "candidate_state": "not_checked",
            "candidate_count": 0,
            "candidates": [],
            "omitted_candidate_count": 0,
            "next_action": "restore a valid architecture config, then refresh",
        }
    status_name = "MISSING" if not directory.exists() else "INVALID"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": status_name,
        "freshness": "missing" if status_name == "MISSING" else "stale",
        "failures": [f"architecture config is unavailable: {error}"],
        "last_good_available": False,
        "next_action": "restore a valid architecture config, then refresh",
    }


def status(config_path: Path, explicit: str | None, record: dict[str, Any] | None = None, *, _observed: dict[str, Any] | None = None) -> dict[str, Any]:
    directory = state(config_path, explicit); old_path = last_path(directory)
    try:
        config = load(config_path)
    except (OSError, ValueError) as error:
        if record is None and old_path.exists():
            try:
                record = load(old_path)
            except ValueError:
                record = None
        if _observed is not None: _observed["record"] = record
        return unavailable_status(directory, record, error)
    try:
        repo = repo_for(config_path, config); facts, relation_facts, failures = validate(repo, config)
    except ValueError as e: repo, facts, relation_facts, failures = None, {}, [], [str(e)]
    # Reuse this read for incremental delivery; the public status stays metadata-only.
    if _observed is not None: _observed.update(config=config, repo=repo, facts=facts, relation_facts=relation_facts)
    if _observed is not None:
        try:
            settings = archify_config(config, repo) if repo else None
            _observed["view_hash"] = sha(settings["view"].read_bytes()) if settings else None
        except (OSError, ValueError): _observed["view_hash"] = None
    if not old_path.exists(): return {"protocol_version": PROTOCOL_VERSION, "status": "MISSING", "freshness": "missing", "repo": str(repo) if repo else None, "next_action": "run refresh"}
    old = record if record is not None else load(old_path); current = context(config, revision(repo, {"components": facts, "relations": relation_facts}), facts, relation_facts) if repo and not failures else None
    try: visual_failure = archify_stale_reason(config, repo, directory, old) if repo and not failures else None
    except (OSError, ValueError) as error: visual_failure = f"Archify projection check failed: {error}"
    try: observation = candidate_observation(config, repo, old) if repo and not failures else {"state": "not_checked", "candidates": []}
    except (OSError, ValueError) as error: observation = {"state": "incomplete", "candidates": [], "error": str(error)}
    if _observed is not None: _observed.update(record=old, current=current, candidates=observation, failures=failures)
    fresh = current is not None and architecture_semantic(current) == architecture_semantic(old["context"]) and semantic(config) == old.get("config_hash") and visual_failure is None
    candidate_state = observation.get("state")
    candidates = observation.get("candidates", [])
    blocked = candidate_state in ("incomplete", "baseline_missing", "baseline_incomplete", "rules_changed") or bool(candidates)
    reason = failures or ([visual_failure] if visual_failure else ["source revision, config, or architecture context changed"])
    if candidates: reason = ["high-value architecture candidate requires review"]
    elif candidate_state in ("incomplete", "baseline_incomplete"): reason = ["candidate observation incomplete; tighten configured drift rules before trusting this context"]
    elif candidate_state == "baseline_missing": reason = ["candidate baseline missing; run refresh to establish it"]
    elif candidate_state == "rules_changed": reason = ["candidate rules changed; run refresh to establish a new reviewed baseline"]
    result = freshness(old, "FRESH" if fresh and not blocked else "STALE", None if fresh and not blocked else reason) | {"last_good_available": True, "last_good_context_hash": old.get("context_hash"), "graph": persistent_graph(old.get("graph", {}))} | candidate_fields(observation)
    if candidates: result["next_action"] = "inspect candidates, update canonical config/evidence if accepted, then run accept or reject"
    elif candidate_state in ("baseline_missing", "rules_changed"): result["next_action"] = "review current source, then run refresh with reset_candidate_baseline"
    return result

def diagnose_status(config_path: Path, explicit: str | None) -> dict[str, Any]:
    """Explain this invocation's selected inputs; never discover, migrate or refresh."""
    config_path = config_path.resolve()
    directory = state(config_path, explicit).resolve()
    selection = "explicit" if explicit else "legacy_nested" if config_path.parent.name == ".archctx" and directory == (config_path.parent / ".archctx").resolve() else "config_adjacent"
    try:
        value = status(config_path, str(directory))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        value = {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [f"cannot read selected architecture state: {type(error).__name__}: {error}"], "next_action": "inspect the selected config and state; diagnosis does not require refresh"}
    return value | {"diagnostics": {
        "tool": {"version": SERVER_VERSION, "core_source_sha256": CORE_SOURCE_SHA256, "module": str(CORE_SOURCE_PATH), "python": sys.executable},
        "config": {"path": str(config_path), "exists": config_path.is_file()},
        "state": {"path": str(directory), "selection": selection},
        "last_good": {"path": str(last_path(directory)), "exists": last_path(directory).is_file()},
    }}

def _refresh_locked(config_path: Path, explicit: str | None, directory: Path, changed: list[str] | None = None, acknowledged: dict[str, dict[str, Any]] | None = None, expected_context_hash: str | None = None, reset_candidate_baseline: bool = False, understand_proof: dict[str, Any] | None = None, expected_input_hash: str | None = None) -> dict[str, Any]:
    try:
        config, repo, facts, relation_facts, candidate, inputs = refresh_inputs(config_path)
    except (OSError, ValueError) as error:
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [str(error)], "last_good_preserved": last_path(directory).exists(), "next_action": "repair evidence/config, then refresh"}
    old = load(last_path(directory)) if last_path(directory).exists() else None
    if expected_context_hash is not None and (not isinstance(old, dict) or old.get("context_hash") != expected_context_hash):
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": ["last-good changed while candidate was being reviewed"], "last_good_preserved": bool(old), "next_action": "re-run candidate review"}
    if expected_input_hash is not None and semantic(inputs) != expected_input_hash:
        return refresh_retry(directory, old, "source, config, or view changed after semantic binding review", ["reviewed_inputs"])
    if understand_proof is not None:
        from archctx_understand import check_publication
        try: check_publication(repo, directory, understand_proof)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return refresh_retry(directory, old, str(error), ["source_analysis"])
    try:
        observation = candidate_observation(config, repo, old)
    except (OSError, ValueError) as error:
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [f"candidate observation failed: {error}"], "last_good_preserved": bool(old), "next_action": "repair configured drift rules, then refresh"}
    if observation.get("state") in ("incomplete", "baseline_incomplete"):
        return {"protocol_version": PROTOCOL_VERSION, "status": "CANDIDATE_CHECK_INCOMPLETE", "freshness": "stale", "last_good_preserved": bool(old), "next_action": "tighten configured drift rules before refresh"} | candidate_fields(observation)
    if old is not None and observation.get("state") in ("baseline_missing", "rules_changed") and not reset_candidate_baseline:
        return {"protocol_version": PROTOCOL_VERSION, "status": "CANDIDATE_CHECK_INCOMPLETE", "freshness": "stale", "last_good_preserved": True, "next_action": "review current source, then refresh with reset_candidate_baseline"} | candidate_fields(observation)
    pending = observation.get("candidates", []) if isinstance(observation.get("candidates"), list) else []
    acknowledged = acknowledged or {}
    if pending and set(value["id"] for value in pending) != set(acknowledged):
        return {"protocol_version": PROTOCOL_VERSION, "status": "CANDIDATE_REVIEW_REQUIRED", "freshness": "stale", "last_good_preserved": bool(old), "next_action": "inspect every candidate, then accept or reject it before refresh"} | candidate_fields(observation)
    reused_validation = False
    try:
        reused_validation = bool(understand_proof is not None and expected_input_hash is not None and old
            and old.get("transaction", {}).get("input_hash") == semantic(inputs)
            and old.get("context_hash") == semantic(candidate) and old.get("config_hash") == semantic(config)
            and archify_stale_reason(config, repo, directory, old) is None)
        if reused_validation:
            graph, receipts, archify = old.get("graph", {}), old.get("gates", []), old.get("archify", {"configured": False})
        else:
            graph = graph_refresh(config, repo, directory, changed, candidate, old)
            receipts = gates(config, repo, directory)
            archify = archify_projection(config, repo, directory, candidate, old)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [str(error)], "last_good_preserved": last_path(directory).exists(), "next_action": "repair external validator, then refresh"}
    changed_inputs, input_error = final_refresh_check(config_path, inputs)
    if understand_proof is not None:
        try: check_publication(repo, directory, understand_proof)
        except (OSError, ValueError, KeyError, TypeError) as error:
            changed_inputs.append("source_analysis"); input_error = str(error)
    if reused_validation:
        try: artifact_error = archify_stale_reason(config, repo, directory, old)
        except (OSError, ValueError) as error: artifact_error = str(error)
        if artifact_error:
            changed_inputs.append("accepted_artifacts"); input_error = artifact_error
    if changed_inputs:
        if not reused_validation:
            cleanup_generation(directory, archify.get("generation"))
        detail = input_error or "source, config, or view changed while refresh validation was running"
        return refresh_retry(directory, old, detail, changed_inputs)
    decisions = [{key: value[key] for key in ("id", "kind", "rule_id", "decision", "bindings", "source_analysis", "content_revision", "evidence_revision") if key in value} for value in acknowledged.values()]
    record = {"record_version": CONFIG_VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "repo": str(repo), "revision": candidate["revision"], "config_hash": semantic(config), "context_hash": semantic(candidate), "context": candidate, "graph": persistent_graph(graph), "gates": persistent_gates(receipts), "archify": archify, "candidate_baseline": observation["current"], "transaction": {"input_hash": semantic(inputs), "generation_limit": GENERATION_LIMIT}}
    previous_snapshot = snapshot_record(directory, record["context_hash"])
    previous_decisions = previous_snapshot.get("candidate_decisions", []) if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("candidate_decisions"), list) else []
    if old:
        before, after = declaration_context(old["context"]), declaration_context(candidate)
        stable_bindings = {f"{kind}:{relation_id(item) if kind == 'relation' else item['id']}"
                           for key, kind in (("components", "component"), ("relations", "relation"))
                           for item in before[key] if item in after[key]}
        previous_decisions += [d for d in analysis_decisions(directory, old) if d.get("bindings") and set(d["bindings"]) <= stable_bindings]
    merged_decisions = []
    for decision in previous_decisions + decisions:
        if isinstance(decision, dict) and decision not in merged_decisions:
            merged_decisions.append(decision)
    if merged_decisions:
        record["candidate_decisions"] = merged_decisions[-DECISION_LIMIT:]
    if reset_candidate_baseline:
        record["candidate_baseline_reset"] = True
    elif isinstance(previous_snapshot, dict) and previous_snapshot.get("candidate_baseline_reset"):
        record["candidate_baseline_reset"] = True
    try:
        # This replacement is the one current-authority commit. Generated output is only mirrored afterwards.
        atomic(last_path(directory), record)
    except OSError as error:
        if not reused_validation:
            cleanup_generation(directory, archify.get("generation"))
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [str(error)], "last_good_preserved": bool(old), "next_action": "repair local state storage, then refresh"}
    history_state = "synced"
    try:
        atomic(directory / "snapshots" / f"{record['context_hash']}.json", record)
        prune_snapshots(directory)
        prune_generations(directory)
    except OSError:
        history_state = "deferred"
    output_state = compatibility_output(archify_config(config, repo), archify)
    return freshness(record, "PASS") | {"context_hash": record["context_hash"], "graph": graph, "gates": receipts, "archify": archify, "changed_files": changed or [], "candidate_baseline_reset": bool(reset_candidate_baseline), "history": history_state, "compatibility_output": output_state, "validation": "reused_accepted_inputs" if reused_validation else "executed"}


def refresh(config_path: Path, explicit: str | None, changed: list[str] | None = None, acknowledged: dict[str, dict[str, Any]] | None = None, expected_context_hash: str | None = None, reset_candidate_baseline: bool = False) -> dict[str, Any]:
    directory = state(config_path, explicit)
    try:
        with refresh_lock(directory):
            return _refresh_locked(config_path, explicit, directory, changed, acknowledged, expected_context_hash, reset_candidate_baseline)
    except RefreshBusyError as error:
        try:
            old = load(last_path(directory)) if last_path(directory).exists() else None
        except ValueError:
            old = None
        return refresh_retry(directory, old, str(error), ["writer_lock"])

def snapshot(config_path: Path, explicit: str | None) -> dict[str, Any]:
    directory = state(config_path, explicit); path = last_path(directory)
    if not path.exists(): return status(config_path, explicit)
    old = load(path)
    value = status(config_path, explicit, old)
    return value | {"context": old["context"], "graph": persistent_graph(old.get("graph", {})) if isinstance(old.get("graph"), dict) else {}, "gates": persistent_gates(old.get("gates", [])) if isinstance(old.get("gates"), list) else [], "archify": old.get("archify", {"configured": False}), "candidate_decisions": old.get("candidate_decisions", [])}

def analysis_decisions(directory: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    decisions = list(record.get("candidate_decisions", []))
    try:
        decisions += [d for d in decision_store(directory) if d.get("state") == "final" and d.get("context_hash") == record.get("context_hash")]
    except (OSError, ValueError): pass
    return [{k: d[k] for k in ("id", "kind", "decision", "bindings", "source_analysis", "content_revision", "evidence_revision") if k in d}
            for d in decisions if d.get("decision") == "accepted" and isinstance(d.get("source_analysis"), dict)]

def accepted_analysis(directory: Path, record: dict[str, Any], binding: str) -> list[dict[str, Any]]:
    """Historical interpretation provenance; never evidence of current source/runtime freshness."""
    found = {}
    for decision in analysis_decisions(directory, record):
        if binding in decision.get("bindings", []):
            found[decision["id"]] = {"candidate_id": decision["id"], **decision["source_analysis"], "meaning": "historical accepted analysis provenance; not current source or runtime proof"}
    return list(found.values())[-CANDIDATE_OUTPUT_LIMIT:]

def canonical(config_path: Path, explicit: str | None, ident: str) -> dict[str, Any]:
    value = snapshot(config_path, explicit)
    for x in value.get("context", {}).get("components", []):
        if x["id"] == ident:
            directory = state(config_path, explicit)
            record = {"context_hash": value.get("last_good_context_hash"), "candidate_decisions": value.get("candidate_decisions", [])}
            return {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "confidence", "next_action") if k in value} | {"canonical": x, "warning": value.get("reason"), "source_analysis": accepted_analysis(directory, record, f"component:{ident}")}
    return {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "next_action") if k in value} | {"error": f"unknown component: {ident}", "warning": value.get("reason")}

def search(config_path: Path, explicit: str | None, query: str, limit: int = 3) -> dict[str, Any]:
    if limit < 0: raise ValueError("search limit must be non-negative")
    value = snapshot(config_path, explicit)
    terms = [term for term in query.casefold().split() if term]
    matches = []
    for item in value.get("context", {}).get("components", []):
        haystack = json.dumps({key: item.get(key) for key in ("id", "name", "purpose", "tags", "truth_sources")}, ensure_ascii=False).casefold()
        score = sum(term in haystack for term in terms)
        if score:
            matches.append((score, item))
    selected = sorted(matches, key=lambda row: (-row[0], row[1]["id"])); visible = selected if limit == 0 else selected[:limit]
    compact = [{key: item[key] for key in ("id", "name", "purpose", "tags", "truth_sources", "evidence") if key in item} for _, item in visible]
    return {key: value[key] for key in ("protocol_version", "status", "revision", "freshness", "confidence", "next_action") if key in value} | {"query": query, "matches": compact, "match_count": len(selected), "omitted_match_count": len(selected) - len(visible), "warning": value.get("reason")}

CODEX_BEGIN, CODEX_END = "<!-- archctx:begin -->", "<!-- archctx:end -->"

def instruction_path(value: str) -> str:
    if any(x in value for x in ("\n", "\r", "`")):
        raise ValueError("managed paths cannot contain newlines or Markdown backticks")
    if value and all(x.isalnum() or x in "/._-" for x in value): return value
    return "'" + value.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(value)

def codex_state(config_path: Path, repo: Path, target: Path, explicit: str | None) -> str | None:
    if not explicit: return None
    directory = state(config_path, explicit)
    try: relative = directory.relative_to(repo).as_posix()
    except ValueError as error: raise ValueError("managed Codex state must live inside its repository") from error
    private_config = config_path.parent == directory and directory.name == ".archctx"
    if relative == "." or directory == target or directory in target.parents or (not private_config and (directory == config_path or directory in config_path.parents)):
        raise ValueError("managed state must be a dedicated directory, not the repo root or a parent of config/AGENTS")
    if ".archctx" not in Path(relative).parts and git(repo, "ls-files", "--", ":(literal)" + relative):
        raise ValueError("managed state must not contain tracked source files")
    instruction_path(relative)
    return relative

def codex_block(relative_config: str, command: str = "archctx", relative_state: str | None = None) -> str:
    if not command.strip() or any(x in command for x in ("\n", "\r", "`")):
        raise ValueError("--command must be a non-empty, single-line CLI prefix without Markdown backticks")
    prefix = f"{command} --config {instruction_path(relative_config)}"
    if relative_state: prefix += f" --state-dir {instruction_path(relative_state)}"
    return f'''{CODEX_BEGIN}
## Architecture context

Use Archctx only when it shrinks the next broad source read (canonical/truth/evidence, cross-component path, freshness/delta, or legacy ambiguity); skip obvious local work.
From the repository root, run `{prefix} status`. Keep this exact CLI prefix for queries; do not substitute a global installation. Use `status --diagnose` if tool/config identity is unclear. Use its `FRESH`/`STALE` label, not unrelated Git dirtiness. If `FRESH`, use the smallest matching query: `search` to locate; `canonical`/`evidence` for a known component; `impact --files <paths>` before cross-component edits; `history` for prior context; `changed-since`/`drift` only with a supplied base revision.
When this installed version supports it, `updates` can replace that status read at the next relevant task boundary; reuse the returned cursor with `updates --since <cursor>` (MCP: `architecture_updates` with `since`). Keep the cursor in the caller, not a new event log. Do not invoke it on every tool call.
Read only returned evidence and the next directly needed source file. Source wins; stale, missing, or irrelevant context means normal targeted discovery. Default `watch` only observes; opt-in `watch --apply` may refresh already-declared evidence after validation, never candidates. Orientation, never a gate.
When completed work changes architecture semantics, maintain the affected shared config/view from source, whether or not this task queried LAC. Existing candidate review and validation still govern promotion.
After an explicitly authorized `{prefix} setup`, `{prefix} map` keeps the shared map live. For a real understanding gap during authorized development, use `{prefix} understand "question" --files <small-scope>`; follow its compact `NEEDS_AGENT` contract and resume with the returned analysis ID. LAC performs mechanical steps; this Agent supplies semantics. Reuse existing findings with `understand --show`; ordinary queries never install or invoke a model. Keep the map open during candidate acceptance.
{CODEX_END}
'''

def install_codex(config_path: Path, target: Path, check: bool, command: str = "archctx", explicit: str | None = None, allow_empty: bool = False) -> dict[str, Any]:
    config, repo = load(config_path), repo_for(config_path, load(config_path))
    if not (allow_empty and config.get("components") == [] and config.get("relations") == []): components(config)
    try: relative = config_path.relative_to(repo).as_posix()
    except ValueError as error: raise ValueError("Codex config must live inside its repository (normally .archctx/architecture.json)") from error
    relative_state = codex_state(config_path, repo, target, explicit)
    block = codex_block(relative, command, relative_state)
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

def ensure_ignored(repo: Path, check: bool, extra: str | None = None) -> bool:
    path = repo / ".gitignore"; before = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = {line.strip() for line in before.splitlines() if line.strip() and not line.lstrip().startswith("#")}
    missing = [item for item in (".archctx/", *([extra] if extra else [])) if item not in entries]
    if not missing: return False
    if not check: path.write_text(before + ("" if not before or before.endswith("\n") else "\n") + "\n".join(missing) + "\n", encoding="utf-8")
    return True

def parsed_evidence(values: list[str]) -> list[dict[str, str]]:
    result = []
    for value in values:
        path, separator, contains = value.partition("::")
        if not separator or not path or not contains: raise ValueError("--evidence must be PATH::EXACT_SOURCE_TEXT")
        result.append({"path": path.replace("\\", "/"), "contains": contains})
    if not result: raise ValueError("new onboarding needs --evidence PATH::EXACT_SOURCE_TEXT; source evidence is required")
    return result

def selected_config(repo: Path, explicit: str | None) -> Path:
    if explicit: return Path(explicit).resolve()
    # No scanning or fallback: a shared definition must be selected explicitly.
    if (repo / "architecture" / "architecture.json").exists():
        raise ValueError("shared architecture/architecture.json exists; choose --config explicitly (including when a private .archctx/architecture.json also exists)")
    return repo / ".archctx" / "architecture.json"

def native_config(repo: Path, explicit: str | None) -> Path:
    if explicit: return Path(explicit).resolve()
    choices = [repo / folder / "architecture.json" for folder in ("architecture", ".archctx")]
    existing = [path for path in choices if path.is_file()]
    if len(existing) > 1: raise ValueError("multiple architecture definitions exist; choose --config explicitly")
    return existing[0] if existing else choices[0]

def understand(config_path: Path, explicit: str | None, args: dict[str, Any]) -> dict[str, Any]:
    from archctx_understand import discoveries, native_understand
    for name in ("show", "details"):
        if name in args and not isinstance(args[name], bool): raise ValueError(f"{name} must be boolean")
    for name in ("question", "resume"):
        if args.get(name) is not None and not isinstance(args[name], str): raise ValueError(f"{name} must be a string")
    files = args.get("files")
    if files is not None and (not isinstance(files, list) or not all(isinstance(path, str) for path in files)):
        raise ValueError("files must be a string array")
    if args.get("show"):
        if args.get("question") or files or args.get("resume"): raise ValueError("--show is read-only; do not combine it with question/files/resume")
        return discoveries(config_path, explicit, details=args.get("details", False))
    if args.get("details"): raise ValueError("--details requires --show")
    try:
        return native_understand(config_path, explicit, args.get("question"), files, args.get("resume"))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {"status": "INVALID", "reason": str(error)[:1600], "last_good_preserved": last_path(state(config_path, explicit)).exists(),
                "next_action": "repair the reported local input or component failure, then resume; other queries remain available"}

def init(repo: Path, target: Path, component: str | None, truth_sources: list[str], evidence_values: list[str], check: bool, config_path: Path | None = None, explicit: str | None = None, command: str = "archctx") -> dict[str, Any]:
    if not repo.is_dir(): raise ValueError(f"repo does not exist: {repo}")
    config_path = config_path or selected_config(repo, None); created = False
    try: config_path.relative_to(repo)
    except ValueError as error: raise ValueError("onboarding config must live inside --repo") from error
    extra_ignore = None
    relative_state = codex_state(config_path, repo, target, explicit)
    if relative_state and ".archctx" not in Path(relative_state).parts:
        # Escape gitignore patterns; the selected state is one literal directory.
        extra_ignore = "/" + "".join("\\" + x if x in "\\*?[]!# " else x for x in relative_state) + "/"
    codex_block(config_path.relative_to(repo).as_posix(), command)
    if not config_path.exists():
        if check: raise ValueError("init --check needs an existing selected architecture config")
        if not component: raise ValueError("new onboarding needs --component and --evidence; no architecture is inferred")
        facts = parsed_evidence(evidence_values)
        config = {"version": CONFIG_VERSION, "repo": Path(os.path.relpath(repo, config_path.parent)).as_posix(), "components": [{"id": component, "truth_sources": truth_sources or [x["path"] for x in facts], "evidence": facts}], "relations": []}
        if not check: atomic(config_path, config)
        created = True
    config = load(config_path)
    if repo_for(config_path, config) != repo.resolve(): raise ValueError("onboarding config repo must resolve to --repo")
    ignored_added = ensure_ignored(repo, check, extra_ignore)
    install = install_codex(config_path, target, check, command, explicit)
    refreshed = refresh(config_path, explicit) if not check else {"status": "CHECK"}
    return {"protocol_version": PROTOCOL_VERSION, "status": refreshed["status"], "action": "created" if created else "updated", "config": str(config_path), "gitignore_updated": ignored_added, "codex": install, "refresh": refreshed, "next_action": "start a new Codex session in this repo"}

def authored(context_value: dict[str, Any], ident: str, direction: str) -> list[str]:
    seen, todo = {ident}, [ident]
    while todo:
        current = todo.pop(0)
        for r in context_value.get("relations", []):
            neighbor = r.get("to") if direction == "downstream" and r.get("from") == current else r.get("from") if direction == "upstream" and r.get("to") == current else None
            if neighbor and neighbor not in seen: seen.add(neighbor); todo.append(neighbor)
    return sorted(seen)
def direct_relations(context_value: dict[str, Any], ident: str, direction: str) -> list[dict[str, Any]]:
    key = "from" if direction == "downstream" else "to"
    return [relation for relation in context_value.get("relations", []) if relation.get(key) == ident]
def code_query(config: dict[str, Any], repo: Path, directory: Path, symbol: str, direction: str) -> dict[str, Any]:
    graph = config.get("code_graph", {})
    if not isinstance(graph, dict) or not isinstance(graph.get("query"), list): return {"available": False, "reason": "no code_graph.query configured"}
    argv, r = run(graph["query"], repo, int(graph.get("timeout_seconds", 30)), {"{repo}": str(repo), "{state}": str(directory / "codegraph"), "{symbol}": symbol, "{direction}": direction})
    if r.returncode: return {"available": False, "provider": graph.get("provider", "external"), "error": r.stderr.strip()[-1000:], "command": argv}
    try: result: Any = json.loads(r.stdout)
    except json.JSONDecodeError: result = {"stdout_tail": r.stdout.strip()[-1000:]}
    return {"available": True, "kind": "code_graph_query", "provider": graph.get("provider", "external"), "confidence": "provider_reported", "result": result}
def trace(config_path: Path, explicit: str | None, ident: str, direction: str, include_code: bool = False, target: str | None = None) -> dict[str, Any]:
    value = snapshot(config_path, explicit); ctx = value.get("context", {})
    answer = {k: value[k] for k in ("protocol_version", "status", "revision", "freshness", "next_action") if k in value} | {"kind": "authored_architecture_trace", "provenance": "authored_architecture", "origin": ident, "direction": direction, "components": authored(ctx, ident, direction), "relations": direct_relations(ctx, ident, direction), "warning": value.get("reason")}
    if target:
        answer["target"] = target
        answer["target_reachable"] = target in answer["components"]
        answer["target_direct"] = any(relation.get("to") == target if direction == "downstream" else relation.get("from") == target for relation in answer["relations"])
    if include_code and ident in {x["id"] for x in ctx.get("components", [])}:
        config = load(config_path); component = next(x for x in ctx["components"] if x["id"] == ident); answer["code_graph"] = code_query(config, repo_for(config_path, config), state(config_path, explicit), component.get("code_symbol", ident), direction)
    return answer

def paths_for(repo: Path, base: str | None, files: list[str] | None) -> list[str]:
    if files: return sorted({x.replace("\\", "/") for x in files})
    if not base: raise ValueError("impact needs --base or --files")
    result = git(repo, "diff", "--no-renames", "--name-only", f"{base}..HEAD")  # Keep both old/new names as scope inputs.
    if result is None: raise ValueError(f"cannot diff base {base}")
    return [x.replace("\\", "/") for x in result.splitlines() if x]
def owners(config: dict[str, Any], paths: list[str]) -> list[str]:
    """Affected components, including both endpoints of changed relation evidence."""
    changed, declared = set(paths), components(config)
    def matches(value: dict[str, Any]) -> bool:
        return any(e["path"].replace("\\", "/") in changed for e in value.get("evidence", []) if isinstance(e, dict) and isinstance(e.get("path"), str))
    affected = {x["id"] for x in declared if matches(x)}
    for relation in config.get("relations", []):
        if matches(relation): affected.update((relation["from"], relation["to"]))
    return [x["id"] for x in declared if x["id"] in affected]
def declaration_context(value: dict[str, Any]) -> dict[str, Any]:
    """Compare authored inputs without treating validated evidence locations as edits."""
    result = {}
    for key in ("components", "relations"):
        result[key] = [{k: ([{field: e[field] for field in ("path", "contains") if field in e} for e in v] if k == "evidence" else v)
                        for k, v in item.items() if k not in ("confidence", "provenance") and (key != "components" or k in (*COMPONENT_FIELDS, "evidence"))}
                       for item in value.get(key, [])]
    return result


def dependency_direction(relation: dict[str, Any]) -> tuple[str, str]:
    if "dependency" in relation:
        return relation["dependency"], "explicit"
    if relation["kind"] in ("calls", "uses", "depends-on"):
        return "from_to", "kind_default"
    return "unclassified", "unclassified"


def change_scope(record: dict[str, Any], config: dict[str, Any] | None, paths: list[str],
                 config_path_relative: str | None = None, freshness: str = "unknown", details: bool = False) -> dict[str, Any]:
    """One pure, version-separated review scope for Agent queries and saved-file overlays."""
    old = record.get("context", {"components": [], "relations": []})
    paths = sorted({p.replace("\\", "/") for p in paths})
    selected_config = config_path_relative in paths
    invalid = None
    try:
        if config is None: raise ValueError("working config is unavailable")
        components(config)
        definition_changed = declaration_context(old) != declaration_context(config)
    except (ValueError, KeyError, TypeError) as error:
        invalid, definition_changed = str(error)[:500], True
    seeds = {"accepted": set(), "working": set()}
    if selected_config and definition_changed and not invalid:
        difference = record_diff({"context": declaration_context(old)}, {"context": declaration_context(config)})
        affected_relations = set(difference["changed_relations"] + difference["evidence_changed_relations"])
        affected_relations.update(relation_id(r) for r in difference["added_relations"] + difference["removed_relations"])
        for name, ctx in (("accepted", old), ("working", config)):
            ids = {c["id"] for c in ctx.get("components", [])}
            seeds[name].update(ids.intersection(difference["changed_components"] + difference["evidence_changed_components"]))
            for relation in ctx.get("relations", []):
                if relation_id(relation) in affected_relations:
                    seeds[name].update((relation["from"], relation["to"]))

    def scope(ctx, name):
        declared = {"version": CONFIG_VERSION, **ctx}
        associations = {}
        for path in paths:
            for ident in owners(declared, [path]):
                associations.setdefault(ident, []).append(path)
        direct = set(associations) | seeds[name]
        edges = []
        for relation in ctx.get("relations", []):
            direction, _ = dependency_direction(relation)
            if direction in ("from_to", "to_from"):
                source, target = (relation["from"], relation["to"]) if direction == "from_to" else (relation["to"], relation["from"])
                edges.append({"from": source, "to": target})
        graph = {"relations": edges}
        downstream = set().union(*(set(authored(graph, ident, "downstream")) for ident in direct)) if direct else set()
        upstream = set().union(*(set(authored(graph, ident, "upstream")) for ident in direct)) if direct else set()
        witnesses = []
        for relation in sorted(ctx.get("relations", []), key=relation_id):
            direction, semantics = dependency_direction(relation)
            source, target = (relation["to"], relation["from"]) if direction == "to_from" else (relation["from"], relation["to"])
            if not ({source, target} & direct or direction in ("none", "unclassified") and {source, target} & (downstream | upstream) or direction in ("from_to", "to_from") and
                    ({source, target} <= downstream or {source, target} <= upstream)):
                continue
            evidence_items = relation.get("evidence", [])
            witnesses.append({"id": relation_id(relation), "from": relation["from"], "to": relation["to"], "kind": relation["kind"],
                              "dependency": direction, "semantics": semantics,
                              "evidence_state": ("accepted_source_evidence" if name == "accepted" else "working_anchor_unvalidated") if evidence_items else "authored_only",
                              "evidence": evidence_items if details else [{k: e[k] for k in ("path", "line", "sha256") if k in e} for e in evidence_items[:1]],
                              "omitted_evidence": 0 if details else max(0, len(evidence_items) - 1)})
        return {"direct_components": sorted(direct), "dependencies": sorted(downstream - direct), "dependents": sorted(upstream - direct),
                "relations": witnesses, "associations": [{"component": k, "paths": v} for k, v in sorted(associations.items())],
                "config_seeds": sorted(seeds[name]), "uncovered_files": [p for p in paths if p != config_path_relative and not any(p in v for v in associations.values())], "omitted": {}}

    value = {"contract": "declared_change_scope/v1", "proof": "declared_review_scope_not_runtime_impact",
             "accepted_context_hash": record.get("context_hash"), "accepted_revision": record.get("revision"),
             "working_config_hash": semantic(config) if config else None, "freshness": "stale" if invalid else freshness,
             "accepted": scope(old, "accepted") if old.get("components") else None,
             "working": {"error": invalid} if invalid else scope(config, "working") if definition_changed else None,
             "working_provenance": "unaccepted_definition" if definition_changed else "same_declarations_as_accepted",
             "limitations": ["Declared dependency directions define checks, not runtime impact. Other kinds retain raw direction and do not propagate.",
                             "Accepted evidence belongs to its context; recheck stale evidence. Working anchors are unvalidated. Uncovered files are unknown."],
             "detail_query": "impact with the same files and --details (MCP details:true); compare context/config hashes"}
    if not details:
        groups = [(s, key) for s in (value["accepted"], value["working"]) if s and "omitted" in s
                  for key in ("direct_components", "dependencies", "dependents", "relations", "associations", "config_seeds", "uncovered_files")]
        for parent, key in groups:
            limit = 8 if key == "relations" else 12
            if len(parent[key]) > limit:
                parent["omitted"][key] = len(parent[key]) - limit
                parent[key] = parent[key][:limit]
        while len(json.dumps(value, ensure_ascii=False).encode()) > 8 * 1024:
            available = [(parent, key) for parent, key in groups if parent[key]]
            if not available: break
            parent, key = max(available, key=lambda pair: len(json.dumps(pair[0][pair[1]], ensure_ascii=False).encode()))
            parent[key].pop()
            parent["omitted"][key] = parent["omitted"].get(key, 0) + 1
    return value


def impact(config_path: Path, explicit: str | None, base: str | None, files: list[str] | None, details: bool = False) -> dict[str, Any]:
    if not isinstance(details, bool): raise ValueError("details must be a boolean")
    observed: dict[str, Any] = {}
    retained = status(config_path, explicit, _observed=observed)
    config, record, repo = observed.get("config"), observed.get("record") or {}, observed.get("repo")
    changed = paths_for(repo, base, files) if repo else paths_for(Path(), None, files)
    relative = os.path.relpath(config_path, repo).replace("\\", "/") if repo else None
    scopes = change_scope(record, config, changed, relative, retained.get("freshness", "unknown"), details)
    # Preserve the old fields' direction/selection, but do not use them as the shared contract.
    direct = owners(config, changed) if config and not (scopes.get("working") or {}).get("error") else []
    ctx = record.get("context", {})
    reach = sorted(set().union(*(set(authored(ctx, x, "downstream")) for x in direct))) if direct else []
    next_action = "inspect affected evidence and test" if retained.get("status") == "FRESH" else "refresh" if direct else "no canonical evidence owner; no architecture refresh needed"
    try:
        current_record = load(last_path(state(config_path, explicit))) if last_path(state(config_path, explicit)).exists() else {}
        moved = current_record != record or config is not None and load(config_path) != config
    except (ValueError, OSError):
        moved = True
    if moved:
        return {"protocol_version": PROTOCOL_VERSION, "status": "RETRY", "freshness": "stale", "reason": "config or accepted context changed during impact; retry the read"}
    return {"protocol_version": PROTOCOL_VERSION, "kind": "authored_architecture_impact", "provenance": "source_evidence_plus_authored_architecture", "base": base, "changed_files": changed, "direct_components": direct, "reachable_components": reach,
            "legacy_semantics": {"direct_components": "working evidence owners only", "reachable_components": "all-kind outgoing authored reach from legacy direct IDs in the accepted graph; not dependency or runtime impact"},
            "change_scope": scopes, "code_graph": {"available": isinstance((config or {}).get("code_graph"), dict), "note": "Code edges remain separate provider facts; use trace --code."}, "freshness": retained.get("freshness"), "status": retained.get("status"), "warning": retained.get("reason"), "next_action": next_action}

def snapshot_files(directory: Path) -> list[Path]:
    root = directory / "snapshots"
    if not root.is_dir(): return []
    return sorted((path for path in root.glob("*.json") if len(path.stem) == 64 and all(char in "0123456789abcdef" for char in path.stem)), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
def snapshot_record(directory: Path, context_hash: str) -> dict[str, Any] | None:
    if len(context_hash) != 64 or any(char not in "0123456789abcdef" for char in context_hash): return None
    path = directory / "snapshots" / f"{context_hash}.json"
    try: return load(path) if path.is_file() else None
    except ValueError: return None
def prune_snapshots(directory: Path) -> None:
    try:
        for path in snapshot_files(directory)[SNAPSHOT_LIMIT:]: path.unlink()
    except OSError: pass
def retained(directory: Path, rev: str) -> dict[str, Any] | None:
    matches = [record for path in snapshot_files(directory) if (record := load(path)).get("revision") == rev]
    return min(matches, key=lambda value: str(value.get("created_at", ""))) if matches else None
def semantic_component(component: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in component.items() if key != "evidence"}
def semantic_relation(relation: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in relation.items() if key not in ("evidence", "confidence")}
def relation_map(relations: list[Any]) -> dict[str, dict[str, Any]]:
    return {relation_id(relation): relation for relation in relations if isinstance(relation, dict)}
def record_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    a, b = {x["id"]: x for x in old["context"]["components"]}, {x["id"]: x for x in new["context"]["components"]}
    ar, br = relation_map(old["context"].get("relations", [])), relation_map(new["context"].get("relations", []))
    shared, shared_relations = a.keys() & b.keys(), ar.keys() & br.keys()
    return {
        "changed_components": sorted(x for x in shared if semantic_component(a[x]) != semantic_component(b[x])) + sorted(a.keys() ^ b.keys()),
        "evidence_changed_components": sorted(x for x in shared if a[x].get("evidence") != b[x].get("evidence")),
        "added_relations": [br[x] for x in sorted(br.keys() - ar.keys())],
        "removed_relations": [ar[x] for x in sorted(ar.keys() - br.keys())],
        "changed_relations": sorted(x for x in shared_relations if semantic_relation(ar[x]) != semantic_relation(br[x])),
        "evidence_changed_relations": sorted(x for x in shared_relations if ar[x].get("evidence") != br[x].get("evidence")),
    }

def bounded_updates(value: dict[str, Any]) -> dict[str, Any]:
    """Updates are notices; explicit queries retain full identifiers and evidence."""
    truncated = False
    def compact(item: Any, key: str = "") -> Any:
        nonlocal truncated
        if isinstance(item, str) and key != "cursor" and len(item) > 240:
            truncated = True
            return item[:240]
        if isinstance(item, dict): return {k: compact(v, k) for k, v in item.items()}
        if isinstance(item, list): return [compact(v) for v in item]
        return item
    result = compact(value)
    if truncated: result["truncated_strings"] = True; result["more_available"] = True
    groups = [(result, "overview", "omitted_overview_components"), (result, "candidates", "omitted_candidate_count"), (result, "affected_components", "omitted_affected_components"), (result, "reason", "omitted_reason_count"), (result, "failures", "omitted_failure_count")]
    delta = result.get("accepted_delta", {})
    groups += [(delta, key, f"omitted_{key}") for key, items in delta.items() if isinstance(items, list)]
    analysis = result.get("source_analysis", {})
    groups += [(analysis, key, {"candidates": "omitted_candidate_count", "tour": "omitted_tour_steps"}.get(key, f"omitted_{key}")) for key, items in analysis.items() if isinstance(items, list)]
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > UPDATES_BYTES:
        available = [(parent, key, omitted) for parent, key, omitted in groups if parent.get(key)]
        if not available: break
        parent, key, omitted = max(available, key=lambda group: len(json.dumps(group[0][group[1]], ensure_ascii=False).encode()))
        parent[key].pop(); parent[omitted] = parent.get(omitted, 0) + 1
        result["more_available"] = True
    return result


def analysis_receipt_identity(directory: Path) -> str | None:
    from archctx_understand import current_path, bounded_raw, SUMMARY_BYTES
    path = current_path(directory)
    if not path.exists(): return None
    try:
        decisions = candidate_decision_path(directory)
        return semantic({"receipt": sha(bounded_raw(path, SUMMARY_BYTES)),
                         "decisions": sha(bounded_raw(decisions, DECISION_BYTES)) if decisions.exists() else None})
    except (OSError, ValueError) as error:
        return semantic({"invalid_analysis": str(error)})


def updates(config_path: Path, explicit: str | None, since: str | None = None) -> dict[str, Any]:
    """Read relevant changes since a caller-held cursor; never advance watcher or accepted state."""
    directory = state(config_path, explicit)
    selection = semantic({"config": str(config_path.resolve()), "state": str(directory.resolve())})
    previous = None
    if since is not None:
        if not isinstance(since, str) or len(since) > 300: raise ValueError("invalid updates cursor")
        parts = since.split(":")
        if len(parts) != 5 or parts[0] != "u1" or any(part != "-" and (len(part) != 64 or any(c not in "0123456789abcdef" for c in part)) for part in parts[1:]):
            raise ValueError("invalid updates cursor")
        previous = dict(zip(("selection", "context", "view", "observation"), parts[1:]))
        if previous["selection"] != selection: raise ValueError("updates cursor belongs to another config/state selection")
    observed: dict[str, Any] = {}
    value = status(config_path, explicit, _observed=observed)
    analysis_identity = analysis_receipt_identity(directory)
    from archctx_understand import discoveries
    analysis = discoveries(config_path, explicit) if analysis_identity is not None else {"configured": False}
    old = observed.get("record")
    config, repo = observed.get("config", {}), observed.get("repo")
    candidate = observed.get("candidates", {"state": "not_checked", "candidates": []})
    view_hash = None
    if repo:
        try:
            if settings := archify_config(config, repo): view_hash = sha(settings["view"].read_bytes())
        except (OSError, ValueError): pass  # The status response already reports the unavailable view.
    def identity(path: Path) -> str | None:
        try: return semantic(load(path))
        except (OSError, ValueError): return None
    # Publication/config/view can change during this read. Never consume a mixed observation.
    if identity(config_path) != (semantic(config) if "config" in observed else None) or identity(last_path(directory)) != (semantic(old) if old else None) or view_hash != observed.get("view_hash") or analysis_receipt_identity(directory) != analysis_identity:
        return {"protocol_version": PROTOCOL_VERSION, "kind": "architecture_updates", "status": "RETRY", "freshness": "stale", "changed": False, "cursor": since, "reason": ["config, accepted context, view, or source analysis changed during the observation"], "next_action": "retry the relevant read with the same cursor"}
    current = observed.get("current")
    observation_hash = semantic({
        "config": semantic(config), "context": architecture_semantic(current) if current else None,
        "facts": observed.get("facts"), "relation_facts": observed.get("relation_facts"),
        "status": value.get("status"), "reason": value.get("reason", value.get("failures")),
        "candidate_state": candidate.get("state"), "candidate_ids": sorted(x["id"] for x in candidate.get("candidates", [])),
        "source_analysis": analysis, "analysis_receipt": analysis_identity,
        "blueprint": {key: (old or {}).get("archify", {}).get(key) for key in ("view_sha256", "ir_sha256", "html_sha256")},
    })
    context_hash = old.get("context_hash") if isinstance(old, dict) else None
    cursor = ":".join(("u1", selection, context_hash or "-", view_hash or "-", observation_hash))
    base = {key: value[key] for key in ("protocol_version", "status", "freshness", "confidence", "next_action") if key in value}
    base.update(kind="architecture_updates", cursor=cursor)
    if previous and since == cursor: return base | {"changed": False}
    before = snapshot_record(directory, previous["context"]) if previous and previous["context"] != "-" else None
    if old and previous and previous["context"] == context_hash: before = old
    baseline = "initial" if previous is None or previous["context"] == "-" else "retained" if before else "unavailable"
    accepted: dict[str, Any] = {"baseline": baseline, "from_context_hash": previous["context"] if previous and previous["context"] != "-" else None, "to_context_hash": context_hash}
    accepted_changed = baseline == "unavailable"
    difference: dict[str, Any] = {}
    if before and old:
        difference = record_diff(before, old)
        for key, items in difference.items():
            # Relationship evidence stays in explicit queries, not repeated update notices.
            compact = [relation_id(item) if isinstance(item, dict) else item for item in items]
            accepted[key] = compact[:CANDIDATE_OUTPUT_LIMIT]
            accepted[f"omitted_{key}"] = max(0, len(compact) - CANDIDATE_OUTPUT_LIMIT)
        accepted["coverage_changed"] = before["context"].get("coverage") != old["context"].get("coverage")
        accepted_changed = any(difference.values()) or accepted["coverage_changed"]
    view_changed = previous is not None and previous["view"] != (view_hash or "-")
    changed = previous is None or previous["observation"] != observation_hash or view_changed or accepted_changed
    if not changed: return base | {"changed": False}
    paths_before = {e["path"]: e.get("sha256") for x in (old or {}).get("context", {}).get("components", []) for e in x.get("evidence", [])}
    paths_before.update({e["path"]: e.get("sha256") for x in (old or {}).get("context", {}).get("relations", []) for e in x.get("evidence", [])})
    paths_now = {e["path"]: e.get("sha256") for evidence_items in observed.get("facts", {}).values() for e in evidence_items}
    paths_now.update({e["path"]: e.get("sha256") for evidence_items in observed.get("relation_facts", []) for e in evidence_items})
    changed_paths = sorted(path for path in paths_before.keys() | paths_now.keys() if paths_before.get(path) != paths_now.get(path)) if repo else []
    try: affected = set(owners(config, changed_paths)) if repo else set()
    except ValueError: affected = set()
    if old: affected.update(owners({"version": CONFIG_VERSION, **old["context"]}, changed_paths))
    affected.update(difference.get("changed_components", [])); affected.update(difference.get("evidence_changed_components", []))
    working = record_diff(old, {"context": current}) if old and current else {}
    affected.update(working.get("changed_components", [])); affected.update(working.get("evidence_changed_components", []))
    changed_relations = set(difference.get("changed_relations", []) + difference.get("evidence_changed_relations", []))
    changed_relations.update(working.get("changed_relations", []) + working.get("evidence_changed_relations", []))
    contexts = ((before or {}).get("context", {}), (old or {}).get("context", {}), current or {})
    for relation in difference.get("added_relations", []) + difference.get("removed_relations", []) + working.get("added_relations", []) + working.get("removed_relations", []) + [r for ctx in contexts for r in ctx.get("relations", []) if relation_id(r) in changed_relations]:
        affected.update((relation["from"], relation["to"]))
    affected_list = sorted(affected)
    blueprint = (old or {}).get("archify", {})
    answer = base | {
        "changed": True, "accepted_delta": accepted,
        "affected_components": affected_list[:CANDIDATE_OUTPUT_LIMIT], "affected_component_count": len(affected_list), "omitted_affected_components": max(0, len(affected_list) - CANDIDATE_OUTPUT_LIMIT),
        **candidate_fields(candidate),
        "source_analysis": analysis,
        "view_changed": view_changed, "view_sha256": view_hash,
        "accepted_blueprint": {key: blueprint[key] for key in ("configured", "view_sha256", "ir_sha256", "html_sha256") if key in blueprint} | {"context_hash": context_hash},
    }
    for key in ("reason", "failures"):
        if value.get(key):
            items = value[key] if isinstance(value[key], list) else [value[key]]
            answer[key] = bounded_strings(items)
            if len(items) > len(answer[key]): answer[f"omitted_{key}_count"] = len(items) - len(answer[key])
    if previous is None:
        declared = (old or {}).get("context", {}).get("components", [])
        answer["overview"] = [{key: str(item[key]) if key == "id" else str(item[key])[:160] for key in ("id", "name", "purpose") if key in item} for item in declared[:CANDIDATE_OUTPUT_LIMIT]]
        answer["component_count"] = len(declared); answer["omitted_overview_components"] = max(0, len(declared) - CANDIDATE_OUTPUT_LIMIT)
        answer["revision"] = (old or {}).get("revision")
        coverage_value = (old or {}).get("context", {}).get("coverage")
        if isinstance(coverage_value, dict):
            limitations = coverage_value.get("limitations", [])
            answer["coverage"] = {"scope": str(coverage_value.get("scope", ""))[:240], "limitations": bounded_strings(limitations), "omitted_limitations": max(0, len(limitations) - CANDIDATE_OUTPUT_LIMIT)}
    return bounded_updates(answer)

def history(directory: Path, context_hash: str | None, limit: int) -> dict[str, Any]:
    if context_hash is not None:
        record = snapshot_record(directory, context_hash)
        if record is None: return {"protocol_version": PROTOCOL_VERSION, "status": "ERROR", "error": f"no retained snapshot for context hash {context_hash}"}
        return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "kind": "architecture_history_snapshot", "freshness": "historical", "not_current_authority": True, "revision": record["revision"], "snapshot_created_at": record["created_at"], "context_hash": record["context_hash"], "context": record["context"], "graph": persistent_graph(record.get("graph", {})) if isinstance(record.get("graph"), dict) else {}, "gates": persistent_gates(record.get("gates", [])) if isinstance(record.get("gates"), list) else []}
    if limit < 0: raise ValueError("history limit must be non-negative")
    records = list(reversed([load(path) for path in snapshot_files(directory)]))
    summaries = []
    for index, record in enumerate(records):
        previous = records[index - 1] if index else None; delta = record_diff(previous, record) if previous else None
        summaries.append({"created_at": record.get("created_at"), "revision": record.get("revision"), "context_hash": record.get("context_hash"), "component_count": len(record.get("context", {}).get("components", [])), "relation_count": len(record.get("context", {}).get("relations", [])), "graph": persistent_graph(record.get("graph", {})) if isinstance(record.get("graph"), dict) else {}, "gates": persistent_gates(record.get("gates", [])) if isinstance(record.get("gates"), list) else [], "candidate_baseline_reset": bool(record.get("candidate_baseline_reset")), "candidate_decisions": record.get("candidate_decisions", []) if isinstance(record.get("candidate_decisions"), list) else [], "delta_from_previous": {"changed_components": delta["changed_components"], "evidence_changed_components": delta["evidence_changed_components"], "changed_relations": delta["changed_relations"], "evidence_changed_relations": delta["evidence_changed_relations"], "relation_change_count": len(delta["added_relations"]) + len(delta["removed_relations"]) + len(delta["changed_relations"])} if delta else None})
    visible = list(reversed(summaries)) if limit == 0 else list(reversed(summaries[-limit:]))
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "kind": "architecture_history", "snapshots": visible, "snapshot_count": len(summaries), "omitted_snapshot_count": len(summaries) - len(visible), "retention": {"max_snapshots": SNAPSHOT_LIMIT, "content": "source-evidence snapshots; use context_hash to retrieve one"}}
def changed_since(config_path: Path, explicit: str | None, rev: str) -> dict[str, Any]:
    directory = state(config_path, explicit)
    if not last_path(directory).exists(): return {"status": "MISSING", "next_action": "run refresh"}
    old = retained(directory, rev)
    if old is None: return {"status": "ERROR", "error": f"no retained snapshot for revision {rev}"}
    new = load(last_path(directory)); return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "from_revision": rev, "to_revision": new["revision"]} | record_diff(old, new)
def delta(config_path: Path, explicit: str | None, rev: str) -> dict[str, Any]: return changed_since(config_path, explicit, rev) | {"kind": "architecture_delta", "provenance": "retained_source_evidence_snapshots", "archify": "Use configured Archify gate receipts for typed IR/visual delta; no renderer is reimplemented here."}

def candidates(config_path: Path, explicit: str | None, limit: int = CANDIDATE_OUTPUT_LIMIT) -> dict[str, Any]:
    config, directory = load(config_path), state(config_path, explicit)
    analysis_identity = analysis_receipt_identity(directory)
    from archctx_understand import discoveries
    analysis = discoveries(config_path, explicit, limit) if analysis_identity is not None else {"configured": False}
    if analysis_receipt_identity(directory) != analysis_identity:
        return {"protocol_version": PROTOCOL_VERSION, "status": "RETRY", "freshness": "stale", "next_action": "analysis changed during query; retry"}
    if not last_path(directory).exists(): return {"protocol_version": PROTOCOL_VERSION, "status": "MISSING", "freshness": "missing", "source_analysis": analysis, "next_action": "run refresh to establish canonical and candidate baselines"}
    old = load(last_path(directory))
    try: observation = candidate_observation(config, repo_for(config_path, config), old)
    except (OSError, ValueError) as error: return {"protocol_version": PROTOCOL_VERSION, "status": "CANDIDATE_CHECK_INCOMPLETE", "freshness": "stale", "last_good_preserved": True, "failures": [str(error)], "next_action": "repair drift rule observation"}
    state_name, found = observation["state"], observation["candidates"]
    status_name = "CANDIDATE_REVIEW_REQUIRED" if found else "CANDIDATE_CHECK_INCOMPLETE" if state_name in ("incomplete", "baseline_missing", "baseline_incomplete", "rules_changed") else "PASS"
    next_action = "inspect candidates, then accept or reject" if found else "query normally" if status_name == "PASS" else "review current source, then run refresh with reset_candidate_baseline" if state_name in ("baseline_missing", "rules_changed") else "run refresh to establish or repair candidate baseline"
    if analysis_receipt_identity(directory) != analysis_identity:
        return {"protocol_version": PROTOCOL_VERSION, "status": "RETRY", "freshness": "stale", "next_action": "analysis changed during query; retry"}
    return {"protocol_version": PROTOCOL_VERSION, "status": status_name, "freshness": "stale" if status_name != "PASS" else "fresh", "base_context_hash": old.get("context_hash"), "last_good_preserved": True, "provenance": "deterministic_rule_derived_source_fact", "next_action": next_action, "source_analysis": analysis} | candidate_fields(observation, limit)

def binding_parts(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or ":" not in value: raise ValueError("bind must be component:<id> or relation:<id>")
    kind, ident = value.split(":", 1)
    if kind not in ("component", "relation") or not ident: raise ValueError("bind must be component:<id> or relation:<id>")
    return kind, ident

def binding_evidence(context_value: dict[str, Any], binding: tuple[str, str]) -> list[dict[str, Any]]:
    kind, ident = binding
    if kind == "component":
        component = next((value for value in context_value.get("components", []) if value.get("id") == ident), None)
        return component.get("evidence", []) if isinstance(component, dict) and isinstance(component.get("evidence"), list) else []
    relation = next((value for value in context_value.get("relations", []) if isinstance(value, dict) and relation_id(value) == ident), None)
    return relation.get("evidence", []) if isinstance(relation, dict) and isinstance(relation.get("evidence"), list) else []

def candidate_pattern(config: dict[str, Any], candidate_value: dict[str, Any]) -> str:
    for rule in normalized_drift_rules(config):
        if rule["id"] != candidate_value.get("rule_id"): continue
        for direction, patterns in (("added", rule["added_contains"]), ("removed", rule["removed_contains"])):
            for pattern in patterns:
                if signal_key(direction, pattern) == candidate_value.get("signal"): return pattern
    raise ValueError("candidate signal no longer matches configured drift rule")

def candidate_for_review(config_path: Path, explicit: str | None, ident: str) -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    config, directory = load(config_path), state(config_path, explicit)
    if not last_path(directory).exists(): raise ValueError("candidate review needs last-good context")
    old, repo = load(last_path(directory)), repo_for(config_path, config)
    observation = candidate_observation(config, repo, old)
    if observation["state"] != "ready": raise ValueError("candidate observation is not complete")
    candidate = next((value for value in observation["candidates"] if value["id"] == ident), None)
    if candidate is None: raise ValueError("candidate is no longer pending; re-run candidates")
    return config, directory, old, observation, candidate

def changed_candidate_bindings(config: dict[str, Any], old: dict[str, Any], current: dict[str, Any], candidate_value: dict[str, Any]) -> set[tuple[str, str]]:
    changes = record_diff(old, {"context": current})
    changed_components = set(changes["changed_components"])
    changed_relations = {relation_id(value) for value in changes["added_relations"] + changes["removed_relations"]} | set(changes["changed_relations"])
    evidence_context = old["context"] if candidate_value.get("change") == "removed" else current
    path = candidate_value.get("evidence", {}).get("path")
    line = candidate_value.get("evidence", {}).get("line")
    pattern = candidate_pattern(config, candidate_value)
    changed = {("component", value) for value in changed_components} | {("relation", value) for value in changed_relations}
    if not isinstance(path, str) or not isinstance(line, int) or not pattern: return set()
    return {binding for binding in changed if any(path == evidence.get("path") and line == evidence.get("line") and pattern in str(evidence.get("contains", "")) for evidence in binding_evidence(evidence_context, binding) if isinstance(evidence, dict))}

def validate_acceptance(config: dict[str, Any], old: dict[str, Any], current: dict[str, Any], candidate_value: dict[str, Any], bindings: list[str]) -> list[tuple[str, str]]:
    parsed = [binding_parts(value) for value in bindings]
    if not parsed or len(parsed) > 4 or len(parsed) != len(set(parsed)) or any(len(value) > 160 for value in bindings): raise ValueError("candidate bindings must be 1-4 unique short values")
    changes = record_diff(old, {"context": current})
    changed_components = set(changes["changed_components"])
    changed_relations = {relation_id(value) for value in changes["added_relations"] + changes["removed_relations"]} | set(changes["changed_relations"])
    for kind, value in parsed:
        if kind == "component" and value not in changed_components: raise ValueError(f"candidate bind is not a semantic component change: {value}")
        if kind == "relation" and value not in changed_relations: raise ValueError(f"candidate bind is not a semantic relation change: {value}")
    if not set(parsed).intersection(changed_candidate_bindings(config, old, current, candidate_value)):
        raise ValueError("at least one candidate bind needs source evidence at the exact candidate signal")
    return parsed

def current_context(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    repo = repo_for(config_path, config); facts, relation_facts, failures = validate(repo, config)
    if failures: raise ValueError("cannot review candidate while config/evidence is invalid")
    return context(config, revision(repo, {"components": facts, "relations": relation_facts}), facts, relation_facts)

def pending_review_result(old: dict[str, Any], observation: dict[str, Any], summary: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "status": "CANDIDATE_REVIEW_REQUIRED", "freshness": "stale", "last_good_preserved": True, "base_context_hash": old["context_hash"], "candidate": summary, "reviewed_candidate_count": len(decisions), "remaining_candidate_count": len(observation["candidates"]) - len(decisions), "next_action": "review every remaining candidate before promotion"} | candidate_fields(observation)

def _submit_candidate_decision_locked(config_path: Path, explicit: str | None, directory: Path, ident: str, summary: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    config, directory, old, observation, candidate_value = candidate_for_review(config_path, explicit, ident)
    record = {"at": datetime.now(timezone.utc).isoformat(), "state": "pending", **summary, **details, "base_context_hash": old["context_hash"]}
    record_decision(directory, record)
    decisions = pending_decisions(directory, old["context_hash"], observation["candidates"])
    if set(decisions) != {candidate["id"] for candidate in observation["candidates"]}:
        return pending_review_result(old, observation, summary, decisions)
    current = current_context(config_path, config)
    by_id = {candidate["id"]: candidate for candidate in observation["candidates"]}
    for decision_id, decision in decisions.items():
        if decision.get("decision") == "accepted": validate_acceptance(config, old, current, by_id[decision_id], decision.get("bindings", []))
    acknowledged = {decision_id: {key: decision[key] for key in ("id", "kind", "rule_id", "decision")} for decision_id, decision in decisions.items()}
    result = _refresh_locked(config_path, explicit, directory, acknowledged=acknowledged, expected_context_hash=old["context_hash"])
    if result.get("status") == "PASS":
        try: finalize_decisions(directory, old["context_hash"], decisions, result["context_hash"])
        except (OSError, ValueError): result["decision_receipt_cleanup"] = "deferred"
    return result | {"candidate": summary}


def submit_candidate_decision(config_path: Path, explicit: str | None, ident: str, summary: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    directory = state(config_path, explicit)
    try:
        with refresh_lock(directory):
            return _submit_candidate_decision_locked(config_path, explicit, directory, ident, summary, details)
    except RefreshBusyError as error:
        return refresh_retry(directory, None, str(error), ["writer_lock"])

def accept_candidate(config_path: Path, explicit: str | None, ident: str, bindings: list[str]) -> dict[str, Any]:
    directory = state(config_path, explicit)
    try:
        if ident.startswith("ua:"):
            from archctx_understand import review
            return review(config_path, explicit, ident, bindings=bindings)
        with refresh_lock(directory):
            config, _, old, _, candidate_value = candidate_for_review(config_path, explicit, ident)
            summary = {"id": ident, "kind": candidate_value["kind"], "rule_id": candidate_value["rule_id"], "decision": "accepted"}
            validate_acceptance(config, old, current_context(config_path, config), candidate_value, bindings)
            return _submit_candidate_decision_locked(config_path, explicit, directory, ident, summary, {"bindings": bindings})
    except RefreshBusyError as error:
        return refresh_retry(directory, None, str(error), ["writer_lock"])

def reject_candidate(config_path: Path, explicit: str | None, ident: str, reason: str) -> dict[str, Any]:
    allowed = {"not_architecture", "existing_canonical", "test_fixture", "false_match"}
    if reason not in allowed: raise ValueError("reject reason must be one of not_architecture, existing_canonical, test_fixture, false_match")
    directory = state(config_path, explicit)
    try:
        if ident.startswith("ua:"):
            from archctx_understand import review
            return review(config_path, explicit, ident, reason=reason)
        with refresh_lock(directory):
            config, _, old, _, candidate_value = candidate_for_review(config_path, explicit, ident)
            if changed_candidate_bindings(config, old, current_context(config_path, config), candidate_value): raise ValueError("candidate has changed canonical evidence; accept it instead of rejecting")
            summary = {"id": ident, "kind": candidate_value["kind"], "rule_id": candidate_value["rule_id"], "decision": "rejected"}
            return _submit_candidate_decision_locked(config_path, explicit, directory, ident, summary, {"reason": reason})
    except RefreshBusyError as error:
        return refresh_retry(directory, None, str(error), ["writer_lock"])

def drift(config_path: Path, base: str) -> dict[str, Any]:
    config = load(config_path); patch = git(repo_for(config_path, config), "diff", "--unified=0", f"{base}..HEAD")
    if patch is None: raise ValueError(f"cannot diff base {base}")
    found = []
    for rule in normalized_drift_rules(config):
        path = ""
        for line in patch.splitlines():
            if line.startswith("+++ b/"): path = line[6:]
            elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")) and any(fnmatch.fnmatch(path, pattern) for pattern in rule["paths"]):
                direction, patterns = ("added", rule["added_contains"]) if line.startswith("+") else ("removed", rule["removed_contains"])
                for pattern in patterns:
                    if pattern in line[1:]:
                        candidate = {"kind": rule["kind"], "rule_id": rule["id"], "path": path, "signal": direction, "status": "CANDIDATE_REVIEW_REQUIRED", "provenance": "deterministic_git_diff_fact"}
                        if candidate not in found: found.append(candidate)
    return {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "base": base, "candidates": found[:CANDIDATE_OUTPUT_LIMIT], "candidate_count": len(found), "omitted_candidate_count": max(0, len(found) - CANDIDATE_OUTPUT_LIMIT), "note": "Configured high-value additions/removals only; candidates are not architecture facts.", "next_action": "review candidates, then update canonical config if appropriate" if found else "none"}

def ignored(path: str, patterns: list[Any]) -> bool: return any(fnmatch.fnmatch(path, str(x)) for x in patterns)
def manifest(config_path: Path, repo: Path, config: dict[str, Any]) -> dict[str, str | None]:
    paths = {e["path"].replace("\\", "/") for x in components(config) for e in x["evidence"] if isinstance(e, dict) and isinstance(e.get("path"), str)}
    paths |= {e["path"].replace("\\", "/") for relation in config.get("relations", []) if isinstance(relation, dict) for e in relation.get("evidence", []) if isinstance(e, dict) and isinstance(e.get("path"), str)}
    mandatory: set[str] = set()
    try:
        config_relative = config_path.resolve().relative_to(repo.resolve()).as_posix()
        paths.add(config_relative); mandatory.add(config_relative)
    except ValueError: pass
    if settings := archify_config(config, repo):
        paths.add(settings["view_relative"]); mandatory.add(settings["view_relative"])
    watch = config.get("watch", {})
    if not isinstance(watch, dict): raise ValueError("watch must be an object")
    ignore = watch.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(pattern, str) for pattern in ignore): raise ValueError("watch.ignore must contain strings")
    paths = {path for path in paths if path in mandatory or not ignored(path, ignore)}
    def include(path: Path) -> None:
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
        if not ignored(relative, ignore): paths.add(relative)
        if len(paths) > WATCH_FILE_LIMIT: raise ValueError(f"watch scope exceeds {WATCH_FILE_LIMIT} files; narrow watch.paths")
    if len(paths) > WATCH_FILE_LIMIT: raise ValueError(f"watch scope exceeds {WATCH_FILE_LIMIT} files; narrow watch.paths")
    for pattern in watch.get("paths", []):
        if not isinstance(pattern, str): raise ValueError("watch.paths must contain strings")
        for path in repo.glob(relative_glob(pattern, "watch.paths")):
            if path.is_file(): include(path)
    candidate_paths, _ = candidate_files(repo, normalized_drift_rules(config), ignore)
    for path in candidate_paths: include(path)
    selected = [path for path in sorted(paths) if path in mandatory or not ignored(path, ignore)]
    if len(selected) > WATCH_FILE_LIMIT: raise ValueError(f"watch scope exceeds {WATCH_FILE_LIMIT} files; narrow watch.paths")
    total = 0; result: dict[str, str | None] = {}
    for relative in selected:
        path = repo / relative
        if not path.is_file(): result[relative] = None; continue
        total += path.stat().st_size
        if total > WATCH_SOURCE_BYTES: raise ValueError(f"watch scope exceeds {WATCH_SOURCE_BYTES} source bytes; narrow watch.paths")
        result[relative] = sha(path.read_bytes())
    return result
def watch_once(config_path: Path, explicit: str | None, apply: bool = False) -> dict[str, Any]:
    try:
        config = load(config_path); repo, directory = repo_for(config_path, config), state(config_path, explicit); live = directory / "live-state.json"; current = manifest(config_path, repo, config)
    except (OSError, ValueError) as error:
        directory = state(config_path, explicit)
        return {"protocol_version": PROTOCOL_VERSION, "status": "INVALID", "freshness": "stale", "failures": [str(error)], "last_good_preserved": last_path(directory).exists(), "next_action": "repair watch/config scope before continuing"}
    previous = load(live).get("manifest", {}) if live.exists() else None
    def persist() -> None:
        atomic(live, {"manifest": current, "observed_at": datetime.now(timezone.utc).isoformat()})
    if previous is None:
        persist()
        observed = candidates(config_path, explicit) if last_path(directory).exists() else {"status": "PASS"}
        if observed.get("status") != "PASS": return observed | {"event": "ARCHITECTURE_CANDIDATE_OBSERVED", "changed_files": [], "direct_components": [], "watch_changed_files": []}
        return {"protocol_version": PROTOCOL_VERSION, "status": "WATCH_READY", "watched_files": len(current), "next_action": "keep watching"}
    changed = sorted(p for p in set(previous) | set(current) if previous.get(p) != current.get(p)); direct = owners(config, changed)
    controls = set()
    try:
        controls.add(config_path.resolve().relative_to(repo.resolve()).as_posix())
    except ValueError: pass
    if settings := archify_config(config, repo): controls.add(settings["view_relative"])
    control_changed = sorted(set(changed) & controls); source_changed = [path for path in changed if path not in controls]
    observed = candidates(config_path, explicit) if changed else {"status": "PASS"}
    if observed.get("status") != "PASS":
        persist()
        return observed | {"event": "ARCHITECTURE_CANDIDATE_OBSERVED", "changed_files": changed, "direct_components": direct, "watch_changed_files": changed}
    if not direct and not control_changed:
        if changed:
            persist()
        return {"protocol_version": PROTOCOL_VERSION, "status": "NO_RELEVANT_CHANGE", "changed_files": changed, "direct_components": [], "next_action": "no canonical evidence, architecture config/view, or high-value candidate changed"}
    if apply:
        refreshed = refresh(config_path, explicit, changed=source_changed)
        if refreshed.get("status") != "RETRY":
            persist()
        event = "ARCHITECTURE_CONTEXT_REFRESHED" if refreshed.get("status") == "PASS" else "ARCHITECTURE_REFRESH_RETRY" if refreshed.get("status") == "RETRY" else "ARCHITECTURE_REFRESH_FAILED"
        return refreshed | {"event": event, "direct_components": direct, "watch_changed_files": changed, "control_changed_files": control_changed, "auto_refresh": "existing_declared_context_only"}
    persist()
    event = "ARCHITECTURE_CONFIG_CHANGED" if control_changed and not direct else "CANONICAL_EVIDENCE_CHANGED"
    return status(config_path, explicit) | {"event": event, "direct_components": direct, "watch_changed_files": changed, "control_changed_files": control_changed, "next_action": "inspect changed evidence/config, then run refresh explicitly when it remains canonical"}
def watch(config_path: Path, explicit: str | None, poll_ms: int, max_events: int | None, apply: bool = False) -> int:
    count = 0; previous_retry = None
    while max_events is None or count < max_events:
        started = time.monotonic(); value = watch_once(config_path, explicit, apply)
        retry = semantic({key: value.get(key) for key in ("status", "failures", "changed_inputs", "watch_changed_files")}) if value["status"] == "RETRY" else None
        if value["status"] != "NO_RELEVANT_CHANGE" and (retry is None or retry != previous_retry):
            dump(value)
            if value["status"] not in ("WATCH_READY", "RETRY"): record_usage(state(config_path, explicit), "watch", value, int((time.monotonic() - started) * 1000), "watch")
        previous_retry = retry
        if value["status"] not in ("WATCH_READY", "NO_RELEVANT_CHANGE", "RETRY"): count += 1
        time.sleep(max(50, poll_ms) / 1000)
    return 0

def mcp_tools() -> list[dict[str, Any]]:
    empty = {"type": "object", "properties": {}}
    status_input = {"type": "object", "properties": {"diagnose": {"type": "boolean", "default": False}}}
    refresh_input = {"type": "object", "properties": {"reset_candidate_baseline": {"type": "boolean", "default": False}}}
    candidates_input = {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 0, "default": CANDIDATE_OUTPUT_LIMIT}}}
    ident = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    search_input = {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 0, "default": 3}}, "required": ["query"]}
    history_input = {"type": "object", "properties": {"context_hash": {"type": "string"}, "limit": {"type": "integer", "minimum": 0, "default": 10}}}
    usage_input = {"type": "object", "properties": {"operation": {"type": "string"}, "limit": {"type": "integer", "minimum": 0, "default": 10}}}
    updates_input = {"type": "object", "properties": {"since": {"type": "string"}}}
    accept_input = {"type": "object", "properties": {"id": {"type": "string"}, "bindings": {"type": "array", "minItems": 1, "items": {"type": "string"}}}, "required": ["id", "bindings"]}
    reject_input = {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"enum": ["not_architecture", "existing_canonical", "test_fixture", "false_match"]}}, "required": ["id", "reason"]}
    return [
        {"name": "architecture_status", "description": "Freshness and last-known-good metadata; optional diagnose reports local input paths and core tool identity, without writes.", "inputSchema": status_input},
        {"name": "architecture_refresh", "description": "Validate and atomically promote context only when no candidate is pending; reset_candidate_baseline is an explicit audited migration.", "inputSchema": refresh_input},
        {"name": "architecture_snapshot", "description": "Compact last-known-good context.", "inputSchema": empty},
        {"name": "architecture_history", "description": "Bounded source-evidence snapshot history; pass context_hash only for one historical context.", "inputSchema": history_input},
        {"name": "architecture_usage", "description": "Bounded local receipts of meaningful architecture operations, not session logs.", "inputSchema": usage_input},
        {"name": "architecture_updates", "description": "Read-only bounded changes since a caller-held cursor; no refresh, renderer or delivery state writes.", "inputSchema": updates_input},
        {"name": "architecture_candidates", "description": "Pending deterministic high-value source candidates; not canonical facts. Default is compact; limit 0 returns the bounded full set.", "inputSchema": candidates_input},
        {"name": "architecture_accept_candidate", "description": "Promote a manually updated canonical config after one candidate is bound to changed source-evidence-backed architecture.", "inputSchema": accept_input},
        {"name": "architecture_reject_candidate", "description": "Record one bounded fixed-code rejection and advance the verified candidate baseline.", "inputSchema": reject_input},
        {"name": "architecture_canonical", "description": "Canonical component and evidence.", "inputSchema": ident},
        {"name": "architecture_search", "description": "Match the current task to compact canonical components; defaults to three results and reports omissions.", "inputSchema": search_input},
        {"name": "architecture_understand", "description": "Explicit bounded source understanding after setup: run recoverable mechanical stages, then return a compact semantic task for the already-authorized Agent. Never invokes a model. show is strictly read-only and reuses retained findings.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "resume": {"type": "string"}, "show": {"type": "boolean"}, "details": {"type": "boolean"}}}},
        {"name": "architecture_evidence", "description": "Source evidence for one component.", "inputSchema": ident},
        {"name": "architecture_trace", "description": "Authored relations; optional code graph stays separate.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "direction": {"enum": ["upstream", "downstream"]}, "include_code_edges": {"type": "boolean"}}, "required": ["id"]}},
        {"name": "architecture_impact", "description": "Shared declared change_scope: direct components, dependencies, dependents, typed relation evidence; accepted and unaccepted working definitions stay separate. Not runtime impact. details expands omissions; legacy reachable_components is all-kind outgoing reach.", "inputSchema": {"type": "object", "properties": {"base": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "details": {"type": "boolean"}}}},
        {"name": "architecture_changed_since", "description": "Retained architecture delta by revision.", "inputSchema": {"type": "object", "properties": {"revision": {"type": "string"}}, "required": ["revision"]}},
        {"name": "architecture_drift", "description": "Configured high-value historical Git-diff candidates only.", "inputSchema": {"type": "object", "properties": {"base": {"type": "string"}}, "required": ["base"]}},
        {"name": "architecture_stale", "description": "Alias for freshness status, including optional read-only diagnose.", "inputSchema": status_input},
    ]
def mcp_value(config_path: Path, explicit: str | None, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in ("architecture_status", "architecture_stale"):
        diagnose = args.get("diagnose", False)
        if not isinstance(diagnose, bool): raise ValueError("diagnose must be boolean")
        return diagnose_status(config_path, explicit) if diagnose else status(config_path, explicit)
    if name == "architecture_refresh":
        reset = args.get("reset_candidate_baseline", False)
        if not isinstance(reset, bool): raise ValueError("reset_candidate_baseline must be boolean")
        return refresh(config_path, explicit, reset_candidate_baseline=reset)
    if name == "architecture_snapshot": return snapshot(config_path, explicit)
    if name == "architecture_history": return history(state(config_path, explicit), args.get("context_hash"), int(args.get("limit", 10)))
    if name == "architecture_usage": return usage(state(config_path, explicit), args.get("operation"), int(args.get("limit", 10)))
    if name == "architecture_updates": return updates(config_path, explicit, args.get("since"))
    if name == "architecture_candidates":
        limit = args.get("limit", CANDIDATE_OUTPUT_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool): raise ValueError("candidate limit must be an integer")
        return candidates(config_path, explicit, limit)
    if name == "architecture_accept_candidate":
        bindings = args.get("bindings", [])
        if not isinstance(bindings, list) or not all(isinstance(value, str) for value in bindings): raise ValueError("bindings must be a string array")
        return accept_candidate(config_path, explicit, str(args.get("id", "")), bindings)
    if name == "architecture_reject_candidate": return reject_candidate(config_path, explicit, str(args.get("id", "")), str(args.get("reason", "")))
    if name == "architecture_canonical": return canonical(config_path, explicit, str(args.get("id", "")))
    if name == "architecture_search": return search(config_path, explicit, str(args.get("query", "")), int(args.get("limit", 3)))
    if name == "architecture_understand": return understand(config_path, explicit, args)
    if name == "architecture_evidence":
        value = canonical(config_path, explicit, str(args.get("id", ""))); return {k: value[k] for k in value if k != "canonical"} | {"evidence": value.get("canonical", {}).get("evidence", [])}
    if name == "architecture_trace": return trace(config_path, explicit, str(args.get("id", "")), str(args.get("direction", "downstream")), bool(args.get("include_code_edges")))
    if name == "architecture_impact": return impact(config_path, explicit, args.get("base"), args.get("files"), args.get("details", False))
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
                if not isinstance(arguments, dict): raise ValueError("tool arguments must be an object")
                started = time.monotonic(); value = mcp_value(config_path, explicit, name, arguments); elapsed = int((time.monotonic() - started) * 1000)
                if name not in ("architecture_status", "architecture_stale", "architecture_history", "architecture_usage", "architecture_updates", "architecture_understand"): telemetry(state(config_path, explicit), f"mcp:{name}", value, elapsed)
                record_usage(state(config_path, explicit), name, value, elapsed, "mcp", arguments.get("id") if isinstance(arguments, dict) else None)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}], "isError": value.get("status") in ("ERROR", "INVALID")}
            elif "id" not in request: continue
            else: raise ValueError("method not found")
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, OSError) as e:
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": str(e)}}, ensure_ascii=False), flush=True)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", help="defaults to .archctx/architecture.json in the current repository"); parser.add_argument("--state-dir"); sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "mcp", "telemetry"): sub.add_parser(name)
    x = sub.add_parser("setup", help="explicitly prepare compatible project-local analysis/render components and Codex guidance; no model or refresh")
    x.add_argument("--analysis-home", help="reuse an existing compatible source checkout without modifying it"); x.add_argument("--renderer-home", help="reuse an existing compatible renderer checkout without modifying it"); x.add_argument("--command", dest="cli_command", default="archctx")
    x = sub.add_parser("understand", help="bounded source understanding; mechanical work is recoverable, semantics stay with the authorized Agent")
    x.add_argument("question", nargs="?"); x.add_argument("--files", nargs="+"); x.add_argument("--resume"); x.add_argument("--show", action="store_true", help="read-only retained discoveries; no analysis"); x.add_argument("--details", action="store_true")
    x = sub.add_parser("map", help="open the shared map and observe saved development changes")
    x.add_argument("--port", type=int, default=0); x.add_argument("--no-open", action="store_true"); x.add_argument("--read-only", action="store_true", help="disable automatic maintenance for this viewer")
    x = sub.add_parser("status"); x.add_argument("--diagnose", action="store_true", help="read-only core identity and selected config/state paths; works even without a config")
    x = sub.add_parser("refresh"); x.add_argument("--reset-candidate-baseline", action="store_true")
    x = sub.add_parser("candidates"); x.add_argument("--limit", type=int, default=CANDIDATE_OUTPUT_LIMIT)
    x = sub.add_parser("history"); x.add_argument("--context-hash"); x.add_argument("--limit", type=int, default=10)
    x = sub.add_parser("usage"); x.add_argument("--operation"); x.add_argument("--limit", type=int, default=10); x.add_argument("--import-legacy", action="store_true")
    x = sub.add_parser("updates"); x.add_argument("--since")
    x = sub.add_parser("canonical"); x.add_argument("id", nargs="?"); x.add_argument("--component"); x = sub.add_parser("search"); x.add_argument("query", nargs="?"); x.add_argument("--query", dest="query_flag"); x.add_argument("--limit", type=int, default=3); x = sub.add_parser("evidence"); x.add_argument("id", nargs="?"); x.add_argument("--component")
    x = sub.add_parser("trace"); x.add_argument("id", nargs="?"); x.add_argument("--from", dest="source"); x.add_argument("--to", dest="target"); x.add_argument("--direction", choices=("upstream", "downstream"), default="downstream"); x.add_argument("--code", action="store_true")
    x = sub.add_parser("impact"); x.add_argument("--base"); x.add_argument("--files", nargs="*"); x.add_argument("--details", action="store_true", help="expand declared scope/evidence omissions; not runtime impact")
    x = sub.add_parser("changed-since"); x.add_argument("--revision", required=True); x = sub.add_parser("delta"); x.add_argument("--revision", required=True); x = sub.add_parser("drift"); x.add_argument("--base", required=True)
    x = sub.add_parser("accept"); x.add_argument("id"); x.add_argument("--bind", action="append", required=True)
    x = sub.add_parser("reject"); x.add_argument("id"); x.add_argument("--reason", required=True)
    x = sub.add_parser("watch"); x.add_argument("--once", action="store_true"); x.add_argument("--apply", action="store_true", help="promote only already-declared context after source, graph, gate, and optional Archify validation pass"); x.add_argument("--poll-ms", type=int, default=500); x.add_argument("--max-events", type=int)
    x = sub.add_parser("install-codex"); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--check", action="store_true"); x.add_argument("--command", dest="cli_command", default="archctx", help="CLI prefix written to AGENTS.md only; not executed")
    x = sub.add_parser("uninstall-codex"); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--check", action="store_true")
    x = sub.add_parser("init"); x.add_argument("--repo", default="."); x.add_argument("--target", default="AGENTS.md"); x.add_argument("--component"); x.add_argument("--truth-source", action="append", default=[]); x.add_argument("--evidence", action="append", default=[]); x.add_argument("--check", action="store_true"); x.add_argument("--command", dest="cli_command", default="archctx", help="CLI prefix written to AGENTS.md only; not executed")
    args = parser.parse_args()
    if args.command == "search" and bool(args.query) == bool(args.query_flag):
        parser.error("search requires exactly one query (positional or --query)")
    if args.command in ("canonical", "evidence") and bool(args.id) == bool(args.component):
        parser.error(f"{args.command} requires exactly one component (positional or --component)")
    if args.command == "trace" and bool(args.id) == bool(args.source):
        parser.error("trace requires exactly one origin (positional or --from)")
    try:
        if args.command == "init":
            repo = Path(args.repo).resolve(); target = Path(args.target); target = target if target.is_absolute() else repo / target
            dump(init(repo, target, args.component, args.truth_source, args.evidence, args.check, selected_config(repo, args.config), args.state_dir, args.cli_command)); return 0
        config_path = native_config(Path.cwd(), args.config) if args.command in ("setup", "understand", "map") else selected_config(Path.cwd(), args.config)
        if args.command == "setup":
            from archctx_runtime import setup
            from archctx import RefreshBusyError as ComponentBusy
            try:
                result = setup(config_path, args.state_dir, args.analysis_home, args.renderer_home)
            except ComponentBusy:
                result = {"status": "RETRY", "next_action": "another setup or publication owns this project state; retry after it finishes"}
            if result.get("status") == "READY":
                repo = repo_for(config_path, load(config_path)); target = repo / "AGENTS.md"
                result["codex"] = install_codex(config_path, target, False, args.cli_command, args.state_dir, allow_empty=True)
            dump(result); return 0 if result.get("status") == "READY" else 2
        if args.command == "status" and args.diagnose:
            dump(diagnose_status(config_path, args.state_dir)); return 0
        if not config_path.is_file(): raise ValueError("--config is required except for init (or run from a repository with .archctx/architecture.json)")
        if args.command == "understand":
            value = understand(config_path, args.state_dir, {key: getattr(args, key) for key in ("question", "files", "resume", "show", "details")})
            dump(value); return 2 if value.get("status") in ("ERROR", "INVALID") else 0
        if args.command == "map":
            from archctx_blueprint import main as map_main
            options = ["--config", str(config_path), "--port", str(args.port)]
            if args.state_dir: options.extend(["--state-dir", args.state_dir])
            if args.no_open: options.append("--no-open")
            if not args.read_only: options.append("--live")
            return map_main(options)
        if args.command == "mcp": return serve_mcp(config_path, args.state_dir)
        if args.command == "telemetry": dump(telemetry_summary(state(config_path, args.state_dir))); return 0
        if args.command == "history": dump(history(state(config_path, args.state_dir), args.context_hash, args.limit)); return 0
        if args.command == "usage": dump(import_legacy_usage(state(config_path, args.state_dir)) if args.import_legacy else usage(state(config_path, args.state_dir), args.operation, args.limit)); return 0
        if args.command == "updates": dump(updates(config_path, args.state_dir, args.since)); return 0
        if args.command == "watch":
            if args.once:
                started = time.monotonic(); value = watch_once(config_path, args.state_dir, args.apply); elapsed = int((time.monotonic() - started) * 1000)
                if value["status"] not in ("NO_RELEVANT_CHANGE", "WATCH_READY"): telemetry(state(config_path, args.state_dir), "watch", value, elapsed); record_usage(state(config_path, args.state_dir), "watch", value, elapsed, "watch")
                dump(value); return 0
            return watch(config_path, args.state_dir, args.poll_ms, args.max_events, args.apply)
        target = Path(args.target) if args.command in ("install-codex", "uninstall-codex") else None
        install_target = target if target and target.is_absolute() else (repo_for(config_path, load(config_path)) / target) if target else None
        if args.command == "install-codex":
            dump(install_codex(config_path, install_target.resolve(), args.check, args.cli_command, args.state_dir)); return 0
        actions = {"status": lambda: status(config_path, args.state_dir), "refresh": lambda: refresh(config_path, args.state_dir, reset_candidate_baseline=args.reset_candidate_baseline), "snapshot": lambda: snapshot(config_path, args.state_dir), "candidates": lambda: candidates(config_path, args.state_dir, args.limit), "accept": lambda: accept_candidate(config_path, args.state_dir, args.id, args.bind), "reject": lambda: reject_candidate(config_path, args.state_dir, args.id, args.reason), "canonical": lambda: canonical(config_path, args.state_dir, args.component or args.id), "search": lambda: search(config_path, args.state_dir, args.query_flag or args.query, args.limit), "evidence": lambda: mcp_value(config_path, args.state_dir, "architecture_evidence", {"id": args.component or args.id}), "trace": lambda: trace(config_path, args.state_dir, args.source or args.id, args.direction, args.code, args.target), "impact": lambda: impact(config_path, args.state_dir, args.base, args.files, args.details), "changed-since": lambda: changed_since(config_path, args.state_dir, args.revision), "delta": lambda: delta(config_path, args.state_dir, args.revision), "drift": lambda: drift(config_path, args.base), "uninstall-codex": lambda: uninstall_codex(install_target.resolve(), args.check)}
        started = time.monotonic(); value = actions[args.command]()
        elapsed = int((time.monotonic() - started) * 1000)
        if args.command not in ("status", "install-codex", "uninstall-codex"): telemetry(state(config_path, args.state_dir), args.command, value, elapsed)
        record_usage(state(config_path, args.state_dir), args.command, value, elapsed, subject_id=args.id if args.command in ("canonical", "evidence", "trace", "accept", "reject") else None)
        dump(value); return 0
    except (ValueError, OSError, subprocess.SubprocessError) as e: dump({"protocol_version": PROTOCOL_VERSION, "status": "ERROR", "error": str(e)}); return 2
if __name__ == "__main__": raise SystemExit(main())
