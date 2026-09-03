import unittest

from calm_query import CalmError, compact, decode_response, endpoint, graph_ready


class CalmQueryTest(unittest.TestCase):
    def test_compact_preserves_count_confidence_and_caps_edges(self):
        edges = [{"symbol": f"caller-{i}", "edge_confidence": "resolved", "edge_kind": "call"} for i in range(13)]
        value = compact({"result": {"structuredContent": {"direct": edges, "direct_count": 13, "direct_by_confidence": {"resolved": 13}, "edges_ready": True}}}, "upstream")
        self.assertEqual(value["direct_count"], 13)
        self.assertEqual(len(value["edges"]), 12)
        self.assertTrue(value["truncated"])

    def test_loopback_endpoint_and_sse_response_are_supported(self):
        self.assertEqual(endpoint("http://127.0.0.1:9123"), "http://127.0.0.1:9123/mcp")
        value = decode_response(b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n\n", "text/event-stream")
        self.assertEqual(value["id"], 1)
        with self.assertRaises(CalmError):
            endpoint("https://example.com/mcp")

    def test_graph_ready_requires_calm_watcher_freshness(self):
        fresh = {"result": {"structuredContent": {"indexing_phase": "ready", "files_indexed": 2, "files_total": 2, "edges_ready": True, "derived_status": {"overall": "ready"}, "watcher": {"armed": True, "freshness": "fresh"}}}}
        graph_ready(fresh)
        fresh["result"]["structuredContent"]["watcher"]["freshness"] = "stale"
        with self.assertRaises(CalmError):
            graph_ready(fresh)
        fresh["result"]["structuredContent"]["watcher"]["freshness"] = "fresh"
        fresh["result"]["structuredContent"].pop("files_total")
        with self.assertRaises(CalmError):
            graph_ready(fresh)


if __name__ == "__main__":
    unittest.main()
