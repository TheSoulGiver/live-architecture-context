"""Bounded, isolated source/dependency capture for the pinned understanding tool."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator

import archctx
from archctx_analysis_storage import linked


SOURCE_LIMIT, BYTE_LIMIT, INVENTORY_LIMIT = 64, 4 * 1024 * 1024, 20000
INVENTORY_BYTES = 16 * 1024 * 1024
RESOLVER_NAMES = {"tsconfig.json", "go.mod", "composer.json", "Package.swift"}
LOCAL_NAMES = {".git", ".archctx", ".ua", ".understand-anything"}
WALK_IGNORE = LOCAL_NAMES | {"node_modules", "__pycache__", ".venv", "venv", ".next", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "target", "coverage", "vendor"}


def _path(repo: Path, name: str) -> Path:
    path, normalized = archctx.repo_file(repo, name, "analysis input")
    parts = Path(name).parts + path.relative_to(repo.resolve()).parts
    if normalized != name or any(p.casefold() in LOCAL_NAMES for p in parts):
        raise ValueError("analysis input must be a normalized product path")
    return path


def _read(repo: Path, name: str, budget: int) -> bytes:
    with _path(repo, name).open("rb") as stream:
        value = stream.read(budget + 1)
    if len(value) > budget:
        raise ValueError("analysis source and dependency evidence exceed 4 MiB")
    return value


def _git_inventory(repo: Path) -> tuple[list[str], bool] | None:
    # Trust only the explicitly selected project for this fixed read: Windows
    # sandbox tokens can differ from its owner. No hooks, lazy fetch or config writes.
    command = ["git", "-c", "safe.directory=" + str(repo.resolve()), "-c", "core.fsmonitor=false",
               "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
    expired = False

    def stop():
        if process.poll() is None:
            try:
                process.kill()  # Only this invocation's owned Popen/PID.
            except ProcessLookupError:
                pass

    def timeout():
        nonlocal expired
        expired = True
        stop()

    timer = threading.Timer(30, timeout)
    timer.daemon = True
    timer.start()
    raw, names, truncated = bytearray(), 0, False
    try:
        with process.stdout:
            while chunk := process.stdout.read1(min(65536, INVENTORY_BYTES + 1 - len(raw))):
                raw.extend(chunk)
                names += chunk.count(b"\0")
                if len(raw) > INVENTORY_BYTES or names > INVENTORY_LIMIT:
                    truncated = True
                    stop()
                    break
        code = process.wait(timeout=30)
        if expired or code and not truncated:
            return None
        # A byte boundary can split a name or UTF-8 character. Keep only complete
        # NUL-terminated names; the missing suffix is explicitly incomplete.
        pieces = bytes(raw[:INVENTORY_BYTES]).split(b"\0")[:-1]
        return [name.decode("utf-8") for name in pieces[:INVENTORY_LIMIT]], not truncated
    finally:
        timer.cancel()
        stop()
        process.wait(timeout=5)
        timer.join(timeout=1)


def inventory(repo: Path, selected: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Read file names only; explicit selections survive ordinary ignore rules."""
    repo = repo.resolve()
    if len(set(selected)) > INVENTORY_LIMIT:
        raise ValueError("selected source exceeds inventory bound")
    for name in selected:
        _path(repo, name)
    try:
        result = _git_inventory(repo)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        result = None
    paths, complete = set(selected), True
    git_omitted = 0
    if result is not None:
        names, complete = result
        git_omitted = int(not complete)
        paths.update(name for name in names if name and not any(p.casefold() in LOCAL_NAMES for p in Path(name).parts))
        coverage = {"method": "git-index-and-untracked", "ignored": "Git ignore rules and local analysis state"}
    else:
        coverage = {"method": "bounded-filesystem", "ignored": sorted(WALK_IGNORE)}
        # ponytail: name-only fallback with explicit omissions; use Git inventory for repository ignore semantics.
        walk_errors = []
        for current, directories, names in os.walk(repo, followlinks=False, onerror=walk_errors.append):
            directories[:] = sorted(d for d in directories if d.casefold() not in WALK_IGNORE
                                      and not linked((Path(current) / d).lstat()))
            for name in sorted(names):
                path = Path(current) / name
                if name.casefold() in LOCAL_NAMES or linked(path.lstat()):
                    continue
                paths.add(path.relative_to(repo).as_posix())
                if len(paths) > INVENTORY_LIMIT:
                    complete = False
                    break
            if not complete:
                break
        if walk_errors:
            complete = False
            coverage["unreadable_directories"] = len(walk_errors)
    omitted = max(git_omitted, len(paths) - INVENTORY_LIMIT)
    complete = complete and not omitted
    selected_set = set(selected)
    paths = sorted(selected_set | set(sorted(paths - selected_set)[:max(0, INVENTORY_LIMIT - len(selected_set))]))
    coverage.update(complete=complete, omitted_at_least=omitted)
    value = {"paths": paths, "coverage": coverage}
    return value | {"hash": archctx.semantic(value)}


def verify_inventory(repo: Path, selected: list[str] | tuple[str, ...] = ()) -> str:
    return inventory(repo, selected)["hash"]


