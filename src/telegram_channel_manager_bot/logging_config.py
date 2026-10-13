import sys

from loguru import logger

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, format=LOG_FORMAT, colorize=True, level=level, diagnose=False)
