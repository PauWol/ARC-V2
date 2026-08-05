import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from typing_extensions import override

from arc.foundation.constants import (
    LOG_BACKUP_COUNT,
    LOG_CONSOLE,
    LOG_FILE,
    LOG_JSON,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOG_ROTATE,
    TERMINAL_NO_COLOR,
)

RESET = "\033[0m"
COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


class LoggingConfig:
    level: str = str(LOG_LEVEL)
    file: str = str(LOG_FILE)
    console: bool = bool(LOG_CONSOLE)
    json: bool = bool(LOG_JSON)
    rotate: bool = bool(LOG_ROTATE)
    max_bytes: int = int(LOG_MAX_BYTES)
    backup_count: int = int(LOG_BACKUP_COUNT)


class BootColorFormatter(logging.Formatter):
    def __init__(
        self, fmt: str, datefmt: str | None = None, use_color: bool = True
    ) -> None:
        super().__init__(fmt, datefmt)
        self.use_color: bool = use_color

    @override
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)

        if TERMINAL_NO_COLOR or not self.use_color:
            return msg

        color = COLORS.get(record.levelno, "")
        if not color:
            return msg

        return f"{color}{msg}{RESET}"


def setup_logging(cfg: LoggingConfig | None = None) -> logging.Logger:
    if cfg is None:
        cfg = LoggingConfig()

    logger = logging.getLogger()
    logger.setLevel(cfg.level)

    if logger.handlers:
        return logger

    fmt = "[%(asctime)s] %(levelname)-8s %(name)-25s %(message)s"

    Path(cfg.file).parent.mkdir(parents=True, exist_ok=True)

    if cfg.rotate:
        fh = RotatingFileHandler(
            cfg.file,
            maxBytes=cfg.max_bytes,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        )
    else:
        fh = logging.FileHandler(cfg.file, encoding="utf-8")

    fh.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)

    if cfg.console:
        ch = logging.StreamHandler()
        ch.setFormatter(BootColorFormatter(fmt, datefmt="%H:%M:%S", use_color=True))
        logger.addHandler(ch)

    return logger
