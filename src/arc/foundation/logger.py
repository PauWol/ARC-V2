import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


from src.arc.foundation.constants import (
    LOG_BACKUP_COUNT,
    LOG_LEVEL,
    LOG_FILE,
    LOG_CONSOLE,
    LOG_JSON,
    LOG_MAX_BYTES,
    LOG_ROTATE,
)


class LoggingConfig:
    level: str = str(LOG_LEVEL)
    file: str = str(LOG_FILE)
    console: bool = bool(LOG_CONSOLE)
    json: bool = bool(LOG_JSON)
    rotate: bool = bool(LOG_ROTATE)
    max_bytes: int = int(LOG_MAX_BYTES)
    backup_count: int = int(LOG_BACKUP_COUNT)


def setup_logging(cfg: LoggingConfig | None = None) -> logging.Logger:
    if cfg is None:
        cfg = LoggingConfig()

    logger = logging.getLogger()

    logger.setLevel(cfg.level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    Path(cfg.file).parent.mkdir(parents=True, exist_ok=True)

    if cfg.rotate:
        fh = RotatingFileHandler(
            cfg.file,
            maxBytes=cfg.max_bytes,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        )
    else:
        fh = logging.FileHandler(
            cfg.file,
            encoding="utf-8",
        )

    fh.setFormatter(formatter)
    logger.addHandler(fh)

    if cfg.console:
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    return logger
