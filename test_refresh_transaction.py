import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx


PYTHON = sys.executable


class RefreshTransactionTest(unittest.TestCase):
    @staticmethod
    def render_stub(config, repo, _directory, generation, ir, candidate, previous):
        artifacts = {}
        for name, content in {
            "current.html": f"<svg>current {candidate['revision']}</svg>",
            "comparison.html": "<svg>comparison</svg>",
            "compare.json": "{}",
        }.items():
            path = generation / name
            path.write_text(content, encoding="utf-8")
            artifacts[name] = archctx.sha(path.read_bytes())
        view, _ = archctx.repo_file(repo, config["archify"]["view"], "archify.view")
        binding = {
            "context_hash": archctx.semantic(candidate),
            "revision": candidate["revision"],
            "ir_sha256": archctx.sha(ir.read_bytes()),
            "view_hash": archctx.semantic(archctx.load(view)),
            "delta_kind": "initial" if previous is None else "source_evidence",
            "artifacts": dict(artifacts),
        }
        binding_path = generation / "binding.json"
        archctx.atomic(binding_path, binding)
        return binding | {"render": "PASS", "compare": "PASS", "artifacts": artifacts | {"binding.json": archctx.sha(binding_path.read_bytes())}}

    def fixture(self, root: Path, render: bool = False):
        source = root / "runtime.py"
        source.write_text("OWNER = 'one'\n", encoding="utf-8")
        config, state, view, output = root / "architecture.json", root / "state", root / "view.json", root / "compatibility.json"
        view.write_text(json.dumps({"title": "Runtime", "nodes": [{"id": "runtime", "pos": [0, 0]}]}), encoding="utf-8")
        archify = {
            "view": view.name,
            "output": output.name,
            "validate": [PYTHON, "-c", "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))", "{archify_output}"],
        }
        if render:
            archify |= {"render": ["stub-render", "{archify_output}", "{archify_html}"], "compare": ["stub-compare", "{archify_before}", "{archify_output}", "{archify_compare}", "{archify_receipt}"]}
        value = {
            "version": 1,
            "repo": ".",
            "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "OWNER"}]}],
            "watch": {"paths": ["runtime.py"]},
            "archify": archify,
        }
        config.write_text(json.dumps(value), encoding="utf-8")
        first = archctx.refresh(config, str(state))
        self.assertEqual(first["status"], "PASS")
        if render:
            self.assertIsNone(archctx.archify_artifact_reason(state, first["archify"]))
            self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")
        return config, state, source, view, output

    def test_final_input_recheck_keeps_prior_lkg_and_compatibility_output(self):
        """Before the transaction guard, each mutation below could have promoted as PASS."""
        for changed_input in ("source_evidence", "config", "view"):
            with self.subTest(changed_input=changed_input), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, state, source, view, output = self.fixture(root)
                old = json.loads((state / "last-good.json").read_text(encoding="utf-8"))
                old_output = output.read_bytes()
                original = archctx.archify_projection

                def mutate_after_validation(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if changed_input == "source_evidence":
                        source.write_text("OWNER = 'two'\n", encoding="utf-8")
                    elif changed_input == "config":
                        changed = json.loads(config.read_text(encoding="utf-8"))
                        changed["components"][0]["name"] = "Renamed runtime"
                        config.write_text(json.dumps(changed), encoding="utf-8")
                    else:
                        changed = json.loads(view.read_text(encoding="utf-8"))
                        changed["title"] = "Runtime moved"
                        view.write_text(json.dumps(changed), encoding="utf-8")
                    return result

                with patch.object(archctx, "archify_projection", side_effect=mutate_after_validation):
                    result = archctx.refresh(config, str(state))

                self.assertEqual(result["status"], "RETRY")
                self.assertIn(changed_input, result["changed_inputs"])
                self.assertEqual(json.loads((state / "last-good.json").read_text(encoding="utf-8"))["context_hash"], old["context_hash"])
                self.assertEqual(output.read_bytes(), old_output)
                self.assertEqual(len(list((state / "generations").iterdir())), 1)

    def test_nonwaiting_writer_lock_prevents_old_refresh_from_winning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, state, source, _view, _output = self.fixture(root)
            barrier, release, finished = threading.Barrier(2), threading.Event(), []
            original = archctx.archify_projection

            def pause_inside_transaction(*args, **kwargs):
                barrier.wait(timeout=5)
                self.assertTrue(release.wait(timeout=5))
                return original(*args, **kwargs)

            worker = threading.Thread(target=lambda: finished.append(archctx.refresh(config, str(state))))
            with patch.object(archctx, "archify_projection", side_effect=pause_inside_transaction):
                worker.start()
                barrier.wait(timeout=5)
                source.write_text("OWNER = 'two'\n", encoding="utf-8")
                blocked = archctx.refresh(config, str(state))
                release.set()
                worker.join(timeout=10)

            self.assertFalse(worker.is_alive())
            self.assertEqual((blocked["status"], blocked["changed_inputs"]), ("RETRY", ["writer_lock"]))
            self.assertEqual(finished[0]["status"], "RETRY")
            promoted = archctx.refresh(config, str(state))
            self.assertEqual(promoted["status"], "PASS")
            self.assertEqual(archctx.snapshot(config, str(state))["context"]["components"][0]["evidence"][0]["sha256"], archctx.sha(source.read_text(encoding="utf-8").encode()))

    def test_apply_watcher_retries_final_input_change_and_generation_is_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, state, source, _view, output = self.fixture(root)
            self.assertEqual(archctx.watch_once(config, str(state))['status'], "WATCH_READY")
            source.write_text("OWNER = 'two'\n", encoding="utf-8")
            original, changed = archctx.archify_projection, False

            def mutate_once(*args, **kwargs):
                nonlocal changed
                result = original(*args, **kwargs)
                if not changed:
                    changed = True
                    source.write_text("OWNER = 'three'\n", encoding="utf-8")
                return result

            with patch.object(archctx, "archify_projection", side_effect=mutate_once):
                retry = archctx.watch_once(config, str(state), apply=True)
                promoted = archctx.watch_once(config, str(state), apply=True)

            self.assertEqual((retry["status"], retry["event"]), ("RETRY", "ARCHITECTURE_REFRESH_RETRY"))
            self.assertEqual(promoted["status"], "PASS")
            receipt = promoted["archify"]
            self.assertTrue(Path(receipt["ir"]).is_file())
            output.unlink()
            self.assertEqual(archctx.status(config, str(state))["status"], "FRESH")

    def test_candidate_acceptance_rechecks_source_before_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime.py"
            runtime.write_text("def run(): return 'ok'\n", encoding="utf-8")
            config, state = root / "architecture.json", root / "state"
            value = {
                "version": 1,
                "repo": ".",
                "components": [{"id": "runtime", "evidence": [{"path": "runtime.py", "contains": "def run"}]}],
                "drift_rules": [{"id": "provider", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["def send_to_provider"]}],
            }
            config.write_text(json.dumps(value), encoding="utf-8")
            first = archctx.refresh(config, str(state))
            self.assertEqual(first["status"], "PASS")
            (root / "provider.py").write_text("def send_to_provider(): return 'ok'\n", encoding="utf-8")
            candidate = archctx.candidates(config, str(state))["candidates"][0]
            runtime.write_text("from provider import send_to_provider\n\ndef run(): return send_to_provider()\n", encoding="utf-8")
            value["components"].append({"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send_to_provider"}]})
            value["relations"] = [{"from": "runtime", "to": "provider", "kind": "uses-provider", "evidence": [{"path": "runtime.py", "contains": "return send_to_provider()"}]}]
            config.write_text(json.dumps(value), encoding="utf-8")
            original = archctx.archify_projection

            def mutate_after_candidate_validation(*args, **kwargs):
                result = original(*args, **kwargs)
                runtime.write_text("from provider import send_to_provider\n\n# changed after candidate review\ndef run(): return send_to_provider()\n", encoding="utf-8")
                return result

            with patch.object(archctx, "archify_projection", side_effect=mutate_after_candidate_validation):
                result = archctx.accept_candidate(config, str(state), candidate["id"], ["component:provider", "relation:runtime--uses-provider--provider"])

            self.assertEqual((result["status"], result["changed_inputs"][0]), ("RETRY", "source_evidence"))
            self.assertEqual(json.loads((state / "last-good.json").read_text(encoding="utf-8"))["context_hash"], first["context_hash"])

    def test_rendered_bundle_is_not_published_when_final_source_recheck_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("archctx_blueprint.render_bundle", side_effect=self.render_stub):
                config, state, source, _view, output = self.fixture(root, render=True)
                old = json.loads((state / "last-good.json").read_text(encoding="utf-8"))
                old_artifact = Path(old["archify"]["generation"]) / "current.html"
                old_output = output.read_bytes()
                original = self.render_stub

                def render_then_mutate(*args, **kwargs):
                    receipt = original(*args, **kwargs)
                    source.write_text("OWNER = 'two'\n", encoding="utf-8")
                    return receipt

                with patch("archctx_blueprint.render_bundle", side_effect=render_then_mutate):
                    result = archctx.refresh(config, str(state))

            current = json.loads((state / "last-good.json").read_text(encoding="utf-8"))
            self.assertEqual((result["status"], current["context_hash"]), ("RETRY", old["context_hash"]))
            self.assertTrue(old_artifact.is_file())
            self.assertEqual(output.read_bytes(), old_output)
            self.assertEqual([path.resolve() for path in (state / "generations").iterdir()], [Path(old["archify"]["generation"]).resolve()])

    def test_lkg_publish_failure_keeps_old_rendered_bundle_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("archctx_blueprint.render_bundle", side_effect=self.render_stub):
                config, state, source, _view, output = self.fixture(root, render=True)
                old = json.loads((state / "last-good.json").read_text(encoding="utf-8"))
                old_output = output.read_bytes()
                source.write_text("OWNER = 'two'\n", encoding="utf-8")
                original_atomic = archctx.atomic

                def fail_only_lkg(path, value):
                    if path.resolve() == archctx.last_path(state).resolve():
                        raise OSError("simulated local state failure")
                    return original_atomic(path, value)

                with patch.object(archctx, "atomic", side_effect=fail_only_lkg):
                    result = archctx.refresh(config, str(state))

            snapshot = archctx.snapshot(config, str(state))
            self.assertEqual((result["status"], snapshot["archify"]["generation"]), ("INVALID", old["archify"]["generation"]))
            self.assertTrue((Path(old["archify"]["generation"]) / "current.html").is_file())
            self.assertEqual(output.read_bytes(), old_output)

    def test_invalid_config_keeps_coherent_last_good_snapshot_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, state, _source, _view, _output = self.fixture(root)
            old = json.loads((state / "last-good.json").read_text(encoding="utf-8"))
            config.write_text("{ not valid json", encoding="utf-8")
            status = archctx.status(config, str(state))
            snapshot = archctx.snapshot(config, str(state))

            self.assertEqual((status["status"], status["last_good_context_hash"]), ("STALE", old["context_hash"]))
            self.assertEqual((snapshot["status"], snapshot["context"]["components"][0]["id"]), ("STALE", "runtime"))

    def test_mismatched_render_receipt_or_binding_is_stale(self):
        for mismatch in ("receipt", "binding"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch("archctx_blueprint.render_bundle", side_effect=self.render_stub):
                    config, state, _source, _view, _output = self.fixture(root, render=True)
                record = archctx.load(archctx.last_path(state))
                receipt = record["archify"]
                if mismatch == "receipt":
                    receipt["context_hash"] = "not-the-accepted-context"
                else:
                    binding_path = Path(receipt["generation"]) / "binding.json"
                    binding = archctx.load(binding_path)
                    binding["delta_kind"] = "another-accepted-context"
                    archctx.atomic(binding_path, binding)
                    receipt["artifacts"]["binding.json"] = archctx.sha(binding_path.read_bytes())
                archctx.atomic(archctx.last_path(state), record)

                stale = archctx.status(config, str(state))
                self.assertEqual(stale["status"], "STALE")
                self.assertIn("Archify render", stale["reason"][0])

    def test_drift_signal_line_move_does_not_create_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "provider.py"
            source.write_text("def send_to_provider(): pass\n", encoding="utf-8")
            config, state = root / "architecture.json", root / "state"
            config.write_text(json.dumps({
                "version": 1,
                "repo": ".",
                "components": [{"id": "provider", "evidence": [{"path": "provider.py", "contains": "def send_to_provider"}]}],
                "drift_rules": [{"id": "provider-boundary", "kind": "new-provider", "paths": ["provider.py"], "added_contains": ["def send_to_provider"]}],
            }), encoding="utf-8")
            self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")

            source.write_text("# ordinary implementation comment\ndef send_to_provider(): pass\n", encoding="utf-8")
            observed = archctx.candidates(config, str(state))

            self.assertEqual((observed["status"], observed["candidate_count"]), ("PASS", 0))


if __name__ == "__main__":
    unittest.main()
