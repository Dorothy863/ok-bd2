"""Headless run of SquareGoddessTask (广场女神像) for quick-switch full-frame fix validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ok

from src.config import config


def main() -> int:
    config["trigger_tasks"] = []
    config.setdefault("windows", {})["start_exe"] = False
    app = ok.OK(config)
    from src.tasks.SquareGoddessTask import SquareGoddessTask

    task = app.get_task(SquareGoddessTask)[0]
    app.run_onetime_task(task, exit_after=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
