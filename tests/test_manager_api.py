import copy
import tempfile
import time
import unittest

from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cbtools.anilist_auth import AniListAuth
from cbtools.activity import StageIssue
from cbtools.config import DEFAULT_CONFIG
from cbtools.manager.api import create_app
from cbtools.manager.runtime import ManagerRuntime
from cbtools.manager.settings import SettingsStore


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        values = copy.deepcopy(DEFAULT_CONFIG)
        values["manager.library_path"] = str(self.root)
        self.store = SettingsStore(self.root / "config.json", values)
        self.runtime = ManagerRuntime(0, event_limit=5)
        self.auth = AniListAuth(self.root / "anilist-auth.json")
        self.app = create_app(self.runtime, self.store, self.auth)
        self.client = self.app.test_client()
        self.token = self.client.get("/api/v1/snapshot").json["csrf_token"]
        self.headers = {"X-CSRF-Token": self.token}

    def post(self, url, value=None):
        return self.client.post(url, json={} if value is None else value, headers=self.headers)

    def test_oauth_callback_is_browser_bound_and_mutations_use_existing_guards(self):
        from test_anilist_auth import OAUTH_ENV, response

        with patch.dict("os.environ", {
            **OAUTH_ENV, "ANILIST_REDIRECT_URI": "http://localhost/api/v1/anilist/callback",
        }):
            url = "/api/v1/anilist/connect"
            self.assertEqual(self.client.post(url, json={}).status_code, 403)
            self.assertEqual(self.client.post(url, json={}, headers={
                **self.headers, "Origin": "https://other.example",
            }).status_code, 403)
            self.assertEqual(self.client.post(url, data="{}", headers=self.headers).status_code, 415)
            start = self.post(url)
            cookie = start.headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)
            self.assertIn("Path=/api/v1/anilist/callback", cookie)
            state = parse_qs(urlsplit(start.json["url"]).query)["state"][0]
            callback = "/api/v1/anilist/callback?code=fixture-code&state=" + state
            navigation = {
                "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            }
            with patch("cbtools.anilist_auth.requests.post", side_effect=[
                response({"access_token": "fixture-access-token", "expires_in": 3600}),
                response({"data": {"Viewer": {"id": 7, "name": "FixtureAccount"}}}),
            ]) as post:
                other = self.app.test_client()
                rejected = other.get(callback, headers=navigation)
                self.assertIn("anilist_error=", rejected.location)
                post.assert_not_called()
                with other.get(rejected.location, headers=navigation) as page:
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(page.mimetype, "text/html")
                finished = self.client.get(callback, headers=navigation)
                self.assertEqual(finished.status_code, 303)
                self.assertEqual(finished.location, "/ui/?anilist_connected=1")
                self.assertEqual(post.call_count, 2)
                with self.client.get(finished.location, headers=navigation) as page:
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(page.mimetype, "text/html")
            for endpoint in ("/api/v1/anilist", "/api/v1/settings", "/api/v1/snapshot", "/"):
                result = self.client.get(endpoint)
                self.assertNotIn("fixture-access-token", result.get_data(as_text=True))
                self.assertNotIn(OAUTH_ENV["ANILIST_CLIENT_SECRET"], result.get_data(as_text=True))
            self.assertEqual(self.client.delete("/api/v1/anilist", json={}).status_code, 403)
            removed = self.client.delete("/api/v1/anilist", json={}, headers=self.headers)
            self.assertEqual(removed.json["state"], "not_connected")
            self.assertFalse(self.auth.path.exists())

    def test_oauth_missing_setup_and_failed_callbacks_do_not_disclose_provider_errors(self):
        from test_anilist_auth import OAUTH_ENV, response

        with patch.dict("os.environ", {**OAUTH_ENV, "ANILIST_CLIENT_SECRET": ""}):
            status = self.client.get("/api/v1/anilist")
            self.assertFalse(status.json["can_connect"])
            self.assertEqual(self.post("/api/v1/anilist/connect").status_code, 409)
        with patch.dict("os.environ", OAUTH_ENV):
            start = self.post("/api/v1/anilist/connect")
            self.assertIn("Secure", start.headers["Set-Cookie"])
            state = parse_qs(urlsplit(start.json["url"]).query)["state"][0]
            with patch("cbtools.anilist_auth.requests.post", return_value=response({
                "message": "fixture-secret", "access_token": "fixture-secret",
            }, 401)):
                failed = self.client.get("/api/v1/anilist/callback?code=fixture-code&state=" + state)
            self.assertIn("anilist_error=", failed.location)
            self.assertNotIn("fixture-secret", failed.location)
            self.assertEqual(self.auth.status()["state"], "not_connected")

    def test_legacy_contracts_and_library_containment(self):
        series = self.root / "Series"
        series.mkdir()
        (series / "V1.cbz").write_bytes(b"fixture")
        self.assertEqual(self.client.post("/rescan", json={}).status_code, 204)
        self.assertEqual(self.client.get("/list").json, [str(series.resolve())])
        self.assertEqual(self.client.post("/flush").status_code, 204)
        self.assertEqual(self.client.post("/clear").status_code, 204)
        self.assertEqual(self.client.get("/list").json, [])
        self.assertIn("manager.library_path", self.client.get("/").json)
        self.assertEqual(self.client.post("/rescan", json={"paths": [".."]}).status_code, 400)
        self.assertEqual(self.runtime.list_items(), [])

    def test_origin_csrf_and_json_guards(self):
        self.assertEqual(self.client.post("/api/v1/queue/clear", json={}).status_code, 403)
        headers = {**self.headers, "Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/v1/queue/clear", json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/clear", headers={"Origin": "null"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/queue/clear", data="{}", headers=self.headers).status_code, 415)
        self.assertEqual(self.post("/api/v1/queue/clear", []).status_code, 400)

    def test_cross_site_ui_navigation_preserves_request_guards(self):
        navigation = {
            "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        with self.client.get("/ui", headers=navigation, follow_redirects=True) as page:
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.mimetype, "text/html")
        for endpoint, headers in (
            ("/ui/", {**navigation, "Sec-Fetch-Mode": "cors"}),
            ("/ui/", {**navigation, "Sec-Fetch-Dest": "iframe"}),
            ("/ui/", {**navigation, "Origin": "https://other.example"}),
            ("/ui/assets/index.html", navigation),
            ("/api/v1/snapshot", navigation),
            ("/api/v1/anilist", navigation),
            ("/", navigation),
        ):
            with self.subTest(endpoint=endpoint, headers=headers):
                result = self.client.get(endpoint, headers=headers)
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.json["error"]["code"], "ORIGIN_REJECTED")
        for endpoint in ("/api/v1/queue/clear", "/clear"):
            with self.subTest(endpoint=endpoint):
                result = self.client.post(endpoint, json={}, headers={**self.headers, **navigation})
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.json["error"]["code"], "ORIGIN_REJECTED")

    def test_settings_revisions_and_bulk_acknowledgement(self):
        settings = self.client.get("/api/v1/settings")
        url = "/api/v1/settings"
        self.assertEqual(self.client.patch(url, json={}, headers=self.headers).status_code, 428)
        headers = {**self.headers, "If-Match": settings.headers["ETag"]}
        self.assertEqual(self.client.patch(url, json={"manager.test_mode": True}, headers=headers).status_code, 200)
        self.assertEqual(self.client.patch(url, json={}, headers=headers).status_code, 412)
        job_id = self.runtime.enqueue(self.root)
        self.runtime.claim()
        self.runtime.finish(job_id, StageIssue("MISSING", "Missing ID", "Add it"))
        result = self.post("/api/v1/alerts/acknowledge", {"job_ids": [job_id, "missing"]})
        self.assertEqual(result.status_code, 404)
        self.assertFalse(self.runtime.snapshot()["jobs"][job_id]["acknowledged"])
        self.assertEqual(self.post("/api/v1/alerts/acknowledge", {"job_ids": [job_id]}).json, {"acknowledged": 1})
        self.assertEqual(self.runtime.snapshot()["jobs"][job_id]["state"], "failed")

    def test_tagging_extension_options_describe_the_installed_choices(self):
        settings = self.client.get("/api/v1/settings").json
        field = next(field for field in settings["fields"] if field["key"] == "tag.extensions")
        self.assertEqual([option["value"] for option in field["options"]], field["choices"])
        self.assertTrue(all(option["label"] and option["help"] for option in field["options"]))
        self.assertIsInstance(settings["configured"]["tag.extensions"], list)

    def test_uninstalled_configured_extension_remains_visible_for_removal(self):
        values, _ = self.store.capture()
        values["tag.extensions"] = ["missing_extension", "tag_oneshot"]
        store = SettingsStore(self.root / "other.json", values)
        settings = create_app(self.runtime, store).test_client().get("/api/v1/settings").json
        field = next(field for field in settings["fields"] if field["key"] == "tag.extensions")
        self.assertNotIn("missing_extension", field["choices"])
        self.assertEqual(field["options"][-1], {
            "value": "missing_extension", "label": "missing_extension",
            "help": "Not installed; uncheck to remove.",
        })
        self.assertEqual(settings["configured"]["tag.extensions"], values["tag.extensions"])

    def test_network_access_supports_published_addresses_and_hostnames(self):
        client = self.client
        base = "http://192.168.1.25:8080"
        remote = {"REMOTE_ADDR": "192.168.1.2"}
        response = client.get("/api/v1/snapshot", base_url=base, environ_overrides=remote)
        self.assertEqual(response.status_code, 200)
        token = response.json["csrf_token"]
        with client.get("/ui/", base_url=base, environ_overrides=remote) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/", base_url="http://comics.local:8080",
                                    environ_overrides=remote).status_code, 200)
        for hostname in ("comics.local", "tower", "comics.example.com"):
            with self.subTest(hostname=hostname):
                self.assertEqual(client.get("/", base_url=f"http://{hostname}",
                                            environ_overrides=remote).status_code, 200)
        url = "/api/v1/queue/clear"
        self.assertEqual(client.post(url, json={}, base_url=base, environ_overrides=remote).status_code, 403)
        headers = {"X-CSRF-Token": token, "Origin": "http://evil.example"}
        self.assertEqual(client.post(url, json={}, base_url=base, headers=headers,
                                     environ_overrides=remote).status_code, 403)
        headers["Origin"] = base
        self.assertEqual(client.post(url, json={}, base_url=base, headers=headers,
                                     environ_overrides=remote).status_code, 200)
        headers["Sec-Fetch-Site"] = "cross-site"
        self.assertEqual(client.post(url, json={}, base_url=base, headers=headers,
                                     environ_overrides=remote).status_code, 403)

    def test_async_rescan_exposes_completion_and_deduplicates(self):
        series = self.root / "Series"
        series.mkdir()
        (series / "V1.cbz").write_bytes(b"fixture")
        result = self.post("/api/v1/queue/rescan")
        self.assertEqual(result.status_code, 202)
        deadline = time.monotonic() + 2
        while self.runtime.snapshot()["rescan"]["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.runtime.snapshot()["rescan"]["state"], "finished")
        self.assertEqual(self.runtime.list_items(), [str(series)])
        self.runtime.begin_scan()
        self.assertEqual(self.post("/api/v1/queue/rescan").status_code, 409)

    def test_sse_limit_replay_reset_and_closed_stream_cleanup(self):
        cursor = self.runtime.snapshot()["cursor"]
        streams = [self.client.get("/api/v1/events?after=" + cursor, buffered=False) for _ in range(4)]
        try:
            self.assertTrue(all(response.status_code == 200 for response in streams))
            self.assertEqual(self.client.get("/api/v1/events").status_code, 503)
        finally:
            for response in streams:
                response.close()
        response = self.client.get("/api/v1/events?after=old-boot:1", buffered=False)
        try:
            chunks = iter(response.response)
            next(chunks)
            self.assertIn(b"event: reset", next(chunks))
        finally:
            response.close()
