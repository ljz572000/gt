"""Allow `python -m gt_automation` to launch the desktop UI."""

from gt_automation.app import main


if __name__ == "__main__":
    raise SystemExit(main())
