from __future__ import annotations

import asyncio
import logging
import threading
import traceback

import requests
from glob import escape
from pathlib import Path
from waitress import create_server
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler, FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from cbtools.activity import StageIssue, StageResult
from cbtools.config import config
from cbtools.core import ComicArchive, expand_paths
from cbtools.exceptions import AnilistApiError, CbtoolsError, InvalidArgumentError
from cbtools.manager.api import app
from cbtools.manager.queue import manager_queue
from cbtools.manager.runtime import JobReporter, ManagerRuntime, RuntimeCapacity, canonical
from cbtools.manager.settings import SettingsStore, settings_store
from cbtools.tag import _get_series_id, tag
from cbtools.rename import rename
from cbtools.repack import repack
from cbtools.anilist_auth import AniListAuthError


API_BASE_URL = f"http://localhost:{config['manager.api_port']}"
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class LibraryHandler(PatternMatchingEventHandler):
    def __init__(self, runtime: ManagerRuntime = manager_queue, settings: SettingsStore = settings_store):
        self.runtime = runtime
        self.settings, _ = settings.capture()
        self.library = Path(self.settings["manager.library_path"]).resolve()
        patterns = [f"**/*.{ext}" for ext in ComicArchive._allowed_file_exts]
        patterns.append(f'**/{escape(self.settings["tag.series_id_filename"])}')
        super().__init__(patterns=patterns, ignore_patterns=[], ignore_directories=True, case_sensitive=False)

    def _enqueue(self, path: Path):
        path = path.resolve()
        if not path.is_relative_to(self.library) or self.runtime.is_active(path):
            return
        try:
            self.runtime.enqueue(path, "watcher")
        except RuntimeCapacity:
            logger.exception("Watcher cannot admit work; review the manager queue/history capacity")
            self.runtime.set_health("admission", "capacity_reached")

    def on_created(self, event):
        self._handle_new_file(Path(event.src_path))

    def on_moved(self, event):
        self.runtime.request_archive_count(Path(event.src_path).parent)
        self._handle_new_file(Path(event.dest_path))

    def on_deleted(self, event):
        self.runtime.request_archive_count(Path(event.src_path).parent)

    def _handle_new_file(self, path: Path):
        if (path.suffix.lstrip(".").lower() in ComicArchive._allowed_file_exts
                or path.name == self.settings["tag.series_id_filename"]):
            self.runtime.request_archive_count(path.parent)
            self._enqueue(path.parent)

    def on_modified(self, event):
        path = Path(event.src_path)
        if path.name == self.settings["tag.series_id_filename"]:
            self._enqueue(path.parent)


def count_queued_archives(runtime: ManagerRuntime = manager_queue) -> None:
    try:
        while not runtime.closed:
            request = runtime.next_archive_count()
            if request is None:
                continue
            job_id, path = request
            try:
                if not path.is_dir():
                    raise NotADirectoryError(f"Queued folder is unavailable: {path}")
                count = sum(1 for _ in expand_paths([path]))
            except OSError as error:
                if runtime.set_queued_archives(job_id, None, str(error)):
                    logger.warning("Could not count volumes in %s", path, exc_info=True)
            else:
                runtime.set_queued_archives(job_id, count)
    except Exception:
        logger.exception("Queued volume counting failed")
        runtime.set_health("volume_counts", "failed")


async def is_folder_write_inprogress(path):
    def size():
        return sum(file.stat().st_size for file in path.glob("*") if file.is_file())

    before = size()
    await asyncio.sleep(2)
    return before != size()


def _failure(error: Exception, stage: str | None, settings: dict) -> StageIssue:
    if isinstance(error, AniListAuthError):
        return StageIssue("ANILIST_AUTH_ERROR", str(error), "Check AniList authentication in Settings, then rescan.")
    if isinstance(error, PermissionError):
        return StageIssue("PERMISSION_DENIED", str(error), "Check library permissions, then rescan the folder.")
    if isinstance(error, InvalidArgumentError) and stage == "tagging":
        return StageIssue("SERIES_ID_MISSING", str(error),
                          f'Add a valid AniList ID to {settings["tag.series_id_filename"]}, then rescan the folder.')
    if isinstance(error, (AnilistApiError, requests.RequestException)):
        return StageIssue("ANILIST_ERROR", str(error),
                          "Check network access, the series ID, and AniList availability, then rescan.")
    if isinstance(error, CbtoolsError):
        return StageIssue("PROCESSING_ERROR", str(error), "Review the archive and diagnostic details, then rescan.")
    return StageIssue("UNEXPECTED_ERROR", str(error) or type(error).__name__,
                      "Review the diagnostic details and correct the problem before rescanning.")


