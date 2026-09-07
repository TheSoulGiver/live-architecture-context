#!/usr/bin/env python3
"""Project a declared Archctx view into Archify's typed architecture IR."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from archctx import components as validated_components
from archctx import relation_id, repo_for, validate


COMPONENT_TYPES = {"frontend", "backend", "database", "cloud", "security", "messagebus", "external"}
VARIANTS = {"default", "emphasis", "security", "dashed"}
FULL_SHA = 40


def github_repository_url(remote: str) -> str | None:
    """Normalize one supported GitHub origin into Archify's public HTTPS form."""
    raw = remote.strip()
    prefixes = ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
    tail = next((raw[len(prefix):] for prefix in prefixes if raw.lower().startswith(prefix)), None)
    if tail is None:
        return None
    tail = tail.rstrip("/")
    if tail.lower().endswith(".git"):
        tail = tail[:-4]
    pieces = tail.split("/")
    if len(pieces) != 2 or any(not piece or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in piece) for piece in pieces):
        return None
    return f"https://github.com/{pieces[0]}/{pieces[1]}"


def repo_relative_path(value: Any) -> str | None:
    """Return an Archify-safe repo-relative POSIX path, or no path."""
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    pieces = value.split("/")
    if any(not piece or piece in (".", "..") for piece in pieces) or pieces[0] == ".git":
        return None
    return "/".join(pieces)


