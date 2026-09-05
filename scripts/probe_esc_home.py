"""Headless runner for BD2SceneEscProbeTask.

Usage:
    uv run python scripts/probe_esc_home.py [不发送|后台|前台|后台→前台]
Aliases: none|background|foreground|both (default: 后台→前台)

Requires the BrownDust II game window to be running. The runner never closes
the game: exit_after=False is required so ok does not kill the managed game
process when the probe finishes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ok

from src.config import config
from src.tasks.debug_registry import install_debug_tasks

_ALIASES = {
    "none": "不发送",
    "background": "后台",
    "foreground": "前台",
    "both": "后台→前台",
}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "后台→前台"
    mode = _ALIASES.get(mode, mode)
    if mode not in ("不发送", "后台", "前台", "后台→前台"):
        print(f"未知 ESC 模式: {mode}")
        return 2

    install_debug_tasks(config)
    config.setdefault("windows", {})["start_exe"] = False

    app = ok.OK(config)
    from src.tasks.BD2SceneEscProbeTask import BD2SceneEscProbeTask

    task = app.get_task(BD2SceneEscProbeTask)[0]
    task.config["ESC 模式"] = mode
    # exit_after=False：结束时不要关闭 ok 管理的游戏进程。
    app.run_onetime_task(task, exit_after=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
