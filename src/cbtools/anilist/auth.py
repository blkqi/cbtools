from __future__ import annotations

import json
import math
import os
import secrets
import tempfile
import threading
import time

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from cbtools.config import CONFIG_FILE_PATH
from cbtools.log import logger


CALLBACK_PATH = "/api/v1/anilist/callback"
STATE_SECONDS = 600
OAUTH_ENV = ("ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "ANILIST_REDIRECT_URI")


class AniListAuthError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    account: str
    expires_at: float


def _token(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 16384 and value.isascii()
            and value.isprintable() and not any(char.isspace() for char in value))


class AniListAuth:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._credentials: Credentials | None = None
        self._loaded = False
        self._load_error: str | None = None
        self._rejected_token: str | None = None
        self._generation = 0
        self._pending: dict[str, tuple[float, int]] = {}

    def _load(self) -> None:
        if not self._loaded:
            self._loaded = True
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(value, dict) or not _token(value.get("access_token"))
                        or not isinstance(value.get("account"), str) or not 0 < len(value["account"]) <= 256
                        or type(value.get("expires_at")) not in (int, float)
                        or not math.isfinite(value["expires_at"]) or value["expires_at"] <= 0):
                    raise ValueError("Invalid credential document")
                self._credentials = Credentials(value["access_token"], value["account"], value["expires_at"])
            except FileNotFoundError:
                pass
            except (OSError, ValueError, UnicodeError):
                self._load_error = "Could not read AniList credentials. Check file permissions or reconnect to replace the file."
                logger.warning(self._load_error)
        if self._load_error:
            raise AniListAuthError(self._load_error, 503)

    def _setup(self) -> tuple[dict[str, str], str | None]:
        values = {key: os.getenv(key, "") for key in OAUTH_ENV}
        missing = [key for key, value in values.items() if not value.strip()]
        if missing:
            return values, "Set " + ", ".join(missing) + " on the server to enable OAuth."
        try:
            uri = urlsplit(values["ANILIST_REDIRECT_URI"])
            valid = (uri.scheme in ("http", "https") and uri.hostname and not uri.username and not uri.password
                     and uri.path == CALLBACK_PATH and not uri.query and not uri.fragment)
            uri.port
        except ValueError:
            valid = False
        if not valid:
            return values, f"ANILIST_REDIRECT_URI must be an HTTP(S) URL ending in {CALLBACK_PATH}, without a query or fragment."
        return values, None

    def status(self) -> dict:
        with self._lock:
            _, setup_error = self._setup()
            environment = os.getenv("ANILIST_TOKEN")
            if environment:
                return {"state": "invalid" if environment == self._rejected_token else "connected",
                        "source": "environment", "account": None, "expires_at": None,
                        "can_connect": False, "can_remove": False, "setup_error": setup_error, "error": None}
            try:
                self._load()
            except AniListAuthError as error:
                return {"state": "storage_error", "source": "oauth", "account": None, "expires_at": None,
                        "can_connect": not setup_error, "can_remove": True, "setup_error": setup_error,
                        "error": str(error)}
            credential = self._credentials
            state = "not_connected"
            if credential:
                state = ("expired" if credential.expires_at <= self._clock() else
                         "invalid" if credential.access_token == self._rejected_token else "connected")
            return {"state": state, "source": "oauth" if credential else None,
                    "account": credential.account if credential else None,
                    "expires_at": credential.expires_at if credential else None,
                    "can_connect": not setup_error, "can_remove": credential is not None,
                    "setup_error": setup_error, "error": None}

    def token(self) -> str | None:
        with self._lock:
            environment = os.getenv("ANILIST_TOKEN")
            if environment:
                return environment if environment != self._rejected_token else None
            self._load()
            credential = self._credentials
            if not credential or credential.access_token == self._rejected_token:
                return None
            if credential.expires_at <= self._clock():
                self.reject(credential.access_token)
                return None
            return credential.access_token

    def reject(self, token: str) -> None:
        with self._lock:
            if token != self._rejected_token:
                self._rejected_token = token
                logger.warning("AniList authentication expired or was rejected; reconnect in Settings. Anonymous access remains available.")

    def begin(self, previous_state: str | None = None) -> tuple[str, str, bool]:
        with self._lock:
            if os.getenv("ANILIST_TOKEN"):
                raise AniListAuthError("Remove ANILIST_TOKEN from the environment before connecting through OAuth.", 409)
            values, error = self._setup()
            if error:
                raise AniListAuthError(error, 409)
            now = self._clock()
            self._pending = {key: value for key, value in self._pending.items() if value[0] > now}
            if previous_state:
                self._pending.pop(previous_state, None)
            if len(self._pending) >= 16:
                raise AniListAuthError("Too many pending AniList connections. Wait ten minutes before trying again.", 429)
            state = secrets.token_urlsafe(32)
            self._pending[state] = (now + STATE_SECONDS, self._generation)
            url = "https://anilist.co/api/v2/oauth/authorize?" + urlencode({
                "client_id": values["ANILIST_CLIENT_ID"], "redirect_uri": values["ANILIST_REDIRECT_URI"],
                "response_type": "code", "state": state,
            })
            return url, state, urlsplit(values["ANILIST_REDIRECT_URI"]).scheme == "https"

    @staticmethod
    def _post(url: str, **kwargs) -> dict:
        try:
            with requests.post(url, timeout=(10, 30), allow_redirects=False, **kwargs) as response:
                if response.status_code != 200:
                    raise AniListAuthError("AniList authorization failed. Start a new connection and try again.", 502)
                value = response.json()
                if not isinstance(value, dict):
                    raise AniListAuthError("AniList returned an invalid authorization response.", 502)
                return value
        except AniListAuthError:
            raise
        except (requests.RequestException, ValueError):
            raise AniListAuthError("Could not complete AniList authorization. Check network access and try again.", 502) from None

    def complete(self, state: str, browser_state: str, code: str, *, denied=False) -> None:
        with self._lock:
            if (not state or len(state) > 128 or not state.isascii() or not browser_state or not browser_state.isascii()
                    or not secrets.compare_digest(state, browser_state)):
                raise AniListAuthError("AniList connection could not be verified. Start again from Settings.", 403)
            pending = self._pending.pop(state, None)
            if not pending or pending[0] <= self._clock() or pending[1] != self._generation:
                raise AniListAuthError("AniList connection expired or was already used. Start again from Settings.", 403)
            generation = pending[1]
            if denied:
                raise AniListAuthError("AniList authorization was cancelled.")
            if not code or len(code) > 4096:
                raise AniListAuthError("AniList did not return a valid authorization code.")
            values, error = self._setup()
            if error:
                raise AniListAuthError(error, 409)
        value = self._post("https://anilist.co/api/v2/oauth/token", json={
            "grant_type": "authorization_code", "client_id": values["ANILIST_CLIENT_ID"],
            "client_secret": values["ANILIST_CLIENT_SECRET"], "redirect_uri": values["ANILIST_REDIRECT_URI"],
            "code": code,
        })
        token, expires = value.get("access_token"), value.get("expires_in")
        if not _token(token) or type(expires) is not int or not 0 < expires <= 2147483647:
            raise AniListAuthError("AniList returned an invalid access token or expiration.", 502)
        expires_at = self._clock() + expires
        viewer = self._post("https://graphql.anilist.co",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"query": "query { Viewer { id name } }"})
        data = viewer.get("data")
        account = data.get("Viewer") if isinstance(data, dict) else None
        if (viewer.get("errors") or not isinstance(account, dict) or type(account.get("id")) is not int
                or account["id"] <= 0 or not isinstance(account.get("name"), str)
                or not 0 < len(account["name"]) <= 256):
            raise AniListAuthError("AniList could not verify the connected account.", 502)
        credential = Credentials(token, account["name"], expires_at)
        with self._lock:
            if generation != self._generation or os.getenv("ANILIST_TOKEN"):
                raise AniListAuthError("AniList authentication changed during connection. Start again from Settings.", 409)
            self._save(credential)
            self._credentials, self._loaded, self._load_error = credential, True, None
            self._rejected_token = None
            self._generation += 1
            self._pending.clear()

    def _save(self, credential: Credentials) -> None:
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".anilist-auth-", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"access_token": credential.access_token, "account": credential.account,
                           "expires_at": credential.expires_at}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AniListAuthError("Could not save AniList credentials. Check write access to the configuration directory.", 503) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def remove(self) -> None:
        with self._lock:
            if os.getenv("ANILIST_TOKEN"):
                raise AniListAuthError("Authentication is managed by ANILIST_TOKEN; remove it from the environment instead.", 409)
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                raise AniListAuthError("Could not remove AniList credentials. Check configuration directory permissions.", 503) from None
            self._credentials, self._loaded, self._load_error = None, True, None
            self._rejected_token = None
            self._generation += 1
            self._pending.clear()


anilist_auth = AniListAuth(Path(CONFIG_FILE_PATH).parent / "anilist-auth.json")
