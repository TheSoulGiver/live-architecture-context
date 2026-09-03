#!/usr/bin/env python3
"""Project an accepted Archctx view into Archify's typed architecture IR."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def compact_text(value: Any, limit: int = 84) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def project(config: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    if config.get("version") != 1:
        raise ValueError("Archctx config version 1 is required")
    source = {item.get("id"): item for item in config.get("components", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    nodes = view.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("view needs a non-empty nodes list")
    selected: list[str] = []
    components = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError("every view node needs an id")
        ident = node["id"]
        if ident not in source:
            raise ValueError(f"view references unknown Archctx component: {ident}")
        position = node.get("pos")
        if not isinstance(position, list) or len(position) != 2 or not all(isinstance(value, (int, float)) for value in position):
            raise ValueError(f"view node {ident} needs numeric pos [x, y]")
        if ident in selected:
            raise ValueError(f"view repeats component: {ident}")
        selected.append(ident)
        canonical = source[ident]
        component = {
            "id": ident,
            "type": node.get("type", "backend"),
            "label": node.get("label", canonical.get("name", ident)),
            "sublabel": node.get("sublabel", compact_text(canonical.get("purpose") or ", ".join(canonical.get("tags", [])))),
            "pos": position,
            "size": node.get("size", [168, 64]),
        }
        if isinstance(node.get("tag"), str):
            component["tag"] = node["tag"]
        components.append(component)
    visible = set(selected)
    canonical_relations = [relation for relation in config.get("relations", []) if isinstance(relation, dict) and relation.get("from") in visible and relation.get("to") in visible]
    variants = view.get("relation_variants", {})
    selected_relations = view.get("relations")
    if selected_relations is None:
        selected_relations = canonical_relations
    if not isinstance(selected_relations, list):
        raise ValueError("view relations must be a list when supplied")
    connections = []
    connection_ids: set[str] = set()
    for relation in selected_relations:
        if not isinstance(relation, dict) or not isinstance(relation.get("from"), str) or not isinstance(relation.get("to"), str):
            raise ValueError("every view relation needs from and to")
        if relation["from"] not in visible or relation["to"] not in visible:
            raise ValueError("view relation must use selected components")
        kind = relation.get("kind")
        matches = [item for item in canonical_relations if item.get("from") == relation["from"] and item.get("to") == relation["to"] and (kind is None or item.get("kind") == kind)]
        if not matches:
            raise ValueError(f"view relation is not canonical: {relation['from']} -> {relation['to']}")
        canonical = matches[0]
        relation_id = canonical.get("id")
        if not isinstance(relation_id, str) or not relation_id:
            relation_id = f"{canonical['from']}--{canonical.get('kind', 'relates-to')}--{canonical['to']}"
        if relation_id in connection_ids:
            raise ValueError(f"canonical relation needs a unique id: {relation_id}")
        connection_ids.add(relation_id)
        connection = {"id": relation_id, "from": relation["from"], "to": relation["to"], "label": relation.get("label", canonical.get("kind", "relates-to"))}
        for key in ("variant", "fromSide", "toSide", "route", "via", "labelDx", "labelDy", "labelAt", "labelSegment"):
            if key in relation:
                connection[key] = relation[key]
        variant = variants.get(canonical.get("kind")) if isinstance(variants, dict) else None
        if "variant" not in connection and isinstance(variant, str):
            connection["variant"] = variant
        connections.append(connection)
    return {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {
            "title": view.get("title", "Canonical architecture"),
            "subtitle": view.get("subtitle", "Accepted architecture projection; source remains authoritative"),
        },
        "components": components,
        "connections": connections,
        "boundaries": view.get("boundaries", []),
        "cards": view.get("cards", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    result = project(load(Path(args.config)), load(Path(args.view)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "output": str(output), "component_count": len(result["components"]), "relation_count": len(result["connections"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