async def process_job(runtime: ManagerRuntime, settings: SettingsStore, job: dict) -> None:
    job_id, path = job["id"], Path(job["path"])
    values = {}
    stage = None
    finished = False
    try:
        values, revision = settings.capture()
        values = runtime.configure_job(job_id, values, revision)
        root = Path(values["manager.library_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_dir():
            raise InvalidArgumentError("The queued directory is no longer inside the accessible library.")
        if await is_folder_write_inprogress(path):
            runtime.defer(job_id)
            finished = True
            return
        count, resources = 0, set()
        for archive in expand_paths([path]):
            count += 1
            if count <= 1000:
                resources.add(canonical(archive.with_suffix("")))
        runtime.set_archives(job_id, count, resources)
        reporter = JobReporter(runtime, job_id)
        series_id = _get_series_id(path, values)
        if series_id:
            reporter.identity(series_id)
        dryrun = values["manager.test_mode"]
        operations = (
            ("repacking", repack, {"remove_source": True, "use_webp": values["manager.force_webp"]}),
            ("tagging", tag, {}),
            ("renaming", rename, {"root": values["manager.library_path"], "enforce_root": True}),
        )
        for stage, operation, arguments in operations:
            runtime.start_stage(job_id, stage)
            result = operation([path], dryrun=dryrun, settings=values, reporter=reporter, **arguments)
            if not isinstance(result, StageResult):
                raise RuntimeError(f"{stage} did not return a structured result.")
            runtime.finish_stage(job_id, result)
            if result.failed:
                issue = result.issues[0] if result.issues else StageIssue(
                    "PARTIAL_FAILURE", f"{stage.capitalize()} did not complete.",
                    "Review the diagnostic details, then rescan.",
                )
                runtime.finish(job_id, issue, "\n".join(item.message for item in result.issues))
                finished = True
                logger.error("%s: %s", path, issue.message, extra={"job_id": job_id, "stage": stage})
                return
        runtime.finish(job_id)
        finished = True
        logger.info("Finished processing %s%s", path, " (test mode)" if dryrun else "",
                    extra={"job_id": job_id, "series_id": series_id})
    except Exception as error:
        if finished:
            raise
        runtime.finish(job_id, _failure(error, stage, values), traceback.format_exc())
        finished = True
        logger.exception("Job %s failed while processing %s", job_id, path)
    finally:
        if not finished:
            runtime.finish(job_id, StageIssue("INTERRUPTED", "Processing was interrupted.",
                                             "Rescan after restarting the manager."), interrupted=True)


async def worker(runtime: ManagerRuntime = manager_queue, settings: SettingsStore = settings_store,
                 stop: threading.Event | None = None, observer=None):
    runtime.set_health("worker", "running")
    try:
        while not runtime.closed and not (stop and stop.is_set()):
            if observer is not None and not observer.is_alive():
                runtime.set_health("watcher", "failed")
            job = runtime.claim()
            if job is None:
                await asyncio.sleep(10)
            else:
                await process_job(runtime, settings, job)
    finally:
        runtime.set_health("worker", "stopped")


def rescan(files=None):
    body = {}
    if files:
        paths = expand_paths(files)
        directories = {str(path.parent.resolve()) for path in paths}
        if not directories:
            logger.warning("No valid directories found in paths")
            return
        body = {"paths": list(directories)}
    return requests.post(f"{API_BASE_URL}/rescan", json=body)


def flush():
    return requests.post(f"{API_BASE_URL}/flush")


def list_items():
    return requests.get(f"{API_BASE_URL}/list")


def clear():
    return requests.post(f"{API_BASE_URL}/clear")


def manager():
    logger.info("Starting cbmanager...")
    stop = threading.Event()
    observer = Observer()
    handler = LibraryHandler()
    observer.schedule(handler, path=config["manager.library_path"], recursive=True,
                      event_filter=[FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent])
    sockets = {}
    server = create_server(
        app, map=sockets, host="0.0.0.0", port=config["manager.api_port"], threads=12,
        outbuf_high_watermark=65536, outbuf_overflow=262144, channel_timeout=45,
    )
    def serve_api():
        try:
            server.run()
        except Exception:
            logger.exception("Manager HTTP server failed")
        finally:
            stop.set()

    thread = threading.Thread(target=serve_api, name="cbmanager-http", daemon=True)
    count_thread = threading.Thread(target=count_queued_archives, name="cbmanager-volume-counts", daemon=True)
    try:
        count_thread.start()
        observer.start()
        manager_queue.set_health("watcher", "running")
        thread.start()
        logger.info("Web UI listening on port %s (/ui/)", config["manager.api_port"])
        asyncio.run(worker(stop=stop, observer=observer))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        manager_queue.close()
        if count_thread.is_alive():
            count_thread.join(timeout=5)
        if count_thread.is_alive():
            logger.warning("Volume counting is still waiting for filesystem access during shutdown")
        observer.stop()
        if observer.is_alive():
            observer.join()
        server.task_dispatcher.shutdown(timeout=10)
        for channel in list(sockets.values()):
            channel.close()
        if thread.is_alive():
            thread.join(timeout=5)
