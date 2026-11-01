"""Application and task log helpers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_app_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    app_log = log_dir / "gt_studio.log"
    logger = logging.getLogger("gt_automation")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(
        isinstance(handler, RotatingFileHandler)
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(
            app_log,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    return app_log


def get_app_logger() -> logging.Logger:
    return logging.getLogger("gt_automation")
