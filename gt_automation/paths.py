"""Cross-platform project and runtime paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "GT Automation Studio"
IS_FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if IS_FROZEN:
    if sys.platform == "darwin":
        APP_HOME = (
            Path.home()
            / "Library"
            / "Application Support"
            / APP_NAME
        )
    elif sys.platform == "win32":
        APP_HOME = Path(
            os.environ.get("APPDATA", str(Path.home()))
        ) / APP_NAME
    else:
        APP_HOME = Path(
            os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
        ) / APP_NAME
    if sys.platform == "darwin":
        INSTALL_ROOT = Path(sys.executable).resolve().parents[3]
    else:
        INSTALL_ROOT = Path(sys.executable).resolve().parent
else:
    APP_HOME = PROJECT_ROOT
    INSTALL_ROOT = PROJECT_ROOT

DEFAULT_DATA_DIR = INSTALL_ROOT
ACCOUNT_FILE = INSTALL_ROOT / "account.json"
TASK_DIR = INSTALL_ROOT / "task"
DAILY_TASK_FILE = TASK_DIR / "daily_welfare.json"
STAR_TASK_FILE = TASK_DIR / "siyi_star.json"
ZHAOXIAN_FILE = INSTALL_ROOT / "zhaoxian.json"
QILUO_JINGYI_FILE = INSTALL_ROOT / "qiluo_jingyi.json"
# Retained only for migrating files created by older releases.
DEFAULT_GT_FILE = INSTALL_ROOT / "gt.json"
HAR_DIR = APP_HOME / "har" if IS_FROZEN else PROJECT_ROOT / "data" / "har"
LOGS_DIR = INSTALL_ROOT / "logs"
RUNTIME_DIR = APP_HOME / "runtime"
SETTINGS_FILE = RUNTIME_DIR / "settings.json"


def ensure_runtime_dirs() -> None:
    HAR_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
