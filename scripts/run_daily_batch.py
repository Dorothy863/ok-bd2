"""Headless run of the built-in 一键完成日常 (DailyBatchTask).

Usage:
    uv run python scripts/run_daily_batch.py

Notes:
- AutoLogin trigger is removed so the login gate is bypassed (the game should
  already be on the main page = "logged in").
- exit_after=False: never kill the game process.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ok

from src.config import config


def main() -> int:
    # 跳过自动登录门控：当前手动场景等价于“已登录在主页”。
    config["trigger_tasks"] = []
    config.setdefault("windows", {})["start_exe"] = False

    app = ok.OK(config)
    from src.tasks.DailyBatchTask import DailyBatchTask

    batch = app.get_task(DailyBatchTask)[0]
    app.run_onetime_task(batch, exit_after=False)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
