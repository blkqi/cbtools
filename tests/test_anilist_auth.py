import json
import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from cbtools.anilist.auth import AniListAuth, AniListAuthError
from cbtools.anilist import AniList
from cbtools.exceptions import AnilistApiError
from cbtools.tag import _fetch_comic_info


def response(value, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(value).encode()
    result._content_consumed = True
    return result


OAUTH_ENV = {
    "ANILIST_CLIENT_ID": "123",
    "ANILIST_CLIENT_SECRET": "fixture-client-secret",
    "ANILIST_REDIRECT_URI": "https://manager.example/api/v1/anilist/callback",
    "ANILIST_TOKEN": "",
}


class AniListAuthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock = 1000.0
        self.auth = AniListAuth(self.root / "anilist-auth.json", clock=lambda: self.clock)
        self.env = patch.dict(os.environ, OAUTH_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)

    def connect(self, token="fixture-access-token"):
        _, state, _ = self.auth.begin()
        with patch("cbtools.anilist.auth.requests.post", side_effect=[
            response({"access_token": token, "expires_in": 3600}),
            response({"data": {"Viewer": {"id": 7, "name": "FixtureAccount"}}}),
        ]) as post:
            self.auth.complete(state, state, "fixture-code")
        return state, post

    def test_oauth_validates_account_and_persists_without_exposing_the_token(self):
        state, post = self.connect()
        self.assertEqual(post.call_args_list[0].kwargs["json"]["client_secret"], OAUTH_ENV["ANILIST_CLIENT_SECRET"])
        self.assertEqual(post.call_args_list[1].kwargs["headers"], {"Authorization": "Bearer fixture-access-token"})
        status = self.auth.status()
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["account"], "FixtureAccount")
        self.assertEqual(status["expires_at"], 4600)
        self.assertNotIn("fixture-access-token", json.dumps(status))
        restored = AniListAuth(self.auth.path, clock=lambda: self.clock)
        self.assertEqual(restored.token(), "fixture-access-token")
        if os.name != "nt":
            self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)
        with patch("cbtools.anilist.auth.requests.post") as repeat:
            with self.assertRaises(AniListAuthError):
                self.auth.complete(state, state, "fixture-code")
            repeat.assert_not_called()

    def test_browser_binding_expiration_and_denial_prevent_token_exchange(self):
        url, state, secure = self.auth.begin()
        self.assertTrue(secure)
        self.assertEqual(parse_qs(urlsplit(url).query)["state"], [state])
        self.assertNotIn(OAUTH_ENV["ANILIST_CLIENT_SECRET"], url)
        with patch("cbtools.anilist.auth.requests.post") as post:
            for browser_state in ("", "another-browser", "\N{SNOWMAN}"):
                with self.subTest(browser_state=browser_state), self.assertRaises(AniListAuthError):
                    self.auth.complete(state, browser_state, "fixture-code")
            self.clock += 601
            with self.assertRaises(AniListAuthError):
                self.auth.complete(state, state, "fixture-code")
            _, denied_state, _ = self.auth.begin()
            with self.assertRaisesRegex(AniListAuthError, "cancelled"):
                self.auth.complete(denied_state, denied_state, "", denied=True)
            post.assert_not_called()

    def test_failed_exchange_or_account_validation_preserves_existing_credentials(self):
        self.connect()
        fixtures = [
            [requests.Timeout("fixture-secret")],
            [response({"error": "fixture-access-token"}, 401)],
            [response({"access_token": "bad\nheader", "expires_in": 3600})],
            [response({"access_token": "replacement", "expires_in": True})],
            [response({"access_token": "replacement", "expires_in": 3600}),
             response({"data": {"Viewer": None}, "errors": [{"message": "replacement"}]})],
        ]
        for results in fixtures:
            with self.subTest(results=len(results)):
                _, state, _ = self.auth.begin()
                with patch("cbtools.anilist.auth.requests.post", side_effect=results):
                    with self.assertRaises(AniListAuthError) as error:
                        self.auth.complete(state, state, "fixture-code")
                self.assertNotIn("fixture-access-token", str(error.exception))
                self.assertNotIn("replacement", str(error.exception))
                self.assertEqual(self.auth.token(), "fixture-access-token")

    def test_failed_atomic_save_does_not_replace_active_or_persisted_credentials(self):
        self.connect()
        before = self.auth.path.read_bytes()
        with patch("cbtools.anilist.auth.os.replace", side_effect=PermissionError("fixture failure")):
            with self.assertRaisesRegex(AniListAuthError, "Could not save"):
                self.connect("replacement")
        self.assertEqual(self.auth.token(), "fixture-access-token")
        self.assertEqual(self.auth.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".anilist-auth-*")), [])

    def test_removal_cancels_an_inflight_connection(self):
        _, state, _ = self.auth.begin()

        def exchange(url, **kwargs):
            if url.endswith("/token"):
                self.auth.remove()
                return response({"access_token": "fixture-access-token", "expires_in": 3600})
            return response({"data": {"Viewer": {"id": 7, "name": "FixtureAccount"}}})

        with patch("cbtools.anilist.auth.requests.post", side_effect=exchange):
            with self.assertRaisesRegex(AniListAuthError, "changed during"):
                self.auth.complete(state, state, "fixture-code")
        self.assertIsNone(self.auth.token())
        self.assertFalse(self.auth.path.exists())

    def test_environment_override_and_expired_or_rejected_credentials_are_explicit(self):
        self.connect()
        with patch.dict(os.environ, {"ANILIST_TOKEN": "fixture-environment-token"}):
            self.assertEqual(self.auth.token(), "fixture-environment-token")
            self.assertEqual(self.auth.status()["source"], "environment")
            with self.assertRaises(AniListAuthError):
                self.auth.begin()
            with self.assertRaises(AniListAuthError):
                self.auth.remove()
        self.auth.reject("fixture-access-token")
        self.assertIsNone(self.auth.token())
        self.assertEqual(self.auth.status()["state"], "invalid")
        self.connect("replacement")
        self.clock = 4601
        self.assertEqual(self.auth.status()["state"], "expired")
        self.assertIsNone(self.auth.token())
        self.auth.remove()
        self.assertEqual(self.auth.status()["state"], "not_connected")

    def test_corrupt_storage_and_malformed_setup_are_reported_without_secrets(self):
        self.auth.path.write_text('{"access_token": "fixture-secret"}', encoding="utf-8")
        status = self.auth.status()
        self.assertEqual(status["state"], "storage_error")
        self.assertNotIn("fixture-secret", json.dumps(status))
        with self.assertRaises(AniListAuthError):
            self.auth.token()
        self.connect()
        for uri in ("https://[invalid", "https://manager.example:bad/api/v1/anilist/callback",
                    "https://manager.example/wrong", "https://manager.example/api/v1/anilist/callback?extra=1"):
            with self.subTest(uri=uri), patch.dict(os.environ, {"ANILIST_REDIRECT_URI": uri}):
                self.assertFalse(self.auth.status()["can_connect"])
                with self.assertRaises(AniListAuthError):
                    self.auth.begin()

    def test_cached_metadata_uses_each_tagging_settings_snapshot(self):
        payload = {"data": {"Media": {
            "id": 7, "title": {"romaji": "Example Series", "english": "Example English"},
            "siteUrl": "https://anilist.co/manga/7", "volumes": 2, "averageScore": 84,
            "startDate": {"year": 2020}, "genres": [], "tags": [],
            "staff": {"edges": []}, "studios": {"edges": []},
        }}}
        with patch("cbtools.anilist.client.anilist_auth", self.auth):
            client = AniList(reporter=Mock())
        self.addCleanup(client.session.close)
        with patch.object(client.session, "post", return_value=response(payload)) as post:
            first = _fetch_comic_info(client, self.root / "V1.cbz", 7, dryrun=True,
                                     settings={"tag.extensions": ["community_rating", "localized_series"]})
            self.assertEqual(first["Series"], "Example Series")
            self.assertEqual(first["Count"], "2")
            self.assertEqual(first["CommunityRating"], "4.2")
            self.assertEqual(first["LocalizedSeries"], "Example English")
            second = _fetch_comic_info(client, self.root / "V2.cbz", 7, dryrun=True,
                                      settings={"tag.extensions": []})
            self.assertNotIn("CommunityRating", second)
            self.assertNotIn("LocalizedSeries", second)
            with self.assertRaisesRegex(AnilistApiError, "tagging extension"):
                _fetch_comic_info(client, self.root / "V3.cbz", 7, dryrun=True,
                                 settings={"tag.extensions": ["missing_extension"]})
            post.assert_called_once()

    def test_tagging_uses_saved_credentials_and_a_rejected_token_becomes_anonymous(self):
        self.connect()
        unauthorized = response({}, 401)
        with patch("cbtools.anilist.client.anilist_auth", self.auth):
            client = AniList()
            self.assertEqual(client.session.headers["Authorization"], "Bearer fixture-access-token")
            with patch.object(client.session, "post", side_effect=[
                unauthorized, response({"data": {"Media": {"id": 7}}}),
            ]) as post, patch.object(unauthorized, "close") as close:
                self.assertEqual(client.search(7).media["id"], 7)
                self.assertEqual(post.call_count, 2)
                close.assert_called_once()
            self.assertEqual(self.auth.status()["state"], "invalid")
            self.assertNotIn("Authorization", AniList().session.headers)
        client.session.close()


if __name__ == "__main__":
    unittest.main()