def candidate_evidence(context_value: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Collect only the already validated evidence facts carried by a context."""
    components = context_value.get("components")
    if not isinstance(components, list) or not components:
        return None
    result: list[dict[str, Any]] = []
    for item in components:
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if not isinstance(evidence, list) or not evidence:
            return None
        if not all(isinstance(fact, dict) for fact in evidence):
            return None
        result.extend(evidence)
    relations = context_value.get("relations", [])
    if not isinstance(relations, list):
        return None
    for item in relations:
        if not isinstance(item, dict) or "evidence" not in item:
            continue
        evidence = item["evidence"]
        if not isinstance(evidence, list) or not all(isinstance(fact, dict) for fact in evidence):
            return None
        result.extend(evidence)
    return result


def repository_evidence(repo: Path, context_value: dict[str, Any]) -> dict[str, Any] | None:
    """Return public commit evidence only when every candidate fact matches that commit.

    This uses local Git objects only. A dirty worktree is allowed when its
    validated evidence facts still hash to the pinned commit; otherwise no
    repository metadata is returned and callers must render without source
    links rather than claiming the dirty content is committed.
    """
    revision = context_value.get("revision") if isinstance(context_value, dict) else None
    facts = candidate_evidence(context_value) if isinstance(context_value, dict) else None
    if not isinstance(revision, str) or len(revision) != FULL_SHA or any(char not in "0123456789abcdefABCDEF" for char in revision) or not facts:
        return None
    root = Path(repo).resolve()
    if not root.is_dir():
        return None

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    top_level = git("rev-parse", "--show-toplevel")
    remote = git("remote", "get-url", "origin")
    url = github_repository_url(remote or "")
    if top_level is None or Path(top_level.strip()).resolve() != root or url is None:
        return None
    revision = revision.lower()
    if git("cat-file", "-e", f"{revision}^{{commit}}") is None:
        return None
    for fact in facts:
        path = repo_relative_path(fact.get("path"))
        expected = fact.get("sha256")
        if path is None or not isinstance(expected, str) or len(expected) != 64 or any(char not in "0123456789abcdefABCDEF" for char in expected):
            return None
        content = git("show", f"{revision}:{path}")
        if content is None:
            return None
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual != expected.lower():
            return None
    return {"url": url, "revision": revision, "evidence_revision_verified": True}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def compact_text(value: Any, limit: int = 84) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def archify_id(prefix: str, ident: str) -> str:
    """Injectively map arbitrary Archctx IDs into Archify's ID grammar."""
    if not isinstance(ident, str) or not ident:
        raise ValueError("canonical id must be a non-empty string")
    return f"{prefix}-{ident.encode('utf-8').hex()}"


def only(value: dict[str, Any], allowed: set[str], subject: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{subject} has unsupported fields: {', '.join(extra)}")


def text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def point(value: Any, field: str, positive: bool = False) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        raise ValueError(f"{field} needs numeric [x, y]")
    if positive and not all(item > 0 for item in value):
        raise ValueError(f"{field} values must be positive")
    return value


def source_label(component: dict[str, Any], ident: str) -> str:
    value = component.get("name")
    return value if isinstance(value, str) and value.strip() else ident


def verified_repository(context_value: dict[str, Any] | None, repository: dict[str, Any] | None) -> dict[str, str] | None:
    """Accept only metadata returned by :func:`repository_evidence`."""
    if repository is None:
        return None
    if not isinstance(context_value, dict):
        raise ValueError("repository evidence needs a validated context")
    if not isinstance(repository, dict) or set(repository) != {"url", "revision", "evidence_revision_verified"}:
        raise ValueError("repository evidence must be the verified repository_evidence result")
    url, revision = repository.get("url"), repository.get("revision")
    context_revision = context_value.get("revision")
    if repository.get("evidence_revision_verified") is not True or not isinstance(url, str) or github_repository_url(url) != url:
        raise ValueError("repository evidence must use a verified public GitHub HTTPS URL")
    if not isinstance(revision, str) or len(revision) != FULL_SHA or any(char not in "0123456789abcdefABCDEF" for char in revision):
        raise ValueError("repository evidence must pin a full commit SHA")
    if not isinstance(context_revision, str) or context_revision.lower() != revision.lower():
        raise ValueError("repository evidence revision must match the validated context")
    return {"url": url, "revision": revision.lower()}


def context_component_map(context_value: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(context_value, dict) or not isinstance(context_value.get("components"), list):
        raise ValueError("repository evidence needs validated component facts")
    values: dict[str, dict[str, Any]] = {}
    for component in context_value["components"]:
        ident = component.get("id") if isinstance(component, dict) else None
        evidence = component.get("evidence") if isinstance(component, dict) else None
        if not isinstance(ident, str) or not ident or ident in values or not isinstance(evidence, list) or not evidence:
            raise ValueError("repository evidence needs one validated evidence list per component")
        values[ident] = component
    return values


def archify_sources(component: dict[str, Any], ident: str) -> list[dict[str, Any]]:
    """Keep at most Archify's three verified, line-addressable source refs."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for fact in component["evidence"]:
        path = repo_relative_path(fact.get("path")) if isinstance(fact, dict) else None
        line = fact.get("line") if isinstance(fact, dict) else None
        digest = fact.get("sha256") if isinstance(fact, dict) else None
        if path is None or not isinstance(line, int) or isinstance(line, bool) or line < 1 or not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError(f"repository evidence for {ident} is not a validated source fact")
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        result.append({"path": path, "line": line})
        if len(result) == 3:
            break
    if not result:
        raise ValueError(f"repository evidence for {ident} has no Archify source references")
    return result


def project(
    config: dict[str, Any],
    view: dict[str, Any],
    *,
    context_value: dict[str, Any] | None = None,
    repository: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_components = validated_components(config)
    source = {item["id"]: item for item in canonical_components}
    repository_metadata = verified_repository(context_value, repository)
    candidate_components = context_component_map(context_value) if repository_metadata else {}
    only(view, {"title", "subtitle", "nodes", "relations", "relation_variants"}, "view")
    nodes = view.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("view needs a non-empty nodes list")

    selected: list[str] = []
    components = []
    component_ids: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError("every view node needs an id")
        only(node, {"id", "type", "label", "sublabel", "tag", "pos", "size"}, f"view node {node['id']}")
        ident = node["id"]
        if ident not in source:
            raise ValueError(f"view references unknown Archctx component: {ident}")
        if ident in selected:
            raise ValueError(f"view repeats component: {ident}")
        selected.append(ident)
        canonical = source[ident]
        component_type = node.get("type", "backend")
        if not isinstance(component_type, str) or component_type not in COMPONENT_TYPES:
            raise ValueError(f"view node {ident} needs a supported Archify type")
        label = text(node.get("label", source_label(canonical, ident)), f"view node {ident} label")
        sublabel = node.get("sublabel")
        if sublabel is None:
            sublabel = compact_text(canonical.get("purpose") or ", ".join(str(tag) for tag in canonical.get("tags", [])))
        elif not isinstance(sublabel, str):
            raise ValueError(f"view node {ident} sublabel must be a string")
        tag = node.get("tag")
        if tag is not None and not isinstance(tag, str):
            raise ValueError(f"view node {ident} tag must be a string")
        archify_ident = archify_id("c", ident)
        component_ids[ident] = archify_ident
        component = {"id": archify_ident, "type": component_type, "label": label, "sublabel": sublabel, "pos": point(node.get("pos"), f"view node {ident} pos"), "size": point(node.get("size", [168, 64]), f"view node {ident} size", positive=True)}
        if tag is not None:
            component["tag"] = tag
        if repository_metadata:
            candidate = candidate_components.get(ident)
            if candidate is None:
                raise ValueError(f"repository evidence is missing validated component facts for {ident}")
            component["sources"] = archify_sources(candidate, ident)
        components.append(component)

    visible = set(selected)
    canonical_relations = [relation for relation in config.get("relations", []) if isinstance(relation, dict) and relation.get("from") in visible and relation.get("to") in visible]
    relations_by_id = {relation_id(relation): relation for relation in canonical_relations}
    variants = view.get("relation_variants", {})
    if not isinstance(variants, dict) or not all(isinstance(kind, str) and isinstance(variant, str) and variant in VARIANTS for kind, variant in variants.items()):
        raise ValueError("relation_variants must map kinds to supported Archify variants")
    selected_relations = view.get("relations")
    if selected_relations is None:
        selected_relations = [{"id": relation_id(relation)} for relation in canonical_relations]
    if not isinstance(selected_relations, list):
        raise ValueError("view relations must be a list when supplied")

    connections = []
    selected_relation_ids: set[str] = set()
    for relation in selected_relations:
        if not isinstance(relation, dict):
            raise ValueError("every view relation needs a canonical id")
        ident = relation.get("id")
        if not isinstance(ident, str) or not ident:
            raise ValueError("every view relation needs a canonical id")
        only(relation, {"id", "label", "variant"}, "view relation")
        if ident in selected_relation_ids:
            raise ValueError(f"view repeats canonical relation: {ident}")
        canonical = relations_by_id.get(ident)
        if canonical is None:
            raise ValueError(f"view relation is not canonical and visible: {ident}")
        selected_relation_ids.add(ident)
        label = relation.get("label", canonical["kind"])
        if not isinstance(label, str):
            raise ValueError(f"view relation {ident} label must be a string")
        variant = relation.get("variant", variants.get(canonical["kind"]))
        if variant is not None and (not isinstance(variant, str) or variant not in VARIANTS):
            raise ValueError(f"view relation {ident} needs a supported Archify variant")
        connection = {"id": archify_id("r", ident), "from": component_ids[canonical["from"]], "to": component_ids[canonical["to"]], "label": label}
        if variant is not None:
            connection["variant"] = variant
        connections.append(connection)

    title = view.get("title", "Canonical architecture")
    subtitle = view.get("subtitle", "Declared canonical architecture projection; source remains authoritative")
    meta = {"title": text(title, "view title"), "subtitle": text(subtitle, "view subtitle")}
    if repository_metadata:
        meta["repository"] = repository_metadata
    return {"schema_version": 1, "diagram_type": "architecture", "meta": meta, "components": components, "connections": connections}


def validate_source(config_path: Path, config: dict[str, Any]) -> None:
    _, _, failures = validate(repo_for(config_path, config), config)
    if failures:
        raise ValueError("Archctx source validation failed: " + "; ".join(failures[:3]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load(config_path)
    validate_source(config_path, config)
    result = project(config, load(Path(args.view)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({"status": "PASS", "source_validation": "PASS", "output": str(output), "component_count": len(result["components"]), "relation_count": len(result["connections"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
