"""Debug-only probe: recognize current scene and test ESC returning home.

Runs as a one-time task: captures a frame, runs the same home-page
recognition used in production (OCR 3-signal + home-button template),
optionally sends ESC (background PostMessage and/or foreground SendInput),
then reports whether the game reached the home page.
"""

from __future__ import annotations

from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.task_vision_mixin import TaskVisionMixin


class BD2SceneEscProbeTask(TaskVisionMixin, BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "场景识别+ESC 探针"
        self.description = "识别当前场景（主页/非主页），并测试后台/前台 ESC 能否返回主页。"
        self.icon = FluentIcon.SEARCH
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.visible = True
        self.default_config.update(
            {
                "ESC 模式": "后台→前台",  # 不发送 / 后台 / 前台 / 后台→前台
                "每次 ESC 后等待秒数": 1.5,
                "保存截图": True,
                # 主页按钮模板匹配阈值
                "小屋按钮阈值": 0.78,
                "主页 OCR 阈值": 0.2,
            }
        )
        self._init_vision_state()

    def run(self) -> bool:
        mode = str(self.config.get("ESC 模式", "后台→前台"))
        wait = float(self.config.get("每次 ESC 后等待秒数", 1.5))
        save = bool(self.config.get("保存截图", True))
        self.log_info(f"场景识别+ESC 探针开始：ESC 模式={mode}", notify=True)

        lines = [f"ESC 模式={mode}"]
        before = self._snapshot("BEFORE", save)
        lines += before["lines"]
        home = before["home"]

        for esc_mode in self._esc_plan(mode):
            if home:
                break
            sent = self._send_esc(esc_mode, wait)
            print(f"=== ESC mode={esc_mode} sent={sent} ===")
            after = self._snapshot(f"AFTER_{esc_mode}", save)
            lines += ["", f"=== ESC mode={esc_mode} sent={sent} ==="]
            lines += after["lines"]
            home = after["home"]

        lines += ["", f"最终 home={home}"]
        out = self.write_probe_text("esc_home_probe_latest.txt", lines)
        self.log_completion(f"场景识别+ESC 探针完成：home={home}，详见 {out}")
        return True

    @staticmethod
    def _esc_plan(mode: str) -> list[str]:
        return {
            "不发送": [],
            "后台": ["后台"],
            "前台": ["前台"],
            "后台→前台": ["后台", "前台"],
        }.get(mode, ["后台"])

    def _snapshot(self, tag: str, save: bool) -> dict:
        lines = []
        try:
            frame = self.capture_frame(f"esc_probe_{tag}" if save else None)
        except Exception as exc:
            lines.append(f"[{tag}] capture ERR: {exc}")
            print(f"[{tag}] capture ERR: {exc}")
            return {"home": False, "lines": lines}

        confirmed = False
        left = -1
        p95 = -1.0
        gacha = ""
        try:
            confirmed, left, p95, gacha = self._home_confirmation_signals(frame, "场景探针")
        except Exception as exc:
            lines.append(f"[{tag}] OCR3信号 ERR: {exc}")

        texts: list[str] = []
        try:
            boxes = self.ocr_frame(frame=frame, threshold=0.2)
            texts = [str(getattr(box, "name", "")) for box in boxes if getattr(box, "name", "")]
        except Exception as exc:
            lines.append(f"[{tag}] 全屏OCR ERR: {exc}")

        btn = -1.0
        btn_passed = False
        try:
            from src.tasks.trigger.AutoLoginTask import AutoLoginTask

            btn_obj, spec = AutoLoginTask._match_home_button(self, frame)
            btn = float(btn_obj.score)
            btn_passed = bool(self._passes(btn_obj, spec))
        except Exception as exc:
            lines.append(f"[{tag}] 主页按钮模板 ERR: {exc}")

        home = bool(confirmed)
        ocr_sample = " ".join(texts)[:180] or "-"
        lines.append(f"[{tag}] home={home} (OCR3={confirmed})")
        lines.append(
            f"[{tag}] left={left} p95={p95:.1f} gacha={gacha!r} "
            f"home_btn={btn:.3f} passed={btn_passed}"
        )
        lines.append(f"[{tag}] ocr={ocr_sample}")
        print(
            f"[{tag}] home={home} left={left} p95={p95:.1f} gacha={gacha!r} "
            f"home_btn={btn:.3f} passed={btn_passed}"
        )
        print(f"[{tag}] ocr={ocr_sample}")
        return {"home": home, "lines": lines}

    def _send_esc(self, mode: str, wait: float) -> bool:
        try:
            if mode == "前台":
                interaction = getattr(self.executor, "interaction", None)
                hwnd_window = (
                    getattr(interaction, "hwnd_window", None)
                    if interaction is not None
                    else None
                )
                if hwnd_window is not None:
                    hwnd_window.bring_to_front()
                    self.sleep(0.4)
                import pydirectinput

                pydirectinput.press("esc")
                self.sleep(wait)
            else:  # 后台
                self.send_key("esc", after_sleep=wait)
            return True
        except Exception as exc:
            self.log_warning(f"发送 ESC 失败 mode={mode}: {exc}")
            print(f"ESC send error {mode}: {exc}")
            return False
