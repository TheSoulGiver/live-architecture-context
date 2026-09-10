"""LAC's explicitly provisioned, pinned local analysis and rendering components."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import archctx

ARCHIFY_REVISION = "5de7275fe87a66a19d52a4d9b0b3a4f2a5a90115"
ARCHIFY_URL = "https://github.com/tt-a1i/archify.git"
PNPM_VERSION = "10.6.2"


def execute(argv: list[str], cwd: Path, *, timeout: int = 300) -> str:
    # Only the diagnostic suffix is retained, even during a verbose install.
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    tail = bytearray()

    def drain():
        with process.stdout:
            while chunk := process.stdout.read(4096):
                tail[:] = (tail + chunk)[-16384:]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()  # This Popen instance owns precisely this PID.
        process.wait()
        reader.join(timeout=1)
        raise ValueError(f"component command timed out after {timeout}s: " + bytes(tail).decode("utf-8", errors="replace")[-1600:])
    reader.join(timeout=1)
    if reader.is_alive():
        raise ValueError("component command left its output stream open after exit")
    output = bytes(tail).decode("utf-8", errors="replace").strip()
    if code:
        raise ValueError(f"component command failed ({code}): " + output[-1600:])
    return output


def executable(name: str) -> str:
    value = shutil.which("npm.cmd" if os.name == "nt" and name == "npm" else name)
    if not value:
        raise ValueError(f"LAC setup needs {name} on PATH")
    return value


def node_runtime(minimum: int, cwd: Path) -> str:
    node = executable("node")
    version = execute([node, "--version"], cwd)
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", version)
    if not match or int(match[1]) < minimum:
        raise ValueError(f"The compatible LAC components need Node.js {minimum}+")
    return node


def checkout(path: Path, revision: str, repository: str, *, install: bool = False) -> Path:
    path = path.resolve()
    git = executable("git")
    marker = path.parent / ("." + path.name + ".install.json")
    expected = {"repository": repository, "revision": revision}
    pending = marker.exists() and archctx.load(marker) == expected
    empty = path.is_dir() and not any(child.name != ".git" for child in path.iterdir()) and not (path / ".git").is_file()
    if install and (not path.exists() or pending or empty):
        path.parent.mkdir(parents=True, exist_ok=True)
        if marker.exists() and not pending:
            raise ValueError("component installation identity changed; existing files were preserved")
        archctx.atomic(marker, expected)
        if (path / ".git").exists():
            if not (path / ".git").is_dir() or (path / ".git").resolve().parent != path:
                raise ValueError("incomplete component has an external Git directory; existing files were preserved")
        else:
            if path.exists() and any(path.iterdir()):
                raise ValueError("incomplete component contains unknown files; existing files were preserved")
        execute([git, "init", "--quiet", str(path)], path.parent)
        execute([git, "-C", str(path), "fetch", "--depth", "1", repository, revision], path.parent)
        execute([git, "-C", str(path), "checkout", "--detach", revision], path.parent)
    if not (path / ".git").exists():
        raise ValueError("LAC components are not ready; run archctx setup once")
    if execute([git, "-C", str(path), "rev-parse", "HEAD"], path) != revision:
        raise ValueError("component version is incompatible; existing checkout was preserved")
    if execute([git, "-C", str(path), "status", "--porcelain", "--untracked-files=no"], path):
        raise ValueError("component has tracked edits; existing checkout was preserved")
    if install and marker.exists() and archctx.load(marker) == expected:
        marker.unlink()
    return path


def roots(directory: Path) -> dict[str, Path]:
    """Read explicit setup only. Queries never install or execute a component."""
    path = directory / "tools/runtime.json"
    if not path.is_file():
        raise ValueError("LAC components are not configured; run archctx setup once")
    if path.stat().st_size > 4096:
        raise ValueError("invalid local component settings")
    value = archctx.load(path)
    if value.get("version") != 1:
        raise ValueError("unsupported local component settings")
    if any(not isinstance(value.get(key), str) or not Path(value[key]).is_absolute() for key in ("analysis", "renderer")):
        raise ValueError("component locations must be explicit absolute paths")
    return {key: Path(value[key]).resolve() for key in ("analysis", "renderer")}


def setup(config_path: Path, explicit: str | None, analysis_home: str | None = None,
          renderer_home: str | None = None) -> dict:
    from archctx_understand import PROVIDER_REVISION, PROVIDER_URL, provider_root
    config_path = config_path.resolve()
    config = archctx.load(config_path) if config_path.exists() else None
    repo = archctx.repo_for(config_path, config).resolve() if config is not None else Path.cwd().resolve()
    if config is not None and not (config.get("version") == archctx.CONFIG_VERSION and config.get("components") == [] and config.get("relations", []) == []):
        archctx.components(config)
    config_path.relative_to(repo)
    directory = archctx.state(config_path, explicit).resolve()
    directory.relative_to(repo)
    relative_state = archctx.codex_state(config_path, repo, repo / "AGENTS.md", str(directory))
    node = node_runtime(20, repo)
    with archctx.refresh_lock(directory):
        # Exclude rebuildable state before a download can fail or be interrupted.
        extra = "/" + "".join("\\" + x if x in "\\*?[]!# " else x for x in relative_state) + "/" if ".archctx" not in Path(relative_state).parts else None
        ignored_added = archctx.ensure_ignored(repo, False, extra)
        previous = roots(directory) if (directory / "tools/runtime.json").exists() else {}
        analysis = Path(analysis_home).resolve() if analysis_home else previous.get("analysis", directory / "tools/understand")
        renderer = Path(renderer_home).resolve() if renderer_home else previous.get("renderer", directory / "tools/archify")
        managed = not analysis_home and analysis == directory / "tools/understand"
        for path, default in ((analysis, directory / "tools/understand"), (renderer, directory / "tools/archify")):
            if path == default:
                path.resolve().relative_to(directory)
        analysis = checkout(analysis, PROVIDER_REVISION, PROVIDER_URL, install=managed)
        renderer = checkout(renderer, ARCHIFY_REVISION, ARCHIFY_URL, install=not renderer_home and renderer == directory / "tools/archify")
        if not (analysis / "understand-anything-plugin/packages/core/dist/index.js").exists():
            if not managed:
                raise ValueError("external analysis runtime is not built; it was not modified")
            runner = directory / "tools/pnpm"
            pnpm = runner / "node_modules/pnpm/bin/pnpm.cjs"
            if not pnpm.exists():
                runner.mkdir(parents=True, exist_ok=True)
                npm = Path(executable("npm"))
                # Invoke npm's JS directly on Windows, avoiding .cmd quoting and POSIX shim selection.
                npm_script = npm.parent / "node_modules/npm/bin/npm-cli.js"
                if os.name == "nt" and not npm_script.is_file():
                    raise ValueError("npm.cmd has no adjacent npm CLI; repair this Node installation before setup")
                npm_command = [node, str(npm_script)] if os.name == "nt" else [str(npm)]
                execute([*npm_command, "install", "--prefix", str(runner), "--cache", str(directory / "tools/npm-cache"),
                         "--no-save", "--ignore-scripts", "pnpm@" + PNPM_VERSION], runner)
            if execute([node, str(pnpm), "--version"], analysis) != PNPM_VERSION:
                raise ValueError("local package manager does not match the supported component bundle")
            execute([node, str(pnpm), "--filter", "@understand-anything/skill...", "install", "--frozen-lockfile", "--ignore-scripts", "--store-dir", str(directory / "tools/store")], analysis)
            execute([node, str(pnpm), "--filter", "@understand-anything/core", "build"], analysis)
        provider_root(analysis)
        if not (renderer / "archify/bin/archify.mjs").is_file():
            raise ValueError("renderer entrypoint is missing; existing component was preserved")
        # The installation can be long; incorporate saved edits before wiring the project.
        config = archctx.load(config_path) if config_path.exists() else None
        if config is not None and archctx.repo_for(config_path, config).resolve() != repo:
            raise ValueError("project selection changed during setup; shared configuration was preserved")
        if config is not None and not (config.get("version") == archctx.CONFIG_VERSION and config.get("components") == [] and config.get("relations", []) == []):
            archctx.components(config)
        archctx.atomic(directory / "tools/runtime.json", {"version": 1, "analysis": str(analysis), "renderer": str(renderer)})
        # Source declarations remain empty on first setup; the Agent supplies reviewed ownership.
        if config is None:
            config = {"version": 1, "repo": os.path.relpath(repo, config_path.parent).replace("\\", "/"), "components": [], "relations": []}
        if "archify" not in config:
            view = config_path.with_name(config_path.stem + ".view.json")
            if not view.exists():
                archctx.atomic(view, {"title": repo.name, "nodes": [
                    {"id": item["id"], "pos": [40 + 280 * (index % 4), 60 + 180 * (index // 4)]}
                    for index, item in enumerate(config.get("components", []))]})
            prefix = ["{python}", "{lac_runtime}", "--state", "{state}"]
            suffix = ["--quality", "standard", "--repo-root", "{repo}", "--json"]
            config["archify"] = {"view": view.relative_to(repo).as_posix(),
                "output": (directory / "architecture.archify.json").relative_to(repo).as_posix(),
                "validate": [*prefix, "validate", "architecture", "{archify_output}", *suffix],
                "render": [*prefix, "deliver", "architecture", "{archify_output}", "{archify_html}", *suffix],
                "compare": [*prefix, "compare", "architecture", "{archify_before}", "{archify_output}", "{archify_compare}", "--receipt", "{archify_receipt}", *suffix],
                "timeout_seconds": 180}
            archctx.atomic(config_path, config)
    return {"status": "READY", "scope": "project-local components; no model or shared installation configured",
            "gitignore_updated": ignored_added,
            "architecture": "draft_unreviewed" if not config.get("components") else "configured",
            "components": {"analysis": PROVIDER_REVISION, "renderer": ARCHIFY_REVISION},
            "next_action": "archctx understand <development question>; archctx map"}


def archify_main(directory: Path, args: list[str]) -> int:
    """Shared renderer implementation for the old wrapper and installed LAC."""
    target = Path(os.environ["ARCHIFY_HOME"]).resolve() if os.environ.get("ARCHIFY_HOME") else (
        roots(directory)["renderer"] if (directory / "tools/runtime.json").exists() else directory / "tools/archify")
    node = node_runtime(18, directory.parent if directory.parent.exists() else Path.cwd())
    if args == ["setup"]:
        with archctx.refresh_lock(directory):
            managed = not os.environ.get("ARCHIFY_HOME") and target == directory / "tools/archify"
            if managed:
                target.resolve().relative_to(directory.resolve())
            pending = target.parent / ("." + target.name + ".install.json")
            target = checkout(target, ARCHIFY_REVISION, ARCHIFY_URL, install=managed or not target.exists() or pending.exists())
        print(f"LAC renderer ready ({ARCHIFY_REVISION})")
        return 0
    target = checkout(target, ARCHIFY_REVISION, ARCHIFY_URL)
    return subprocess.run([node, str(target / "archify/bin/archify.mjs"), *args]).returncode


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2 or args[0] != "--state":
        raise ValueError("internal renderer requires --state <project-state>")
    return archify_main(Path(args[1]).resolve(), args[2:])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
