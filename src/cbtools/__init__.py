import logging

from logging.handlers import TimedRotatingFileHandler

from cbtools.config import config

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(config['logging.format']))
logger.addHandler(handler)
logger.setLevel(config['logging.level'])

def enable_file_logging(name: str) -> None:
    if not config['logging.path'].exists():
        config['logging.path'].mkdir(exist_ok=True)

    handler = TimedRotatingFileHandler(
        filename=config['logging.path'] / f'{name}.log',
        when='D',
        interval=1,
        backupCount=6,
        encoding='utf-8',
        delay=False,
    )
    handler.setFormatter(logging.Formatter(config['logging.format']))
    logger.addHandler(handler)
