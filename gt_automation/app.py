"""Application entry point shared by the launcher and console script."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def package_check() -> int:
    from gt_automation.paths import (
        ACCOUNT_FILE,
        APP_HOME,
        DAILY_TASK_FILE,
        IS_FROZEN,
        LOGS_DIR,
        STAR_TASK_FILE,
        ensure_runtime_dirs,
    )
    from gt_automation.task_launcher import task_base_command

    ensure_runtime_dirs()
    helpers = {}
    for task in ("daily", "star", "zhaoxian", "qiluo"):
        try:
            command = task_base_command(task)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        helpers[task] = command[0]
        if IS_FROZEN and not Path(command[0]).exists():
            print(
                json.dumps(
                    {"ok": False, "error": "缺少执行器：%s" % command[0]},
                    ensure_ascii=False,
                )
            )
            return 2
    print(
        json.dumps(
            {
                "ok": True,
                "frozen": IS_FROZEN,
                "app_home": str(APP_HOME),
                "account_file": str(ACCOUNT_FILE),
                "daily_task_file": str(DAILY_TASK_FILE),
                "star_task_file": str(STAR_TASK_FILE),
                "logs_dir": str(LOGS_DIR),
                "helpers": helpers,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    if "--package-check" in sys.argv:
        return package_check()

    from gt_automation.ui.main_window import GtAutomationApp

    app = GtAutomationApp()
    app.mainloop()
    return 0
