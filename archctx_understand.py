"""Optional, pinned Understand Anything analysis of explicit saved-source scopes.

Upstream owns scanning, parsing and semantic agent instructions. This adapter
captures input identity; it never runs a model or treats analysis as canonical.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import archctx

PROVIDER_REVISION = "5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc"
PROVIDER_URL = "https://github.com/Egonex-AI/Understand-Anything"
SOURCE_LIMIT, SOURCE_BYTES = 64, 4 * 1024 * 1024
GRAPH_BYTES = 8 * 1024 * 1024
SUMMARY_BYTES = 64 * 1024
RUN_LIMIT = 8


def bounded_raw(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"analysis input exceeds {limit} bytes: {path.name}")
    return raw


def local_json(path: Path, limit: int = GRAPH_BYTES) -> dict[str, Any]:
    value = json.loads(bounded_raw(path, limit))
    if not isinstance(value, dict):
        raise ValueError("analysis JSON must be an object")
    return value


def source_bytes(repo: Path, files: list[str], budget: int | None = None) -> dict[str, bytes]:
    if not files or len(files) > SOURCE_LIMIT or len(files) != len(set(files)):
        raise ValueError("analysis needs 1-64 unique explicit repository files")
    result = {}
    remaining = SOURCE_BYTES if budget is None else budget
    for relative in sorted(files):
        path, normalized = archctx.repo_file(repo, relative, "analysis file")
        segments = {p.casefold() for p in Path(relative).parts + path.relative_to(repo.resolve()).parts}
        if normalized != relative or segments.intersection({".git", ".archctx", ".ua"}):
            raise ValueError("analysis source must be a normalized product path, not Git/local state")
        result[relative] = bounded_raw(path, remaining)
        remaining -= len(result[relative])
    return result


def content_hashes(contents: dict[str, bytes]) -> dict[str, str]:
    return {path: archctx.sha(raw) for path, raw in contents.items()}


def verify_sources(repo: Path, receipt: dict[str, Any]) -> list[str]:
    """A commit is provenance, not proof of the bytes saved in a worktree."""
    if str(repo.resolve()) != receipt.get("worktree"):
        raise ValueError("analysis belongs to a different worktree")
    expected = receipt["source_hashes"]
    if not isinstance(expected, dict) or not 0 < len(expected) <= SOURCE_LIMIT:
        raise ValueError("invalid analysis source scope")
    if any(not isinstance(p, str) or not isinstance(d, str) or len(d) != 64
           or any(c not in "0123456789abcdef" for c in d) for p, d in expected.items()):
        raise ValueError("invalid source path/hash in analysis receipt")
    changed, remaining = [], SOURCE_BYTES
    for relative, digest in expected.items():
        try:
            contents = source_bytes(repo, [relative], remaining)
            remaining -= len(contents[relative])
            actual = content_hashes(contents)[relative]
        except OSError:
            actual = None
        if actual != digest:
            changed.append(relative)
    return changed


def provider_root(path: Path) -> Path:
    path = path.resolve()
    if archctx.git(path, "rev-parse", "HEAD") != PROVIDER_REVISION:
        raise ValueError(f"Understand Anything must be pinned to {PROVIDER_REVISION}")
    if archctx.git(path, "status", "--porcelain", "--untracked-files=no") != "":
        raise ValueError("Understand Anything tracked source is modified")
    plugin = path / "understand-anything-plugin"
    if not (plugin / "packages/core/dist/index.js").is_file():
        raise ValueError("build the pinned upstream core first; analysis never installs dependencies")
    return plugin


def run_upstream(plugin: Path, script: str, args: list[Path | str], cwd: Path) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is required for the optional Understand provider")
    command = [node, str(plugin / "skills/understand" / script), *map(str, args)]
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120,
                            env={**os.environ, "UNDERSTAND_NO_WORKTREE_REDIRECT": "1"})
    receipt = {"script": script, "exit_code": result.returncode,
               "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
               "stdout_tail": result.stdout[-2000:],
               "stderr_tail": result.stderr[-2000:]}
    if result.returncode:
        raise ValueError(f"upstream {script} failed ({result.returncode}): {result.stderr[-1000:]}")
    return receipt


def reusable_analysis(directory: Path, receipt: dict[str, Any], imports: dict[str, Any]) -> tuple[dict, dict, list[str]]:
    """Reuse exact saved bytes, never upstream's signature-only COSMETIC skip."""
    if not current_path(directory).exists():
        return {}, {"nodes": [], "edges": []}, []
    old = local_json(current_path(directory), SUMMARY_BYTES)
    if old.get("worktree") != receipt["worktree"] or old.get("provider", {}).get("revision") != PROVIDER_REVISION:
        return {}, {"nodes": [], "edges": []}, []
    run = (directory / "understand/runs" / old["analysis_id"]).resolve()
    run.relative_to((directory / "understand/runs").resolve())
    raw = bounded_raw(run / "graph.json", GRAPH_BYTES)
    if archctx.sha(raw) != old["graph_sha256"]:
        raise ValueError("previous imported graph changed; retain it for diagnosis before reanalysis")
    graph = json.loads(raw)
    reused = [p for p, digest in receipt["source_hashes"].items()
              if old["source_hashes"].get(p) == digest
              and old.get("import_hashes", {}).get(p) == archctx.semantic(imports.get(p, []))]
    return old, graph, reused


