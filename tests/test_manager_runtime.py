import concurrent.futures
import tempfile
import unittest

from pathlib import Path

from cbtools.activity import StageIssue, StageResult
from cbtools.manager.runtime import CursorReset, ManagerRuntime, RuntimeCapacity, STAGES


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = 0
        self.runtime = ManagerRuntime(20, clock=lambda: self.now)

    def successful(self, runtime, job_id, resources=("volume",), dry_run=False):
        runtime.configure_job(job_id, {"manager.test_mode": dry_run}, "revision")
        runtime.set_archives(job_id, len(resources), resources)
        for stage in STAGES:
            runtime.start_stage(job_id, stage)
            runtime.finish_stage(job_id, StageResult(unchanged=len(resources)))
        runtime.finish(job_id)

    def test_completed_volume_total_counts_successful_attempts_once_and_survives_retention(self):
        runtime = ManagerRuntime(0, recent_limit=1, event_limit=4)
        failed = runtime.enqueue(self.root)
        runtime.claim()
        runtime.set_archives(failed, 8)
        runtime.finish(failed, StageIssue("FAILED", "Failed", "Retry"))
        self.assertEqual(runtime.snapshot()["completed_volumes"], 0)
        completed = []
        for resources, dry_run in [(("one", "two", "three"), False), (("one", "two"), True), ((), False)]:
            job_id = runtime.enqueue(self.root)
            runtime.claim()
            self.successful(runtime, job_id, resources, dry_run)
            completed.append(job_id)
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["completed"], 3)
        self.assertEqual(snapshot["completed_volumes"], 5)
        self.assertNotIn(completed[0], snapshot["jobs"])
        self.assertEqual(snapshot["events"][-1]["data"]["completed_volumes"], 5)
        removed = runtime.enqueue(self.root / "removed")
        runtime.set_queued_archives(removed, 10)
        runtime.clear()
        self.assertEqual(runtime.snapshot()["completed_volumes"], 5)

    def test_unknown_completed_volume_count_is_not_reported_as_zero(self):
        runtime = ManagerRuntime(0)
        job_id = runtime.enqueue(self.root)
        runtime.claim()
        runtime.finish(job_id)
        self.assertIsNone(runtime.snapshot()["completed_volumes"])

    def test_queued_counts_coalesce_and_cannot_replace_active_or_removed_counts(self):
        job_id = self.runtime.enqueue(self.root)
        self.assertIsNone(self.runtime.snapshot()["jobs"][job_id]["archives"])
        self.assertEqual(self.runtime.next_archive_count(0), (job_id, self.root))
        self.assertTrue(self.runtime.set_queued_archives(job_id, 2))
        cursor = self.runtime.snapshot()["cursor"]
        self.assertFalse(self.runtime.set_queued_archives(job_id, 2))
        self.assertEqual(self.runtime.snapshot()["cursor"], cursor)
        self.runtime.enqueue(self.root)
        self.runtime.enqueue(self.root)
        self.assertEqual(self.runtime.next_archive_count(0), (job_id, self.root))
        self.assertIsNone(self.runtime.next_archive_count(0))
        self.runtime.flush()
        self.runtime.claim()
        self.runtime.set_archives(job_id, 5)
        self.assertFalse(self.runtime.set_queued_archives(job_id, 2))
        self.assertEqual(self.runtime.snapshot()["jobs"][job_id]["archives"], 5)
        self.runtime.defer(job_id)
        self.assertEqual(self.runtime.next_archive_count(0), (job_id, self.root))
        self.assertTrue(self.runtime.set_queued_archives(job_id, None, "Folder unavailable"))
        job = self.runtime.snapshot()["jobs"][job_id]
        self.assertIsNone(job["archives"])
        self.assertEqual(job["archive_count_error"], "Folder unavailable")
        self.runtime.request_archive_count(self.root / "nested")
        self.assertEqual(self.runtime.next_archive_count(0), (job_id, self.root))
        self.assertTrue(self.runtime.set_queued_archives(job_id, 0))
        self.assertIsNone(self.runtime.snapshot()["jobs"][job_id]["archive_count_error"])
        self.runtime.clear()
        self.assertFalse(self.runtime.set_queued_archives(job_id, 1))

    def test_fifo_duplicate_delay_and_active_exclusion(self):
        first = self.runtime.enqueue(self.root / "one")
        self.now = 10
        self.assertEqual(self.runtime.enqueue(self.root / "one"), first)
        second = self.runtime.enqueue(self.root / "two")
        self.assertIsNone(self.runtime.claim())
        self.now = 20
        self.assertEqual(self.runtime.claim()["id"], first)
        self.assertEqual(self.runtime.enqueue(self.root / "one"), first)
        self.assertIsNone(self.runtime.claim())
        self.runtime.finish(first)
        self.assertIsNone(self.runtime.claim())
        self.runtime.flush()
        self.assertEqual(self.runtime.claim()["id"], second)

    def test_deferred_job_keeps_identity_and_clear_preserves_active(self):
        first = self.runtime.enqueue(self.root / "one")
        self.now = 20
        self.runtime.claim()
        self.runtime.defer(first)
        self.assertEqual(self.runtime.snapshot()["jobs"][first]["wait_reason"], "files_changing")
        self.assertIsNone(self.runtime.claim())
        self.runtime.flush()
        self.assertEqual(self.runtime.claim()["id"], first)
        self.runtime.enqueue(self.root / "two")
        self.assertEqual(self.runtime.clear(), 1)
        self.assertEqual(self.runtime.snapshot()["active"], first)
        self.assertEqual(self.runtime.list_items(), [])

    def test_acknowledgement_is_atomic_idempotent_and_preserves_failure(self):
        job_id = self.runtime.enqueue(self.root / "one")
        self.runtime.flush()
        self.runtime.claim()
        self.runtime.finish(job_id, StageIssue("MISSING", "Missing ID", "Add it"))
        with self.assertRaises(KeyError):
            self.runtime.acknowledge([job_id, "missing-job"])
        self.assertFalse(self.runtime.snapshot()["jobs"][job_id]["acknowledged"])
        self.assertEqual(self.runtime.acknowledge([job_id]), 1)
        cursor = self.runtime.snapshot()["cursor"]
        self.assertEqual(self.runtime.acknowledge([job_id]), 0)
        self.assertEqual(self.runtime.snapshot()["cursor"], cursor)
        self.assertEqual(self.runtime.snapshot()["jobs"][job_id]["state"], "failed")
        new_id = self.runtime.enqueue(self.root / "one")
        self.assertNotEqual(new_id, job_id)

    def test_recovery_requires_same_resources_and_real_nonempty_success(self):
        runtime = ManagerRuntime(0)
        failed = runtime.enqueue(self.root)
        runtime.claim()
        runtime.set_archives(failed, 1, ["volume"])
        runtime.finish(failed, StageIssue("MISSING", "Missing ID", "Add it"))
        for resources, dry_run in [((), False), (("different-volume",), False), (("volume",), True)]:
            retry = runtime.enqueue(self.root)
            runtime.claim()
            self.successful(runtime, retry, resources, dry_run)
            self.assertTrue(runtime.snapshot()["jobs"][failed]["requires_intervention"])
        retry = runtime.enqueue(self.root)
        runtime.claim()
        self.successful(runtime, retry)
        original = runtime.snapshot()["jobs"][failed]
        self.assertEqual(original["resolved_by"], retry)
        self.assertEqual(original["state"], "failed")

    def test_replay_reset_and_retention_keep_unresolved_alerts(self):
        runtime = ManagerRuntime(0, event_limit=3, recent_limit=1)
        failed = runtime.enqueue(self.root)
        runtime.claim()
        runtime.finish(failed, StageIssue("MISSING", "Missing ID", "Add it"))
        runtime.acknowledge([failed])
        before = runtime.snapshot()["cursor"]
        runtime.enqueue(self.root / "next")
        self.assertEqual(runtime.events_after(before, 0)[0]["type"], "job.queued")
        runtime.claim()
        runtime.finish(runtime.snapshot()["active"])
        self.assertIn(failed, runtime.snapshot()["jobs"])
        with self.assertRaises(CursorReset):
            runtime.events_after(runtime.boot_id + ":0", 0)
        with self.assertRaises(CursorReset):
            runtime.events_after("old-boot:1", 0)

    def test_concurrent_admission_is_deduplicated_and_capacity_is_explicit(self):
        runtime = ManagerRuntime(0, queue_limit=1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(lambda _: runtime.enqueue(self.root), range(30)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(RuntimeCapacity):
            runtime.enqueue(self.root / "other")
        snapshot = runtime.snapshot()
        snapshot["jobs"].clear()
        self.assertEqual(len(runtime.snapshot()["jobs"]), 1)
