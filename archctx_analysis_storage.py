"""Byte-bounded local analysis storage; call under the existing mechanical lock."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable

CAPACITY_BYTES = 256 * 1024 * 1024
RECEIPT_BYTES = 64 * 1024
GRAPH_BYTES = 8 * 1024 * 1024
KEEP_MARKERS = {".keep", "keep", "keep.json"}
MECHANICAL_TMP = re.compile(
    r"(?:scan|import-input|import-output|"
    r"ua-file-analyzer-input-\d+|ua-file-extract-results-\d+)\.json\Z"
)


def linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def inventory(root: Path) -> tuple[dict[Path, os.stat_result], set[Path]]:
    """Never follow a symlink or Windows junction, including the entry root."""
    files, links = {}, set()
    if not root.exists() and not root.is_symlink():
        return files, links
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if linked(info):
            links.add(path)
            files[path] = info
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
        elif stat.S_ISREG(info.st_mode):
            files[path] = info
    return files, links


def small_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(RECEIPT_BYTES + 1)
    if len(raw) > RECEIPT_BYTES:
        raise ValueError("analysis storage receipt exceeds 64 KiB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("analysis storage receipt must be an object")
    return value


def completed(run: Path, files: dict[Path, os.stat_result]) -> bool:
    """An old directory or finished merge alone does not prove a completed run."""
    if any(run / name not in files for name in ("input.json", "completed.json", "graph.json")):
        return False
    try:
        receipt, inputs = small_json(run / "completed.json"), small_json(run / "input.json")
        if (receipt.get("analysis_id") != run.name or inputs.get("analysis_id") != run.name
                or inputs.get("keep") or receipt.get("keep")
                or files[run / "graph.json"].st_size > GRAPH_BYTES):
            return False
        with (run / "graph.json").open("rb") as stream:
            raw = stream.read(GRAPH_BYTES + 1)
        return len(raw) <= GRAPH_BYTES and hashlib.sha256(raw).hexdigest() == receipt.get("graph_sha256")
    except (OSError, ValueError, TypeError):
        return False


def mechanical(path: Path, run: Path) -> bool:
    relative = path.relative_to(run).as_posix()
    if relative.startswith("source/.git/"):
        return True
    if path.parent == run / "source/.ua/tmp":
        return bool(MECHANICAL_TMP.fullmatch(path.name))
    # These are exported mechanical copies. Agent batch/system/layer/tour
    # results and unknown diagnostics remain intact, even after completion.
    return relative in {
        "source/.ua/knowledge-graph.json", "source/.ua/fingerprints.json",
        "source/.ua/meta.json", "source/.ua/intermediate/assembled-graph.json",
        "source/.ua/intermediate/fingerprint-input.json",
    }


class CapacityError(ValueError):
    def __init__(self, metrics: dict[str, Any]):
        self.metrics = metrics
        super().__init__(
            f"analysis storage capacity: used={metrics['used_bytes']} bytes, "
            f"reserve={metrics['reserve_bytes']} bytes, limit={metrics['capacity_bytes']} bytes, "
            f"reclaimable={metrics['reclaimable_bytes']} bytes, protected={metrics['protected_bytes']} bytes; "
            "current/referenced, active or resumable, legacy, keep-marked and original evidence are retained; "
            "no files removed. Reduce the requested scope or explicitly review retained storage before retrying."
        )


def ensure_capacity(directory: Path, reserve_bytes: int = 0,
                    protected_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Reserve bytes, reclaiming only completed, unreferenced mechanical files.

    The caller protects all catalog/review references and the run being written,
    and holds its existing mechanical lock. No scheduler, archive or run-count
    limit lives here. On insufficient reclaimable space nothing is deleted.
    """
    if not isinstance(reserve_bytes, int) or isinstance(reserve_bytes, bool) or reserve_bytes < 0:
        raise ValueError("analysis storage reservation must be non-negative bytes")
    base = directory.resolve() / "understand"
    files, links = inventory(base)
    if base in links or base / "runs" in links:
        raise ValueError("analysis storage root is linked; preserve it and use an isolated local state directory")
    used = sum(info.st_size for info in files.values())
    metrics = {"capacity_bytes": CAPACITY_BYTES, "used_bytes": used,
               "reserve_bytes": reserve_bytes, "reclaimed_bytes": 0, "reclaimed_files": 0}
    if used + reserve_bytes <= CAPACITY_BYTES:
        return metrics
    protected = set(protected_ids)
    current = base / "current.json"
    if current in files:
        if current in links:
            raise ValueError("current analysis receipt is linked; storage cleanup deferred")
        protected.add(small_json(current).get("analysis_id"))
    runs: dict[Path, dict[Path, os.stat_result]] = {}
    for path, info in files.items():
        relative = path.relative_to(base)
        if len(relative.parts) >= 3 and relative.parts[0] == "runs":
            runs.setdefault(base / "runs" / relative.parts[1], {})[path] = info
    candidates, protected_runs = [], []
    # ponytail: linear metadata scan on explicit writes; index only if measured
    # retained history makes these writes slow. Never scan on idle map polling.
    for run, entries in sorted(runs.items()):
        if (run.name in protected or run in links or links.intersection(entries)
                or any(path.name.casefold() in KEEP_MARKERS for path in entries)
                or not completed(run, entries)):
            protected_runs.append(run.name)
            continue
        candidates.extend((path, info) for path, info in entries.items() if mechanical(path, run))
    reclaimable = sum(info.st_size for _, info in candidates)
    metrics.update(reclaimable_bytes=reclaimable, protected_bytes=used - reclaimable,
                   protected_runs=protected_runs[:8], omitted_protected_runs=max(0, len(protected_runs) - 8))
    if used + reserve_bytes - reclaimable > CAPACITY_BYTES:
        raise CapacityError(metrics)
    for path, info in sorted(candidates, key=lambda item: (item[1].st_mtime_ns, str(item[0]))):
        if metrics["used_bytes"] + reserve_bytes <= CAPACITY_BYTES:
            break
        # Resolve and check every ancestor immediately before each exact unlink.
        path.resolve().relative_to(base.resolve())
        if any(linked(ancestor.lstat()) for ancestor in (path, *path.parents)
               if ancestor == base or base in ancestor.parents):
            raise ValueError("analysis cache path became linked; cleanup stopped without following it")
        if path.lstat() != info:
            raise ValueError("analysis cache changed during cleanup; retry under the mechanical lock")
        path.unlink()
        metrics["used_bytes"] -= info.st_size
        metrics["reclaimed_bytes"] += info.st_size
        metrics["reclaimed_files"] += 1
    return metrics