def check_reused_graph(source: Path, receipt: dict[str, Any], graph: dict[str, Any]) -> None:
    """Do not let upstream's dangling-edge cleanup silently erase a retained caller."""
    if not receipt.get("incremental", {}).get("reused_files"):
        return
    baseline = local_json(source / ".ua/intermediate/batch-existing.json")
    if archctx.semantic(baseline) != receipt["incremental"]["baseline_hash"]:
        raise ValueError("reused graph baseline changed during analysis")
    nodes = {node["id"]: node for node in graph["nodes"]}
    if any(nodes.get(node["id"]) != node for node in baseline["nodes"]):
        raise ValueError("unchanged source nodes were lost or rewritten; inspect the incremental merge")
    edges = {archctx.semantic(edge) for edge in graph["edges"]}
    if any(archctx.semantic(edge) not in edges for edge in baseline["edges"]):
        raise ValueError("retained incoming relation lost/changed; review surviving symbol IDs or reanalyze its caller")


def prepare(config_path: Path, explicit: str | None, provider: Path, files: list[str]) -> dict[str, Any]:
    """Capture saved bytes, then run the real upstream scanner/import resolver.

    The isolated scope is a Git root only to prevent upstream discovery from
    walking into the product checkout. No product commit/stash/clean is used.
    """
    config = archctx.load(config_path)
    repo = archctx.repo_for(config_path, config).resolve()
    directory = archctx.state(config_path, explicit).resolve()
    plugin = provider_root(provider)
    contents = source_bytes(repo, files)
    hashes = content_hashes(contents)
    source_id = archctx.semantic({"worktree": str(repo), "source_hashes": hashes,
                                 "provider_revision": PROVIDER_REVISION})
    run = directory / "understand" / "runs" / source_id
    # ponytail: eight explicit snapshots, not an unbounded analysis archive.
    # Keep history intact; the owner can archive obsolete runs when this fills.
    if not run.exists() and run.parent.exists() and sum(p.is_dir() for p in run.parent.iterdir()) >= RUN_LIMIT:
        raise ValueError("eight local analysis runs retained; archive obsolete runs before another optional analysis")
    manifest_path = run / "input.json"
    if manifest_path.exists():
        existing = local_json(manifest_path)
        if existing.get("source_hashes") != hashes or existing.get("worktree") != str(repo):
            raise ValueError("analysis input identity collision")
        inputs_ready = all((run / f"source/.ua/tmp/ua-file-analyzer-input-{i}.json").is_file() for i in range(len(hashes)))
        present = [p for p in files if (run / "source" / p).exists()]
        if present and content_hashes(source_bytes(run / "source", present)) != {p: hashes[p] for p in present}:
            raise ValueError("cached source snapshot was modified; preserve it and prepare a new explicit analysis")
        mirror_matches = len(present) == len(files)
        if existing.get("prepared") and inputs_ready and mirror_matches:
            if changed := verify_sources(repo, existing):
                raise ValueError("source moved during cache lookup: " + ", ".join(changed))
            return {"status": "REUSED_INPUT", "analysis_id": source_id,
                    "input": str(manifest_path), "source_root": str(run / "source"),
                    "next_action": "reuse completed semantic results or continue the existing upstream agents"}
    source = run / "source"
    ua = source / ".ua"
    (ua / "tmp").mkdir(parents=True, exist_ok=True)
    (ua / "intermediate").mkdir(parents=True, exist_ok=True)
    for relative, raw in contents.items():
        archctx.atomic_bytes(source / relative, raw)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True, capture_output=True)
    receipt = {"version": 1, "analysis_id": source_id, "worktree": str(repo),
               "source_revision": archctx.revision(repo), "source_hashes": hashes,
               "source_bytes": sum(map(len, contents.values())), "source_root": str(source),
               "snapshot_commit": None,
               "provider": {"name": "understand-anything", "url": PROVIDER_URL,
                            "revision": PROVIDER_REVISION, "root": str(plugin)},
               "scope": "explicit files only; absence is not deletion evidence",
               "semantic_executor": "existing authorized Codex session; not run by this command"}
    archctx.atomic(manifest_path, receipt)
    if changed := verify_sources(repo, receipt):
        raise ValueError("source moved during capture: " + ", ".join(changed))
    steps = [run_upstream(plugin, "scan-project.mjs", [source, ua / "tmp/scan.json", "--exclude-analysis-data"], source)]
    scan = local_json(ua / "tmp/scan.json")
    if not scan.get("scriptCompleted") or {f["path"] for f in scan["files"]} != set(hashes):
        raise ValueError("upstream scan did not cover the exact selected source; nothing accepted")
    archctx.atomic(ua / "tmp/import-input.json", {"projectRoot": str(source), "files": scan["files"]})
    steps.append(run_upstream(plugin, "extract-import-map.mjs", [ua / "tmp/import-input.json", ua / "tmp/import-output.json"], source))
    imports = local_json(ua / "tmp/import-output.json")
    if not imports.get("scriptCompleted") or imports.get("failures"):
        raise ValueError("upstream import extraction incomplete; inspect local output")
    scan_result = {key: scan[key] for key in ("files", "totalFiles", "filteredByIgnore", "estimatedComplexity")}
    scan_result.update(name=repo.name, description="Explicit saved-source scope; semantic interpretation pending",
                       languages=sorted(scan.get("stats", {}).get("byLanguage", {})), frameworks=[],
                       importMap=imports["importMap"])
    archctx.atomic(ua / "intermediate/scan-result.json", scan_result)
    old, previous, reused = reusable_analysis(directory, receipt, imports["importMap"])
    previous_nodes = {node["id"]: node for node in previous["nodes"]}
    retained = {node["id"] for node in previous["nodes"] if node.get("filePath") in reused}
    in_scope = {node["id"] for node in previous["nodes"] if node.get("filePath") in hashes}
    baseline = {"nodes": [node for node in previous["nodes"] if node["id"] in retained],
                "edges": [edge for edge in previous["edges"] if edge["source"] in retained
                          and edge["target"] in in_scope and edge["type"] != "imports"]}
    if reused:
        archctx.atomic(ua / "intermediate/batch-existing.json", baseline)
    receipt["incremental"] = {"base_analysis_id": old.get("analysis_id"),
        "base_graph_sha256": old.get("graph_sha256"), "reused_files": reused,
        "analyze_files": [p for p in hashes if p not in reused], "baseline_hash": archctx.semantic(baseline)}
    # One input per file permits scoped semantic updates without re-reading an
    # unchanged batch. Actual extraction remains the upstream agent's first step.
    for index, file in enumerate(sorted(scan["files"], key=lambda f: f["path"])):
        relative = file["path"]
        symbols = []
        for node in previous["nodes"]:
            if node.get("filePath") == relative and node["type"] in ("function", "class", "method"):
                owners = [previous_nodes[edge["source"]]["name"] for edge in previous["edges"]
                          if edge["type"] == "contains" and edge["target"] == node["id"]
                          and previous_nodes[edge["source"]]["type"] == "class"]
                symbols.append({**{k: node[k] for k in ("id", "name", "type", "filePath", "lineRange") if k in node}, "owners": owners})
        archctx.atomic(ua / f"tmp/ua-file-analyzer-input-{index}.json",
                       {"projectRoot": str(source), "batchFiles": [file],
                        "batchImportData": {relative: imports["importMap"].get(relative, [])},
                        "previousSymbols": symbols})
        if relative in reused:
            previous_index = sorted(old["source_hashes"]).index(relative)
            old_run = directory / "understand/runs" / old["analysis_id"]
            raw = bounded_raw(old_run / f"source/.ua/tmp/ua-file-extract-results-{previous_index}.json", GRAPH_BYTES)
            archctx.atomic_bytes(ua / f"tmp/ua-file-extract-results-{index}.json", raw)
    if changed := verify_sources(repo, receipt):
        raise ValueError("source moved during preparation: " + ", ".join(changed))
    receipt.update(structure_preparation=steps, prepared=True)
    archctx.atomic(manifest_path, receipt)
    return {"status": "NEEDS_SEMANTIC_ANALYSIS", "analysis_id": source_id,
            "input": str(manifest_path), "source_root": str(source), "files": scan["files"],
            "incremental": receipt["incremental"],
            "steps": steps, "next_action": "use pinned file-analyzer, merge, architecture-analyzer and tour-builder; then import the actual knowledge-graph"}


