from __future__ import annotations

import copy
import os
import threading
import time
import uuid

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from cbtools.activity import StageIssue, StageResult


EVENT_LIMIT = 1000
RECENT_JOB_LIMIT = 100
JOB_LIMIT = 1500
QUEUE_LIMIT = 500
STAGES = ("repacking", "tagging", "renaming")


class RuntimeConflict(ValueError):
    pass


class RuntimeCapacity(RuntimeError):
    pass


class CursorReset(ValueError):
    pass


def canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


@dataclass
class Job:
    id: str
    path: str
    source: str
    enqueued_at: float
    ready_at: float
    state: str = "queued"
    stage: Optional[str] = None
    series_id: Optional[int] = None
    series_title: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    started_clock: Optional[float] = None
    elapsed: float = 0
    archives: Optional[int] = None
    archive_count_error: Optional[str] = None
    config_revision: Optional[str] = None
    dry_run: bool = False
    wait_reason: Optional[str] = None
    resume_at: Optional[float] = None
    requires_intervention: bool = False
    acknowledged: bool = False
    resolved_by: Optional[str] = None
    error: Optional[dict] = None
    results: dict = field(default_factory=dict)
    output_paths: list[str] = field(default_factory=list)
    settings: Optional[dict] = None
    aliases: set[str] = field(default_factory=set)
    resources: set[str] = field(default_factory=set)

    def public(self, clock: float) -> dict:
        elapsed = self.elapsed
        if self.started_clock is not None and self.finished_at is None:
            elapsed = max(0, clock - self.started_clock)
        return {
            "id": self.id, "path": self.path, "source": self.source,
            "title": self.series_title or Path(self.path).name,
            "series_id": self.series_id, "series_title": self.series_title,
            "state": self.state, "stage": self.stage,
            "enqueued_at": self.enqueued_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "elapsed": round(elapsed, 1),
            "eligible_in": round(max(0, self.ready_at - clock), 1),
            "archives": self.archives, "archive_count_error": self.archive_count_error,
            "config_revision": self.config_revision,
            "dry_run": self.dry_run, "wait_reason": self.wait_reason,
            "resume_at": self.resume_at,
            "requires_intervention": self.requires_intervention,
            "acknowledged": self.acknowledged, "resolved_by": self.resolved_by,
            "error": copy.deepcopy(self.error), "results": copy.deepcopy(self.results),
            "output_paths": self.output_paths[:10],
        }


