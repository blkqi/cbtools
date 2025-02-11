import json
import logging
import os
import pathlib

from cbtools.log import logger

CONFIG_FILE_PATH = os.getenv('CONFIG_FILE_PATH', os.getcwd() + '/config.json')
DEFAULT_CONFIG = {
    "logging.path": str(pathlib.Path(CONFIG_FILE_PATH).parent / 'logs'),
    "logging.level": logging.INFO,
    "logging.format": '%(asctime)s [%(levelname)8s] %(message)s (%(name)s :: %(filename)s:%(lineno)s)',
    "manager.test_mode": False,
    "manager.library_path": "/library",
    "manager.processing_interval": 2,
    "manager.api_port": 8080,
    "rename.move_includes": [".anilist.txt", "cover.jpg"],
    "rename.pattern": "${Series} (${Year})/${Series} ${Volume}",
    "tag.series_id_filename": ".anilist.txt",
    "tag.write_series_id_file": False,
    "tag.extensions": [],
}

def load_config():
    config = DEFAULT_CONFIG

    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            DEFAULT_CONFIG.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning('Failed to load config, using defaults')

    return config

config = load_config()
