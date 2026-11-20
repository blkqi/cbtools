from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pkgutil
import string
import tempfile
import threading

from pathlib import Path, PureWindowsPath

from cbtools.config import CONFIG_FILE_PATH, DEFAULT_CONFIG, config
from cbtools.log import logger
from cbtools.tag import extensions


FIELDS = {
    "manager.test_mode": ("Test mode", "boolean", "Processing", "next_job", "Preview processing without writing archive or naming changes."),
    "manager.force_webp": ("Convert JPEG pages to WebP", "boolean", "Processing", "next_job", "Convert eligible JPEG pages during repacking."),
    "manager.library_path": ("Library path", "string", "Library", "restart", "The folder watched for incoming archives."),
    "manager.api_port": ("HTTP port", "integer", "Library", "restart", "Port shared by the web UI, manager API, and CLI."),
    "tag.series_id_filename": ("AniList ID filename", "string", "Tagging & renaming", "restart", "Filename containing the AniList series ID."),
    "tag.write_series_id_file": ("Write missing ID files", "boolean", "Tagging & renaming", "next_job", "Write a known ID without replacing an existing sidecar."),
    "rename.pattern": ("Rename pattern", "string", "Tagging & renaming", "next_job", "Placeholders: Series, Year, Volume, Writer."),
    "rename.move_includes": ("Move alongside archives", "list", "Tagging & renaming", "next_job", "Additional filenames, such as cover.jpg. The manager also preserves the AniList ID file."),
    "tag.extensions": ("Tagging extensions", "list", "Tagging & renaming", "next_job", "Optional metadata additions applied during tagging."),
    "logging.level": ("Log level", "integer", "Logging", "restart", "10 debug, 20 info, 30 warning, 40 error, 50 critical."),
    "logging.path": ("Log folder", "string", "Logging", "restart", "The folder used by file logging."),
    "logging.format": ("Log format", "string", "Logging", "restart", "Python logging format."),
}
NEXT_JOB = {key for key, definition in FIELDS.items() if definition[3] == "next_job"}
EXTENSION_INFO = {
    "community_rating": ("Community rating", "Set CommunityRating to the AniList average score on a 0-5 scale."),
    "localized_series": ("Localized series", "Set LocalizedSeries to the English title when it differs from the romaji title."),
    "manga_yes": ("Manga", "Set the Manga field to Yes."),
    "manga_yesandrighttoleft": ("Manga, right-to-left", "Set the Manga field to YesAndRightToLeft."),
    "notes_cbtag": ("Tagger notes", "Write cbtools tagger information and the UTC tagging time to Notes."),
    "tag_adult": ("Adult tag", "Add the Adult tag when AniList marks the series as adult."),
    "tag_highly_rated": ("Highly rated tag", "Add the Highly Rated tag when the AniList average score is at least 85."),
    "tag_oneshot": ("Oneshot tag", "Add the Oneshot tag when the series is finished and has one volume."),
}


def _extension_option(name: str) -> dict[str, str]:
    label, help_text = EXTENSION_INFO.get(name, (name, "Installed custom extension; no description provided."))
    return {"value": name, "label": label, "help": help_text}