def current_path(directory: Path) -> Path:
    return directory / "understand/current.json"


def validate_graph(plugin: Path, raw: bytes) -> dict[str, Any]:
    """Use upstream's real schema; do not import its lossy sanitizer's output."""
    program = """
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {KnowledgeGraphSchema}=await import(pathToFileURL(process.argv[1]).href);
const graph=JSON.parse(readFileSync(0,'utf8'));
const result=KnowledgeGraphSchema.safeParse(graph);
console.log(JSON.stringify({ok:result.success,issues:result.success?[]:result.error.issues.slice(0,8)}));
process.exit(result.success?0:1);
"""
    result = subprocess.run([shutil.which("node") or "node", "--input-type=module", "-e", program,
                             str(plugin / "packages/core/dist/schema.js")], input=raw.decode("utf-8"),
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise ValueError("upstream graph schema rejected the result: " + (result.stdout or result.stderr)[-1200:])
    return json.loads(result.stdout)


def finish(config_path: Path, explicit: str | None, input_path: Path) -> dict[str, Any]:
    """Understand skill's final assembly/save step after its actual agent stages."""
    directory = archctx.state(config_path, explicit).resolve()
    input_path = input_path.resolve()
    input_path.relative_to(directory / "understand/runs")
    receipt = local_json(input_path, SUMMARY_BYTES)
    source = input_path.parent / "source"
    if Path(receipt["source_root"]).resolve() != source:
        raise ValueError("analysis source root does not match run")
    config = archctx.load(config_path)
    repo = archctx.repo_for(config_path, config).resolve()
    if verify_sources(repo, receipt) or content_hashes(source_bytes(source, list(receipt["source_hashes"]))) != receipt["source_hashes"]:
        raise ValueError("analysis input changed before final assembly; prior state retained")
    plugin = provider_root(Path(receipt["provider"]["root"]).parent)
    ua = source / ".ua"
    merged = local_json(ua / "intermediate/assembled-graph.json")
    scan = local_json(ua / "intermediate/scan-result.json")
    layers = json.loads(bounded_raw(ua / "intermediate/layers.json", SUMMARY_BYTES))
    tour = json.loads(bounded_raw(ua / "intermediate/tour.json", SUMMARY_BYTES))
    graph = {"version": "1.0.0", "project": {
        "name": scan["name"], "languages": scan["languages"], "frameworks": scan["frameworks"],
        "description": "Selected saved-source scope, not a whole-repository or commit-only analysis. " + scan["description"],
        "analyzedAt": archctx.datetime.now(archctx.timezone.utc).isoformat(),
        "gitCommitHash": receipt["source_revision"]},
        "nodes": merged["nodes"], "edges": merged["edges"], "layers": layers, "tour": tour}
    check_reused_graph(source, receipt, graph)
    raw = json.dumps(graph, ensure_ascii=False, indent=2).encode("utf-8")
    validate_graph(plugin, raw)
    file_ids = {n["id"] for n in graph["nodes"] if n["type"] == "file"}
    memberships = [n for layer in layers for n in layer["nodeIds"] if n in file_ids]
    if set(memberships) != file_ids or len(memberships) != len(file_ids):
        raise ValueError("upstream layers must cover each analyzed file exactly once")
    archctx.atomic_bytes(ua / "knowledge-graph.json", raw)
    fingerprint_input = ua / "intermediate/fingerprint-input.json"
    archctx.atomic(fingerprint_input, {"projectRoot": str(source), "filePaths": list(receipt["source_hashes"]),
                                       "gitCommitHash": receipt["source_revision"]})
    step = run_upstream(plugin, "build-fingerprints.mjs", [fingerprint_input], source)
    if "Fingerprints baseline:" not in step["stdout_tail"]:
        raise ValueError("upstream did not confirm a fingerprint baseline; metadata was not advanced")
    archctx.atomic(ua / "meta.json", {"lastAnalyzedAt": graph["project"]["analyzedAt"],
        "gitCommitHash": receipt["source_revision"], "version": graph["version"],
        "analyzedFiles": len(receipt["source_hashes"]), "worktreeContentHash": archctx.semantic(receipt["source_hashes"]),
        "snapshotCommit": None, "incrementalPolicy": "LAC content hashes; never upstream COSMETIC implies semantic SKIP"})
    return {"status": "UPSTREAM_GRAPH_SAVED", "graph": str(ua / "knowledge-graph.json"),
            "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]),
            "fingerprints": step, "next_action": "import this actual graph; canonical architecture is still unchanged"}


def graph_candidates(graph: dict[str, Any], receipt: dict[str, Any], contents: dict[str, bytes]) -> list[dict[str, Any]]:
    """Layers are investigation groups, never automatically canonical components."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    groups = graph["layers"] or [{"id": n["id"], "name": n["name"], "description": n["summary"], "nodeIds": [n["id"]]}
                                 for n in graph["nodes"] if n.get("filePath") in receipt["source_hashes"] and n["type"] == "file"]
    result = []
    for group in groups:
        files = sorted({nodes[ident]["filePath"] for ident in group["nodeIds"] if nodes[ident].get("filePath") in receipt["source_hashes"]})
        if not files:
            continue
        evidence = []
        for relative in files:
            symbols = [n for n in nodes.values() if n.get("filePath") == relative and n.get("lineRange")]
            lines = contents[relative].decode("utf-8").splitlines()
            line = symbols[0]["lineRange"][0] if symbols else next((i + 1 for i, text in enumerate(lines) if text.strip()), 1)
            if not 1 <= line <= len(lines):
                raise ValueError("analysis symbol line lies outside its captured source")
            evidence.append({"path": relative, "line": line, "contains": lines[line - 1][:240],
                             "sha256": receipt["source_hashes"][relative]})
        edges = [e for e in graph["edges"] if nodes[e["source"]].get("filePath") in files
                 and e["type"] != "contains" and nodes[e["source"]].get("filePath") != nodes[e["target"]].get("filePath")]
        result.append({"id": "ua:" + archctx.semantic({"provider": PROVIDER_REVISION, "group": group,
                       "sources": {p: receipt["source_hashes"][p] for p in files}, "relations": edges}),
                       "kind": "source_analysis", "title": group["name"][:160], "summary": group["description"][:1000],
                       "upstream_id": group["id"], "node_ids": group["nodeIds"][:16],
                       "omitted_node_ids": max(0, len(group["nodeIds"]) - 16), "files": files,
                       "evidence": evidence[:4], "omitted_evidence": max(0, len(evidence) - 4),
                       "raw_relations": edges[:8], "omitted_raw_relations": max(0, len(edges) - 8),
                       "provenance": "understand_structure_plus_codex_semantic_analysis",
                       "confidence": "unreviewed_semantic_inference", "blocking": False})
    return result


def import_graph(config_path: Path, explicit: str | None, input_path: Path) -> dict[str, Any]:
    directory = archctx.state(config_path, explicit).resolve()
    input_path = input_path.resolve()
    input_path.relative_to(directory / "understand/runs")
    receipt = local_json(input_path, SUMMARY_BYTES)
    config = archctx.load(config_path)
    repo = archctx.repo_for(config_path, config).resolve()
    source = input_path.parent / "source"
    if Path(receipt["source_root"]).resolve() != source:
        raise ValueError("analysis source root does not match its captured run")
    plugin = provider_root(Path(receipt["provider"]["root"]).parent)
    if receipt["provider"].get("revision") != PROVIDER_REVISION:
        raise ValueError("analysis provider identity mismatch")
    contents = source_bytes(source, list(receipt["source_hashes"]))
    if content_hashes(contents) != receipt["source_hashes"]:
        raise ValueError("isolated analysis source changed after capture")
    graph_path = source / ".ua/knowledge-graph.json"
    raw = bounded_raw(graph_path, GRAPH_BYTES)
    graph = json.loads(raw)
    validate_graph(plugin, raw)
    check_reused_graph(source, receipt, graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]) or any(e["source"] not in nodes or e["target"] not in nodes for e in graph["edges"]):
        raise ValueError("duplicate nodes or dangling raw relations; repair the upstream result")
    for group in graph["layers"] + graph["tour"]:
        if any(ident not in nodes for ident in group["nodeIds"]):
            raise ValueError("layer/tour contains an unknown raw node")
    covered = {n.get("filePath") for n in nodes.values() if n["type"] == "file"}
    if covered != set(receipt["source_hashes"]):
        raise ValueError("partial graph: selected files are missing or outside scope; prior discoveries/LKG retained")
    if any(n.get("filePath") and n["filePath"] not in covered for n in nodes.values()):
        raise ValueError("raw graph symbol refers to source outside the captured scope")
    extraction = []
    for index, relative in enumerate(sorted(receipt["source_hashes"])):
        path = source / f".ua/tmp/ua-file-extract-results-{index}.json"
        result = local_json(path)
        if (not result.get("scriptCompleted") or result.get("filesSkipped")
                or result.get("analysisOutcomes", {}).get("structure", {}).get("succeeded") != 1
                or result.get("analysisOutcomes", {}).get("structure", {}).get("failed")
                or result.get("analysisOutcomes", {}).get("callGraph", {}).get("failed")
                or result.get("filesAnalyzed") != 1 or len(result.get("results", [])) != 1
                or result["results"][0].get("path") != relative):
            raise ValueError("partial/failed upstream structural extraction; not deletion evidence")
        extraction.append(result["analysisOutcomes"])
    receipt = {**receipt, "graph_sha256": archctx.sha(raw), "graph_bytes": len(raw),
               "imported_at": archctx.datetime.now(archctx.timezone.utc).isoformat(),
               "node_count": len(nodes), "edge_count": len(graph["edges"]), "extraction": extraction,
               "layers": graph["layers"], "tour": graph["tour"]}
    scan_path = source / ".ua/intermediate/scan-result.json"
    import_map = local_json(scan_path).get("importMap", {}) if scan_path.exists() else {}
    receipt["import_hashes"] = {p: archctx.semantic(import_map.get(p, [])) for p in receipt["source_hashes"]}
    receipt["candidates"] = graph_candidates(graph, receipt, contents)
    if len(json.dumps(receipt, ensure_ascii=False).encode()) > SUMMARY_BYTES:
        raise ValueError("analysis summary exceeds 64 KiB; use a smaller architecture scope")
    with archctx.refresh_lock(directory):
        if changed := verify_sources(repo, receipt):
            raise ValueError("analysis stale; saved source moved: " + ", ".join(changed))
        archctx.atomic_bytes(input_path.parent / "graph.json", raw)
        # The single summary swap does not publish or alter canonical architecture.
        archctx.atomic(current_path(directory), receipt)
    return discoveries(config_path, explicit)


def discoveries(config_path: Path, explicit: str | None, limit: int = 8, details: bool = False) -> dict[str, Any]:
    directory = archctx.state(config_path, explicit)
    path = current_path(directory)
    if not path.exists():
        return {"configured": False}
    try:
        receipt = local_json(path, SUMMARY_BYTES)
        config = archctx.load(config_path)
        repo = archctx.repo_for(config_path, config).resolve()
        changed = verify_sources(repo, receipt)
        completed = {d["id"]: d for d in archctx.decision_store(directory)
                     if d.get("state") == "final" and d.get("id", "").startswith("ua:")}
        findings = []
        for candidate in receipt["candidates"]:
            matches = archctx.owners(config, candidate["files"])
            decision = completed.get(candidate["id"])
            finding = {**candidate, "related_components": matches,
                             "match": "evidence_overlap" if matches else "unmapped",
                             "review_state": decision.get("decision") if decision else "unreviewed"}
            if not details:
                edges = candidate.get("raw_relations", [])
                finding["raw_relations"] = edges[:2]
                finding["omitted_raw_relations"] = candidate.get("omitted_raw_relations", 0) + max(0, len(edges) - 2)
            findings.append(finding)
        limit = len(findings) if limit == 0 else max(0, min(limit, 64))
        return {"configured": True, "status": "STALE" if changed else "FRESH",
                "analysis_id": receipt["analysis_id"], "graph_sha256": receipt["graph_sha256"],
                "source_revision": receipt["source_revision"], "source_set_hash": archctx.semantic(receipt["source_hashes"]),
                "provider": {key: receipt["provider"][key] for key in ("name", "url", "revision")},
                "source_files": list(receipt["source_hashes"]), "changed_files": changed,
                "node_count": receipt["node_count"], "edge_count": receipt["edge_count"],
                "candidate_count": len(findings), "candidates": findings[:limit],
                "omitted_candidate_count": max(0, len(findings) - limit),
                "tour": receipt["tour"][:8], "omitted_tour_steps": max(0, len(receipt["tour"]) - 8),
                "blocking": False,
                "detail_query": "archctx-understand with the same config/state: show --details",
                "limitations": ["Analysis is an explicit partial source scope, not accepted architecture or runtime proof.",
                                "Raw imports/calls/semantic edges retain provider meaning; file/layer overlap is not canonical ownership.",
                                "Provider coverage can omit dynamic imports, embedded languages and cross-batch calls; absent edges are not proof of no dependency.",
                                "Only Codex source review and the existing acceptance path may change the shared architecture."],
                "next_action": "reanalyze changed saved source at the next relevant task boundary" if changed else "review useful findings; leave uncertain findings unaccepted"}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"configured": True, "status": "INVALID", "blocking": False,
                "reason": str(error)[:500], "candidates": [], "next_action": "repair local analysis; accepted architecture is unchanged"}


def publication_proof(receipt: dict[str, Any]) -> dict[str, Any]:
    return {"analysis_id": receipt["analysis_id"], "graph_sha256": receipt["graph_sha256"],
            "source_set_hash": archctx.semantic(receipt["source_hashes"]),
            "provider": "understand-anything", "provider_revision": PROVIDER_REVISION,
            "review": "codex_source_review", "meaning": "analysis provenance, not runtime proof"}


def check_publication(repo: Path, directory: Path, proof: dict[str, Any]) -> None:
    """Called under the existing writer lock, including just before LKG swap."""
    receipt = local_json(current_path(directory), SUMMARY_BYTES)
    if publication_proof(receipt) != proof:
        raise ValueError("analysis changed during semantic review/publication")
    if changed := verify_sources(repo, receipt):
        raise ValueError("analysis source moved: " + ", ".join(changed))
    graph = directory / "understand/runs" / receipt["analysis_id"] / "graph.json"
    graph.resolve().relative_to((directory / "understand/runs").resolve())
    if archctx.sha(bounded_raw(graph, GRAPH_BYTES)) != proof["graph_sha256"]:
        raise ValueError("raw analysis changed after import")


def review(config_path: Path, explicit: str | None, ident: str,
           bindings: list[str] | None = None, reason: str | None = None) -> dict[str, Any]:
    try:
        return _review_transaction(config_path, explicit, ident, bindings, reason)
    except archctx.RefreshBusyError as error:
        # Direct `python archctx.py` and imported archctx can have distinct
        # exception classes. Translate at the module that owns this lock.
        return archctx.refresh_retry(archctx.state(config_path, explicit), None, str(error), ["writer_lock"])


def _review_transaction(config_path: Path, explicit: str | None, ident: str,
           bindings: list[str] | None = None, reason: str | None = None) -> dict[str, Any]:
    """An explicit Agent decision, using the existing lock, LKG and decision log.

    Uncertain discovery does not block unrelated refresh. Choosing a suggestion
    binds that publication to its exact saved-source analysis instead.
    """
    directory = archctx.state(config_path, explicit).resolve()
    with archctx.refresh_lock(directory):
        config = archctx.load(config_path)
        repo = archctx.repo_for(config_path, config).resolve()
        old = archctx.load(archctx.last_path(directory))
        receipt = local_json(current_path(directory), SUMMARY_BYTES)
        candidate = next((c for c in receipt["candidates"] if c["id"] == ident), None)
        if candidate is None:
            raise ValueError("analysis candidate no longer exists; read candidates again")
        proof = publication_proof(receipt)
        check_publication(repo, directory, proof)
        decision = {"id": ident, "kind": "source_analysis", "source_analysis": proof,
                    "at": archctx.datetime.now(archctx.timezone.utc).isoformat(),
                    "base_context_hash": old["context_hash"]}
        if reason is not None:
            if reason not in ("not_architecture", "existing_canonical", "test_fixture", "false_match"):
                raise ValueError("use the existing fixed candidate rejection reasons")
            decision.update(decision="rejected", reason=reason)
            result = {"status": "PASS", "context_hash": old["context_hash"], "publication": "not_requested"}
        else:
            bindings = bindings or []
            parsed = [archctx.binding_parts(value) for value in bindings]
            if not 1 <= len(parsed) <= 4 or len(parsed) != len(set(parsed)) or any(len(b) > 160 for b in bindings):
                raise ValueError("analysis acceptance needs 1-4 unique component/relation bindings")
            current = archctx.current_context(config_path, config)
            if {c["id"] for c in old["context"]["components"]} - {c["id"] for c in current["components"]}:
                raise ValueError("partial source analysis cannot approve canonical component deletion")
            if {archctx.relation_id(r) for r in old["context"].get("relations", [])} - {archctx.relation_id(r) for r in current.get("relations", [])}:
                raise ValueError("partial source analysis cannot approve canonical relation deletion")
            for binding in parsed:
                if not any(e.get("path") in candidate["files"] for e in archctx.binding_evidence(current, binding)):
                    raise ValueError("each analysis binding needs validated source evidence in the reviewed finding")
            decision.update(decision="accepted", bindings=bindings)
            unchanged = (archctx.architecture_semantic(current) == archctx.architecture_semantic(old["context"])
                         and archctx.semantic(config) == old["config_hash"])
            if unchanged:
                check_publication(repo, directory, proof)
                result = {"status": "PASS", "context_hash": old["context_hash"], "publication": "existing_canonical_match"}
            else:
                result = archctx._refresh_locked(config_path, explicit, directory,
                    acknowledged={ident: decision}, expected_context_hash=old["context_hash"], understand_proof=proof)
        if result.get("status") == "PASS":
            try:
                archctx.record_decision(directory, {**decision, "state": "final", "context_hash": result["context_hash"]})
            except (OSError, ValueError):
                if result.get("publication") in ("existing_canonical_match", "not_requested"):
                    raise  # No accepted snapshot contains this review yet.
                result["decision_receipt_cleanup"] = "deferred"  # Already bound in the committed LKG.
        return {**result, "candidate": {"id": ident, "decision": decision["decision"]},
                "source_analysis": proof, "last_good_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="architecture/architecture.json")
    parser.add_argument("--state-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare", help="capture explicit saved source for upstream analysis; no model call")
    command.add_argument("--provider", type=Path, required=True)
    command.add_argument("--files", nargs="+", required=True)
    command = commands.add_parser("import", help="read actual upstream knowledge-graph; never accept architecture")
    command.add_argument("--input", type=Path, required=True)
    command = commands.add_parser("finish", help="assemble real upstream batch/layer/tour results and fingerprints")
    command.add_argument("--input", type=Path, required=True)
    command = commands.add_parser("show", help="compact pending source analysis; no models or render")
    command.add_argument("--details", action="store_true", help="include the retained raw edge witnesses")
    args = parser.parse_args()
    try:
        config_path = Path(args.config).resolve()
        if args.command == "prepare":
            result = prepare(config_path, args.state_dir, args.provider, args.files)
        elif args.command == "import":
            result = import_graph(config_path, args.state_dir, args.input)
        elif args.command == "finish":
            result = finish(config_path, args.state_dir, args.input)
        else:
            result = discoveries(config_path, args.state_dir, details=args.details)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        archctx.dump({"status": "INVALID", "reason": str(error), "last_good_preserved": True})
        raise SystemExit(1)
    archctx.dump(result)


if __name__ == "__main__":
    main()
