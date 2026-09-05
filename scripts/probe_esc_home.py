"""Headless runner for BD2SceneEscProbeTask.

Usage:
    uv run python scripts/probe_esc_home.py [mode]
    uv run python scripts/probe_esc_home.py click X Y     # 纯后台点击像素 (X,Y)

modes: 不发送|后台|前台|后台→前台  (aliases none|background|foreground|both)
Requires the BrownDust II game window to be running. Never closes the game
(exit_after=False).
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
    args = sys.argv[1:]
    mode = "后台→前台"
    click_x = -1
    click_y = -1
    click_count = 1
    click_impl = "postmessage"
    if args and args[0] in ("click", "clicko") and len(args) >= 3:
        mode = "不发送"
        click_x = int(args[1])
        click_y = int(args[2])
        if args[0] == "clicko":
            click_impl = "operate"
        if len(args) >= 4:
            click_count = int(args[3])
    elif args:
        mode = _ALIASES.get(args[0], args[0])

    if mode not in ("不发送", "后台", "前台", "后台→前台"):
        print(f"未知 ESC 模式: {mode}")
        return 2

    install_debug_tasks(config)
    config.setdefault("windows", {})["start_exe"] = False

    app = ok.OK(config)
    from src.tasks.BD2SceneEscProbeTask import BD2SceneEscProbeTask

    task = app.get_task(BD2SceneEscProbeTask)[0]
    task.config["ESC 模式"] = mode
    task.config["后台点击 X 像素"] = click_x
    task.config["后台点击 Y 像素"] = click_y
    task.config["后台点击次数"] = click_count
    task.config["后台点击实现"] = click_impl
    # exit_after=False：结束时不要关闭 ok 管理的游戏进程。
    app.run_onetime_task(task, exit_after=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
