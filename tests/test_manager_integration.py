import asyncio
import copy
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile

from pathlib import Path
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

import requests
from waitress import create_server
from watchdog.observers import Observer

from cbtools.activity import StageResult
from cbtools.anilist.auth import AniListAuthError
from cbtools.config import DEFAULT_CONFIG
from cbtools.manager import LibraryHandler, count_queued_archives, process_job
from cbtools.manager.api import create_app
from cbtools.manager.runtime import JobReporter, ManagerRuntime
from cbtools.manager.settings import SettingsStore
from cbtools.rename import rename
from cbtools.repack import repack
from cbtools.tag import tag
from cbtools.anilist.client import AniListAdapter


class ManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.values = copy.deepcopy(DEFAULT_CONFIG)
        self.values["manager.library_path"] = str(self.library)
        self.store = SettingsStore(self.root / "config.json", self.values)
        self.runtime = ManagerRuntime(0)

    def run_job(self, directory):
        job_id = self.runtime.enqueue(directory)
        job = self.runtime.claim()
        with patch("cbtools.manager.is_folder_write_inprogress", new=AsyncMock(return_value=False)):
            asyncio.run(process_job(self.runtime, self.store, job))
        return job_id

    @contextmanager
    def counting_archives(self):
        thread = threading.Thread(target=count_queued_archives, args=(self.runtime,), daemon=True)
        thread.start()
        try:
            yield
        finally:
            self.runtime.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "The owned volume-counting thread did not stop")

    def wait_for_archives(self, job_id, expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.runtime.snapshot()["jobs"][job_id]
            if job["archives"] == expected and (expected is not None or job["archive_count_error"]):
                return job
            time.sleep(.01)
        self.fail(f"Archive count did not become {expected}: {job}")

    def test_partial_repack_is_not_success_and_does_not_run_tagging(self):
        series = self.library / "Series"
        series.mkdir()
        (series / "V1.cbr").write_bytes(b"fixture")
        (series / "V1.cbz").write_bytes(b"existing destination")
        with patch("cbtools.manager.tag") as tagging:
            job_id = self.run_job(series)
        job = self.runtime.snapshot()["jobs"][job_id]
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["code"], "REPACK_CONFLICT")
        self.assertIsNone(self.runtime.snapshot()["active"])
        tagging.assert_not_called()
        self.assertEqual((series / "V1.cbz").read_bytes(), b"existing destination")

    def test_unexpected_error_releases_active_and_next_job_can_run(self):
        series = self.library / "Series"
        series.mkdir()
        with patch("cbtools.manager.repack", side_effect=RuntimeError("unexpected failure")):
            failed = self.run_job(series)
        self.assertEqual(self.runtime.snapshot()["jobs"][failed]["error"]["code"], "UNEXPECTED_ERROR")
        self.assertIsNone(self.runtime.snapshot()["active"])
        self.runtime.enqueue(self.library)
        self.assertIsNotNone(self.runtime.claim())

    def test_credential_storage_failure_has_an_actionable_settings_remedy(self):
        series = self.library / "Series"
        series.mkdir()
        (series / "V1.cbz").write_bytes(b"fixture")
        with patch("cbtools.manager.repack", return_value=StageResult()), patch(
            "cbtools.manager.tag", side_effect=AniListAuthError("Could not read AniList credentials.", 503)
        ):
            job_id = self.run_job(series)
        error = self.runtime.snapshot()["jobs"][job_id]["error"]
        self.assertEqual(error["code"], "ANILIST_AUTH_ERROR")
        self.assertIn("Settings", error["remedy"])
        self.assertIsNone(self.runtime.snapshot()["active"])

    def test_network_timeout_is_an_actionable_anilist_error(self):
        series = self.library / "Series"
        series.mkdir()
        with patch("cbtools.manager.tag", side_effect=requests.Timeout("AniList request timed out")):
            job_id = self.run_job(series)
        job = self.runtime.snapshot()["jobs"][job_id]
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["code"], "ANILIST_ERROR")
        self.assertIsNone(self.runtime.snapshot()["active"])

    def test_rate_limit_closes_response_before_reporting_bounded_wait_and_retrying(self):
        job_id = self.runtime.enqueue(self.library)
        self.runtime.claim()
        self.runtime.start_stage(job_id, "tagging")
        adapter = AniListAdapter(reporter=JobReporter(self.runtime, job_id))
        self.addCleanup(adapter.close)
        limited = Mock(status_code=429, headers={"Retry-After": "9999999"})
        accepted = Mock(status_code=200)
        waiting = []

        def sleep(_seconds):
            self.assertTrue(limited.close.called)
            waiting.append(self.runtime.snapshot()["jobs"][job_id])

        request = requests.Request("POST", "https://graphql.anilist.co").prepare()
        with patch.object(requests.adapters.HTTPAdapter, "send", side_effect=[limited, accepted]) as send, patch("cbtools.anilist.client.time.sleep", side_effect=sleep) as delay:
            self.assertIs(adapter.send(request), accepted)
        self.assertEqual(send.call_count, 2)
        delay.assert_called_once_with(3600)
        self.assertEqual(waiting[0]["wait_reason"], "rate_limited")
        self.assertIsNotNone(waiting[0]["resume_at"])
        accepted.close.assert_not_called()

    def test_settings_saved_mid_job_apply_only_to_the_next_attempt(self):
        series = self.library / "Series"
        series.mkdir()
        captured = []

        def repacking(_files, **kwargs):
            captured.append(kwargs["settings"]["manager.test_mode"])
            self.store.save({"manager.test_mode": True}, self.store.read()["revision"])
            return StageResult()

        def operation(_files, **kwargs):
            captured.append(kwargs["dryrun"])
            return StageResult()

        with patch("cbtools.manager.repack", side_effect=repacking), patch("cbtools.manager.tag", side_effect=operation), patch("cbtools.manager.rename", side_effect=operation):
            self.run_job(series)
        self.assertEqual(captured, [False, False, False])
        self.assertTrue(self.store.capture()[0]["manager.test_mode"])

    def test_standalone_empty_commands_keep_their_success_return_contract(self):
        self.assertIsNone(repack([]))
        self.assertIsNone(rename([]))
        self.assertIsNone(tag([]))

    def test_manager_rename_preserves_custom_id_and_unmatched_hidden_files(self):
        source = self.library / "Incoming"
        source.mkdir()
        (source / "V1.cbz").write_bytes(b"fixture")
        (source / ".series-id.txt").write_text("123")
        (source / ".keep").write_text("unmatched content")
        values = {**self.values, "tag.series_id_filename": ".series-id.txt"}
        with patch("cbtools.rename.ComicArchive") as archive:
            archive.return_value.info.return_value = {"Series": "Example", "Year": "2020", "Volume": "1"}
            result = rename([source], root=self.library, settings=values, reporter=Mock(), enforce_root=True)
        output = self.library / "Example (2020)"
        self.assertEqual(result.changed, 1)
        self.assertTrue((output / "Example V01.cbz").exists())
        self.assertEqual((output / ".series-id.txt").read_text(), "123")
        self.assertEqual((source / ".keep").read_text(), "unmatched content")

    def test_manager_rename_companion_conflict_does_not_move_any_inputs(self):
        source = self.library / "Incoming"
        source.mkdir()
        (source / "V1.cbz").write_bytes(b"fixture")
        (source / "cover.jpg").write_bytes(b"new")
        output = self.library / "Example (2020)"
        output.mkdir()
        (output / "cover.jpg").write_bytes(b"existing")
        with patch("cbtools.rename.ComicArchive") as archive:
            archive.return_value.info.return_value = {"Series": "Example", "Year": "2020", "Volume": "1"}
            result = rename([source], root=self.library, settings=self.values, reporter=Mock(), enforce_root=True)
        self.assertEqual(result.issues[0].code, "RENAME_CONFLICT")
        self.assertTrue((source / "V1.cbz").exists())
        self.assertEqual((source / "cover.jpg").read_bytes(), b"new")
        self.assertEqual((output / "cover.jpg").read_bytes(), b"existing")

    def test_manager_rename_rejects_one_companion_with_multiple_destinations(self):
        source = self.library / "Incoming"
        source.mkdir()
        for volume in (1, 2):
            (source / f"V{volume}.cbz").write_bytes(b"fixture")
        (source / ".anilist.txt").write_text("123")
        values = {**self.values, "rename.pattern": "${Series}/${Volume}/${Series} ${Volume}"}
        with patch("cbtools.rename.ComicArchive") as archive:
            archive.return_value.info.side_effect = [
                {"Series": "Example", "Year": "2020", "Volume": str(volume)} for volume in (1, 2)
            ]
            result = rename([source], root=self.library, settings=values, reporter=Mock(), enforce_root=True)
        self.assertEqual(result.issues[0].code, "RENAME_CONFLICT")
        self.assertTrue((source / "V1.cbz").exists())
        self.assertTrue((source / "V2.cbz").exists())
        self.assertTrue((source / ".anilist.txt").exists())

    def test_real_archives_metadata_enrichment_and_rename_use_one_lookup(self):
        if not shutil.which("7z"):
            self.skipTest("7z is required for archive integration")
        series = self.library / "Incoming"
        series.mkdir()
        for volume in (1, 2):
            with zipfile.ZipFile(series / f"V{volume}.cbz", "w") as archive:
                archive.writestr("page.jpg", b"fixture image; WebP disabled")
        (series / ".anilist.txt").write_text("123")
        payload = {"data": {"Media": {
            "id": 123, "title": {"romaji": "Example Series", "english": "Example Series"},
            "siteUrl": "https://anilist.co/manga/123", "volumes": 2,
            "startDate": {"year": 2020}, "genres": [], "tags": [],
            "staff": {"edges": []}, "studios": {"edges": []},
        }}}
        response = Mock(status_code=200)
        response.json.return_value = payload
        with patch("cbtools.anilist.client.requests.Session.post", return_value=response) as post, patch(
            "cbtools.anilist.client.anilist_auth.token", return_value=None,
        ):
            job_id = self.run_job(series)
            self.assertEqual(post.call_count, 1)
            output = self.library / "Example Series (2020)"
            original_archives = {path.name: path.read_bytes() for path in output.glob("*.cbz")}
            post.reset_mock()
            unchanged_id = self.run_job(output)
            self.assertEqual(post.call_count, 1)
        job = self.runtime.snapshot()["jobs"][job_id]
        self.assertEqual(job["state"], "succeeded", job["error"])
        self.assertEqual(job["series_id"], 123)
        self.assertEqual(job["series_title"], "Example Series")
        self.assertEqual(job["results"]["tagging"]["changed"], 2)
        self.assertEqual(post.call_count, 1)
        output = self.library / "Example Series (2020)"
        self.assertTrue((output / "Example Series V01.cbz").is_file())
        self.assertTrue((output / "Example Series V02.cbz").is_file())
        self.assertEqual((output / ".anilist.txt").read_text(), "123")
        unchanged = self.runtime.snapshot()["jobs"][unchanged_id]
        self.assertEqual(unchanged["state"], "succeeded", unchanged["error"])
        self.assertEqual(set(unchanged["results"]), {"repacking", "tagging", "renaming"})
        self.assertTrue(all(result["outcome"] == "unchanged" for result in unchanged["results"].values()))
        self.assertEqual(original_archives, {path.name: path.read_bytes() for path in output.glob("*.cbz")})

    def test_watchdog_created_files_admit_one_correlated_job(self):
        observer = Observer()
        observer.schedule(LibraryHandler(self.runtime, self.store), str(self.library), recursive=True)
        with self.assertLogs("cbtools.manager", level="WARNING"), self.counting_archives():
            observer.start()
            try:
                first = self.library / "V1.cbz"
                first.write_bytes(b"fixture")
                (self.library / ".anilist.txt").write_text("123")
                deadline = time.monotonic() + 3
                while not self.runtime.list_items() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(self.runtime.list_items(), [str(self.library)])
                job_id = self.runtime.snapshot()["queue"][0]
                self.wait_for_archives(job_id, 1)
                (self.library / "cover.jpg").write_bytes(b"not an archive")
                (self.library / ".hidden.cbz").write_bytes(b"hidden archive")
                second = self.library / "V2.CBR"
                second.write_bytes(b"fixture")
                self.wait_for_archives(job_id, 2)
                first.unlink()
                self.wait_for_archives(job_id, 1)
                second.rename(self.root / "V2.CBR")
                self.wait_for_archives(job_id, 0)
                self.assertEqual(len(self.runtime.snapshot()["jobs"]), 1)
                self.assertNotIn("volume_counts", self.runtime.snapshot()["health"])
                missing = self.runtime.enqueue(self.library / "missing")
                unavailable = self.wait_for_archives(missing, None)
                self.assertIn("unavailable", unavailable["archive_count_error"])
            finally:
                observer.stop()
                observer.join(timeout=3)

    def test_slow_volume_count_does_not_block_http_or_replace_a_claimed_count(self):
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        job_id = self.runtime.enqueue(self.library)
        original_set = self.runtime.set_queued_archives

        def blocked(_paths):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("The fixture count was not released")
            return iter([self.library / "V1.cbz"])

        def counted(*args, **kwargs):
            result = original_set(*args, **kwargs)
            attempted.set()
            return result

        with self.serve_app() as base, patch("cbtools.manager.expand_paths", side_effect=blocked), \
                patch.object(self.runtime, "set_queued_archives", side_effect=counted), self.counting_archives():
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                snapshot = requests.get(base + "/api/v1/snapshot", timeout=2).json()
                self.assertLess(time.monotonic() - started, 1)
                self.assertIsNone(snapshot["jobs"][job_id]["archives"])
                self.runtime.claim()
                self.runtime.set_archives(job_id, 4)
                release.set()
                self.assertTrue(attempted.wait(2))
                self.assertEqual(self.runtime.snapshot()["jobs"][job_id]["archives"], 4)
            finally:
                release.set()

    @contextmanager
    def serve_app(self):
        app = create_app(self.runtime, self.store)
        app.config["SSE_HEARTBEAT"] = .02
        sockets = {}
        server = create_server(app, map=sockets, host="127.0.0.1", port=0, threads=12,
                               outbuf_high_watermark=65536, outbuf_overflow=262144, channel_timeout=45)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.effective_port}"
        try:
            yield base
        finally:
            self.runtime.close()
            server.task_dispatcher.shutdown(timeout=3)
            for channel in list(sockets.values()):
                channel.close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "The owned test HTTP server did not stop")

    def test_real_waitress_keeps_commands_responsive_with_four_streams(self):
        streams = []
        readers = []
        with self.serve_app() as base:
            self.addCleanup(lambda: [response.close() for response in streams])
            initial = requests.get(base + "/api/v1/snapshot", timeout=2).json()
            for _ in range(4):
                response = requests.get(base + "/api/v1/events?after=" + initial["cursor"],
                                        stream=True, timeout=2)
                self.assertEqual(response.status_code, 200)
                # Closing a partial reader can close its SSE response.
                readers.append(response.iter_lines(chunk_size=1))
                self.assertTrue(next(readers[-1]).startswith(b"retry:"))
                streams.append(response)
            self.assertEqual(requests.get(base + "/api/v1/events", timeout=2).status_code, 503)
            started = time.monotonic()
            response = requests.post(base + "/api/v1/queue/clear", json={},
                                     headers={"X-CSRF-Token": initial["csrf_token"]}, timeout=2)
            self.assertEqual(response.status_code, 200)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(requests.get(base + "/ui/", timeout=2).status_code, 200)
            for response in streams:
                response.close()
            streams.clear()
            deadline = time.monotonic() + 3
            while True:
                response = requests.get(base + "/api/v1/events", stream=True, timeout=2)
                if response.status_code == 200:
                    streams.append(response)
                    break
                response.close()
                if time.monotonic() >= deadline:
                    self.fail("Closed streams did not release their slots")
                time.sleep(.02)
            for response in streams:
                response.close()
            streams.clear()

    def test_slow_stream_gets_reset_and_does_not_block_commands(self):
        job_id = self.runtime.enqueue(self.library)
        self.runtime.claim()
        with self.serve_app() as base, socket.socket() as connection:
            port = int(base.rsplit(":", 1)[1])
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            connection.settimeout(3)
            connection.connect(("127.0.0.1", port))
            connection.sendall(f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
            received = b""
            while b"\r\n\r\n" not in received:
                received += connection.recv(4096)
            self.assertIn(b"200 OK", received)
            for index in range(2500):
                self.runtime.identity(job_id, 123, f"Series {index} " + "x" * 150)
            started = time.monotonic()
            self.assertEqual(requests.get(base + "/api/v1/snapshot", timeout=2).status_code, 200)
            self.assertLess(time.monotonic() - started, 1)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            deadline = time.monotonic() + 5
            while b"event: reset" not in received and time.monotonic() < deadline:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
            self.assertTrue(b"event: reset" in received, "Slow client was not told to resnapshot")
