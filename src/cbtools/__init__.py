import logging
import pathlib

from logging.handlers import TimedRotatingFileHandler

from cbtools.log import logger
from cbtools.config import config


def configure_logging() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(config['logging.format']))
    logger.addHandler(handler)
    logger.setLevel(config['logging.level'])

    return logger

def configure_file_logging(name: str) -> None:
    log_path = pathlib.Path(config['logging.path'])

    if not log_path.exists():
        log_path.mkdir(exist_ok=True)

    handler = TimedRotatingFileHandler(
        filename=log_path / f'{name}.log',
        when='D',
        interval=1,
        backupCount=6,
        encoding='utf-8',
        delay=False,
    )
    handler.setFormatter(logging.Formatter(config['logging.format']))
    logger.addHandler(handler)
