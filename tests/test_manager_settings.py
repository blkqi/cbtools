import copy
import importlib
import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from cbtools.config import DEFAULT_CONFIG
from cbtools.manager.settings import SettingsConflict, SettingsStore, SettingsValidation


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "config.json"
        self.values = copy.deepcopy(DEFAULT_CONFIG)
        self.values["manager.library_path"] = str(self.root)
        self.store = SettingsStore(self.path, self.values)

    def test_atomic_save_preserves_unrelated_keys_and_activation_policies(self):
        self.path.write_text(json.dumps({"convert.jobs": 7, "private.token": "not-for-the-ui"}))
        store = SettingsStore(self.path, self.values)
        initial = store.read()
        snapshot, _ = store.capture()
        saved = store.save({"manager.test_mode": True, "manager.api_port": 9000}, initial["revision"])
        self.assertTrue(saved["effective"]["manager.test_mode"])
        self.assertEqual(saved["effective"]["manager.api_port"], 8080)
        self.assertEqual(saved["restart_required"], ["manager.api_port"])
        self.assertFalse(snapshot["manager.test_mode"])
        disk = json.loads(self.path.read_text())
        self.assertEqual(disk["convert.jobs"], 7)
        self.assertEqual(disk["private.token"], "not-for-the-ui")
        self.assertNotIn("not-for-the-ui", json.dumps(saved))
        self.assertNotIn("private.token", store.legacy_settings())

    def test_failed_replace_and_external_edits_do_not_commit(self):
        initial = self.store.read()
        with patch("cbtools.manager.settings.os.replace", side_effect=PermissionError("read-only")):
            with self.assertRaises(PermissionError):
                self.store.save({"manager.test_mode": True}, initial["revision"])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.capture()[0]["manager.test_mode"])
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.path.write_text('{"manager.test_mode": true}')
        with self.assertRaises(SettingsConflict):
            self.store.save({"manager.force_webp": True}, initial["revision"])
        self.assertTrue(self.store.read()["external_change"])

    def test_named_logging_level_has_a_clean_editable_value_and_preserves_legacy_config(self):
        self.values["logging.level"] = "DEBUG"
        self.path.write_text(json.dumps({"logging.level": "DEBUG"}))
        store = SettingsStore(self.path, self.values)
        initial = store.read()
        self.assertEqual(initial["configured"]["logging.level"], 10)
        self.assertEqual(initial["effective"]["logging.level"], 10)
        saved = store.save({"manager.test_mode": True}, initial["revision"])
        self.assertEqual(saved["restart_required"], [])
        self.assertEqual(json.loads(self.path.read_text())["logging.level"], "DEBUG")
        self.assertEqual(store.legacy_settings()["logging.level"], "DEBUG")
        changed = store.save({"logging.level": 20}, saved["revision"])
        self.assertEqual(changed["restart_required"], ["logging.level"])
        restored = store.save({"logging.level": 10}, changed["revision"])
        self.assertEqual(restored["restart_required"], [])

    def test_schema_rejects_bad_types_paths_templates_and_extensions(self):
        changes = {
            "manager.api_port": True, "manager.library_path": str(self.root / "missing"),
            "tag.series_id_filename": "../token", "rename.pattern": "../${Series}",
            "tag.extensions": ["not_installed"], "logging.format": "%(unknown_field)s",
            "private.token": "new", "rename.move_includes": ["../cover.jpg"],
        }
        with self.assertRaises(SettingsValidation) as result:
            self.store.save(changes, self.store.read()["revision"])
        self.assertEqual(set(result.exception.fields), set(changes))
        self.assertFalse(self.path.exists())

    def test_defaults_are_independent_when_loading_configuration(self):
        module = importlib.import_module("cbtools.config")
        self.path.write_text('{"manager.test_mode": true, "tag.extensions": ["manga_yes"]}')
        before = copy.deepcopy(DEFAULT_CONFIG)
        with patch.object(module, "CONFIG_FILE_PATH", str(self.path)):
            loaded = module.load_config()
        loaded["rename.move_includes"].append("extra")
        self.assertEqual(DEFAULT_CONFIG, before)
