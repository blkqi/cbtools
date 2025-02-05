"""Logging configuration."""

import logging
import logging.handlers

from pathlib import Path
from .config import config


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def configure_logging() -> logging.Logger:
    handler = logging.handlers.StreamHandler()
    handler.setFormatter(logging.Formatter(config['logging.format']))
    logger.addHandler(handler)
    logger.setLevel(config['logging.level'])

    return logger


def configure_file_logging(name: str) -> None:
    log_path = Path(config['logging.path'])

    if not log_path.exists():
        log_path.mkdir(exist_ok=True)

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_path / f'{name}.log',
        when='D',
        interval=1,
        backupCount=6,
        encoding='utf-8',
        delay=False,
    )
    handler.setFormatter(logging.Formatter(config['logging.format']))
    logger.addHandler(handler)

    return logger
