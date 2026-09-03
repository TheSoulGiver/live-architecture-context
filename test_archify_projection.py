import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archctx_to_archify import archify_id, project, validate_source


def component(ident, **extra):
    return {"id": ident, "evidence": [{"path": "src/example.py", "contains": "example"}], **extra}


class ArchifyProjectionTest(unittest.TestCase):
    def test_projects_only_declared_components_and_relations(self):
        config = {
            "version": 1,
            "components": [
                component("service", name="Service", purpose="Handles requests", tags=["runtime"]),
                component("store", name="Store", purpose="Durable truth", tags=["data"]),
                component("ignored", name="Ignored"),
            ],
            "relations": [{"from": "service", "to": "store", "kind": "reads"}, {"from": "store", "to": "ignored", "kind": "writes"}],
        }
        result = project(config, {"title": "Demo", "nodes": [{"id": "service", "pos": [0, 0]}, {"id": "store", "pos": [200, 0]}], "relation_variants": {"reads": "emphasis"}})
        self.assertEqual([item["id"] for item in result["components"]], [archify_id("c", "service"), archify_id("c", "store")])
        self.assertEqual(result["components"][0]["sublabel"], "Handles requests")
        self.assertEqual(result["connections"], [{"id": archify_id("r", "service--reads--store"), "from": archify_id("c", "service"), "to": archify_id("c", "store"), "label": "reads", "variant": "emphasis"}])

    def test_projects_invalid_archctx_ids_to_archify_safe_ids(self):
        config = {"version": 1, "components": [component("service.api"), component("2nd-store")], "relations": [{"id": "service.api -> 2nd-store", "from": "service.api", "to": "2nd-store", "kind": "reads"}]}
        result = project(config, {"nodes": [{"id": "service.api", "pos": [0, 0]}, {"id": "2nd-store", "pos": [200, 0]}]})
        self.assertRegex(result["components"][0]["id"], r"^[a-zA-Z][a-zA-Z0-9_-]*$")
        self.assertEqual(result["connections"][0]["from"], result["components"][0]["id"])
        self.assertEqual(result["connections"][0]["to"], result["components"][1]["id"])

    def test_rejects_unknown_view_component(self):
        with self.assertRaisesRegex(ValueError, "unknown Archctx component"):
            project({"version": 1, "components": [component("known")]}, {"nodes": [{"id": "missing", "pos": [0, 0]}]})

    def test_rejects_a_view_relation_not_in_canonical_architecture(self):
        config = {"version": 1, "components": [component("a"), component("b")], "relations": []}
        view = {"nodes": [{"id": "a", "pos": [0, 0]}, {"id": "b", "pos": [200, 0]}], "relations": [{"id": "missing"}]}
        with self.assertRaisesRegex(ValueError, "not canonical"):
            project(config, view)

    def test_selects_parallel_relations_by_canonical_id(self):
        config = {"version": 1, "components": [component("a"), component("b")], "relations": [{"id": "read-path", "from": "a", "to": "b", "kind": "uses"}, {"id": "write-path", "from": "a", "to": "b", "kind": "uses"}]}
        view = {"nodes": [{"id": "a", "pos": [0, 0]}, {"id": "b", "pos": [200, 0]}], "relations": [{"id": "write-path", "label": "writes"}]}
        result = project(config, view)
        self.assertEqual(result["connections"], [{"id": archify_id("r", "write-path"), "from": archify_id("c", "a"), "to": archify_id("c", "b"), "label": "writes"}])

    def test_rejects_ambiguous_relation_selection_without_id(self):
        config = {"version": 1, "components": [component("a"), component("b")], "relations": [{"id": "one", "from": "a", "to": "b", "kind": "uses"}, {"id": "two", "from": "a", "to": "b", "kind": "uses"}]}
        view = {"nodes": [{"id": "a", "pos": [0, 0]}, {"id": "b", "pos": [200, 0]}], "relations": [{"from": "a", "to": "b"}]}
        with self.assertRaisesRegex(ValueError, "canonical id"):
            project(config, view)

    def test_rejects_invalid_archify_view_fields(self):
        with self.assertRaisesRegex(ValueError, "supported Archify type"):
            project({"version": 1, "components": [component("a")]}, {"nodes": [{"id": "a", "type": "avatar", "pos": [0, 0]}]})

    def test_rejects_config_without_required_archctx_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidence is required"):
            project({"version": 1, "components": [{"id": "a"}]}, {"nodes": [{"id": "a", "pos": [0, 0]}]})

    def test_cli_source_validation_fails_closed_before_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "example.py").write_text("def example(): pass\n", encoding="utf-8")
            config_path = root / "architecture.json"
            config = {"version": 1, "repo": ".", "components": [component("a")], "relations": []}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            view_path = root / "view.json"
            view_path.write_text(json.dumps({"nodes": [{"id": "a", "pos": [0, 0]}]}), encoding="utf-8")
            output_path = root / "output.json"
            script = Path(__file__).parent / "archctx_to_archify.py"
            passed = subprocess.run([sys.executable, str(script), "--config", str(config_path), "--view", str(view_path), "--output", str(output_path)], capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertTrue(output_path.is_file())
            (root / "src" / "example.py").write_text("def changed(): pass\n", encoding="utf-8")
            self.assertRaisesRegex(ValueError, "source validation failed", validate_source, config_path, config)
            blocked = subprocess.run([sys.executable, str(script), "--config", str(config_path), "--view", str(view_path), "--output", str(root / "blocked.json")], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertFalse((root / "blocked.json").exists())
