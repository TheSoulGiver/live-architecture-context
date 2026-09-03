#!/usr/bin/env python3
"""Project a declared Archctx view into Archify's typed architecture IR."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from archctx import components as validated_components
from archctx import relation_id, repo_for, validate


COMPONENT_TYPES = {"frontend", "backend", "database", "cloud", "security", "messagebus", "external"}
VARIANTS = {"default", "emphasis", "security", "dashed"}


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


def project(config: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    canonical_components = validated_components(config)
    source = {item["id"]: item for item in canonical_components}
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
    return {"schema_version": 1, "diagram_type": "architecture", "meta": {"title": text(title, "view title"), "subtitle": text(subtitle, "view subtitle")}, "components": components, "connections": connections}


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
