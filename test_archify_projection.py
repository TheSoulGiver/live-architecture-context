import unittest

from prototype.archctx_to_archify import project


class ArchifyProjectionTest(unittest.TestCase):
    def test_projects_only_accepted_components_and_relations(self):
        config = {
            "version": 1,
            "components": [
                {"id": "service", "name": "Service", "purpose": "Handles requests", "tags": ["runtime"]},
                {"id": "store", "name": "Store", "purpose": "Durable truth", "tags": ["data"]},
                {"id": "ignored", "name": "Ignored"},
            ],
            "relations": [{"from": "service", "to": "store", "kind": "reads"}, {"from": "store", "to": "ignored", "kind": "writes"}],
        }
        result = project(config, {"title": "Demo", "nodes": [{"id": "service", "pos": [0, 0]}, {"id": "store", "pos": [200, 0]}], "relation_variants": {"reads": "emphasis"}})
        self.assertEqual([item["id"] for item in result["components"]], ["service", "store"])
        self.assertEqual(result["components"][0]["sublabel"], "Handles requests")
        self.assertEqual(result["connections"], [{"id": "service--reads--store", "from": "service", "to": "store", "label": "reads", "variant": "emphasis"}])

    def test_rejects_unknown_view_component(self):
        with self.assertRaisesRegex(ValueError, "unknown Archctx component"):
            project({"version": 1, "components": []}, {"nodes": [{"id": "missing", "pos": [0, 0]}]})

    def test_rejects_a_view_relation_not_in_canonical_architecture(self):
        config = {"version": 1, "components": [{"id": "a"}, {"id": "b"}], "relations": []}
        view = {"nodes": [{"id": "a", "pos": [0, 0]}, {"id": "b", "pos": [200, 0]}], "relations": [{"from": "a", "to": "b"}]}
        with self.assertRaisesRegex(ValueError, "not canonical"):
            project(config, view)

    def test_rejects_duplicate_derived_relation_ids(self):
        config = {"version": 1, "components": [{"id": "a"}, {"id": "b"}], "relations": [{"from": "a", "to": "b", "kind": "uses"}, {"from": "a", "to": "b", "kind": "uses"}]}
        view = {"nodes": [{"id": "a", "pos": [0, 0]}, {"id": "b", "pos": [200, 0]}]}
        with self.assertRaisesRegex(ValueError, "unique id"):
            project(config, view)


if __name__ == "__main__":
    unittest.main()
