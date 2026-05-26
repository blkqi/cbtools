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
    "stitch.skip_pages": 1,
    "stitch.ribbon_fraction": 0.018,
    "stitch.ribbon_min_px": 12,
    "stitch.ribbon_max_px": 64,
    "stitch.correlation_threshold": 0.45,
    "stitch.min_content_density": 0.06,
    "stitch.white_pixel_value": 240,
    "stitch.min_edge_contrast": 8.5,
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
