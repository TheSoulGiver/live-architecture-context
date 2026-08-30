import unittest

from calm_query import compact


class CalmQueryTest(unittest.TestCase):
    def test_compact_preserves_count_confidence_and_caps_edges(self):
        edges = [{"symbol": f"caller-{i}", "edge_confidence": "resolved", "edge_kind": "call"} for i in range(13)]
        value = compact({"result": {"structuredContent": {"direct": edges, "direct_count": 13, "direct_by_confidence": {"resolved": 13}, "edges_ready": True}}}, "upstream")
        self.assertEqual(value["direct_count"], 13)
        self.assertEqual(len(value["edges"]), 12)
        self.assertTrue(value["truncated"])


if __name__ == "__main__":
    unittest.main()
