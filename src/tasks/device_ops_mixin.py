"""设备/工具操作 mixin：截图、保存、OCR、模板匹配、点击、拖拽与滚轮。

这里只保留“最小操作”，不含游戏业务判定；卡带/主页业务见
:mod:`src.tasks.cartridge_home_mixin`，状态输出与生命周期见
:mod:`src.tasks.BaseBD2Task`。方法体自 ``BaseBD2Task`` 原样搬移，行为等价。
"""

import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from ok import Box
from PIL import Image

from src.utils.image_utils import green_mask_from_template

PROBE_OUTPUT_DIR = Path("probe_outputs")
GREEN_MASK_TOLERANCE = 0


class DeviceOpsMixin:

    @property
    def main_viewport(self) -> Box:
        return self.box_of_screen(0.05, 0.05, 0.95, 0.95, name="main_viewport")

    @property
    def capture_method_name(self) -> str:
        capture_method = getattr(self.executor.device_manager, "capture_method", None)
        return str(capture_method) if capture_method is not None else "<none>"

    def capture_frame(self, screenshot_name: str | None = None):
        frame = self.next_frame()
        if frame is None:
            raise RuntimeError("未能从 BD2 截取画面。")

        height, width = frame.shape[:2]
        self.info_set("截图方式", self.capture_method_name)
        self.info_set("游戏分辨率", f"{width}x{height}")
        if screenshot_name:
            self.save_frame(screenshot_name, frame)
        return frame

    def save_frame(self, name: str, frame) -> Path:
        PROBE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = PROBE_OUTPUT_DIR / f"{name}.png"
        if frame.ndim == 2:
            image = Image.fromarray(frame)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA))
        elif frame.ndim == 3 and frame.shape[2] == 3:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            raise ValueError(f"Unsupported screenshot frame shape: {frame.shape}")
        image.save(output_path)
        self.info_set("截图文件", str(output_path))
        return output_path

    def ocr_frame(self, frame=None, threshold: float = 0.2, screenshot: bool = False):
        frame = frame if frame is not None else self.capture_frame()
        boxes = self.ocr(frame=frame, threshold=threshold, log=True, screenshot=screenshot)
        texts = [box.name for box in boxes if getattr(box, "name", "")]
        self.info_set("OCR 文本数量", len(texts))
        self.info_set("OCR 文本", ", ".join(texts[:30]))
        return boxes

    def write_probe_text(
        self,
        name: str,
        lines: list[str],
        info_label: str = "OCR 文本文件",
    ) -> Path:
        PROBE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = PROBE_OUTPUT_DIR / name
        output_path.write_text("\n".join(lines), encoding="utf-8")
        self.info_set(info_label, str(output_path))
        return output_path

    @staticmethod
    def green_mask(template: np.ndarray, tolerance: int = GREEN_MASK_TOLERANCE) -> np.ndarray:
        return green_mask_from_template(template, tolerance=tolerance)

    def find_one_green_mask(
        self,
        *args,
        green_tolerance: int = GREEN_MASK_TOLERANCE,
        match_method=cv2.TM_CCORR_NORMED,
        **kwargs,
    ):
        kwargs["mask_function"] = lambda template: green_mask_from_template(
            template,
            tolerance=green_tolerance,
        )
        kwargs["match_method"] = match_method
        return self.find_one(*args, **kwargs)

    def find_green_mask_features(
        self,
        *args,
        green_tolerance: int = GREEN_MASK_TOLERANCE,
        match_method=cv2.TM_CCORR_NORMED,
        **kwargs,
    ):
        kwargs["mask_function"] = lambda template: green_mask_from_template(
            template,
            tolerance=green_tolerance,
        )
        kwargs["match_method"] = match_method
        return self.find_feature(*args, **kwargs)

    def click(
        self,
        x: int | float | Box | list[Box] = -1,
        y=-1,
        move_back=None,
        name=None,
        interval=-1,
        move=None,
        down_time=0.02,
        after_sleep=0,
        key="left",
        hcenter=False,
        vcenter=False,
        action_name=None,
    ) -> Any:
        if action_name is not None:
            if not self._check_action_interval(action_name, interval):
                return False
            interval = -1

        if move is None:
            move = self.DEFAULT_MOVE
        if move_back is None:
            move_back = move

        return super().click(
            x,
            y,
            move_back=move_back,
            name=name,
            interval=interval,
            move=move,
            down_time=down_time,
            after_sleep=after_sleep,
            key=key,
            hcenter=hcenter,
            vcenter=vcenter,
        )

    def operate(self, func: Callable, block: bool = True, restore_cursor: bool = True):
        interaction = getattr(self.executor, "interaction", None)
        if interaction is not None and hasattr(interaction, "operate"):
            return interaction.operate(func, block=block, restore_cursor=restore_cursor)
        return func()

    def operate_click(
        self,
        x: int | float | Box | list[Box] = -1,
        y=-1,
        restore_cursor=True,
        name=None,
        interval=-1,
        down_time=0.02,
        after_sleep=0,
        key="left",
        hcenter=False,
        vcenter=False,
        action_name=None,
    ) -> Any:
        action_name = action_name or "operate_click"
        if not self._check_action_interval(action_name, interval):
            return False
        try:
            click_log = self._click_log_message(
                x,
                y,
                int(self.width),
                int(self.height),
                str(name or action_name),
            )
        except Exception:
            click_log = f"{name or action_name}: target={x!r},{y!r}"
        interaction = getattr(self.executor, "interaction", None)
        background_click = bool(
            interaction is not None
            and hasattr(interaction, "background_click_enabled")
            and interaction.background_click_enabled()
        )
        result = self.operate(
            lambda: self.click(
                x,
                y,
                name=name,
                interval=-1,
                move=True,
                down_time=down_time,
                after_sleep=0,
                key=key,
                hcenter=hcenter,
                vcenter=vcenter,
            ),
            block=not background_click,
            restore_cursor=restore_cursor and not background_click,
        )
        self.info_set("鼠标点击", click_log)
        self.sleep(after_sleep)
        return result

    @staticmethod
    def _click_log_message(x, y, width: int, height: int, action_name: str) -> str:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if 0 < x < 1 or 0 < y < 1:
                client_x = int(width * x)
                client_y = int(height * y)
                return (
                    f"{action_name}: client=({client_x},{client_y}), "
                    f"relative=({float(x):.6f},{float(y):.6f})"
                )
            return f"{action_name}: client=({int(x)},{int(y)})"

        if isinstance(x, Box):
            return f"{action_name}: box={x.name or '-'} {x.box}"
        if isinstance(x, list):
            return f"{action_name}: boxes={len(x)}"
        return f"{action_name}: target={x!r},{y!r}"

    def drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        """Drag with foreground mouse input only; these tasks never send keys."""

        def action():
            import win32api
            import win32con

            interaction = getattr(self.executor, "interaction", None)
            if interaction is not None and hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif interaction is not None and hasattr(interaction, "try_activate"):
                interaction.try_activate()
            capture = getattr(interaction, "capture", None)

            def to_screen(point: tuple[int, int]) -> tuple[int, int]:
                if capture is not None and hasattr(capture, "get_abs_cords"):
                    return capture.get_abs_cords(point[0], point[1])
                return point

            start_abs = to_screen(start)
            end_abs = to_screen(end)
            steps = max(6, round(duration / 0.03))
            win32api.SetCursorPos(start_abs)
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                for index in range(1, steps + 1):
                    ratio = index / steps
                    x = round(start_abs[0] + (end_abs[0] - start_abs[0]) * ratio)
                    y = round(start_abs[1] + (end_abs[1] - start_abs[1]) * ratio)
                    win32api.SetCursorPos((x, y))
                    time.sleep(duration / steps)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def scroll_client(
        self,
        relative_point: tuple[float, float],
        scroll_amount: int,
        count: int = 1,
        interval: float = 0.1,
        after_sleep: float = 0.0,
    ) -> None:
        """Send foreground mouse-wheel events at a relative client point."""

        frame = self.capture_frame()
        height, width = frame.shape[:2]
        x = round(max(0.0, min(1.0, relative_point[0])) * width)
        y = round(max(0.0, min(1.0, relative_point[1])) * height)
        wheel_count = max(1, int(count))
        wheel_interval = max(0.0, float(interval))
        interaction = getattr(self.executor, "interaction", None)
        if interaction is None or not hasattr(interaction, "scroll"):
            raise RuntimeError("当前交互对象不支持鼠标滚轮")

        def action():
            import win32api

            if hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif hasattr(interaction, "try_activate"):
                interaction.try_activate()
            capture = getattr(interaction, "capture", None)
            if capture is not None and hasattr(capture, "get_abs_cords"):
                win32api.SetCursorPos(capture.get_abs_cords(x, y))
            for index in range(wheel_count):
                interaction.scroll(x, y, int(scroll_amount))
                if index + 1 < wheel_count:
                    time.sleep(wheel_interval)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def _sleep_after_recognition(self) -> None:
        seconds = float(self.config.get("识别成功后等待秒数", 1.0))
        if seconds > 0:
            self.sleep(seconds)
