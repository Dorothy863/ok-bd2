"""Shared vision state and template/OCR helpers for daily-style tasks."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from src.tasks.ports import TaskSurface
from src.utils import task_vision
from src.utils.calibration import FHD_1080
from src.utils.home_confirmation import (
    HOME_DIMMED_P95_THRESHOLD_DEFAULT,
    HOME_GACHA_OCR_RELATIVE_ROI,
    HOME_LEFT_COLUMN_OCR_RELATIVE_ROI,
    HOME_LEFT_COLUMN_REQUIRED_HITS,
    home_confirmation_passes,
    home_gacha_ocr_with_fallback,
    home_left_column_hits,
    home_left_column_p95_brightness,
)
from src.utils.image_utils import to_gray
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text
from src.utils.polling import PollDeadline
from src.utils.vision_models import MatchResult, TemplateSpec
from src.utils.vision_runtime import Vision

REFERENCE_WIDTH = FHD_1080.width

REFERENCE_HEIGHT = FHD_1080.height

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"

LOADING_TEMPLATE = TemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)


@runtime_checkable
class HomeReturnHost(TaskSurface, Protocol):
    """``TaskVisionMixin`` 宿主在端口之外还提供的主页业务方法。

    协议只描述被调用面，不代表接口稳定承诺。这两个方法不是驱动端口，而是 ``BaseBD2Task`` 的业务面
    （``src/tasks/BaseBD2Task.py:312`` / ``:651``）；``_wait_for_home_confirmation`` 与
    ``_auto_return_home_after_timeout`` 会调用它们，所以那两个方法的 ``self`` 标注为
    本协议而不是 ``TaskSurface``，避免类型标注说谎。P4 拆出页面业务后应改为
    「页面识别 + InputPort」。
    """

    def attempt_return_home(self, name: str = "自动返回主页") -> bool:
        """点击右上角主页按钮把角色带回主界面。"""
        ...

    def clear_temporary_home_announcement_if_needed(
        self,
        *,
        left_hits: int,
        required_left_hits: int,
        brightness: float,
        brightness_threshold: float,
        gacha_ocr_text: object,
        context: str,
    ) -> bool:
        """亮度不足时按登录公告流程清理临时公告。"""
        ...


class TaskVisionMixin:
    """公共视觉基座：``self`` 是混入宿主（``BaseBD2Task``）。

    方法签名里的 ``self: TaskSurface`` / ``self: HomeReturnHost`` 声明宿主被当作
    鸭子类型使用的调用面（P3）；``TaskSurface`` 只描述被调用面，不是接口稳定承诺。
    """

    def _init_vision_state(self) -> None:
        self._templates: dict[str, np.ndarray] = {}
        self._template_masks: dict[str, np.ndarray | None] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0

    def _quick_vision(self: TaskSurface) -> Vision:
        vision = getattr(self, "_quick_hunt_vision", None)
        if vision is None:
            vision = Vision(self)
            self._quick_hunt_vision = vision
        return vision

    _normalize_text = staticmethod(normalize_ocr_text)

    _to_gray = staticmethod(to_gray)

    def _status_set(self: TaskSurface, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def _home_confirmation_signals(
        self,
        frame,
        ocr_name: str,
    ) -> tuple[bool, int, float, str]:
        vision = self._quick_vision()
        left_text = vision.ocr_text(
            frame,
            f"{ocr_name} 左列",
            relative_roi=HOME_LEFT_COLUMN_OCR_RELATIVE_ROI,
        )
        left_hits = home_left_column_hits(left_text)
        p95_brightness = home_left_column_p95_brightness(frame)
        gacha_result = home_gacha_ocr_with_fallback(
            lambda scale: vision.ocr_text(
                frame,
                f"{ocr_name} x{scale:g}",
                relative_roi=HOME_GACHA_OCR_RELATIVE_ROI,
                ocr_scale=scale,
            )
        )
        gacha_text = gacha_result.text
        self._status_set(f"{ocr_name} OCR 尝试", gacha_result.trace)
        confirmed = home_confirmation_passes(
            left_hits=left_hits,
            required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
            brightness=p95_brightness,
            brightness_threshold=self._home_p95_threshold(),
            gacha_ocr_text=gacha_text,
        )
        return confirmed, left_hits, p95_brightness, gacha_text

    def _click_reference(self: TaskSurface, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _wait_loading_or_template(
        self: TaskSurface,
        task_name: str,
        spec: TemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = PollDeadline(float(self.config.get("loading 出现等待秒数", 6.0)), monotonic)
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template(
                    task_name,
                    spec,
                    name,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False

    def _wait_loading_gone_or_template(
        self: TaskSurface,
        task_name: str,
        spec: TemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = PollDeadline(float(self.config.get("loading 消失等待秒数", 35.0)), monotonic)
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False

    def _frame_confirms_home(self, frame, name: str) -> bool:
        confirmed, _left_hits, _p95, _gacha = self._home_confirmation_signals(
            frame,
            f"{name} 主页误检",
        )
        return confirmed

    def _wait_loading_or_template_or_ocr(
        self: TaskSurface,
        task_name: str,
        spec: TemplateSpec,
        keywords: list[str],
        name: str,
        interval: float = 0.5,
        reject_template_on_home: bool = False,
    ) -> tuple[str, bool, str]:
        end_at = PollDeadline(float(self.config.get("loading 出现等待秒数", 6.0)), monotonic)
        last_text = ""
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if (
                reject_template_on_home
                and self._passes(result, spec)
                and self._frame_confirms_home(frame, name)
            ):
                # BUG-20260901-02：金色签到弹窗模板在主页/切页帧打出 0.771
                # 误越过 0.76 阈值，导致脚本在进入公会页前就判定签到成功；
                # 确认主页三信号的帧不算命中。
                self.info_set(f"{name} 模板", f"{result.score:.3f} 主页帧误命中，忽略")
                self.log_info(
                    f"{task_name}：{spec.name} 模板在主页帧误命中"
                    f"（{result.score:.3f}），忽略并继续等待。"
                )
            elif self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template_or_ocr(
                    task_name,
                    spec,
                    keywords,
                    name,
                    last_text=last_text,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False, last_text

    def _wait_loading_gone_or_template_or_ocr(
        self: TaskSurface,
        task_name: str,
        spec: TemplateSpec,
        keywords: list[str],
        name: str,
        last_text: str = "",
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = PollDeadline(float(self.config.get("loading 消失等待秒数", 35.0)), monotonic)
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False, last_text
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False, last_text

    def _wait_for_template(
        self: TaskSurface,
        spec: TemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = PollDeadline(max(0.0, timeout), monotonic)
        last_score = -1.0
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _wait_for_template_or_ocr(
        self: TaskSurface,
        spec: TemplateSpec,
        keywords: list[str],
        timeout: float,
        name: str,
        interval: float = 0.5,
        reject_template_on_home: bool = False,
    ) -> tuple[bool, str]:
        end_at = PollDeadline(max(0.0, timeout), monotonic)
        last_text = ""
        while not end_at.poll_expired():
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if (
                reject_template_on_home
                and self._passes(result, spec)
                and self._frame_confirms_home(frame, name)
            ):
                self.info_set(f"{name} 模板", f"{result.score:.3f} 主页帧误命中，忽略")
                self.log_info(
                    f"{spec.name} 模板在主页帧误命中（{result.score:.3f}），"
                    "忽略并继续等待。"
                )
            elif self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_ocr_keywords(
        self: TaskSurface,
        keywords: list[str],
        timeout: float,
        minimum_matches: int,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = PollDeadline(max(0.0, timeout), monotonic)
        last_text = ""
        while not end_at.poll_expired():
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name)
            last_text = text
            count = self._keyword_match_count(text, keywords)
            self.info_set(f"{name} 关键字", f"{count}/{len(keywords)}")
            if count >= minimum_matches:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_home_confirmation(
        self: HomeReturnHost,
        name: str,
        interval: float = 0.35,
        timeout: float | None = None,
        auto_return: bool = True,
    ) -> bool:
        if timeout is None:
            timeout = float(self.config.get("主页确认等待秒数", 10.0))
        end_at = PollDeadline(timeout, monotonic)
        last_left_hits = 0
        last_p95 = 0.0
        last_gacha_text = ""
        while not end_at.poll_expired():
            frame = self.capture_frame()
            (
                confirmed,
                last_left_hits,
                last_p95,
                last_gacha_text,
            ) = self._home_confirmation_signals(frame, f"{name} 抽抽乐")
            self.info_set(
                f"{name} 左列关键词",
                f"{last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}",
            )
            self.info_set(
                f"{name} 亮度p95",
                f"{last_p95:.0f}/{self._home_p95_threshold():.0f}",
            )
            self.info_set(f"{name} 抽抽乐 OCR", last_gacha_text or "-")
            if confirmed:
                # 返回主页/确认主页后，转场动画仍在进行，立刻点击会被吞
                # （BUG-20260905：小屋/收菜入口点击被吞）。确认后再稳定等待几秒。
                settle = float(self.config.get("主页确认后稳定等待秒数", 6.0))
                if settle > 0:
                    self.sleep(settle)
                return True
            self.clear_temporary_home_announcement_if_needed(
                left_hits=last_left_hits,
                required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
                brightness=last_p95,
                brightness_threshold=self._home_p95_threshold(),
                gacha_ocr_text=last_gacha_text,
                context=name,
            )
            self.sleep(interval)

        if auto_return:
            return self._auto_return_home_after_timeout(name, interval=interval)

        self.log_info(
            f"{name}：未同时确认左列关键词、亮度和抽抽乐文字，"
            f"left={last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}, "
            f"p95={last_p95:.0f}/{self._home_p95_threshold():.0f}, "
            f"ocr={last_gacha_text or '-'}。"
        )
        return False

    def _auto_return_home_after_timeout(
        self: HomeReturnHost,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        """主页确认超时后，自动点击右上角主页按钮把角色带回主页面再确认。"""
        tries = int(self.config.get("主页确认自动返回主页次数", 2))
        self.log_info(
            f"{name}：未确认到主页，尝试自动点击返回主页按钮（最多 {tries} 次）。"
        )
        for _ in range(tries):
            self.attempt_return_home(name=f"{name} 自动返回主页")
            if self._wait_for_home_confirmation(
                name,
                interval=interval,
                timeout=float(self.config.get("主页确认等待秒数", 10.0)),
                auto_return=False,
            ):
                return True
        return False

    @staticmethod
    def _empty_match() -> MatchResult:
        return MatchResult(-1.0, (0, 0), (0, 0))

    def _match_best(
        self,
        frame,
        specs: tuple[TemplateSpec, ...],
    ) -> tuple[MatchResult, TemplateSpec]:
        best = self._empty_match()
        best_spec = specs[0]
        for spec in specs:
            result = self._match(frame, spec)
            if result.score > best.score:
                best = result
                best_spec = spec
        return best, best_spec

    def _match(self: TaskSurface, frame, spec: TemplateSpec) -> MatchResult:
        empty = MatchResult(-1.0, (0, 0), (0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            return task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=self._templates,
                min_size=8,
                loader=lambda _template_dir, spec: (
                    self._load_template(spec),
                    self._load_template_mask(spec),
                ),
            )
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

    def _load_template(self, spec: TemplateSpec) -> np.ndarray:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)[0]

    def _load_template_mask(self, spec: TemplateSpec) -> np.ndarray | None:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)[1]

    def _passes(self: TaskSurface, result: MatchResult, spec: TemplateSpec) -> bool:
        return task_vision.passes_match(result, spec, self.config)

    def _ocr_text(self: TaskSurface, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("日常 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        return keyword_match_count(text, keywords)

    def _home_p95_threshold(self: TaskSurface) -> float:
        return float(self.config.get("主页压暗阈值", HOME_DIMMED_P95_THRESHOLD_DEFAULT))
