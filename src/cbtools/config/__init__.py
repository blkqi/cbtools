import json
import logging
import os
import pathlib

from typing import Dict, Any

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

CONFIG_FILE_PATH: str = os.getenv('CONFIG_FILE_PATH', os.getcwd() + '/config.json')
DEFAULT_CONFIG: Dict[str, Any] = {
    "test_mode": False,
    "library_path": "/library",
    "log_path": pathlib.Path(CONFIG_FILE_PATH).parent / 'logs',
    "series_id_filename": ".anilist.txt",
    "write_series_id_file": False,
    "move_includes": [".anilist.txt", "cover.jpg"],
    "rename_pattern": "${Series} (${Year})/${Series} ${Volume}",
}

def load_config() -> Dict[str, Any]:
    config = DEFAULT_CONFIG

    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            DEFAULT_CONFIG.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning('Failed to load config, using defaults')

    return config

config: Dict[str, Any] = load_config()
