import logging

logger: logging.Logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
