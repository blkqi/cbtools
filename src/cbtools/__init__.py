import logging

from logging.handlers import TimedRotatingFileHandler

from cbtools.config import config

_logger = logging.getLogger()
_logger.setLevel(logging.DEBUG)
_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)8s] %(message)s (%(filename)s:%(lineno)s)'
)
_handler = logging.StreamHandler()
_handler.setFormatter(_formatter)
_logger.addHandler(_handler)

class FileHandler(TimedRotatingFileHandler):
    def __init__(self, name: str):
        self._create_log_dir()
        super().__init__(
            filename=config['log_path'] / f'{name}.log',
            when='D',
            interval=1,
            backupCount=6,
            encoding='utf-8',
            delay=False,
        )
        self.setFormatter(_formatter)

    @staticmethod
    def _create_log_dir() -> None:
        if not config['log_path'].exists():
            config['log_path'].mkdir(exist_ok=True)
