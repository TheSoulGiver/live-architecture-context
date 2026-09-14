"""Synthetic Git fixtures; these are not natural consumer adoption evidence."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archctx
from archctx_development import DevelopmentObserver, PAYLOAD_BYTES


class DevelopmentTest(unittest.TestCase):
    def setUp(self):
        fixture_root = Path(__file__).parent / ".archctx"
        fixture_root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=fixture_root)
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.config = self.repo / "architecture.json"
        self.state = self.repo / "state"
        (self.repo / ".gitignore").write_text("state/\n")
        (self.repo / "owner.py").write_text("OWNER = 1\n")
        (self.repo / "consumer.py").write_text("CONSUMER = 1\n")
        (self.repo / "wiring.py").write_text("from owner import OWNER\n")
        self.definition = {"version": 1, "repo": ".", "components": [
            {"id": "owner", "purpose": "Own example", "evidence": [{"path": "owner.py", "contains": "OWNER"}]},
            {"id": "consumer", "purpose": "Consume example", "evidence": [{"path": "consumer.py", "contains": "CONSUMER"}]}],
            "relations": [{"id": "calls", "from": "consumer", "to": "owner", "kind": "calls", "evidence": [{"path": "wiring.py", "contains": "import OWNER"}]}],
            "watch": {"paths": ["*.py"]}}
        self.save()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Synthetic Fixture")
        self.git("add", ".gitignore", "architecture.json", "owner.py", "consumer.py", "wiring.py")
        self.git("commit", "-qm", "fixture baseline")
        self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def save(self):
        self.config.write_text(json.dumps(self.definition))

    def observer(self, **kwargs):
        observer = DevelopmentObserver(self.config, str(self.state), **kwargs)
        self.addCleanup(observer.stop)
        return observer

    def test_relation_evidence_git_rename_delete_and_uncovered_are_distinct(self):
        observer = self.observer()
        first = observer.poll_once()
        self.assertEqual(first["changes"], [])
        (self.repo / "wiring.py").write_text("# real body edit in fixture\nfrom owner import OWNER\n")
        result = observer.poll_once()
        change = next(c for c in result["changes"] if c["path"] == "wiring.py")
        self.assertEqual(change["components"], ["consumer", "owner"])
        self.assertTrue(change["evidence_changed"])
        self.assertEqual(result["pending"]["changed_components"], [])
        self.git("mv", "owner.py", "renamed.py")
        (self.repo / "consumer.py").unlink()
        (self.repo / "new_unmodeled.py").write_text("UNMODELED = 1\n")
        result = observer.poll_once()
        rename = next(c for c in result["changes"] if c["path"] == "renamed.py")
        self.assertEqual((rename["kind"], rename["old_path"], rename["components"]), ("rename", "owner.py", ["owner"]))
        self.assertFalse(any(c["path"] == "owner.py" for c in result["changes"]))
        self.assertIn("owner.py", observer._refresh_paths)
        self.assertIn("renamed.py", observer._refresh_paths)
        self.assertEqual(next(c for c in result["changes"] if c["path"] == "consumer.py")["kind"], "delete")
        self.assertFalse(next(c for c in result["changes"] if c["path"] == "new_unmodeled.py")["covered"])
        self.assertEqual(result["pending"]["added_components"], [])
        self.assertEqual(result["updates"]["status"], "STALE")
        self.assertEqual(observer.accepted_record["context_hash"], first["accepted_context_hash"])
        previous = result["observation_id"]
        (self.repo / "new_unmodeled.py").write_text("UNMODELED = 22\n")
        self.assertNotEqual(observer.poll_once()["observation_id"], previous)

    def test_read_and_idle_do_not_hash_validate_watch_render_or_write_logs(self):
        observer = self.observer(live=True)
        first = observer.poll_once()
        files = {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        with patch.object(archctx, "manifest", side_effect=AssertionError("idle hash")), patch.object(archctx, "updates", side_effect=AssertionError("idle validate")), patch.object(archctx, "watch_once", side_effect=AssertionError("idle watcher")), patch.object(archctx, "refresh", side_effect=AssertionError("idle render")):
            observer.read()
            again = observer.poll_once()
        self.assertEqual(first["observation_id"], again["observation_id"])
        self.assertEqual(first["observed_at"], again["observed_at"])
        self.assertEqual(files, {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()})

    def test_worker_idle_wait_is_capped_and_source_change_restores_hot_interval(self):
        observer = self.observer()
        now, waits = [100.], []
        def wait(delay):
            waits.append(delay)
            now[0] += delay
            if len(waits) == 4:
                (self.repo / "owner.py").write_text("OWNER = 2\n")
            return len(waits) == 8
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            first = observer.poll_once()
            with patch.object(observer._stop, "wait", side_effect=wait):
                observer._run()
        self.assertEqual(waits, [.75, 1.5, 3., 3., .75, 1.5, 3., 3.])
        self.assertEqual(observer._metrics["polls"], 8)
        self.assertNotEqual(observer.read()["observation_id"], first["observation_id"])
        self.assertEqual(observer.read()["accepted_context_hash"], first["accepted_context_hash"])

    def test_worker_idle_wait_respects_settle_and_retry_deadlines(self):
        observer = self.observer(live=True, settle_seconds=.5)
        now, waits, attempts = [100.], [], []
        accepted = (self.state / "last-good.json").read_bytes()
        def wait(delay):
            waits.append(delay)
            now[0] += delay
            if len(waits) == 3:
                self.definition["components"][0]["purpose"] = "Revised ownership"
                self.save()
            return len(waits) == 13
        def refresh(*args, **kwargs):
            attempts.append(now[0])
            return {"status": "RETRY" if len(attempts) < 3 else "INVALID", "last_good_preserved": True}
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            observer.poll_once()
            with patch.object(observer._stop, "wait", side_effect=wait), patch.object(archctx, "refresh", side_effect=refresh):
                observer._run()
        expected = [.75, 1.5, 3., .5, .75, .25, .75, .75, .5, .75, .75, 1.5, 3.]
        self.assertEqual(len(waits), len(expected))
        for actual, delay in zip(waits, expected):
            self.assertAlmostEqual(actual, delay)
        self.assertEqual(len(attempts), 3)  # Deterministic failure never retries unchanged inputs.
        for actual, deadline in zip(attempts, [105.75, 106.75, 108.75]):
            self.assertAlmostEqual(actual, deadline)
        self.assertEqual(observer.read()["refresh"]["status"], "INVALID")
        self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)

    def test_debounce_and_failed_refresh_attempt_once_until_input_changes(self):
        observer = self.observer(live=True, settle_seconds=10)
        now = [100.]
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            observer.poll_once()
            (self.repo / "owner.py").write_text("OWNER = 2\n")
            observer.poll_once()
            now[0] += 20
            with patch.object(archctx, "refresh", wraps=archctx.refresh) as refresh:
                observer.poll_once()
                self.assertEqual(refresh.call_count, 0)  # Ordinary save is not an architecture declaration.
            self.definition["components"][0]["purpose"] = "Revised ownership"
            self.save()
            observer.poll_once()
            now[0] += 5
            (self.repo / "owner.py").write_text("OWNER = 3\n")
            observer.poll_once()
            with patch.object(archctx, "refresh", return_value={"status": "INVALID", "failures": ["synthetic renderer failure"], "last_good_preserved": True}) as refresh:
                now[0] += 9
                observer.poll_once()
                self.assertEqual(refresh.call_count, 0)
                now[0] += 2
                failed = observer.poll_once()
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(failed["refresh"]["status"], "INVALID")
                now[0] += 20
                observer.poll_once()
                now[0] += 20
                observer.poll_once()
                self.assertEqual(refresh.call_count, 1)
            self.definition["components"][0]["purpose"] = "Corrected ownership"
            self.save()
            observer.poll_once()
            now[0] += 11
            refresh_impl = archctx.refresh
            def refresh_with_visible_progress(*args, **kwargs):
                payload, retained = observer.bundle()
                self.assertEqual(payload["refresh"]["status"], "REFRESHING")
                self.assertEqual(payload["accepted_context_hash"], retained["context_hash"])
                return refresh_impl(*args, **kwargs)
            with patch.object(archctx, "refresh", side_effect=refresh_with_visible_progress):
                accepted = observer.poll_once()
        self.assertEqual(accepted["refresh"]["status"], "PASS")
        self.assertEqual(accepted["pending"]["changed_components"], [])
        self.assertEqual(observer.bundle()[1]["context_hash"], accepted["accepted_context_hash"])

    def test_writer_lock_release_retries_without_new_inputs(self):
        observer = self.observer(live=True, settle_seconds=0)
        now = [100.]
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            first = observer.poll_once()
            self.definition["components"][0]["purpose"] = "Revised ownership"
            self.save()
            accepted = (self.state / "last-good.json").read_bytes()
            with patch.object(archctx, "refresh", wraps=archctx.refresh) as refresh:
                with archctx.refresh_lock(self.state):  # Real OS lock, not a stubbed RETRY.
                    blocked = observer.poll_once()
                    inputs = observer._quick_inputs()
                self.assertEqual(blocked["refresh"]["status"], "RETRY")
                self.assertTrue(blocked["refresh"]["last_good_preserved"])
                self.assertEqual(blocked["accepted_context_hash"], first["accepted_context_hash"])
                self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)
                observer.poll_once()  # Existing post-attempt observation.
                now[0] += .5
                with patch.object(archctx, "manifest", side_effect=AssertionError("backoff hash")):
                    observer.poll_once()
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(observer._quick_inputs(), inputs)  # Lock release changed no source/config/LKG.
                now[0] += .5
                recovered = observer.poll_once()
                self.assertEqual(recovered["refresh"]["status"], "PASS")
                self.assertEqual(refresh.call_count, 2)
                self.assertEqual(refresh.call_args.kwargs["expected_context_hash"], first["accepted_context_hash"])
        self.assertEqual(recovered["status"], "FRESH")
        self.assertNotEqual(recovered["accepted_context_hash"], first["accepted_context_hash"])
        self.assertEqual(observer.bundle()[1]["context_hash"], recovered["accepted_context_hash"])

    def test_retry_backoff_is_capped_and_stops_on_deterministic_failure(self):
        observer = self.observer(live=True, settle_seconds=0)
        now = [100.]
        refresh_impl = archctx.refresh
        def slow_refresh(*args, **kwargs):
            now[0] += 5  # Backoff starts after validation, not at poll start.
            return refresh_impl(*args, **kwargs)
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            observer.poll_once()
            self.definition["components"][0]["purpose"] = "Revised ownership"
            self.save()
            accepted = (self.state / "last-good.json").read_bytes()
            with patch.object(archctx, "refresh", side_effect=slow_refresh) as refresh:
                with archctx.refresh_lock(self.state):
                    for attempt, delay in enumerate((1, 2, 4, 8, 16, 30, 30), 1):
                        self.assertEqual(observer.poll_once()["refresh"]["status"], "RETRY")
                        self.assertEqual(refresh.call_count, attempt)
                        # Even an external receipt must not reset or accelerate backoff.
                        archctx.record_usage(self.state, "refresh", {"status": "RETRY"}, 1)
                        now[0] += delay - .25
                        observer.poll_once()
                        self.assertEqual(refresh.call_count, attempt)
                        now[0] += .25
                with patch.object(archctx, "archify_projection", side_effect=ValueError("synthetic renderer failure")) as render:
                    failed = observer.poll_once()
                    self.assertEqual(failed["refresh"]["status"], "INVALID")
                    now[0] += 60
                    observer.poll_once()
                    now[0] += 60
                    observer.poll_once()
                    self.assertEqual(render.call_count, 1)
                self.assertEqual(refresh.call_count, 8)
            (self.repo / "owner.py").write_text("ANCHOR_REMOVED = 1\n")
            self.assertEqual(observer.poll_once()["refresh"]["status"], "INVALID")
            with patch.object(archctx, "refresh", side_effect=AssertionError("invalid anchor must not retry")):
                now[0] += 60
                observer.poll_once()
                now[0] += 60
                observer.poll_once()
            self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)

    def test_retry_reconciles_another_writer_and_settles_new_inputs(self):
        observer = self.observer(live=True, settle_seconds=10)
        now = [100.]
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            observer.poll_once()
            self.definition["components"][0]["purpose"] = "Other writer's declaration"
            self.save()
            observer.poll_once()
            now[0] += 10
            with archctx.refresh_lock(self.state):
                self.assertEqual(observer.poll_once()["refresh"]["status"], "RETRY")
            self.assertEqual(archctx.refresh(self.config, str(self.state))["status"], "PASS")
            predecessor = archctx.load(self.state / "last-good.json")["context_hash"]
            with patch.object(archctx, "refresh", wraps=archctx.refresh) as refresh:
                now[0] += 30
                adopted = observer.poll_once()
                self.assertEqual(adopted["status"], "FRESH")
                self.assertEqual(adopted["accepted_context_hash"], predecessor)
                self.assertEqual(refresh.call_count, 0)  # No duplicate publication of the other writer's result.
                self.definition["components"][0]["purpose"] = "New settled declaration"
                self.save()
                observer.poll_once()
                now[0] += 9
                observer.poll_once()
                self.assertEqual(refresh.call_count, 0)
                now[0] += 1
                self.assertEqual(observer.poll_once()["refresh"]["status"], "PASS")
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(refresh.call_args.kwargs["expected_context_hash"], predecessor)

    def test_invalid_intermediate_and_repo_redirect_keep_previous_authority(self):
        observer = self.observer(live=True, settle_seconds=0)
        first = observer.poll_once()
        accepted = (self.state / "last-good.json").read_bytes()
        self.config.write_text('{"version":')
        invalid = observer.poll_once()
        self.assertEqual(invalid["status"], "INVALID")
        self.assertEqual(invalid["updates"]["status"], "INVALID")
        self.assertTrue(invalid["last_good_preserved"])
        with patch.object(archctx, "load", side_effect=AssertionError("unchanged invalid config retried")):
            self.assertEqual(observer.poll_once()["observation_id"], invalid["observation_id"])
        self.save()
        observer.poll_once()
        self.definition["repo"] = ".."
        self.save()
        with patch("archctx_development.git_changes", side_effect=AssertionError("wrong checkout Git read")):
            redirected = observer.poll_once()
        self.assertIn("repository changed", redirected["error"])
        self.assertEqual(redirected["accepted_context_hash"], first["accepted_context_hash"])
        self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)

    def test_candidate_and_invalid_anchor_never_auto_promote(self):
        self.definition["drift_rules"] = [{"id": "provider", "paths": ["provider.py"], "added_contains": ["def send"]}]
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state), reset_candidate_baseline=True)["status"], "PASS")
        observer = self.observer(live=True, settle_seconds=0)
        observer.poll_once()
        accepted = (self.state / "last-good.json").read_bytes()
        (self.repo / "provider.py").write_text("def send(): pass\n")
        self.definition["components"][0]["purpose"] = "Pending declaration"
        self.save()
        result = observer.poll_once()
        self.assertEqual(result["refresh"]["status"], "CANDIDATE_REVIEW_REQUIRED")
        self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)
        with patch.object(archctx, "refresh", side_effect=AssertionError("candidate must not retry")), patch("archctx_development.time.monotonic", return_value=10**12):
            observer.poll_once()
            observer.poll_once()
        (self.repo / "provider.py").unlink()
        (self.repo / "owner.py").write_text("ANCHOR_REMOVED = 1\n")
        self.assertEqual(observer.poll_once()["refresh"]["status"], "INVALID")
        self.assertEqual((self.state / "last-good.json").read_bytes(), accepted)
        with patch.object(observer._stop, "wait", return_value=False), patch.object(observer, "poll_once", side_effect=RuntimeError("synthetic worker failure")):
            observer._run()
        failed = observer.read()
        self.assertFalse(failed["observer_running"])
        self.assertEqual(failed["updates"]["status"], "INVALID")
        self.assertTrue(failed["last_good_preserved"])

    def test_content_hash_normalizes_crlf_and_output_is_bounded(self):
        (self.repo / "owner.py").write_bytes(b"OWNER = 1\r\n")
        observer = self.observer()
        result = observer.poll_once()
        self.assertFalse(any(c["evidence_changed"] for c in result["changes"]))
        synthetic = {"changes": [{"path": "x" * 1000, "components": []} for _ in range(1000)],
                     "counts": {"omitted": 0}, "pending": {}}
        observer._bound(synthetic)
        self.assertLessEqual(len(json.dumps(synthetic, ensure_ascii=False).encode()), PAYLOAD_BYTES)
        self.assertGreater(synthetic["counts"]["omitted"], 0)

    def test_missing_lkg_reconstructs_once_after_settle_and_retries_only_new_input(self):
        fresh = self.state / "empty-state"
        observer = DevelopmentObserver(self.config, str(fresh), live=True, settle_seconds=10)
        self.addCleanup(observer.stop)
        now = [100.]
        with patch("archctx_development.time.monotonic", side_effect=lambda: now[0]):
            self.assertEqual(observer.poll_once()["status"], "MISSING")
            with patch.object(archctx, "refresh", return_value={"status": "INVALID", "failures": ["configured renderer unavailable"], "last_good_preserved": False}) as refresh:
                now[0] += 11
                first = observer.poll_once()
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(first["refresh"]["reasons"], ["configured renderer unavailable"])
                now[0] += 20
                observer.poll_once()
                now[0] += 20
                observer.poll_once()
                self.assertEqual(refresh.call_count, 1)
                self.assertFalse((fresh / "last-good.json").exists())
            (self.repo / "owner.py").write_text("OWNER = 2\n")
            observer.poll_once()
            now[0] += 11
            accepted = observer.poll_once()
        self.assertEqual(accepted["refresh"]["status"], "PASS")
        self.assertEqual(accepted["status"], "FRESH")
        self.assertEqual(observer.bundle()[1]["context_hash"], accepted["accepted_context_hash"])

    def test_source_race_and_foreign_initial_lkg_are_not_current_observations(self):
        observer = self.observer()
        original_updates = archctx.updates
        def save_between_manifest_and_updates(*args, **kwargs):
            (self.repo / "owner.py").write_text("OWNER = 22\n")
            return original_updates(*args, **kwargs)
        with patch.object(archctx, "updates", side_effect=save_between_manifest_and_updates):
            raced = observer.poll_once()
        self.assertEqual(raced["status"], "INVALID")
        self.assertIn("source/config changed during observation", raced["error"])
        foreign = self.state / "foreign"
        record = archctx.load(self.state / "last-good.json")
        record["repo"] = str(self.repo.parent)
        archctx.atomic(foreign / "last-good.json", record)
        selected = DevelopmentObserver(self.config, str(foreign))
        self.addCleanup(selected.stop)
        payload, accepted = selected.bundle()
        self.assertEqual(payload["status"], "INVALID")
        self.assertIsNone(payload["accepted_context_hash"])
        self.assertEqual(accepted, {})

    def test_unrelated_activity_does_not_erase_pending_candidate_notice(self):
        self.definition["drift_rules"] = [{"id": "provider", "paths": ["provider.py"], "added_contains": ["def send"]}]
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state), reset_candidate_baseline=True)["status"], "PASS")
        observer = self.observer()
        observer.poll_once()
        (self.repo / "provider.py").write_text("def send(): pass\n")
        pending = observer.poll_once()["updates"]
        self.assertEqual(pending["candidate_count"], 1)
        (self.repo / "unrelated.txt").write_text("Unrelated product task\n")
        unchanged = observer.poll_once()["updates"]
        self.assertFalse(unchanged["changed"])
        self.assertEqual(unchanged["status"], "STALE")
        self.assertEqual(unchanged["candidate_count"], 1)
        self.assertEqual(unchanged["candidates"], pending["candidates"])
        self.assertEqual(unchanged["reason"], pending["reason"])
        self.config.write_text('{"version":')
        self.assertEqual(observer.poll_once()["updates"]["status"], "INVALID")
        self.save()
        restored = observer.poll_once()["updates"]
        self.assertEqual(restored["candidate_count"], 1)
        self.assertEqual(restored["reason"], pending["reason"])
        (self.repo / "provider.py").unlink()
        cleared = observer.poll_once()["updates"]
        self.assertTrue(cleared["changed"])
        self.assertEqual(cleared["candidate_count"], 0)
        self.assertFalse(cleared.get("reason"))

    def test_external_decision_and_failure_receipts_update_without_query_noise(self):
        self.definition["drift_rules"] = [{"id": "provider", "paths": ["provider.py"], "added_contains": ["def send"]}]
        self.save()
        self.assertEqual(archctx.refresh(self.config, str(self.state), reset_candidate_baseline=True)["status"], "PASS")
        observer = self.observer(live=True, settle_seconds=0)
        observer.poll_once()
        (self.repo / "provider.py").write_text("def send(): pass\n")
        before = observer.poll_once()
        candidate = before["updates"]["candidates"][0]
        # Existing bounded receipts simulate an external CLI attempt; no renderer is invoked.
        archctx.record_decision(self.state, {"id": candidate["id"], "base_context_hash": before["accepted_context_hash"],
                                            "state": "pending", "decision": "accepted"})
        archctx.record_usage(self.state, "accept", {"status": "INVALID", "failures": ["synthetic external failure"]}, 1)
        with patch.object(archctx, "refresh", side_effect=AssertionError("receipt change must not auto refresh")):
            after = observer.poll_once()
        self.assertNotEqual(after["observation_id"], before["observation_id"])
        self.assertEqual(after["updates"]["candidates"][0]["review_decision"], "accepted")
        self.assertEqual(after["updates"]["candidates"][0]["review_state"], "pending_publication")
        self.assertEqual(after["refresh"]["status"], "INVALID")
        self.assertEqual(after["refresh"]["operation"], "accept")
        self.assertIn("not retained", after["refresh"]["reasons"][0])
        archctx.record_usage(self.state, "canonical", {"status": "FRESH", "canonical": {"id": "owner"}}, 1)
        with patch.object(archctx, "manifest", side_effect=AssertionError("query receipt must not hash source")), patch.object(archctx, "updates", side_effect=AssertionError("query receipt must not rescan context")):
            self.assertEqual(observer.poll_once()["observation_id"], after["observation_id"])
        archctx.record_decision(self.state, {"id": candidate["id"], "base_context_hash": before["accepted_context_hash"],
                                            "state": "pending", "decision": "rejected"})
        archctx.record_usage(self.state, "reject", {"status": "INVALID"}, 1)
        self.assertEqual(observer.poll_once()["updates"]["candidates"][0]["review_decision"], "rejected")
        archctx.record_usage(self.state, "refresh", {"status": "INVALID"}, 1)
        self.assertEqual(observer.poll_once()["refresh"]["operation"], "refresh")


if __name__ == "__main__":
    unittest.main()
