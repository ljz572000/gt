#!/usr/bin/env python3
"""GT Automation Studio desktop launcher."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from gt_automation.app import main as app_main
    except ImportError as exc:
        print("启动失败：%s" % exc, file=sys.stderr)
        print("请先执行：pip install -r requirements.txt", file=sys.stderr)
        return 2
    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
