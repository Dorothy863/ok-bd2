"""实机验收工具：确认页面识别 + 自动返回主页（重构计划 P7）。

用法（需要 BrownDust II 客户端在运行）：
    uv run python scripts/verify_auto_return.py identify   # 只识别当前页面，不点击
    uv run python scripts/verify_auto_return.py return     # 识别 → 自动返回主页 → 复验

输出用于人工判定，例如：
    before = gacha_detail (抽抽乐点数/抽1次/抽10次 命中, X@1893,430)
    after  = home
    steps  = 自动返回主页：第 1 步识别到抽卡详情页，点击关闭 X @(1893,430)。

不关闭游戏（exit_after=False）。脚本不修改任何业务逻辑，只调用既有实现。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ok import run_task

from src.config import config
from src.native_shutdown import release_native_resources
from src.tasks.BaseBD2Task import BaseBD2Task


class AutoReturnVerifyTask(BaseBD2Task):
    """一次性任务：识别当前页面，必要时执行自动返回主页并复验。"""

    verify_mode = "identify"

    def run(self) -> None:
        frame = self.capture_frame()
        recognition = self._recognize_page(frame)
        self._report("before", recognition)

        if self.verify_mode == "identify":
            return

        ok_to_home = self.auto_return_main_home()
        frame = self.capture_frame()
        recognition = self._recognize_page(frame)
        self._report("after", recognition)
        self.log_info(f"auto_return_main_home() -> {ok_to_home}")
        for key, value in sorted(self.info_snapshot().items()):
            if "主页" in str(key):
                self.log_info(f"  info[{key}] = {value}")

    def _report(self, label: str, recognition) -> None:
        signals = recognition.signals
        self.log_info(
            f"{label} = {recognition.page.value} "
            f"(home_label_seen={signals.home_label_seen}, "
            f"gacha_keywords={signals.gacha_detail_keywords}, "
            f"house_hint={signals.house_hint_center}, "
            f"detail_close={signals.detail_close_center})"
        )
        # 附带当帧 OCR 标签，便于人工判断当前到底在哪一屏（判定为 UNKNOWN 时尤其需要）。
        try:
            frame = self.capture_frame()
            boxes = list(
                self.ocr(
                    frame=frame,
                    threshold=0.2,
                    log=False,
                    name=f"{label}-标签",
                )
            )
        except Exception as exc:  # noqa: BLE001 - 诊断输出
            self.log_info(f"{label} 标签读取失败：{exc}")
            return
        labels = [str(getattr(box, "name", "") or "") for box in boxes]
        self.log_info(f"{label} 标签 {len(labels)} 个: " + " | ".join(labels))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    mode = args[0] if args else "identify"
    if mode not in {"identify", "return"}:
        print(__doc__)
        return 2
    AutoReturnVerifyTask.verify_mode = mode

    headless_config = dict(config)
    headless_config["trigger_tasks"] = []
    run_task(headless_config, task=AutoReturnVerifyTask, debug=False, exit_after=False)
    release_native_resources()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
