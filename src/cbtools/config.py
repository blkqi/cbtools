import json
import copy
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
    "manager.api_port": 8080,
    "manager.force_webp": False,
    "rename.move_includes": [".anilist.txt", "cover.jpg"],
    "rename.pattern": "${Series} (${Year})/${Series} ${Volume}",
    "tag.series_id_filename": ".anilist.txt",
    "tag.write_series_id_file": False,
    "tag.extensions": [],
    "convert.suffix": "converted",
    "convert.jobs": 16,
    "image.size": (1860, 2480),
    "image.gamma" : 1/1.8,
    "image.gain" : 1,
    "image.format": "JPEG",
    "image.quality": 85,
    "image.optimize": 1,
    "image.background": "black",
    "image.upscale.cutoff": 4,
    "image.upscale.factor": 2,
    "image.upscale.noise": 2,
    "image.upscale.format": "jpg",
    "image.upscale.gpu": "auto",
    "image.upscale.tile_size": 0,
    "image.upscale.thread_count": "1:2:2",
    "stitch.spread_probability": 0.5,
}


def load_config():
    config = copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            values = json.load(f)
            if not isinstance(values, dict):
                raise ValueError('Configuration must be a JSON object')
            config.update(values)
    except (FileNotFoundError, ValueError) as error:
        logger.warning('Failed to load config, using defaults: %s', error)

    return config


config = load_config()
