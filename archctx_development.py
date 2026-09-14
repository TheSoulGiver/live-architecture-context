"""Bounded worktree observations for the existing local blueprint viewer.

No event ledger: Git owns dirty files, LKG owns accepted architecture, and the
existing watcher/refresh transaction owns publication. This module caches reads.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import archctx
import archctx_understand as understand

CHANGE_LIMIT, PAYLOAD_BYTES, GIT_BYTES = 128, 64 * 1024, 256 * 1024
CORE_SOURCE_PATH = Path(__file__).resolve()
try:
    CORE_SOURCE_SHA256 = hashlib.sha256(CORE_SOURCE_PATH.read_bytes()).hexdigest()
except OSError:
    CORE_SOURCE_SHA256 = None


def stamp(path: Path):
    try:
        value = path.stat()
        return value.st_mtime_ns, value.st_ctime_ns, value.st_size, value.st_ino
    except OSError:
        return None


def git_changes(repo: Path) -> tuple[list[dict[str, str]], str | None]:
    """Read NUL-delimited names only; never follow the shared Git common root."""
    try:
        command = ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--renames"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            timer = threading.Timer(3, process.kill)
            timer.start()
            try:
                raw = process.stdout.read(GIT_BYTES + 1)
                if len(raw) > GIT_BYTES:
                    process.kill()
                code = process.wait()
            finally:
                timer.cancel()
        if len(raw) > GIT_BYTES:
            return [], "Git name observation exceeded 256 KiB; dirty-file coverage is unknown"
        if code:
            return [], "Git name observation unavailable or timed out; dirty-file coverage is unknown"
    except OSError:
        return [], "Git is unavailable; dirty-file coverage is unknown"
    items, records, index = raw.decode("utf-8", errors="replace").split("\0"), [], 0
    while index < len(items) and items[index]:
        entry = items[index]
        index += 1
        status, path = entry[:2], entry[3:]
        value = {"path": path, "kind": "delete" if "D" in status else "add" if status == "??" or "A" in status else "modify"}
        if "R" in status or "C" in status:
            if index >= len(items):
                return [], "Incomplete Git rename observation; dirty-file coverage is unknown"
            value.update(kind="rename" if "R" in status else "add", old_path=items[index])
            index += 1
        records.append(value)
    return records, None


def evidence_hashes(record: dict[str, Any]) -> dict[str, str]:
    context = record.get("context", {})
    return {item["path"]: item["sha256"] for owner in context.get("components", []) + context.get("relations", [])
            for item in owner.get("evidence", []) if "sha256" in item}


class DevelopmentObserver:
    def __init__(self, config_path: Path, explicit: str | None = None, *, live: bool = False,
                 poll_seconds: float = .75, settle_seconds: float = .6):
        self.config_path = Path(config_path).resolve()
        self.directory = archctx.state(self.config_path, explicit).resolve()
        self.explicit, self.live = str(self.directory), live
        self.poll_seconds, self.settle_seconds = max(.1, poll_seconds), max(0, settle_seconds)
        self._record = archctx.load(archctx.last_path(self.directory)) if archctx.last_path(self.directory).exists() else {}
        try:
            self._config = archctx.load(self.config_path)
            self.repo = archctx.repo_for(self.config_path, self._config)
            if self._record and Path(self._record["repo"]).resolve() != self.repo:
                self._record = {}  # Never establish a foreign LKG as this viewer's accepted map.
        except ValueError:
            if not self._record:
                raise
            self.repo = Path(self._record["repo"]).resolve()
            self._config = {"version": archctx.CONFIG_VERSION, "repo": str(self.repo), **self._record["context"]}
        self._git_root = archctx.git(self.repo, "rev-parse", "--show-toplevel")
        self._paths: set[str] = set(evidence_hashes(self._record))
        try:
            self._paths.add(self.config_path.relative_to(self.repo).as_posix())
        except ValueError:
            pass
        initial_view = self._config.get("archify", {}).get("view")
        if isinstance(initial_view, str):
            self._paths.add(initial_view)
        self._signature = self._attempted = self._cursor = None
        self._retry_at = self._retry_delay = 0.
        self._input_signature = None
        self._external_stamps, self._external = None, {}
        self._analysis_stamp, self._analysis_paths, self._analysis_metadata = None, set(), {}
        self._analysis_receipts: list[Path] = []
        self._updates: dict[str, Any] = {}
        self._settled_at = time.monotonic()
        self._pending_controls = False
        self._refresh_paths: list[str] = []
        self._refresh: dict[str, Any] = {"status": "NOT_REQUESTED", "reasons": []}
        self._payload: dict[str, Any] = {}
        self._metrics = {"polls": 0, "observations": 0, "full_reads": 0, "refreshes": 0}
        self._lock, self._poll_lock = threading.Lock(), threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def accepted_record(self):
        return self.bundle()[1]

    def bundle(self):
        if not self._payload:
            self.poll_once()
        with self._lock:
            payload = copy.deepcopy(self._payload)
            payload["observer_running"] = bool(self._thread and self._thread.is_alive())
            return payload, copy.deepcopy(self._record)

    def read(self):
        return self.bundle()[0]

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self.poll_once()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="archctx-development", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        delay = self.poll_seconds
        signature = self._signature, self._input_signature
        try:
            while True:
                wait_seconds = delay
                if self.live and self._pending_controls and (self._attempted != self._input_signature or self._retry_at):
                    deadline = self._retry_at or self._settled_at + self.settle_seconds
                    remaining = deadline - time.monotonic()
                    # Failed observations may leave an overdue deadline; do not spin.
                    wait_seconds = min(delay, remaining if remaining > 0 else self.poll_seconds)
                if self._stop.wait(wait_seconds):
                    break
                self.poll_once()
                current = self._signature, self._input_signature
                # ponytail: idle discovery can take 3s; add file notifications only if that latency is insufficient.
                delay = min(max(3., self.poll_seconds), delay * 2) if current == signature else self.poll_seconds
                signature = current
        except Exception as error:
            self._failure(error)  # Keep the old map readable, but never hide a stopped observer.

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()

    def _quick_inputs(self):
        """Stat bounded known/glob names, not source bytes, on an idle poll."""
        paths = set(self._paths)
        watch = self._config.get("watch", {})
        ignore = watch.get("ignore", [])
        patterns = list(watch.get("paths", []))
        patterns += [pattern for rule in archctx.normalized_drift_rules(self._config) for pattern in rule["paths"]]
        for pattern in patterns:
            for path in self.repo.glob(archctx.relative_glob(pattern, "watch.paths")):
                if not path.is_file():
                    continue
                relative = path.resolve().relative_to(self.repo).as_posix()
                if not archctx.ignored(relative, ignore):
                    paths.add(relative)
                if len(paths) > archctx.WATCH_FILE_LIMIT:
                    raise ValueError("development observation exceeds the configured 512-file scope")
        source = {}
        for relative in sorted(paths):
            path = (self.repo / relative).resolve()
            path.relative_to(self.repo)
            source[relative] = stamp(path)
        self._analysis_metadata.pop("path_error", None)
        for relative in self._analysis_paths - paths:
            try:
                path, normalized = archctx.repo_file(self.repo, relative, "analysis source")
                segments = {p.casefold() for p in Path(relative).parts + path.relative_to(self.repo).parts}
                if normalized != relative or segments.intersection({".git", ".archctx", ".ua"}) or path.is_relative_to(self.directory):
                    raise ValueError("analysis source moved into local state")
                source[relative] = stamp(path)
            except (OSError, ValueError) as error:
                # Optional source aliases can change without a receipt write.
                # Keep checking the name for recovery; never taint canonical paths.
                self._analysis_metadata["path_error"] = str(error)[:500]
        controls = {str(self.config_path): stamp(self.config_path)}
        settings = self._config.get("archify", {})
        if isinstance(settings, dict) and isinstance(settings.get("view"), str):
            view = (self.repo / settings["view"]).resolve()
            view.relative_to(self.repo)
            controls[str(view)] = stamp(view)
        return source, controls, stamp(archctx.last_path(self.directory))

    def _load(self):
        if self.config_path.resolve() != self.config_path or self.repo.resolve() != self.repo:
            raise ValueError("selected repository/config identity changed; restart the viewer")
        config = archctx.load(self.config_path)
        if archctx.repo_for(self.config_path, config) != self.repo:
            raise ValueError("selected repository changed; restart the viewer")
        record = archctx.load(archctx.last_path(self.directory)) if archctx.last_path(self.directory).exists() else {}
        if archctx.last_path(self.directory).exists() or not understand.empty_draft(config):
            archctx.components(config)
        if record and Path(record["repo"]).resolve() != self.repo:
            raise ValueError("accepted context belongs to another repository; restart with matching state")
        return config, record

    def _external_state(self):
        def analysis_stamps():
            return (stamp(understand.catalog_path(self.directory)), stamp(understand.current_path(self.directory)),
                    tuple((str(path), stamp(path)) for path in self._analysis_receipts))
        analysis_stamp = analysis_stamps()
        if analysis_stamp != self._analysis_stamp:
            self._analysis_paths = set()
            self._analysis_metadata = {"stamp": analysis_stamp}
            try:
                if any(item is not None for item in analysis_stamp[:2]):
                    manifest = understand.source_manifest(self.directory, self.repo)
                    for relative in manifest["paths"]:
                        path, normalized = archctx.repo_file(self.repo, relative, "analysis source")
                        if normalized != relative or {p.casefold() for p in Path(relative).parts}.intersection({".git", ".archctx"}) or path.is_relative_to(self.directory):
                            raise ValueError("analysis scope contains a non-product path")
                    self._analysis_paths = set(manifest["paths"])
                    self._analysis_metadata.update({key: manifest[key] for key in ("analysis_ids", "omitted_files", "errors", "omitted_errors")})
                    if manifest["errors"]:
                        self._analysis_metadata["error"] = "; ".join(item["reason"] for item in manifest["errors"])[:500]
                    self._analysis_receipts = [Path(path) for path in manifest["receipt_paths"]]
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._analysis_metadata["error"] = str(error)[:500]  # Optional analysis never invalidates the accepted map.
            self._analysis_stamp = analysis_stamps()
            self._analysis_metadata["stamp"] = self._analysis_stamp
        decision_path, usage_path = archctx.candidate_decision_path(self.directory), archctx.usage_path(self.directory)
        stamps = stamp(decision_path), stamp(usage_path)
        if stamps != self._external_stamps:
            for path, limit in ((decision_path, archctx.DECISION_BYTES), (usage_path, archctx.USAGE_BYTES)):
                if path.exists() and path.stat().st_size > 2 * limit:
                    raise ValueError("existing local decision/usage receipt exceeds its bounded read limit")
            decisions = [{key: item[key] for key in ("id", "base_context_hash", "decision", "state") if key in item}
                         for item in archctx.decision_store(self.directory)[-archctx.DECISION_LIMIT:] if item.get("state") == "pending"]
            latest = next((item for item in reversed(archctx.usage_store(self.directory)["records"][-archctx.USAGE_LIMIT:])
                           if item.get("operation") in ("accept", "reject", "refresh")), {})
            operation = {key: latest[key] for key in ("at", "operation", "context_hash") if key in latest}
            operation["status"] = latest.get("result", {}).get("status")
            self._external = {"decisions": decisions, "operation": operation}
            self._external_stamps = stamps
        return {**self._external, "analysis_receipt": self._analysis_metadata}

    def _observe(self, dirty, git_error, external):
        config, record = self._load()
        draft = not record and understand.empty_draft(config)
        current = {} if draft else archctx.manifest(self.config_path, self.repo, config)
        self._metrics["full_reads"] += not draft
        packet = archctx.updates(self.config_path, self.explicit, self._cursor)
        if packet.get("status") == "RETRY":
            raise ValueError("architecture inputs moved during observation; waiting for a settled read")
        if packet.get("changed") is False:
            packet = {**self._updates, **packet}  # A cursor acknowledgement is not an empty architecture observation.
        # A publication between updates and this read must not pair two authorities.
        after = archctx.load(archctx.last_path(self.directory)) if archctx.last_path(self.directory).exists() else {}
        if record != after or archctx.semantic(archctx.load(self.config_path)) != archctx.semantic(config):
            raise ValueError("accepted context/config changed during observation; waiting for a settled read")
        if draft:
            # No canonical scope exists yet. Keep the same bounded receipt/source
            # observation and cursor, without entering watch/refresh/publication.
            self._config = config
            self._pending_controls, self._refresh_paths = False, []
            self._cursor, self._updates = packet.get("cursor"), copy.deepcopy(packet)
            return {}, {"kind": "development_observation", "status": "MISSING", "stale": True,
                        "accepted_context_hash": None, "worktree": str(self.repo),
                        "changes": [], "pending": {}, "updates": packet,
                        "counts": {"changed": 0, "unmapped": 0, "omitted": 0},
                        "limitations": ["Architecture ownership is not declared yet; inspect source analysis independently."] + ([git_error] if git_error else []),
                        "watched_files": 0, "analysis_stat_files": len(self._analysis_paths), "live": self.live,
                        "analysis_omitted_files": self._analysis_metadata.get("omitted_files", 0),
                        "auto_publish": "waiting for source-grounded shared definitions"}
        self._config, self._paths = config, set(current) | set(evidence_hashes(record))
        before = evidence_hashes(record)
        changed_evidence = set()
        for path, expected in before.items():
            if path not in current or current[path] is None:
                changed_evidence.add(path)
            else:
                # Evidence hashes normalize text newlines; raw Git/manifest hashes do not.
                if archctx.sha((self.repo / path).read_text(encoding="utf-8", errors="replace").encode()) != expected:
                    changed_evidence.add(path)
        observed = {item["path"]: dict(item, dirty_git=True) for item in dirty}
        analysis = packet.get("source_analysis", {})
        changed_analysis = {path for scope in [analysis, *analysis.get("scopes", [])]
                            for path in scope.get("changed_files", [])} & self._analysis_paths
        for path in changed_analysis:
            observed.setdefault(path, {"path": path, "kind": "delete" if stamp(self.repo / path) is None else "modify", "dirty_git": False})
        rename_origins = {item["old_path"] for item in dirty if item.get("kind") == "rename"}
        for path in changed_evidence:
            if path in rename_origins:
                continue
            observed.setdefault(path, {"path": path, "kind": "delete" if current.get(path) is None else "modify", "dirty_git": False})
        old_context = record.get("context", {"components": [], "relations": []})
        accepted_config = {"version": archctx.CONFIG_VERSION, **old_context}
        changes = []
        for path, item in sorted(observed.items()):
            try:
                (self.repo / path).resolve().relative_to(self.directory)
                continue  # Rebuildable state is never development activity.
            except ValueError:
                pass
            paths = [path] + ([item["old_path"]] if item.get("old_path") else [])
            direct = set(archctx.owners(config, paths))
            if old_context["components"]:
                direct.update(archctx.owners(accepted_config, paths))
            impacted = set().union(*(set(archctx.authored(old_context, owner, "upstream")) for owner in direct)) if direct else set()
            changes.append({**item, "components": sorted(direct), "impacted_components": sorted(impacted - direct),
                            "sha256": current.get(path), "observation": "manifested_content" if path in current else "metadata_only",
                            "analysis_changed": bool(changed_analysis.intersection(paths)),
                            "evidence_changed": bool(changed_evidence.intersection(paths)), "covered": bool(direct)})
        changes.sort(key=lambda item: (not item["covered"], item["path"]))
        for item in changes[:CHANGE_LIMIT]:
            item["change_scope"] = archctx.change_scope(record, config, [item["path"]] + ([item["old_path"]] if item.get("old_path") else []),
                                                       str(self.config_path.relative_to(self.repo)).replace("\\", "/") if self.config_path.is_relative_to(self.repo) else None,
                                                       packet.get("freshness", "unknown"))
        self._refresh_paths = sorted({path for item in changes if item["covered"] for path in (item["path"], item.get("old_path")) if path})
        working = archctx.context(config, record.get("revision", "unknown"), {c["id"]: c["evidence"] for c in config["components"]},
                                  [r.get("evidence", []) for r in config.get("relations", [])])
        diff = archctx.record_diff({"context": old_context}, {"context": working})
        old_ids, new_ids = {c["id"] for c in old_context["components"]}, {c["id"] for c in working["components"]}
        pending = {key: diff[key] for key in ("changed_components", "added_relations", "removed_relations", "changed_relations")}
        pending.update(added_components=sorted(new_ids - old_ids), removed_components=sorted(old_ids - new_ids),
                       coverage_changed=old_context.get("coverage") != working.get("coverage"))
        for key in ("added_relations", "removed_relations"):
            pending[key] = [{k: r[k] for k in ("id", "from", "to", "kind") if k in r} for r in pending[key]]
        settings = archctx.archify_config(config, self.repo)
        view_changed = bool(settings and archctx.sha(settings["view"].read_bytes()) != record.get("archify", {}).get("view_sha256"))
        self._pending_controls = not record or archctx.semantic(config) != record.get("config_hash") or view_changed
        # A save between manifest, updates, and evidence reads must not mix generations.
        final_manifest = archctx.manifest(self.config_path, self.repo, config)
        self._metrics["full_reads"] += 1
        if current != final_manifest or archctx.semantic(archctx.load(self.config_path)) != archctx.semantic(config):
            raise ValueError("source/config changed during observation; waiting for a settled read")
        if record != (archctx.load(archctx.last_path(self.directory)) if archctx.last_path(self.directory).exists() else {}):
            raise ValueError("accepted context changed during observation; waiting for a settled read")
        self._cursor = packet.get("cursor", self._cursor)
        self._updates = copy.deepcopy(packet)
        decisions = {item["id"]: item for item in external.get("decisions", []) if item.get("base_context_hash") == record.get("context_hash")}
        for candidate in packet.get("candidates", []):
            if decision := decisions.get(candidate.get("id")):
                candidate.update(review_decision=decision.get("decision"), review_state="pending_publication")
        if packet.get("candidates") and all(item.get("review_state") for item in packet["candidates"]) and not packet.get("omitted_candidate_count"):
            packet["reason"] = ["candidate decisions recorded; successful publication is still pending"]
        operation = external.get("operation", {})
        if operation.get("at", "") > self._refresh.get("at", ""):
            self._refresh = {"status": operation.get("status"), "at": operation["at"], "operation": operation.get("operation"),
                             "source": "existing_bounded_usage", "last_good_preserved": bool(record),
                             "reasons": ["External operation reported " + str(operation.get("status")) + "; detailed failure reason was not retained"] if operation.get("status") not in ("PASS", "FRESH") else []}
        return record, {"kind": "development_observation", "status": packet.get("status"), "stale": packet.get("freshness") != "fresh",
                        "accepted_context_hash": record.get("context_hash"),
                        "worktree": str(self.repo), "worktree_revision": archctx.revision(self.repo), "changes": changes, "pending": pending, "updates": packet,
                        "counts": {"changed": len(changes), "unmapped": sum(not c["covered"] for c in changes), "omitted": 0},
                        "limitations": [git_error] if git_error else [], "watched_files": len(current),
                        "analysis_stat_files": len(self._analysis_paths),
                        "analysis_omitted_files": self._analysis_metadata.get("omitted_files", 0),
                        "live": self.live, "metadata_stat_limit": CHANGE_LIMIT,
                        "legacy_semantics": {"components": "union of working/accepted evidence owners", "impacted_components": "all-kind incoming authored reach in accepted graph, minus legacy direct IDs; use change_scope instead"},
                        "omitted_dirty_metadata_stats": max(0, len(dirty) - CHANGE_LIMIT),
                        "auto_publish": "initial reconstruction, then settled shared config/view only" if self.live else "disabled; observation only"}

    def poll_once(self):
        with self._poll_lock:
            started = time.monotonic()
            self._metrics["polls"] += 1
            try:
                # Configuration identity is checked before Git/manifest can read another checkout.
                if stamp(self.config_path) != getattr(self, "_config_stamp", None):
                    if stamp(self.config_path) == getattr(self, "_invalid_config_stamp", False):
                        return self.read()
                    try:
                        self._load()
                    except (OSError, ValueError, KeyError, TypeError):
                        self._invalid_config_stamp = stamp(self.config_path)
                        raise
                    self._invalid_config_stamp = False
                external = self._external_state()
                source, controls, accepted = self._quick_inputs()
                dirty, git_error = git_changes(self.repo) if self._git_root and Path(self._git_root).resolve() == self.repo else ([], "Git root is not this selected repository; dirty-file coverage is unknown")
                for item in dirty[:CHANGE_LIMIT]:
                    if item["path"] in self._analysis_paths and item["path"] not in source:
                        continue  # Already isolated as an invalid optional source alias.
                    path = (self.repo / item["path"]).resolve()
                    path.relative_to(self.repo)
                    source.setdefault(item["path"], stamp(path))
                input_signature = archctx.semantic({"source": source, "controls": controls, "accepted": accepted, "dirty": dirty, "git_error": git_error})
                signature = archctx.semantic({"inputs": input_signature, "external": external})
                changed = signature != self._signature
                inputs_changed = input_signature != self._input_signature
                if inputs_changed:
                    self._retry_at = self._retry_delay = 0.
                retry_due = bool(self._retry_at and started >= self._retry_at)
                if changed or retry_due:
                    if inputs_changed:
                        self._settled_at = started
                    # A due retry must observe current inputs/LKG, never reuse an old predecessor.
                    record, payload = self._observe(dirty, git_error, external)
                    if self.live and inputs_changed and self._config.get("components"):
                        archctx.watch_once(self.config_path, self.explicit, apply=False)
                    self._signature, self._config_stamp = signature, stamp(self.config_path)
                    self._input_signature = input_signature
                    self._metrics["observations"] += 1
                else:
                    payload, record = self.bundle()
                if not self._pending_controls:
                    self._retry_at = self._retry_delay = 0.
                if self.live and self._pending_controls and started - self._settled_at >= self.settle_seconds and (self._attempted != input_signature or retry_due):
                    self._attempted = input_signature
                    self._retry_at = 0.  # Exceptions and deterministic failures stay suppressed.
                    self._metrics["refreshes"] += 1
                    self._refresh = {"status": "REFRESHING", "reasons": ["Validating settled shared config/view; accepted blueprint retained" if record else "Building first accepted blueprint from configured inputs"],
                                     "at": datetime.now(timezone.utc).isoformat(), "last_good_preserved": bool(record)}
                    with self._lock:
                        self._record = record
                        self._payload = {**payload, "refresh": dict(self._refresh), "metrics": dict(self._metrics),
                                         "observation_id": archctx.semantic({"input": signature, "refresh": self._refresh}),
                                         "observed_at": self._refresh["at"]}
                    result = archctx.refresh(self.config_path, self.explicit, expected_context_hash=record.get("context_hash"),
                                             changed=self._refresh_paths)
                    if result.get("status") == "RETRY":
                        self._retry_delay = min(30., max(1., self._retry_delay * 2))
                        self._retry_at = time.monotonic() + self._retry_delay
                    reasons = result.get("failures") or result.get("reason") or ([result["next_action"]] if result.get("status") != "PASS" and result.get("next_action") else [])
                    self._refresh = {"status": result.get("status"), "reasons": archctx.bounded_strings(reasons),
                                     "at": datetime.now(timezone.utc).isoformat(), "last_good_preserved": result.get("last_good_preserved", result.get("status") != "PASS")}
                    record, payload = self._observe(dirty, git_error, external)
                    self._signature = None  # Account for a successful publisher's new LKG on the next poll.
                payload["refresh"] = self._refresh
                payload["metrics"] = dict(self._metrics, last_scan_ms=round((time.monotonic() - started) * 1000, 2))
                identity = {key: payload.get(key) for key in ("accepted_context_hash", "changes", "pending", "updates", "refresh", "limitations")}
                identity["input_fingerprint"] = signature
                payload["observation_id"] = archctx.semantic(identity)
                if payload["observation_id"] != self._payload.get("observation_id"):
                    payload["observed_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    payload["observed_at"] = self._payload["observed_at"]
                self._bound(payload)
                with self._lock:
                    self._record, self._payload = record, payload
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._failure(error)
            return self.read()

    def _failure(self, error):
        with self._lock:
            analysis = self._payload.get("updates", {}).get("source_analysis")
            for change in self._payload.get("changes", []):
                if scope := change.get("change_scope"):
                    scope.update(freshness="stale", observation_state="retained_previous_observation", working_config_hash=None,
                                 working={"error": "Current inputs unavailable; previous observation retained"})
            if self._refresh.get("status") == "REFRESHING":
                self._refresh = {**self._refresh, "status": "INVALID", "reasons": [str(error)[:500]], "last_good_preserved": bool(self._record)}
            identity = archctx.semantic({"error": str(error), "accepted": self._record.get("context_hash")})
            self._payload = {**self._payload, "status": "INVALID", "stale": True, "live": self.live,
                             "accepted_context_hash": self._record.get("context_hash"), "observation_id": identity,
                             "observed_at": self._payload.get("observed_at") if identity == self._payload.get("observation_id") else datetime.now(timezone.utc).isoformat(),
                             "updates": {"status": "INVALID", "freshness": "stale", "cursor": self._cursor, "reason": [str(error)[:500]]},
                             "error": str(error)[:500], "refresh": self._refresh, "metrics": dict(self._metrics),
                             "last_good_preserved": bool(self._record)}
            if analysis:
                self._payload["updates"]["source_analysis"] = {**analysis, "status": "STALE", "retained_previous_observation": True,
                    "scopes": [{**scope, "status": "UNKNOWN"} for scope in analysis.get("scopes", [])],
                    "reason": "Current observation unavailable; retained analysis is not current source evidence"}
            self._bound(self._payload)

    @staticmethod
    def _bound(payload):
        changes = payload.get("changes", [])
        payload.setdefault("counts", {"omitted": 0})
        if len(changes) > CHANGE_LIMIT:
            payload["counts"]["omitted"] += len(changes) - CHANGE_LIMIT
            del changes[CHANGE_LIMIT:]
        while len(json.dumps(payload, ensure_ascii=False).encode()) > PAYLOAD_BYTES:
            if changes:
                changes.pop()
                payload["counts"]["omitted"] += 1
            else:
                payload["pending"] = {"unavailable": "pending definition exceeds compact observation limit; inspect shared config"}
                break
        if len(json.dumps(payload, ensure_ascii=False).encode()) > PAYLOAD_BYTES:
            preserved = {key: payload.get(key) for key in ("observation_id", "observed_at", "accepted_context_hash", "status", "stale", "live", "refresh", "metrics", "counts")}
            packet = payload.get("updates", {})
            payload.clear()
            payload.update(preserved, changes=[], pending={"unavailable": "observation exceeded 64 KiB; use focused architecture queries"},
                           updates={"status": packet.get("status"), "freshness": packet.get("freshness"), "cursor": packet.get("cursor")},
                           details_omitted=True)
            if analysis := packet.get("source_analysis"):
                payload["updates"]["source_analysis"] = {key: analysis.get(key) for key in ("configured", "status", "analysis_id", "candidate_count", "blocking")}
                payload["updates"]["source_analysis"].update(details_omitted=True, next_action="use the source analysis query for bounded details")
