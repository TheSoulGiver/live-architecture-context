"""Declared review scopes, not runtime-impact or natural-adoption evidence."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
from archctx_development import DevelopmentObserver


def component(ident, path=None):
    return {"id": ident, "purpose": ident, "evidence": [{"path": path or f"{ident}.py", "contains": ident}]}


def relation(source, target, kind="calls", **extra):
    return {"id": f"{source}-{target}", "from": source, "to": target, "kind": kind, **extra}


def accepted(config):
    facts = {c["id"]: [{**e, "line": 1, "sha256": "a" * 64} for e in c["evidence"]] for c in config["components"]}
    edges = [[{**e, "line": 1, "sha256": "a" * 64} for e in r.get("evidence", [])] for r in config["relations"]]
    return {"context_hash": "b" * 64, "config_hash": archctx.semantic(config),
            "context": archctx.context(config, "fixture-revision", facts, edges)}


class ImpactTest(unittest.TestCase):
    def setUp(self):
        self.config = {"version": 1, "repo": ".", "components": [component(x) for x in "ABC"],
                       "relations": [relation("A", "B", evidence=[{"path": "wiring.py", "contains": "A(B)"}]), relation("B", "C")]}
        self.record = accepted(self.config)

    def scope(self, paths, config=None, **kwargs):
        return archctx.change_scope(self.record, self.config if config is None else config, paths,
                                    config_path_relative="architecture.json", freshness="fresh", **kwargs)

    def test_chain_cycles_and_evidence_do_not_claim_runtime_impact(self):
        result = self.scope(["B.py"])
        self.assertEqual(result["contract"], "declared_change_scope/v1")
        self.assertEqual(result["proof"], "declared_review_scope_not_runtime_impact")
        self.assertEqual(result["accepted_context_hash"], self.record["context_hash"])
        self.assertIsNone(result["working"])
        scope = result["accepted"]
        self.assertEqual(scope["direct_components"], ["B"])
        self.assertEqual(scope["dependencies"], ["C"])
        self.assertEqual(scope["dependents"], ["A"])
        edge = next(r for r in scope["relations"] if r["id"] == "A-B")
        self.assertEqual((edge["from"], edge["to"], edge["kind"], edge["dependency"], edge["semantics"]),
                         ("A", "B", "calls", "from_to", "kind_default"))
        self.assertEqual(edge["evidence"][0]["path"], "wiring.py")
        self.assertEqual(edge["evidence"][0]["line"], 1)
        self.assertTrue(edge["evidence_state"])
        self.config["relations"].append(relation("C", "B"))
        self.record = accepted(self.config)
        cycle = self.scope(["B.py"])["accepted"]
        self.assertEqual(cycle["dependencies"], ["C"])
        self.assertEqual(cycle["dependents"], ["A", "C"])

    def test_unknown_none_and_explicit_reverse_keep_raw_direction(self):
        self.config["components"] += [component(x) for x in "DEF"]
        self.config["relations"] += [relation("B", "D", "ownership"),
                                     relation("B", "E", "guidance", dependency="none"),
                                     relation("F", "B", "supplies", dependency="to_from")]
        self.record = accepted(self.config)
        scope = self.scope(["B.py"])["accepted"]
        self.assertEqual(scope["dependencies"], ["C", "F"])
        self.assertEqual(scope["dependents"], ["A"])
        edges = {r["id"]: r for r in scope["relations"]}
        self.assertEqual((edges["B-D"]["dependency"], edges["B-D"]["semantics"]), ("unclassified", "unclassified"))
        self.assertEqual((edges["B-E"]["dependency"], edges["B-E"]["semantics"]), ("none", "explicit"))
        self.assertEqual((edges["F-B"]["from"], edges["F-B"]["to"], edges["F-B"]["dependency"]), ("F", "B", "to_from"))
        self.config["relations"][1]["dependency"] = "none"  # Explicitly opt out of the calls default.
        self.record = accepted(self.config)
        self.assertEqual(self.scope(["B.py"])["accepted"]["dependencies"], ["F"])

    def test_invalid_dependency_never_silently_uses_kind_default(self):
        for dependency in (None, "upstream", False, []):
            with self.subTest(dependency=dependency):
                changed = copy.deepcopy(self.config)
                changed["relations"][0]["dependency"] = dependency
                with self.assertRaises(ValueError):
                    archctx.components(changed)
                result = self.scope(["B.py"], changed)
                self.assertTrue(result["working"]["error"])
                self.assertEqual(result["accepted"]["dependents"], ["A"])

    def test_relation_only_evidence_and_unknown_files(self):
        scope = self.scope(["wiring.py", "unmapped.py"])["accepted"]
        self.assertEqual(scope["direct_components"], ["A", "B"])
        self.assertEqual(scope["dependencies"], ["C"])
        self.assertEqual(scope["dependents"], [])
        self.assertEqual(scope["uncovered_files"], ["unmapped.py"])
        associations = {x["component"]: x["paths"] for x in scope["associations"]}
        self.assertEqual(associations, {"A": ["wiring.py"], "B": ["wiring.py"]})

    def test_config_retarget_delete_and_rename_do_not_mix_graphs(self):
        changed = copy.deepcopy(self.config)
        changed["relations"][0]["to"] = "C"  # Stable ID, different endpoint.
        result = self.scope(["architecture.json"], changed)
        self.assertEqual(result["accepted"]["direct_components"], ["A", "B"])
        self.assertEqual(result["working"]["direct_components"], ["A", "C"])
        self.assertEqual(result["accepted"]["dependencies"], ["C"])
        self.assertEqual(result["working"]["dependencies"], [])
        self.assertEqual(result["working_config_hash"], archctx.semantic(changed))
        self.assertTrue(result["accepted"]["config_seeds"])
        self.assertTrue(result["working"]["config_seeds"])
        removed = copy.deepcopy(self.config)
        removed["relations"].pop(0)
        result = self.scope(["architecture.json"], removed)
        self.assertEqual(result["accepted"]["direct_components"], ["A", "B"])
        self.assertEqual(result["working"]["direct_components"], [])
        moved = copy.deepcopy(self.config)
        moved["components"][1]["evidence"][0]["path"] = "moved.py"
        result = self.scope(["architecture.json"], moved)
        self.assertEqual(result["accepted"]["direct_components"], ["B"])
        self.assertEqual(result["working"]["direct_components"], ["B"])
        renamed = copy.deepcopy(self.config)
        renamed["components"][1]["id"] = "Z"
        renamed["components"][1]["evidence"] = [{"path": "Z.py", "contains": "Z"}]
        for edge in renamed["relations"]:
            for endpoint in ("from", "to"):
                if edge[endpoint] == "B":
                    edge[endpoint] = "Z"
        result = self.scope(["B.py", "Z.py"], renamed)
        self.assertEqual(result["accepted"]["direct_components"], ["B"])
        self.assertEqual(result["working"]["direct_components"], ["Z"])
        self.assertEqual(result["accepted"]["uncovered_files"], ["Z.py"])
        self.assertEqual(result["working"]["uncovered_files"], ["B.py"])

    def test_invalid_working_definition_retains_accepted_scope(self):
        changed = copy.deepcopy(self.config)
        changed["relations"][0]["to"] = "missing"
        result = self.scope(["B.py"], changed)
        self.assertEqual(result["accepted"]["direct_components"], ["B"])
        self.assertEqual(result["accepted"]["dependents"], ["A"])
        self.assertTrue(result["working"]["error"])
        self.assertNotIn("dependencies", result["working"])
        self.assertEqual(result["freshness"], "stale")

    def test_component_metadata_outside_context_does_not_invent_pending_definition(self):
        self.config["components"][1]["internal_notes"] = {"fixture_only": True}
        self.record = accepted(self.config)
        self.assertNotIn("internal_notes", self.record["context"]["components"][1])
        result = self.scope(["B.py"])
        self.assertIsNone(result["working"])
        self.assertEqual(result["working_provenance"], "same_declarations_as_accepted")
        self.assertEqual(result["accepted"]["direct_components"], ["B"])

    def test_compact_has_explicit_omissions_and_details_recover_scope(self):
        leaves = [f"leaf-{index:02}" for index in range(20)]
        self.config = {"version": 1, "components": [component(x) for x in ["root", *leaves]],
                       "relations": [relation("root", leaf) for leaf in leaves]}
        self.record = accepted(self.config)
        compact, details = self.scope(["root.py"]), self.scope(["root.py"], details=True)
        self.assertEqual(len(compact["accepted"]["dependencies"]), 12)
        self.assertEqual(len(compact["accepted"]["relations"]), 8)
        self.assertTrue(any(compact["accepted"]["omitted"].values()))
        self.assertEqual(details["accepted"]["dependencies"], leaves)
        self.assertEqual(len(details["accepted"]["relations"]), 20)
        self.assertFalse(any(details["accepted"]["omitted"].values()))
        self.assertIn("--details", str(compact["detail_query"]))

    def test_default_answer_reports_dependents_without_a_direction_flag(self):
        # Field report: changing an SDK file returned only the SDK itself, so both callers that
        # the same accepted graph already knew about never reached the reviewer.
        self.config = {"version": 1, "components": [component(x) for x in ("sdk", "memory", "notify")],
                       "relations": [relation("memory", "sdk"), relation("notify", "sdk")]}
        self.record = accepted(self.config)
        summary = self.scope(["sdk.py"])["summary"]
        self.assertEqual(summary["basis"], "accepted")
        self.assertEqual(summary["direct_components"], ["sdk"])
        self.assertEqual(summary["dependents"], ["memory", "notify"])
        self.assertEqual(summary["dependencies"], [])
        self.assertEqual(summary["counts"], {"direct_components": 1, "dependencies": 0, "dependents": 2})

    def test_summary_counts_stay_true_when_the_compact_list_is_cut(self):
        callers = [f"caller-{index:02}" for index in range(20)]
        self.config = {"version": 1, "components": [component(x) for x in ["sdk", *callers]],
                       "relations": [relation(caller, "sdk") for caller in callers]}
        self.record = accepted(self.config)
        compact, details = self.scope(["sdk.py"]), self.scope(["sdk.py"], details=True)
        self.assertEqual(len(compact["summary"]["dependents"]), 12)
        self.assertEqual(compact["summary"]["omitted"]["dependents"], 8)
        self.assertEqual(compact["summary"]["counts"]["dependents"], 20)
        self.assertEqual(details["summary"]["dependents"], callers)

    def test_undeclared_direction_is_named_instead_of_answering_nothing_depends_on_this(self):
        # Field repro: two components declared against a credential pool with a descriptive kind.
        # `trace --direction upstream` found both; `impact` answered an empty dependents list with
        # no statement that anything had been skipped, which reads as "safe to change".
        self.config = {"version": 1, "components": [component(x) for x in ("credential-pool", "delegate-config", "delegate-tool")],
                       "relations": [relation("delegate-config", "credential-pool", kind="provides-credentials-to"),
                                     relation("delegate-tool", "credential-pool", kind="provides-credentials-to")]}
        self.record = accepted(self.config)
        scopes = self.scope(["credential-pool.py"])
        self.assertEqual(scopes["summary"]["dependents"], [])
        excluded = archctx.excluded_relations(scopes, archctx.summary_ids(scopes))
        self.assertEqual([(x["from"], x["to"], x["dependency"]) for x in excluded],
                         [("delegate-config", "credential-pool", "unclassified"),
                          ("delegate-tool", "credential-pool", "unclassified")])
        # A declared direction removes the exclusion and produces the dependents.
        for edge in self.config["relations"]: edge["dependency"] = "from_to"
        self.record = accepted(self.config)
        declared = self.scope(["credential-pool.py"])
        self.assertEqual(declared["summary"]["dependents"], ["delegate-config", "delegate-tool"])
        self.assertEqual(archctx.excluded_relations(declared, archctx.summary_ids(declared)), [])

    def test_stale_accepted_scope_never_shrinks_the_summary(self):
        # Dogfooded on this repository: the accepted scope predated the current declaration, so
        # a summary that preferred it answered "nothing depends on this" while the working
        # definition already named two dependents.
        self.record = accepted({"version": 1, "components": [component(x) for x in ("sdk", "memory")],
                                "relations": [relation("memory", "sdk", kind="guides-to")]})
        working = {"version": 1, "components": [component(x) for x in ("sdk", "memory", "notify")],
                   "relations": [relation("memory", "sdk"), relation("notify", "sdk")]}
        summary = self.scope(["sdk.py"], config=working)["summary"]
        self.assertEqual(summary["basis"], "accepted+working")
        self.assertEqual(summary["dependents"], ["memory", "notify"])
        self.assertEqual(summary["counts"]["dependents"], 2)
        # The accepted side covers `sdk`, so only the declaration-only IDs are flagged.
        self.assertEqual(summary["unvalidated"], ["memory", "notify"])

    def test_summary_falls_back_to_the_working_definition_and_then_to_none(self):
        working = copy.deepcopy(self.config)
        working["relations"] = [relation("A", "B"), relation("B", "C"), relation("C", "A")]
        self.record = {"context_hash": "b" * 64, "config_hash": "c" * 64,
                       "context": {"components": [], "relations": []}}
        self.assertEqual(self.scope(["B.py"], config=working)["summary"]["basis"], "working")
        unusable = archctx.change_scope(self.record, None, ["B.py"], config_path_relative="architecture.json")
        self.assertEqual(unusable["summary"],
                         {"basis": "none", "direct_components": [], "dependencies": [], "dependents": [],
                          "counts": {"direct_components": 0, "dependencies": 0, "dependents": 0},
                          "unvalidated": [], "omitted": {}})

    def fixture(self):
        root = Path(__file__).parent / ".archctx"
        root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        config, state = repo / "architecture.json", repo / "state"
        for ident in "ABC":
            (repo / f"{ident}.py").write_text(f"{ident} = 1\n")
        (repo / "wiring.py").write_text("A(B)\n")
        (repo / ".gitignore").write_text("state/\n")
        config.write_text(json.dumps(self.config))
        for args in [("init", "-q"), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "user.name", "Synthetic Fixture"),
                     ("add", ".gitignore", "architecture.json", "A.py", "B.py", "C.py", "wiring.py"),
                     ("commit", "-qm", "fixture baseline")]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
        return repo, config, state

    def test_cli_mcp_and_map_share_one_bound_scope_for_same_change(self):
        repo, config, state = self.fixture()
        (repo / "B.py").write_text("B = 2\n")
        observer = DevelopmentObserver(config, str(state))
        self.addCleanup(observer.stop)
        page = observer.poll_once()
        change = next(item for item in page["changes"] if item["path"] == "B.py")
        cli = subprocess.run([sys.executable, "-B", str(Path(archctx.__file__)), "--config", str(config),
                              "--state-dir", str(state), "impact", "--files", "B.py"],
                             check=True, text=True, encoding="utf-8", capture_output=True)
        cli_value = json.loads(cli.stdout)
        mcp = archctx.mcp_value(config, str(state), "architecture_impact", {"files": ["B.py"]})
        self.assertEqual(cli_value["change_scope"], mcp["change_scope"])
        self.assertEqual(change["change_scope"], mcp["change_scope"])
        self.assertEqual(mcp["change_scope"]["accepted_context_hash"], page["accepted_context_hash"])
        self.assertEqual(mcp["change_scope"]["freshness"], "stale")
        # The misread-prone legacy fields are gone, not deprecated in place.
        self.assertNotIn("reachable_components", cli_value)
        self.assertNotIn("legacy_semantics", cli_value)
        self.assertNotIn("impacted_components", change)
        details = archctx.mcp_value(config, str(state), "architecture_impact", {"files": ["B.py"], "details": True})
        self.assertEqual(details["change_scope"]["accepted"]["direct_components"], ["B"])
        self.config["relations"][0]["to"] = "C"
        config.write_text(json.dumps(self.config))
        page = observer.poll_once()
        change = next(item for item in page["changes"] if item["path"] == "architecture.json")
        agent = archctx.impact(config, str(state), None, ["architecture.json"])
        self.assertEqual(change["change_scope"], agent["change_scope"])
        self.assertEqual(agent["change_scope"]["accepted"]["direct_components"], ["A", "B"])
        self.assertEqual(agent["change_scope"]["working"]["direct_components"], ["A", "C"])

    def test_observer_failure_never_labels_retained_scope_as_current(self):
        repo, config, state = self.fixture()
        (repo / "B.py").write_text("B = 2\n")
        self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
        observer = DevelopmentObserver(config, str(state))
        self.addCleanup(observer.stop)
        first = observer.poll_once()
        scope = next(x for x in first["changes"] if x["path"] == "B.py")["change_scope"]
        self.assertEqual(scope["freshness"], "fresh")
        previous = archctx.last_path(state).read_bytes()
        config.write_text("{broken")
        failed = observer.poll_once()
        retained = next(x for x in failed["changes"] if x["path"] == "B.py")["change_scope"]
        self.assertEqual(failed["status"], "INVALID")
        self.assertEqual(retained["freshness"], "stale")
        self.assertEqual(retained["observation_state"], "retained_previous_observation")
        self.assertIsNone(retained["working_config_hash"])
        self.assertTrue(retained["working"]["error"])
        self.assertEqual(retained["accepted_context_hash"], scope["accepted_context_hash"])
        self.assertEqual(retained["accepted"], scope["accepted"])
        self.assertEqual(archctx.last_path(state).read_bytes(), previous)

    def test_mcp_details_requires_a_boolean(self):
        for details in (None, "false", 1, []):
            with self.subTest(details=details), patch.object(archctx, "status", side_effect=AssertionError("invalid details must not read state")), self.assertRaises(ValueError):
                archctx.mcp_value(Path("unused.json"), None, "architecture_impact", {"files": ["B.py"], "details": details})

    def test_impact_rejects_config_or_lkg_movement_during_read(self):
        repo, config, state = self.fixture()
        original_scope = archctx.change_scope
        for moved, malformed in ((path, malformed) for path in (config, archctx.last_path(state)) for malformed in (False, True)):
            with self.subTest(moved=moved.name, malformed=malformed):
                original = moved.read_bytes()

                def change_while_computing(*args, **kwargs):
                    value = original_scope(*args, **kwargs)
                    data = json.loads(original)
                    data["concurrent_fixture_change"] = True
                    moved.write_text("{broken" if malformed else json.dumps(data))
                    return value

                try:
                    with patch.object(archctx, "change_scope", side_effect=change_while_computing):
                        result = archctx.impact(config, str(state), None, ["B.py"])
                    self.assertEqual(result["status"], "RETRY")
                    self.assertNotIn("change_scope", result)
                finally:
                    moved.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
