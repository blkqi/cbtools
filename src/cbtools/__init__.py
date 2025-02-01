import logging

from logging.handlers import TimedRotatingFileHandler

from cbtools.config import config

LOG_FORMAT = '%(asctime)s [%(levelname)8s] %(message)s (%(name)s :: %(filename)s:%(lineno)s)'

logging.basicConfig(
    level=config['logging.level'],
    format=LOG_FORMAT,
)

def enable_file_logging(name: str) -> None:
    if not config['logging.path'].exists():
        config['logging.path'].mkdir(exist_ok=True)

    logger = logging.getLogger()
    handler = TimedRotatingFileHandler(
        filename=config['logging.path'] / f'{name}.log',
        when='D',
        interval=1,
        backupCount=6,
        encoding='utf-8',
        delay=False,
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
