from __future__ import annotations

import json
import secrets
import threading
import time

from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request
from urllib.parse import urlencode
from werkzeug.exceptions import HTTPException

from cbtools.constants import SUPPORTED_FILE_EXTENSIONS
from cbtools.log import logger
from cbtools.manager.queue import manager_queue
from cbtools.manager.runtime import CursorReset, ManagerRuntime, RuntimeCapacity, RuntimeConflict
from cbtools.manager.settings import SettingsConflict, SettingsStore, SettingsValidation, settings_store
from cbtools.anilist.auth import AniListAuth, AniListAuthError, CALLBACK_PATH, STATE_SECONDS, anilist_auth


class APIError(ValueError):
    def __init__(self, message: str, status: int = 400, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.status, self.code = status, code


def contained_directory(root: Path, path: str) -> Path:
    if not path or len(path) > 4096 or "\x00" in path:
        raise APIError("Rescan paths must be valid directory paths.")
    directory = (root / path).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        raise APIError("Rescan paths must be existing directories inside the configured library.")
    return directory


def scan_library(runtime: ManagerRuntime, root: Path, paths: list[Path] | None, *, propagate=False) -> None:
    discovered = queued = 0
    try:
        candidates = paths if paths is not None else root.iterdir()
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            directory = contained_directory(root, str(candidate))
            if paths is None and not any(
                file.is_file() and not file.is_symlink() and file.suffix.lower() in SUPPORTED_FILE_EXTENSIONS
                for file in directory.iterdir()
            ):
                continue
            discovered += 1
            _, admitted = runtime.admit(directory, "rescan")
            queued += int(admitted)
        runtime.finish_scan(discovered, queued)
    except Exception as error:
        logger.exception("Library rescan failed")
        runtime.finish_scan(discovered, queued, str(error)[:1000])
        if propagate:
            raise


def create_app(runtime: ManagerRuntime | None = None, settings: SettingsStore | None = None,
               auth: AniListAuth | None = None) -> Flask:
    runtime = manager_queue if runtime is None else runtime
    settings = settings_store if settings is None else settings
    auth = anilist_auth if auth is None else auth
    app = Flask(__name__, static_folder="web", static_url_path="/ui/assets")
    app.config.update(MAX_CONTENT_LENGTH=65536, SSE_HEARTBEAT=15, SSE_MAX_SECONDS=300)
    csrf_token = secrets.token_urlsafe(32)
    streams = threading.BoundedSemaphore(4)
    oauth_cookie = "cbmanager_anilist_state"

    @app.before_request
    def request_guards():
        # OAuth redirects arrive cross-site; the callback verifies browser-bound state instead.
        if request.endpoint == "anilist_callback" and request.method == "GET":
            return
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            raise APIError("Cross-origin requests are not allowed.", 403, "ORIGIN_REJECTED")
        # OAuth return redirects retain cross-site metadata for the UI document.
        ui_navigation = (
            request.method == "GET"
            and request.endpoint in ("ui", "ui_redirect")
            and request.headers.get("Sec-Fetch-Mode") == "navigate"
            and request.headers.get("Sec-Fetch-Dest") == "document"
        )
        if request.headers.get("Sec-Fetch-Site") == "cross-site" and not ui_navigation:
            raise APIError("Cross-site requests are not allowed.", 403, "ORIGIN_REJECTED")
        if request.path.startswith("/api/v1/") and request.method in ("POST", "PATCH", "DELETE"):
            supplied = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(supplied, csrf_token):
                raise APIError("Refresh the manager connection before changing state.", 403, "CSRF_REJECTED")
            if not request.is_json:
                raise APIError("Send an application/json request.", 415, "JSON_REQUIRED")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if not request.path.startswith("/ui/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(APIError)
    def invalid_request(error):
        return jsonify(error={"code": error.code, "message": str(error)}), error.status

    @app.errorhandler(SettingsValidation)
    def invalid_settings(error):
        return jsonify(error={"code": "INVALID_SETTINGS", "message": str(error), "fields": error.fields}), 400

    @app.errorhandler(AniListAuthError)
    def invalid_anilist_auth(error):
        logger.warning("AniList authentication: %s", error)
        return jsonify(error={"code": "ANILIST_AUTH", "message": str(error)}), error.status

    @app.get("/api/v1/anilist")
    def anilist_status():
        return jsonify(auth.status())

    @app.post("/api/v1/anilist/connect")
    def anilist_connect():
        url, state, secure = auth.begin(request.cookies.get(oauth_cookie))
        response = jsonify(url=url)
        response.set_cookie(oauth_cookie, state, max_age=STATE_SECONDS, httponly=True,
                            secure=secure, samesite="Lax", path=CALLBACK_PATH)
        return response

    @app.get(CALLBACK_PATH)
    def anilist_callback():
        try:
            auth.complete(request.args.get("state", ""), request.cookies.get(oauth_cookie, ""),
                          request.args.get("code", ""), denied="error" in request.args)
        except AniListAuthError as error:
            logger.warning("AniList OAuth callback: %s", error)
            response = redirect("/ui/?" + urlencode({"anilist_error": str(error)}), code=303)
        else:
            response = redirect("/ui/?anilist_connected=1", code=303)
        response.delete_cookie(oauth_cookie, path=CALLBACK_PATH)
        return response

    @app.delete("/api/v1/anilist")
    def anilist_remove():
        auth.remove()
        return jsonify(auth.status())

    @app.errorhandler(SettingsConflict)
    def settings_conflict(error):
        return jsonify(error={"code": "SETTINGS_CONFLICT", "message": str(error)}), 412

    @app.errorhandler(RuntimeConflict)
    def runtime_conflict(error):
        return jsonify(error={"code": "CONFLICT", "message": str(error)}), 409

    @app.errorhandler(RuntimeCapacity)
    def runtime_capacity(error):
        return jsonify(error={"code": "CAPACITY_REACHED", "message": str(error)}), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error={"code": error.name, "message": error.description}), error.code

    @app.errorhandler(Exception)
    def unexpected_error(_error):
        logger.exception("Manager request failed")
        return jsonify(error={"code": "INTERNAL_ERROR", "message": "Manager request failed. Check the diagnostic logs."}), 500

    def body() -> dict:
        value = request.get_json()
        if not isinstance(value, dict):
            raise APIError("The request body must be a JSON object.")
        return value

    def scan_paths(value: dict) -> tuple[Path, list[Path] | None]:
        if set(value) - {"paths"}:
            raise APIError("Only paths may be specified for a rescan.")
        effective, _ = settings.capture()
        root = Path(effective["manager.library_path"]).resolve()
        if not root.is_dir():
            raise APIError("The configured library directory is unavailable.", 409, "LIBRARY_UNAVAILABLE")
        paths = value.get("paths")
        if paths is None or paths == []:
            return root, None
        if not isinstance(paths, list) or len(paths) > 500 or not all(isinstance(path, str) for path in paths):
            raise APIError("paths must be an array of at most 500 directory paths.")
        return root, [contained_directory(root, path) for path in paths]

    @app.get("/")
    def info():
        return jsonify(settings.legacy_settings())

    @app.get("/list")
    def list_items():
        return jsonify(runtime.list_items())

    @app.post("/flush")
    def legacy_flush():
        runtime.flush()
        return "", 204

    @app.post("/clear")
    def legacy_clear():
        runtime.clear()
        return "", 204

    @app.post("/rescan")
    def legacy_rescan():
        value = body() if request.data else {}
        root, paths = scan_paths(value)
        runtime.begin_scan()
        scan_library(runtime, root, paths, propagate=True)
        return "", 204

    @app.get("/ui")
    def ui_redirect():
        return redirect("/ui/", code=308)

    @app.get("/ui/")
    def ui():
        return app.send_static_file("index.html")

    @app.get("/api/v1/snapshot")
    def snapshot():
        value = runtime.snapshot()
        value["csrf_token"] = csrf_token
        return jsonify(value)

    @app.get("/api/v1/events")
    def events():
        if not streams.acquire(blocking=False):
            raise APIError("Four event streams are already open. Use snapshot polling or close another tab.", 503, "STREAM_LIMIT")
        cursor = request.headers.get("Last-Event-ID") or request.args.get("after") or runtime.snapshot()["cursor"]
        release_lock = threading.Lock()
        released = False

        def release():
            nonlocal released
            with release_lock:
                if not released:
                    released = True
                    streams.release()

        def generate():
            nonlocal cursor
            deadline = time.monotonic() + app.config["SSE_MAX_SECONDS"]
            try:
                yield "retry: 2000\n: connected\n\n"
                while not runtime.closed and time.monotonic() < deadline:
                    try:
                        pending = runtime.events_after(cursor, app.config["SSE_HEARTBEAT"])
                    except CursorReset as error:
                        yield "event: reset\ndata: " + json.dumps({"message": str(error)}) + "\n\n"
                        return
                    if not pending:
                        yield ': heartbeat\nevent: heartbeat\ndata: {}\n\n'
                    for event in pending:
                        cursor = event["event_id"]
                        yield f"id: {cursor}\nevent: activity\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            finally:
                release()

        response = Response(generate(), mimetype="text/event-stream")
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.call_on_close(release)
        return response

    @app.post("/api/v1/queue/flush")
    def flush():
        body()
        runtime.flush()
        return "", 204

    @app.post("/api/v1/queue/clear")
    def clear():
        body()
        return jsonify(removed=runtime.clear())

    @app.post("/api/v1/queue/rescan")
    def rescan():
        root, paths = scan_paths(body())
        scan_id = runtime.begin_scan()
        threading.Thread(target=scan_library, args=(runtime, root, paths), daemon=True,
                         name="cbmanager-rescan").start()
        return jsonify(id=scan_id, state="running"), 202

    def acknowledge_ids(ids: list[str]):
        try:
            count = runtime.acknowledge(ids)
        except KeyError as error:
            raise APIError("An alert is no longer retained. Refresh the snapshot.", 404, "JOB_NOT_FOUND") from error
        return jsonify(acknowledged=count)

    @app.post("/api/v1/jobs/<job_id>/acknowledge")
    def acknowledge(job_id):
        body()
        return acknowledge_ids([job_id])

    @app.post("/api/v1/alerts/acknowledge")
    def acknowledge_all():
        value = body()
        ids = value.get("job_ids")
        if set(value) != {"job_ids"} or not isinstance(ids, list) or len(ids) > 1500 or not all(isinstance(item, str) for item in ids):
            raise APIError("job_ids must contain the displayed alert IDs.")
        return acknowledge_ids(ids)

    @app.get("/api/v1/settings")
    def get_settings():
        value = settings.read()
        response = jsonify(value)
        response.set_etag(value["revision"])
        return response

    @app.patch("/api/v1/settings")
    def save_settings():
        expected = request.headers.get("If-Match")
        if not expected:
            raise APIError("Send the settings revision with If-Match.", 428, "REVISION_REQUIRED")
        value = settings.save(body(), expected.strip('"'))
        runtime.settings_saved(value["revision"])
        response = jsonify(value)
        response.set_etag(value["revision"])
        return response

    return app


app = create_app()
