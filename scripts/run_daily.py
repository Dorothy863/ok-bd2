"""Run 一键完成日常 from the current source checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ok import run_task

from src.config import config
from src.native_shutdown import release_native_resources
from src.tasks.DailyBatchTask import DailyBatchTask


class DailyRunTask(DailyBatchTask):
    """One-time task wrapper used for headless daily execution."""


def main() -> None:
    headless_config = dict(config)
    headless_config["trigger_tasks"] = []
    run_task(headless_config, task=DailyRunTask, debug=False, exit_after=False)
    release_native_resources()


if __name__ == "__main__":
    main()
