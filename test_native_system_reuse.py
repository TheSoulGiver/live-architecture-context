"""Full retained semantics versus bounded replies; upstream is a fixture, not a model."""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import archctx
import archctx_analysis_inputs as inputs
import archctx_understand as ua
import test_native_understand as native_fixture


class NativeSystemReuseTest(unittest.TestCase):
    def setUp(self):
        self.fixture = native_fixture.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

        @contextmanager
        def capture(*args):
            with self.fixture.capture(*args) as captured:
                # A static external import makes resolution-inventory changes
                # relevant without changing any selected file or local import.
                for row in captured["dependencies"].values():
                    row["external"] = ["os"]
                yield captured

        context = patch.object(inputs, "capture", side_effect=capture)
        context.start()
        self.addCleanup(context.stop)

    def initial_system(self, layer_count=1):
        f = self.fixture
        if layer_count > 1:
            f.files = [f"unit{i}.py" for i in range(layer_count)]
        for relative in f.files:
            (f.repo / relative).write_bytes(b"import os\nvalue = 1\n")
        pending = f.advance()
        while pending.get("stage") == "source_understanding":
            f.write_files(pending)
            pending = f.advance(pending)
        ids = ["file:" + path for path in sorted(f.files)]
        layers = [{"id": f"layer-{i}", "name": f"Layer {i}", "description": "Fixture coverage",
                   "nodeIds": ids if layer_count == 1 else [ids[i]]} for i in range(layer_count)]
        tour = [{"order": i + 1, "title": f"Step {i}", "description": "Ordered fixture semantics",
                 "nodeIds": [ids[i % len(ids)]]} for i in range(11)]
        system = {"graph_sha256": pending["graph_sha256"], "layers": layers, "tour": tour}
        archctx.atomic(Path(pending["write_result"]), system)
        self.assertEqual(f.advance(pending)["status"], "FINDINGS_READY")
        _, receipt = ua.analysis_receipt(f.directory)
        return system, receipt

    def assert_complete(self, result, system):
        f = self.fixture
        self.assertEqual(result["status"], "FINDINGS_READY", result)
        _, receipt = ua.analysis_receipt(f.directory)
        graph = ua.local_json(ua.graph_file(f.directory, receipt))
        persisted = ua.local_json(f.directory / "understand/runs" / receipt["analysis_id"] / "system.json")
        for value in (receipt, graph, persisted):
            self.assertEqual(value["layers"], system["layers"])
            self.assertEqual(value["tour"], system["tour"])
        self.assertEqual(result["analysis"]["tour"], system["tour"][:8])
        self.assertEqual(result["analysis"]["omitted_tour_steps"], 3)
        self.assertEqual(result["analysis"]["candidate_count"], len(system["layers"]))
        self.assertEqual(sorted(receipt["source_hashes"]), sorted(f.files))
        self.assertEqual(archctx.last_path(f.directory).read_bytes(), f.last_good)

    def reuse_after_inventory_change(self, layer_count):
        f = self.fixture
        system, previous = self.initial_system(layer_count)
        historical = ua.graph_file(f.directory, previous)
        original = historical.read_bytes()
        before = f.calls.copy()
        (f.repo / "new_unselected.py").write_bytes(b"# resolution inventory changed\n")
        result = f.advance()
        self.assert_complete(result, system)
        self.assertNotEqual(result["analysis_id"], previous["analysis_id"])
        for path in f.files:
            self.assertEqual(f.calls["extract:" + path], before["extract:" + path])
        receipt = ua.local_json(f.directory / "understand/runs" / result["analysis_id"] / "input.json")
        self.assertEqual(receipt["incremental"]["reused_files"], sorted(f.files))
        self.assertEqual(len(receipt["previous_system"]["layers"]), min(layer_count, 8))
        self.assertEqual(len(receipt["previous_system"]["tour"]), 8)
        self.assertEqual(historical.read_bytes(), original)

    def test_eleven_ordered_tour_steps_survive_automatic_native_reuse(self):
        self.reuse_after_inventory_change(1)

    def test_nine_layers_keep_full_file_coverage_during_native_reuse(self):
        self.reuse_after_inventory_change(9)

    def test_unavailable_or_changed_full_history_requests_complete_semantics(self):
        f = self.fixture
        system, previous = self.initial_system()
        (f.repo / "new_unselected.py").write_bytes(b"# resolution inventory changed\n")
        prepared = ua.prepare(f.config, None, f.repo / "fixture-provider", f.files)
        graph = ua.graph_file(f.directory, previous)
        # Preserve original fixture evidence, but make both known full copies
        # unavailable to the resumer. Never substitute the eight-step summary.
        fallback = graph.parents[1] / "graph.json"
        graph.rename(graph.with_suffix(".saved"))
        fallback.rename(fallback.with_suffix(".saved"))
        for damaged in (None, {"nodes": [], "edges": [], "layers": [], "tour": []}):
            with self.subTest(damaged=damaged is not None):
                if damaged is not None:
                    archctx.atomic(graph, damaged)
                pending = f.advance(prepared)
                self.assertEqual((pending["status"], pending["stage"]), ("NEEDS_AGENT", "system_understanding"))
                self.assertEqual(pending["previous_system"]["omitted_tour_steps"], 3)
                self.assertFalse(Path(pending["write_result"]).exists())
        archctx.atomic(Path(pending["write_result"]), {**system, "graph_sha256": pending["graph_sha256"]})
        self.assert_complete(f.advance(pending), system)

    def test_changed_semantics_are_reviewed_and_persisted_in_full(self):
        f = self.fixture
        system, previous = self.initial_system(9)
        (f.repo / f.files[-1]).write_bytes(b"import os\nvalue = 2\n")
        pending = f.advance()
        f.write_files(pending)
        batch_path = Path(pending["work"][0]["write_result"])
        batch = ua.local_json(batch_path)
        batch["nodes"][0]["summary"] = "Changed source understanding"
        archctx.atomic(batch_path, batch)
        pending = f.advance(pending)
        self.assertEqual((pending["status"], pending["stage"]), ("NEEDS_AGENT", "system_understanding"))
        self.assertEqual(pending["previous_system"]["omitted_layers"], 1)
        self.assertEqual(pending["previous_system"]["omitted_tour_steps"], 3)
        system["layers"][-1]["description"] = "Changed semantic responsibility"
        system["tour"][-1]["description"] = "Changed final semantic step"
        archctx.atomic(Path(pending["write_result"]), {**system, "graph_sha256": pending["graph_sha256"]})
        self.assert_complete(f.advance(pending), system)
        _, current = ua.analysis_receipt(f.directory)
        self.assertNotEqual(current["candidates"][-1]["content_revision"], previous["candidates"][-1]["content_revision"])


if __name__ == "__main__":
    unittest.main()
