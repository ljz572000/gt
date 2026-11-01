"""Resolve task executables for development and frozen distributions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from gt_automation.paths import IS_FROZEN, PROJECT_ROOT


def _helper_name(task: str) -> str:
    base = {
        "daily": "daily_worker",
        "star": "star_worker",
        "zhaoxian": "zhaoxian_worker",
        "qiluo": "qiluo_jingyi_worker",
    }.get(task)
    if base is None:
        raise ValueError("未知任务类型：%s" % task)
    return base + (".exe" if sys.platform == "win32" else "")


def _frozen_helper(task: str) -> Path:
    executable_dir = Path(sys.executable).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", executable_dir))
    candidates = [
        bundle_root / "bin" / _helper_name(task),
        executable_dir / "bin" / _helper_name(task),
        executable_dir.parent / "Resources" / "bin" / _helper_name(task),
        executable_dir / _helper_name(task),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "打包资源中缺少任务执行器：%s\n检查路径：%s"
        % (_helper_name(task), ", ".join(str(path) for path in candidates))
    )


def task_base_command(task: str) -> List[str]:
    if task not in {"daily", "star", "zhaoxian", "qiluo"}:
        raise ValueError("未知任务类型：%s" % task)
    if IS_FROZEN:
        return [str(_frozen_helper(task))]
    script = {
        "daily": PROJECT_ROOT / "daily_welfare_claim.py",
        "star": PROJECT_ROOT / "siyi_star_auto.py",
        "zhaoxian": PROJECT_ROOT / "zhaoxian_auto.py",
        "qiluo": PROJECT_ROOT / "qiluo_jingyi_auto.py",
    }[task]
    return [sys.executable, str(script)]
