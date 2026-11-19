import copy
import tempfile
import time
import unittest

from pathlib import Path

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
        self.app = create_app(self.runtime, self.store)
        self.client = self.app.test_client()
        self.token = self.client.get("/api/v1/snapshot").json["csrf_token"]
        self.headers = {"X-CSRF-Token": self.token}

    def post(self, url, value=None):
        return self.client.post(url, json={} if value is None else value, headers=self.headers)

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
