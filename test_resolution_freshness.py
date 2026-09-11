"""Scoped resolution proofs; upstream extraction is deterministic fixture input."""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import archctx
import archctx_analysis_inputs as inputs
import archctx_understand as ua
import test_native_understand as native_fixture
import test_project_understanding as project_fixture


class ResolutionFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.f = project_fixture.ProjectUnderstandingTest(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def capture(self, *, dependencies=(), checks=None, supported=True, missing=False, resolver=False):
        f = self.f
        path = f.capture("b.py", "resolution-fixture", dependencies=dependencies, external=("json",))
        receipt = archctx.load(path)
        receipt["inventory_coverage"] = inputs.inventory(f.repo)["coverage"]
        receipt["resolution"] = {"version": 1, "files": {
            relative: {"supported": True, "checks": {}} for relative in receipt["dependencies"]}}
        receipt["resolution"]["files"]["b.py"] = {
            "supported": supported,
            "checks": checks if checks is not None else {"json.py": False, "json/__init__.py": False}}
        if missing:
            receipt["dependencies"]["b.py"].update(external=[], coverage="partial", unresolvedLocal=[".missing"])
        if resolver:
            receipt["resolver_hashes"] = {"tsconfig.json": archctx.sha((f.repo / "tsconfig.json").read_bytes())}
        archctx.atomic(path, receipt)
        return path

    def reviewed(self, path):
        f = self.f
        result = f.publish(path)
        self.assertEqual(ua.review(f.config, str(f.state), result["candidates"][0]["id"],
            bindings=["component:b-owner"], analysis=result["analysis_id"])["status"], "PASS")
        return result

    def test_unrelated_addition_and_deletion_preserve_review_and_need_no_provider(self):
        f = self.f
        (f.repo / "b.py").write_bytes(b"import json\nVALUE = 1\n")
        result = self.reviewed(self.capture())
        path, receipt = ua.analysis_receipt(f.state, result["analysis_id"])
        retained = path.read_bytes()
        last_good = archctx.last_path(f.state).read_bytes()
        for edit in (lambda: (f.repo / "unrelated.py").write_bytes(b"UNRELATED = True\n"),
                     lambda: (f.repo / "uncovered.py").unlink()):
            edit()
            self.assertNotEqual(inputs.inventory(f.repo)["hash"], receipt["inventory_hash"])
            with patch.object(ua, "prepare", side_effect=AssertionError("unrelated edit prepared semantics")), \
                 patch.object(ua, "run_upstream", side_effect=AssertionError("read called provider")), \
                 patch.object(ua, "provider_root", side_effect=AssertionError("reuse needs no provider")):
                shown = f.show(analysis=result["scope_id"])
                self.assertEqual((shown["status"], shown["dependency_status"]), ("FRESH", "CAPTURED_STATIC"), shown)
                self.assertEqual(shown["changed_files"], [])
                self.assertEqual(shown["candidates"][0]["review_state"], "accepted")
                reused = ua.native_understand(f.config, str(f.state), files=["b.py"])
                self.assertEqual(reused["status"], "REUSED")
                self.assertIn("no repeated", reused["next_action"])
                ua.check_publication(f.repo, f.state, ua.publication_proof(receipt))
            self.assertEqual(path.read_bytes(), retained)
            self.assertEqual(archctx.last_path(f.state).read_bytes(), last_good)

    def test_dependency_byte_change_still_invalidates_its_review(self):
        f = self.f
        (f.repo / "b.py").write_bytes(b"import a\nVALUE = 1\n")
        result = self.reviewed(self.capture(dependencies=("a.py",), checks={"a.py": True}))
        (f.repo / "a.py").write_bytes(b"VALUE = 2\n")
        shown = f.show(analysis=result["scope_id"])
        self.assertEqual((shown["status"], shown["changed_files"]), ("STALE", ["a.py"]))
        finding = shown["candidates"][0]
        self.assertEqual((finding["affected_files"], finding["review_state"]), (["a.py"], "unreviewed"))
        self.assertEqual(finding["bindings"], [])
        with self.assertRaisesRegex(ValueError, "stale|source|moved"):
            ua.review(f.config, str(f.state), finding["id"], bindings=["component:b-owner"], analysis=result["analysis_id"])

    def test_new_shadow_names_the_owner_and_leaves_independent_scope_reviewed(self):
        f = self.f
        a = f.publish(f.capture("a.py", "independent"))
        self.assertEqual(ua.review(f.config, str(f.state), a["candidates"][0]["id"],
            bindings=["component:a-owner"], analysis=a["analysis_id"])["status"], "PASS")
        (f.repo / "b.py").write_bytes(b"import json\nVALUE = 1\n")
        b = self.reviewed(self.capture())
        (f.repo / "json.py").write_bytes(b"VALUE = 'shadows stdlib'\n")
        shown = f.show(analysis=b["scope_id"])
        marker = "@dependency-resolution:b.py"
        self.assertEqual((shown["status"], shown["changed_files"]), ("STALE", [marker]), shown)
        self.assertEqual(shown["candidates"][0]["affected_files"], [marker])
        self.assertEqual(shown["candidates"][0]["review_state"], "unreviewed")
        independent = f.show(analysis=a["scope_id"])
        self.assertEqual((independent["status"], independent["candidates"][0]["review_state"]), ("FRESH", "accepted"))

    def test_missing_import_becoming_resolvable_is_not_an_unrelated_addition(self):
        f = self.f
        (f.repo / "b.py").write_bytes(b"from . import missing\nVALUE = 1\n")
        result = f.publish(self.capture(checks={"missing.py": False, "missing/__init__.py": False}, missing=True))
        self.assertEqual(result["dependency_status"], "UNKNOWN")
        (f.repo / "missing.py").write_bytes(b"VALUE = 2\n")
        shown = f.show(analysis=result["scope_id"])
        self.assertEqual((shown["status"], shown["changed_files"]), ("STALE", ["@dependency-resolution:b.py"]))
        self.assertEqual(shown["candidates"][0]["review_state"], "unreviewed")

    def test_captured_resolver_config_changes_still_invalidate(self):
        f = self.f
        (f.repo / "tsconfig.json").write_bytes(b'{"compilerOptions":{}}\n')
        result = self.reviewed(self.capture(resolver=True))
        (f.repo / "tsconfig.json").write_bytes(b'{"compilerOptions":{"baseUrl":"src"}}\n')
        shown = f.show(analysis=result["scope_id"])
        self.assertEqual((shown["status"], shown["changed_files"]), ("STALE", ["tsconfig.json"]))
        self.assertEqual(shown["candidates"][0]["review_state"], "unreviewed")

    def test_unsupported_resolution_stays_unknown_and_inventory_conservative(self):
        f = self.f
        result = f.publish(self.capture(supported=False))
        self.assertEqual(result["dependency_status"], "UNKNOWN")
        (f.repo / "unrelated.py").write_bytes(b"VALUE = 2\n")
        shown = f.show(analysis=result["scope_id"])
        self.assertEqual((shown["status"], shown["dependency_status"]), ("STALE", "UNKNOWN"))
        self.assertEqual(shown["changed_files"], ["@dependency-resolution"])

    def test_incomplete_current_inventory_cannot_reuse_complete_resolution_proof(self):
        f = self.f
        result = f.publish(self.capture())
        inventory = inputs.inventory
        def incomplete(*args, **kwargs):
            value = inventory(*args, **kwargs)
            value["coverage"].update(complete=False, omitted_at_least=1)
            value["hash"] = archctx.semantic({"paths": value["paths"], "coverage": value["coverage"]})
            return value
        with patch.object(inputs, "inventory", side_effect=incomplete):
            shown = f.show(analysis=result["scope_id"])
        self.assertEqual((shown["status"], shown["dependency_status"]), ("STALE", "UNKNOWN"))
        self.assertEqual(shown["changed_files"], ["@dependency-resolution"])

    def test_missing_captured_inventory_coverage_cannot_claim_known_dependencies(self):
        f = self.f
        path = self.capture()
        receipt = archctx.load(path)
        receipt.pop("inventory_coverage")
        archctx.atomic(path, receipt)
        result = f.publish(path)
        self.assertEqual(result["dependency_status"], "UNKNOWN")
        self.assertTrue(any("inventory" in reason for reason in result["unknown_dependencies"]))

    def test_source_move_during_inventory_check_is_not_reported_fresh(self):
        f = self.f
        path = self.capture()
        receipt = archctx.load(path)
        inventory = inputs.inventory
        calls = []
        def moving(*args, **kwargs):
            value = inventory(*args, **kwargs)
            calls.append(1)
            if len(calls) == 2:
                (f.repo / "b.py").write_bytes(b"VALUE = 999\n")
            return value
        with patch.object(inputs, "inventory", side_effect=moving):
            self.assertIn("b.py", ua.verify_sources(f.repo, receipt))
        self.assertEqual(len(calls), 2)

    def test_shadow_created_after_inventory_snapshot_cannot_publish_as_current(self):
        f = self.f
        path = self.capture()
        receipt = archctx.load(path)
        inventory = inputs.inventory
        calls = []
        def moving(*args, **kwargs):
            value = inventory(*args, **kwargs)
            calls.append(1)
            if len(calls) == 2:
                (f.repo / "json.py").write_bytes(b"VALUE = 'late shadow'\n")
            return value
        with patch.object(inputs, "inventory", side_effect=moving):
            self.assertIn("@dependency-resolution:b.py", ua.verify_sources(f.repo, receipt))
        self.assertEqual(len(calls), 2)

    def test_unrelated_creation_during_final_inventory_is_not_a_semantic_change(self):
        f = self.f
        receipt = archctx.load(self.capture())
        inventory = inputs.inventory
        calls = []
        def moving(*args, **kwargs):
            value = inventory(*args, **kwargs)
            calls.append(1)
            if len(calls) == 2:
                (f.repo / "unrelated.py").write_bytes(b"VALUE = 999\n")
            return value
        with patch.object(inputs, "inventory", side_effect=moving):
            self.assertEqual(ua.verify_sources(f.repo, receipt), [])
        self.assertEqual(len(calls), 2)


class NativeResolutionSupplementTest(unittest.TestCase):
    def test_legacy_scope_gets_mechanical_evidence_without_repeating_full_semantics(self):
        f = native_fixture.NativeUnderstandTest(methodName="runTest")
        self.addCleanup(f.doCleanups)
        f.setUp()
        f.files = ["a.py"]
        (f.repo / "a.py").write_bytes(b"import os\nvalue = 1\n")
        supplement = False

        @contextmanager
        def capture(*args):
            with f.capture(*args) as captured:
                captured["dependencies"]["a.py"]["external"] = ["os"]
                if supplement:
                    captured["resolution"] = {"version": 1, "files": {"a.py": {
                        "supported": True, "checks": {"os.py": False, "os/__init__.py": False}}}}
                yield captured

        with patch.object(inputs, "capture", side_effect=capture):
            system = f.system_stage()
            f.write_system(system)
            full = archctx.load(Path(system["write_result"]))
            full["tour"] = [{"order": i + 1, "title": f"Step {i}", "description": "Ordered fixture semantics",
                             "nodeIds": ["file:a.py"]} for i in range(11)]
            archctx.atomic(Path(system["write_result"]), full)
            self.assertEqual(f.advance(system)["status"], "FINDINGS_READY")
            old_path, old = ua.analysis_receipt(f.directory)
            historical = old_path.read_bytes(), ua.graph_file(f.directory, old).read_bytes()
            self.assertEqual(ua.review(f.config, None, old["candidates"][0]["id"],
                bindings=["component:entry"], analysis=old["analysis_id"])["status"], "PASS")
            last_good = archctx.last_path(f.directory).read_bytes()
            before_calls = f.calls.copy()
            (f.repo / "unrelated.py").write_bytes(b"VALUE = 2\n")
            self.assertEqual(ua.discoveries(f.config, None)["changed_files"], ["@dependency-resolution"])
            supplement = True
            result = f.advance()
            self.assertEqual(result["status"], "FINDINGS_READY", result)
            self.assertEqual(result["analysis"]["status"], "FRESH")
            self.assertEqual(result["analysis"]["candidates"][0]["review_state"], "accepted")
            self.assertIn("no repeated", result["next_action"])
            self.assertNotEqual(result["analysis_id"], old["analysis_id"])
            _, receipt = ua.analysis_receipt(f.directory)
            self.assertTrue(receipt["resolution"]["files"]["a.py"]["supported"])
            self.assertEqual(receipt["tour"], full["tour"])
            self.assertEqual(ua.local_json(ua.graph_file(f.directory, receipt))["tour"], full["tour"])
            self.assertEqual(result["analysis"]["tour"], full["tour"][:8])
            self.assertEqual(result["analysis"]["omitted_tour_steps"], 3)
            self.assertEqual(f.calls["extract:a.py"], before_calls["extract:a.py"])
            self.assertEqual((old_path.read_bytes(), ua.graph_file(f.directory, old).read_bytes()), historical)
            self.assertEqual(archctx.last_path(f.directory).read_bytes(), last_good)


if __name__ == "__main__":
    unittest.main()
