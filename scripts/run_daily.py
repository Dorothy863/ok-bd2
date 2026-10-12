"""Run 一键完成日常 from the current source checkout."""

from ok import run_task

from src.config import config
from src.tasks.DailyBatchTask import DailyBatchTask


class DailyRunTask(DailyBatchTask):
    """One-time task wrapper used for headless daily execution."""


def main() -> None:
    headless_config = dict(config)
    headless_config["trigger_tasks"] = []
    run_task(headless_config, task=DailyRunTask, debug=False, exit_after=False)


if __name__ == "__main__":
    main()
