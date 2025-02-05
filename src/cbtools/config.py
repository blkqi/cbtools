"""The cbtools config module."""

import os
import json
import logging

from pathlib import Path
from typing import Dict, Any
from .log import logger


CONFIG_FILE_PATH: str = os.getenv('CONFIG_FILE_PATH', os.getcwd() + '/config.json')
DEFAULT_CONFIG: Dict[str, Any] = {
    "logging.path": str(Path(CONFIG_FILE_PATH).parent / 'logs'),
    "logging.level": logging.INFO,
    "logging.format": '%(asctime)s [%(levelname)8s] %(message)s (%(name)s :: %(filename)s:%(lineno)s)',
    "manager.test_mode": False,
    "manager.library_path": "/library",
    "manager.processing_interval": 2,
    "rename.move_includes": [".anilist.txt", "cover.jpg"],
    "rename.pattern": "${Series} (${Year})/${Series} ${Volume}",
    "tag.series_id_filename": ".anilist.txt",
    "tag.write_series_id_file": False,
    "tag.extensions": [],
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