def _run(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env={**os.environ, "UNDERSTAND_NO_WORKTREE_REDIRECT": "1"})
    receipt = {"script": name, "command": command, "exit_code": result.returncode,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
        "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:]}
    if result.returncode:
        raise ValueError(f"{name} failed ({result.returncode}): {result.stderr[-1200:]}")
    return receipt


def _verify(repo: Path, captured: dict[str, bytes], expected_inventory: str, selected: list[str]) -> None:
    changed = []
    for path, raw in captured.items():
        try:
            current = _read(repo, path, len(raw))
        except (OSError, ValueError):
            current = None
        if current != raw:
            changed.append(path)
    if changed:
        raise ValueError("analysis evidence moved during capture: " + ", ".join(sorted(changed)))
    if verify_inventory(repo, selected) != expected_inventory:
        raise ValueError("analysis inventory moved during capture")


@contextmanager
def capture(repo: Path, directory: Path, plugin: Path, files: list[str]) -> Iterator[dict[str, Any]]:
    """Yield a disposable mechanical snapshot; never touch retained analysis runs."""
    repo, directory, plugin = repo.resolve(), directory.resolve(), plugin.resolve()
    if not files or len(files) > SOURCE_LIMIT or len(files) != len(set(files)):
        raise ValueError("analysis needs 1-64 unique explicit repository files")
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is required for the optional Understand provider")
    contents, remaining = {}, BYTE_LIMIT
    for path in sorted(files):
        contents[path] = _read(repo, path, remaining)
        remaining -= len(contents[path])
    listing = inventory(repo, files)
    parent = directory / "understand"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-", dir=parent) as temporary:
        work = Path(temporary)
        source = work / "source"
        source.mkdir()
        for path, raw in contents.items():
            archctx.atomic_bytes(source / path, raw)
        steps = [_run("isolated-git-root", ["git", "init", "--quiet", str(source)], source)]
        scan_path = work / "scan.json"
        steps.append(_run("scan-project.mjs", [node, str(plugin / "skills/understand/scan-project.mjs"),
            str(source), str(scan_path), "--exclude-analysis-data"], source))
        scan = archctx.load(scan_path)
        if (not scan.get("scriptCompleted") or scan.get("failures")
                or {f["path"] for f in scan.get("files", [])} != set(contents)):
            raise ValueError("upstream scan did not cover the exact selected source")
        resolver_contents, resolver_unknown = {}, []
        resolver_names = set() if all(f.get("language") == "python" for f in scan["files"]) else RESOLVER_NAMES
        for path in listing["paths"]:
            if Path(path).name not in resolver_names:
                continue
            try:
                if path in contents:
                    raw = contents[path]
                else:
                    if len(resolver_contents) >= SOURCE_LIMIT:
                        raise ValueError("resolver configuration count exceeds capture bound")
                    raw = _read(repo, path, remaining)
                    remaining -= len(raw)
                    archctx.atomic_bytes(source / path, raw)
                resolver_contents[path] = raw
            except (OSError, ValueError):
                resolver_unknown.append(path)
        selected_metadata = {f["path"]: f for f in scan["files"]}
        helper_input, helper_output = work / "dependency-input.json", work / "dependencies.json"
        archctx.atomic(helper_input, {"projectRoot": str(source),
            "files": [selected_metadata.get(path, {"path": path}) for path in listing["paths"]],
            "analysisPaths": sorted(contents), "externalModules": sorted(sys.stdlib_module_names)})
        helper = Path(__file__).parent / "archctx_assets/analysis_dependencies.mjs"
        steps.append(_run("analysis_dependencies.mjs", [node, str(helper), str(plugin), str(helper_input), str(helper_output)], source))
        extracted = archctx.load(helper_output)
        if not extracted.get("scriptCompleted") or set(extracted.get("files", {})) != set(contents):
            raise ValueError("dependency extraction did not cover the selected source")
        dependencies = extracted["files"]
        dependency_contents, unknown = {}, set()
        targets = sorted({path for row in dependencies.values() for path in row["dependencies"]} - set(contents))
        for path in targets:
            try:
                if len(dependency_contents) >= SOURCE_LIMIT:
                    raise ValueError("dependency file count exceeds capture bound")
                if path in resolver_contents:
                    raw = resolver_contents[path]
                else:
                    raw = _read(repo, path, remaining)
                    remaining -= len(raw)
                dependency_contents[path] = raw
            except (OSError, ValueError):
                unknown.add(path)
        for row in dependencies.values():
            for path in sorted(unknown.intersection(row["dependencies"])):
                row["unknown"].append("uncaptured dependency: " + path)
            if not listing["coverage"]["complete"]:
                row["unknown"].append("repository inventory is incomplete")
            if resolver_unknown:
                row["unknown"].append("resolver configuration capture is incomplete")
            row["unknown"] = sorted(set(row["unknown"]))
            if row["unknown"] and row["coverage"] == "resolved":
                row["coverage"] = "partial"
        captured = contents | resolver_contents | dependency_contents
        _verify(repo, captured, listing["hash"], files)
        yield {"source": source, "source_hashes": {p: archctx.sha(raw) for p, raw in contents.items()},
            "source_bytes": sum(map(len, contents.values())),
            "dependency_hashes": {p: archctx.sha(raw) for p, raw in dependency_contents.items()},
            "dependency_contents": dependency_contents, "dependencies": dependencies,
            "resolution": extracted.get("resolution"),
            "resolver_hashes": {p: archctx.sha(raw) for p, raw in resolver_contents.items()},
            "resolver_contents": resolver_contents, "resolver_unknown_paths": resolver_unknown,
            "inventory_hash": listing["hash"], "inventory_coverage": listing["coverage"],
            "unknown_dependency_paths": sorted(unknown), "scan": scan, "steps": steps,
            "dependency_failures": extracted.get("failures", []), "dependency_limitations": extracted.get("limitations", [])}
        _verify(repo, captured, listing["hash"], files)