class SettingsValidation(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("Some settings are invalid.")


class SettingsConflict(ValueError):
    pass


def _revision(raw: bytes | None) -> str:
    return hashlib.sha256(b"missing" if raw is None else b"file:" + raw).hexdigest()


def _filename(value: str) -> bool:
    return bool(value.strip()) and len(value) <= 255 and value not in (".", "..") and not any(
        char in value for char in '/\\\x00<>:"|?*'
    )


def _editable_values(values: dict) -> dict:
    result = {key: copy.deepcopy(values[key]) for key in FIELDS}
    if isinstance(result["logging.level"], str):
        level = logging.getLevelName(result["logging.level"])
        if not isinstance(level, int):
            raise SettingsValidation({"logging.level": "Use a valid Python logging level."})
        result["logging.level"] = level
    return result


class SettingsStore:
    def __init__(self, path: str | Path, effective: dict):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._effective = copy.deepcopy(effective)
        self._configured = copy.deepcopy(effective)
        self.extension_names = sorted(
            item.name for item in pkgutil.iter_modules(extensions.__path__)
            if not item.name.startswith("_")
        )
        raw = self._raw()
        self._revision = _revision(raw)
        self._load_error = None
        self._document = {}
        if raw is not None:
            try:
                document = json.loads(raw)
                if not isinstance(document, dict):
                    raise ValueError("The configuration must be a JSON object.")
                self._document = document
            except (ValueError, UnicodeDecodeError) as error:
                self._load_error = f"Fix the configuration file and restart before saving: {error}"

    def _raw(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def capture(self) -> tuple[dict, str]:
        with self._lock:
            values = copy.deepcopy(self._effective)
            values.update({key: copy.deepcopy(self._configured[key]) for key in NEXT_JOB})
            return values, self._revision

    def read(self) -> dict:
        with self._lock:
            effective, _ = self.capture()
            configured = _editable_values(self._configured)
            effective = _editable_values(effective)
            return {
                "revision": self._revision,
                "fields": [{
                    "key": key, "label": definition[0], "type": definition[1],
                    "group": definition[2], "activation": definition[3], "help": definition[4],
                    **({"choices": self.extension_names,
                        "options": [_extension_option(name) for name in self.extension_names] + [
                            {"value": name, "label": name, "help": "Not installed; uncheck to remove."}
                            for name in dict.fromkeys(configured[key]) if name not in self.extension_names
                        ]}
                       if key == "tag.extensions" else {}),
                } for key, definition in FIELDS.items()],
                "configured": configured,
                "effective": effective,
                "defaults": _editable_values(DEFAULT_CONFIG),
                "read_only": {key: copy.deepcopy(self._effective[key])
                              for key in DEFAULT_CONFIG if key not in FIELDS},
                "restart_required": [key for key in FIELDS if key not in NEXT_JOB
                                     and configured[key] != effective[key]],
                "external_change": _revision(self._raw()) != self._revision,
                "load_error": self._load_error,
            }

    def legacy_settings(self) -> dict:
        values, _ = self.capture()
        return {key: values[key] for key in DEFAULT_CONFIG}

    def validate(self, changes: dict) -> None:
        errors = {}
        for key, value in changes.items():
            if key not in FIELDS:
                errors[key] = "This setting is not editable."
                continue
            kind = FIELDS[key][1]
            valid = (
                (kind == "boolean" and type(value) is bool)
                or (kind == "integer" and type(value) is int)
                or (kind == "string" and isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value)
                or (kind == "list" and isinstance(value, list) and len(value) <= 50
                    and all(isinstance(item, str) and len(item) <= 255 for item in value))
            )
            if not valid:
                errors[key] = f"Expected a valid {kind} value."
                continue
            if key == "manager.api_port" and not 1 <= value <= 65535:
                errors[key] = "Port must be between 1 and 65535."
            elif key == "manager.library_path":
                path = Path(value)
                if not path.is_absolute() or not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
                    errors[key] = "Use an absolute, accessible library directory."
            elif key == "tag.series_id_filename" and not _filename(value):
                errors[key] = "Use a single valid filename."
            elif key == "rename.move_includes" and not all(_filename(item) for item in value):
                errors[key] = "Use filenames only, without directory paths."
            elif key == "tag.extensions" and any(item not in self.extension_names for item in value):
                errors[key] = "Choose installed extensions: " + ", ".join(self.extension_names)
            elif key == "rename.pattern":
                try:
                    rendered = string.Template(value).substitute(
                        dict.fromkeys(("Series", "Writer", "Year", "Volume"), "sample")
                    )
                    if (not rendered.strip() or PureWindowsPath(rendered).drive
                            or rendered.startswith(("/", "\\"))
                            or ".." in rendered.replace("\\", "/").split("/")):
                        raise ValueError("The pattern must stay inside the library.")
                except (KeyError, ValueError):
                    errors[key] = "Use a relative pattern with Series, Writer, Year, and Volume placeholders."
            elif key == "logging.level" and value not in (0, 10, 20, 30, 40, 50):
                errors[key] = "Use 0, 10, 20, 30, 40, or 50."
            elif key == "logging.path" and (not value.strip() or "\x00" in value):
                errors[key] = "Use a valid log folder."
            elif key == "logging.format":
                try:
                    logging.Formatter(value).format(logging.LogRecord(
                        "cbtools", logging.INFO, __file__, 1, "message", (), None
                    ))
                except (ValueError, KeyError, TypeError):
                    errors[key] = "Use a valid Python logging format with standard record fields."
        if errors:
            raise SettingsValidation(errors)

    def save(self, changes: dict, expected_revision: str) -> dict:
        self.validate(changes)
        with self._lock:
            if self._load_error:
                raise SettingsConflict(self._load_error)
            if expected_revision != self._revision or _revision(self._raw()) != self._revision:
                raise SettingsConflict("The configuration changed. Restart/reload it before saving this draft.")
            if not changes:
                return self.read()
            document = copy.deepcopy(self._document)
            document.update(changes)
            raw = (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
                ) as file:
                    temporary = Path(file.name)
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                if self.path.exists():
                    os.chmod(temporary, self.path.stat().st_mode & 0o777)
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        logger.warning("Could not remove temporary configuration file %s", temporary, exc_info=True)
            self._document = document
            self._configured.update(copy.deepcopy(changes))
            self._revision = _revision(raw)
            return self.read()


settings_store = SettingsStore(CONFIG_FILE_PATH, config)