class ManagerRuntime:
    def __init__(
        self, delay: float = 20, *, event_limit: int = EVENT_LIMIT,
        recent_limit: int = RECENT_JOB_LIMIT, job_limit: int = JOB_LIMIT,
        queue_limit: int = QUEUE_LIMIT, clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.delay = delay
        self.boot_id = uuid.uuid4().hex
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = threading.Condition(threading.RLock())
        self._events: deque[dict] = deque(maxlen=event_limit)
        self._recent: deque[str] = deque(maxlen=recent_limit)
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._paths: dict[str, str] = {}
        self._active: Optional[str] = None
        self._sequence = 0
        self._completed = 0
        self._completed_archives: Optional[int] = 0
        self._count_requests: dict[str, None] = {}
        self._job_limit = job_limit
        self._queue_limit = queue_limit
        self._health = {"worker": "not_started", "watcher": "not_started"}
        self._rescan: dict = {"state": "idle"}
        self._closed = False

    def _emit(self, kind: str, message: str, job: Optional[Job] = None,
              level: str = "info", **data) -> None:
        self._sequence += 1
        if job is not None:
            data["job"] = job.public(self._clock())
        self._events.append({
            "schema_version": 1,
            "event_id": f"{self.boot_id}:{self._sequence}",
            "occurred_at": datetime.fromtimestamp(self._wall_clock(), timezone.utc).isoformat(),
            "type": kind, "level": level, "job_id": job.id if job else None,
            "message": message, "data": data,
        })
        self._condition.notify_all()

    def _prune(self) -> list[str]:
        retained = set(self._recent) | set(self._queue)
        if self._active:
            retained.add(self._active)
        retained.update(job.id for job in self._jobs.values() if job.requires_intervention)
        evicted = [job_id for job_id in self._jobs if job_id not in retained]
        for job_id in evicted:
            del self._jobs[job_id]
        return evicted

    def enqueue(self, path: Path, source: str = "watcher") -> str:
        return self.admit(path, source)[0]

    def admit(self, path: Path, source: str = "watcher") -> tuple[str, bool]:
        path = Path(path).resolve()
        key = canonical(path)
        with self._condition:
            if key in self._paths:
                job_id = self._paths[key]
                if self._jobs[job_id].state == "queued":
                    self._count_requests[job_id] = None
                    self._condition.notify_all()
                return job_id, False
            if len(self._queue) >= self._queue_limit or len(self._jobs) >= self._job_limit:
                raise RuntimeCapacity("Manager history/queue capacity reached. Clear waiting work or restart after reviewing retained failures.")
            job_id = uuid.uuid4().hex
            job = Job(job_id, str(path), source, self._wall_clock(), self._clock() + self.delay)
            job.aliases.add(key)
            self._jobs[job_id] = job
            self._paths[key] = job_id
            self._queue.append(job_id)
            self._count_requests[job_id] = None
            self._emit("job.queued", "Added to the queue", job)
            return job_id, True

    def claim(self) -> Optional[dict]:
        with self._condition:
            if self._closed or self._active or not self._queue:
                return None
            job = self._jobs[self._queue[0]]
            if self._clock() < job.ready_at:
                return None
            self._queue.popleft()
            self._count_requests.pop(job.id, None)
            self._active = job.id
            job.state = "checking_files"
            job.wait_reason = None
            if job.started_at is None:
                job.started_at = self._wall_clock()
                job.started_clock = self._clock()
            self._emit("job.started", "Checking files", job)
            return job.public(self._clock())

    def configure_job(self, job_id: str, settings: dict, revision: str) -> dict:
        with self._condition:
            job = self._jobs[job_id]
            if job.settings is None:
                job.settings = copy.deepcopy(settings)
                job.config_revision = revision
                job.dry_run = bool(settings["manager.test_mode"])
            return copy.deepcopy(job.settings)

    def set_archives(self, job_id: str, count: int, resources: Iterable[str] = ()) -> None:
        with self._condition:
            job = self._jobs[job_id]
            job.archives = count
            job.archive_count_error = None
            job.resources = set(resources) if count <= 1000 else set()
            self._emit("job.updated", "Archives checked", job)

    def request_archive_count(self, path: Path) -> None:
        path = Path(path).resolve()
        with self._condition:
            for directory in (path, *path.parents):
                job_id = self._paths.get(os.path.normcase(str(directory)))
                if job_id is not None and self._jobs[job_id].state == "queued":
                    self._count_requests[job_id] = None
            self._condition.notify_all()

    def next_archive_count(self, timeout: float = 1) -> Optional[tuple[str, Path]]:
        with self._condition:
            self._condition.wait_for(lambda: self._closed or self._count_requests, timeout)
            while self._count_requests and not self._closed:
                job_id = next(iter(self._count_requests))
                del self._count_requests[job_id]
                job = self._jobs.get(job_id)
                if job is not None and job.state == "queued":
                    return job_id, Path(job.path)
            return None

    def set_queued_archives(self, job_id: str, count: Optional[int], error: Optional[str] = None) -> bool:
        if count is None and not error:
            raise ValueError("Unknown archive counts must include a counting error.")
        if count is not None and count < 0:
            raise ValueError("Archive counts cannot be negative.")
        error = error[:1000] if error else None
        with self._condition:
            job = self._jobs.get(job_id)
            if self._closed or job is None or job.state != "queued":
                return False
            if job.archives == count and job.archive_count_error == error:
                return False
            job.archives, job.archive_count_error = count, error
            self._emit("job.count_failed" if error else "job.counted",
                       f"Volume count unavailable: {error}" if error else
                       f"Waiting: {count} volume{'s' if count != 1 else ''}",
                       job, "warning" if error else "info")
            return True

    def defer(self, job_id: str) -> None:
        with self._condition:
            job = self._jobs[job_id]
            if self._active != job_id:
                raise RuntimeConflict("Only the active file check can be deferred.")
            self._active = None
            job.state = "queued"
            job.wait_reason = "files_changing"
            job.ready_at = self._clock() + self.delay
            self._queue.append(job_id)
            self._count_requests[job_id] = None
            self._emit("job.deferred", "Waiting for files to stop changing", job)

    def start_stage(self, job_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError("Unknown processing stage.")
        with self._condition:
            job = self._jobs[job_id]
            job.stage = job.state = stage
            job.wait_reason = job.resume_at = None
            self._emit("stage.started", stage.capitalize(), job)

    def finish_stage(self, job_id: str, result: StageResult) -> None:
        with self._condition:
            job = self._jobs[job_id]
            job.results[job.stage] = result.to_dict()
            job.wait_reason = job.resume_at = None
            if result.outputs:
                job.output_paths = result.outputs[:10]
            name = {"repacking": "Repack", "tagging": "Tag", "renaming": "Rename"}[job.stage]
            outcome = ("not needed" if result.outcome == "unchanged" else
                       "incomplete" if result.failed and result.changed else
                       "failed" if result.failed else "preview complete" if job.dry_run else "complete")
            self._emit("stage.finished", f"{name} {outcome}", job, "error" if result.failed else "info")

    def identity(self, job_id: str, series_id: int, title: Optional[str]) -> None:
        if type(series_id) is not int or series_id <= 0:
            raise ValueError("AniList IDs must be positive integers.")
        with self._condition:
            job = self._jobs[job_id]
            title = title[:512] if title else job.series_title
            if job.series_id == series_id and job.series_title == title:
                return
            job.series_id, job.series_title = series_id, title
            self._emit("job.identity_updated", "Series identified", job)

    def waiting(self, job_id: str, seconds: float) -> None:
        with self._condition:
            job = self._jobs[job_id]
            job.wait_reason = "rate_limited"
            job.resume_at = self._wall_clock() + seconds
            self._emit("stage.waiting", f"Waiting for AniList; retry in {seconds:g}s", job)

    def reserve_paths(self, job_id: str, directories: Iterable[Path]) -> None:
        keys = [canonical(path) for path in directories]
        with self._condition:
            job = self._jobs[job_id]
            for key in keys:
                if key not in self._paths:
                    self._paths[key] = job_id
                    job.aliases.add(key)

    def is_active(self, path: Path) -> bool:
        key = canonical(path)
        with self._condition:
            return self._active is not None and self._paths.get(key) == self._active

    def finish(self, job_id: str, issue: Optional[StageIssue] = None,
               diagnostic: str = "", *, interrupted: bool = False) -> None:
        with self._condition:
            job = self._jobs[job_id]
            if self._active != job_id:
                raise RuntimeConflict("Only the active job can finish.")
            job.finished_at = self._wall_clock()
            if job.started_clock is not None:
                job.elapsed = max(0, self._clock() - job.started_clock)
            job.state = "failed" if issue else "succeeded"
            job.wait_reason = job.resume_at = None
            if issue:
                job.requires_intervention = not interrupted
                job.error = {
                    "code": issue.code, "message": issue.message[:1000],
                    "remedy": issue.remedy[:1000], "diagnostic": diagnostic[-2000:],
                }
            else:
                self._completed += 1
                self._completed_archives = (self._completed_archives + job.archives
                                            if self._completed_archives is not None and job.archives is not None
                                            else None)
                if not job.dry_run and job.archives and all(stage in job.results for stage in STAGES):
                    for previous in self._jobs.values():
                        if (previous.requires_intervention and previous.path == job.path
                                and previous.resources
                                and previous.resources.issubset(job.resources)):
                            previous.requires_intervention = False
                            previous.resolved_by = job.id
                            self._emit("job.issue_resolved", "Issue resolved by a later successful attempt", previous,
                                       resolved_by=job.id)
            for key in job.aliases:
                if self._paths.get(key) == job.id:
                    del self._paths[key]
            self._active = None
            self._recent.append(job_id)
            evicted = self._prune()
            self._emit("job.finished", issue.message if issue else (
                "Test mode completed" if job.dry_run else "Processing completed"),
                job, "error" if issue else "success",
                completed=self._completed, completed_volumes=self._completed_archives, evicted_job_ids=evicted)

    def acknowledge(self, job_ids: Iterable[str]) -> int:
        ids = list(dict.fromkeys(job_ids))
        with self._condition:
            for job_id in ids:
                job = self._jobs.get(job_id)
                if job is None:
                    raise KeyError(job_id)
                if job.state != "failed" or not (job.requires_intervention or job.resolved_by):
                    raise RuntimeConflict("Only intervention alerts can be dismissed.")
            count = 0
            for job_id in ids:
                job = self._jobs[job_id]
                if job.acknowledged or job.resolved_by:
                    continue
                job.acknowledged = True
                count += 1
                self._emit("job.alert_acknowledged", "Alert dismissed; failure unresolved", job)
            return count

    def list_items(self) -> list[str]:
        with self._condition:
            return [self._jobs[job_id].path for job_id in self._queue]

    def clear(self) -> int:
        with self._condition:
            count = len(self._queue)
            for job_id in self._queue:
                job = self._jobs[job_id]
                job.state = "removed"
                job.finished_at = self._wall_clock()
                for key in job.aliases:
                    if self._paths.get(key) == job_id:
                        del self._paths[key]
                self._recent.append(job_id)
                self._emit("job.removed", "Removed from the waiting queue", job)
            self._queue.clear()
            self._count_requests.clear()
            evicted = self._prune()
            self._emit("queue.cleared", f"{count} waiting jobs removed", evicted_job_ids=evicted)
            return count

    def flush(self) -> None:
        with self._condition:
            for job_id in self._queue:
                self._jobs[job_id].ready_at = 0
            self._emit("queue.delays_skipped", "Wait skipped for queued jobs")

    def set_health(self, service: str, state: str) -> None:
        with self._condition:
            if self._health.get(service) != state:
                self._health[service] = state
                self._emit("service.health_changed", f"{service.replace('_', ' ').capitalize()}: {state}",
                           level="error" if state in ("failed", "stopped") else "info",
                           health=dict(self._health))

    def begin_scan(self) -> str:
        with self._condition:
            if self._rescan["state"] == "running":
                raise RuntimeConflict("A library rescan is already running.")
            scan_id = uuid.uuid4().hex
            self._rescan = {"id": scan_id, "state": "running", "started_at": self._wall_clock()}
            self._emit("rescan.started", "Library rescan started", rescan=dict(self._rescan))
            return scan_id

    def finish_scan(self, discovered: int, queued: int, error: Optional[str] = None) -> None:
        with self._condition:
            self._rescan.update(state="failed" if error else "finished", discovered=discovered,
                                queued=queued, finished_at=self._wall_clock(), error=error)
            self._emit("rescan.failed" if error else "rescan.finished",
                       error or f"Rescan queued {queued} job{'s' if queued != 1 else ''}",
                       level="error" if error else "info", rescan=dict(self._rescan))

    def settings_saved(self, revision: str) -> None:
        with self._condition:
            self._emit("settings.saved", "Settings saved", revision=revision)

    def snapshot(self) -> dict:
        with self._condition:
            return {
                "schema_version": 1, "boot_id": self.boot_id,
                "cursor": f"{self.boot_id}:{self._sequence}",
                "oldest_cursor": self._events[0]["event_id"] if self._events else None,
                "jobs": {key: job.public(self._clock()) for key, job in self._jobs.items()},
                "queue": list(self._queue), "active": self._active,
                "completed": self._completed, "completed_volumes": self._completed_archives,
                "health": dict(self._health),
                "rescan": dict(self._rescan), "events": copy.deepcopy(list(self._events)),
                "limits": {"jobs": self._job_limit, "queue": self._queue_limit,
                           "events": self._events.maxlen, "recent": self._recent.maxlen},
            }

    def events_after(self, cursor: str, timeout: float = 15) -> list[dict]:
        with self._condition:
            boot, separator, raw_sequence = cursor.rpartition(":")
            if not separator or boot != self.boot_id or not raw_sequence.isdecimal():
                raise CursorReset("The manager session changed or the cursor is invalid.")
            sequence = int(raw_sequence)
            if sequence > self._sequence:
                raise CursorReset("The event cursor is ahead of this manager.")
            self._condition.wait_for(lambda: self._sequence > sequence or self._closed, timeout)
            if self._events and sequence < int(self._events[0]["event_id"].rpartition(":")[2]) - 1:
                raise CursorReset("Earlier activity expired. Refresh the snapshot.")
            return copy.deepcopy([event for event in self._events
                                  if int(event["event_id"].rpartition(":")[2]) > sequence][:64])

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._count_requests.clear()
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed


class JobReporter:
    def __init__(self, runtime: ManagerRuntime, job_id: str):
        self.runtime, self.job_id = runtime, job_id

    def identity(self, series_id: int, title: Optional[str] = None) -> None:
        self.runtime.identity(self.job_id, series_id, title)

    def waiting(self, seconds: float) -> None:
        self.runtime.waiting(self.job_id, seconds)

    def moving(self, directories: Iterable[Path]) -> None:
        self.runtime.reserve_paths(self.job_id, directories)
