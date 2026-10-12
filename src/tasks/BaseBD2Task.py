"""BaseBD2Task：状态输出、生命周期与多继承组合根。

工具/设备操作见 :mod:`src.tasks.device_ops_mixin`；本模块保留 ``__init__``、
``info_*`` 状态输出、任务生命周期、``task_info_snapshot`` 与卡带/主页业务方法
（第二步搬出），既有的模块级导入点通过下面的兼容再导出保持可用。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable

import cv2
import numpy as np
from ok import BaseTask, Logger, og

from src.scene.BD2Scene import BD2Scene
from src.scene.ScreenPosition import ScreenPosition
from src.tasks.device_ops_mixin import (
    GREEN_MASK_TOLERANCE,
    PROBE_OUTPUT_DIR,
    DeviceOpsMixin,
)
from src.tasks.page_recognition import PageId, PageRecognition, identify_page
from src.tasks.task_notifications import log_task_completion
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    home_temporary_announcement_detected,
)
from src.utils.image_utils import (
    best_pixel_valid_match,
    green_mask_from_template,
    masked_zncc,
    pixel_similarity,
    resize_mask,
    resize_template,
    template_match_response,
    to_gray,
)
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.polling import PollDeadline
from src.utils.template_resolution import offline_template_scale

logger = Logger.get_logger(__name__)
CARTRIDGE_RECENT_ENTRY_POINT = (0.7875, 0.9111111111111111)
RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS = 3.0
RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS = 3
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
RECENT_PVP_CARTRIDGE_TEMPLATE_FILE = "cartridge-image2-left-lower-cutout.png"
RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD = 0.85
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"
# Shared lock for task instances whose ``__init__`` never ran (object.__new__);
# see ``BaseBD2Task._task_info_lock``.
_INFO_FALLBACK_LOCK = threading.RLock()


# 拆分前可导入的模块级名字继续保留（``__all__`` 同时声明再导出意图）。
__all__ = [
    "BEIJING_TIMEZONE",
    "CARTRIDGE_RECENT_ENTRY_POINT",
    "GREEN_MASK_TOLERANCE",
    "PROBE_OUTPUT_DIR",
    "PROJECT_ROOT",
    "RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS",
    "RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS",
    "RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD",
    "RECENT_PVP_CARTRIDGE_TEMPLATE_FILE",
    "RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD",
    "RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD",
    "TEMPLATE_DIR",
    "BaseBD2Task",
    "RecentPvpCartridgeMatch",
    "green_mask_from_template",
    "task_info_snapshot",
]

@dataclass(frozen=True)
class RecentPvpCartridgeMatch:
    score: float = -1.0
    pixel_score: float = -1.0
    zncc_score: float = -1.0
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)

    @property
    def passed(self) -> bool:
        return (
            self.score >= RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD
            and self.pixel_score >= RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD
            and self.zncc_score >= RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD
        )


class BaseBD2Task(DeviceOpsMixin, BaseTask):
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visible = False
        self.scene: BD2Scene | None = None
        self.default_box = ScreenPosition(self)
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()
        # ``task.info`` is a plain dict shared between the TaskExecutor worker
        # thread (info_set/info_clear/log_* and DailyBatchTask child resets)
        # and UI/diagnostics readers.  All mutations go through the mutators
        # below, which serialize on this lock so readers can take a consistent
        # copy via ``info_snapshot``/``task_info_snapshot``.  ``run_task_by_class``
        # rebinds ``task.info`` as a raw attribute, so every locked section must
        # re-read the attribute instead of caching the dict.
        self._info_lock = threading.RLock()
        self._last_home_announcement_clear_at = 0.0
        self._recent_pvp_cartridge_template_cache: (
            tuple[np.ndarray, np.ndarray] | None
        ) = None
        self.default_config.update(
            {
                "识别成功后等待秒数": 1.0,
                "执行前自动返回主页": True,
                # 自动返回主页按钮位置（右上角“小房子”，格鲁菲餐厅实测 2560x1440 的约 (2400,85)）。
                "返回主页按钮 X 百分比": 93.75,
                "返回主页按钮 Y 百分比": 5.9,
                "主页确认自动返回主页次数": 2,
                "自动返回主页最大步数": 6,
                "返回主页步间等待秒数": 2.5,
                "左上角返回模板阈值": 0.55,
                "左上角返回ROI右百分比": 13,  # 2560x1440 下约 x<333
                "左上角返回ROI底百分比": 11,  # 2560x1440 下约 y<158

            }
        )
        self.config_description.update(
            {
                "识别成功后等待秒数": "识别成功后，执行下一步点击或切换操作前等待多久。",
                "执行前自动返回主页": "任务开始前是否先尝试自动返回主页；关闭后直接执行任务。",
                "返回主页按钮 X 百分比": "自动返回主页按钮的横向百分比。",
                "返回主页按钮 Y 百分比": "自动返回主页按钮的纵向百分比。",
                "主页确认自动返回主页次数": "主页确认超时后，自动点击返回主页按钮的最大次数。",
            }
        )

    def log_completion(self, message: str) -> None:
        """Publish a completion popup unless this task is running as a batch child."""

        log_task_completion(self, message)

    def _task_info_lock(self) -> threading.RLock:
        """The per-instance info lock, or a shared fallback for odd instances.

        Tests routinely build tasks via ``object.__new__`` without running
        ``__init__``; those instances have no ``_info_lock`` yet still go through
        these overrides, so they fall back to one process-wide lock instead of
        raising ``AttributeError``.
        """
        lock = self.__dict__.get("_info_lock")
        return _INFO_FALLBACK_LOCK if lock is None else lock

    def info_clear(self) -> None:
        with self._task_info_lock():
            super().info_clear()

    def info_incr(self, key, inc=1):
        with self._task_info_lock():
            return super().info_incr(key, inc)

    def info_add_to_list(self, key, item):
        with self._task_info_lock():
            return super().info_add_to_list(key, item)

    def info_set(self, key, value):
        with self._task_info_lock():
            return super().info_set(key, value)

    def info_add(self, key, count=1):
        with self._task_info_lock():
            return super().info_add(key, count)

    def info_snapshot(self) -> dict:
        """A consistent point-in-time copy of ``self.info``.

        The copy is taken under the same lock as every mutator, so it can never
        observe a half-applied update or hit "dictionary changed size during
        iteration".  ``self.info`` is re-read inside the lock because
        ``run_task_by_class`` rebinds the attribute as a whole.
        """
        with self._task_info_lock():
            return dict(getattr(self, "info", None) or {})

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor | None:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    @staticmethod
    def submit_periodic_task(delay: float, task: Callable, *args, **kwargs):
        if og.my_app is None:
            return None
        return og.my_app.submit_periodic_task(delay, task, *args, **kwargs)

    def attempt_return_home(self, name: str = "自动返回主页") -> bool:
        """正式路径点击右上角“主页”按钮，把角色从子界面带回主页面。

        注意：需要调用方先确认当前不在主页面（或在主页确认失败后调用），
        避免在主页面误点该位置。会移动真实光标（正式版方案）。
        """
        try:
            x = float(self.config.get("返回主页按钮 X 百分比", 93.75)) / 100.0
            y = float(self.config.get("返回主页按钮 Y 百分比", 5.9)) / 100.0
            self.log_info(f"{name}：点击右上角主页按钮返回主界面。")
            self.operate_click(
                x,
                y,
                name=name,
                after_sleep=2.0,
                down_time=0.02,
            )
            return True
        except Exception as exc:
            self.log_warning(f"{name}：点击返回主页按钮失败：{exc}")
            return False

    def auto_return_main_home(self, max_steps: int | None = None) -> bool:
        """自动返回主页面。

        每步：截图 → ``identify_page`` 识别页面，再按识别结果决策：
        已是主页直接成功；识别到右上角 H/房子提示时点房子；识别到抽卡详情层时点 X 关闭；
        其余用 ROI+模板匹配点左上角返回键（仅非主页时执行，主页上它是“返回战场”）。
        有界循环直到主页或步数用尽。
        """
        step_limit = (
            int(self.config.get("自动返回主页最大步数", 6))
            if max_steps is None
            else int(max_steps)
        )
        settle = float(self.config.get("返回主页步间等待秒数", 2.5))
        for step in range(1, step_limit + 1):
            try:
                frame = self.capture_frame()
            except Exception as exc:
                self.log_warning(f"自动返回主页：截图失败：{exc}")
                return False

            recognition = self._recognize_page(frame)
            if recognition.page is PageId.HOME:
                self.info_set("自动返回主页", f"已是主页（第{step}步）")
                return True

            if recognition.has_house_hint:
                self.log_info(
                    f"自动返回主页：第 {step} 步检测到右上角 H，点房子回主页。"
                )
                x = float(self.config.get("返回主页按钮 X 百分比", 93.75)) / 100.0
                y = float(self.config.get("返回主页按钮 Y 百分比", 5.9)) / 100.0
                self.operate_click(x, y, name="自动返回主页-房子", after_sleep=settle)
                continue

            if self._click_gacha_detail_close(frame, step):
                self.sleep(settle)
                continue

            if self._click_top_left_back(frame, step):
                self.sleep(settle)
                continue

            self.log_warning(
                "自动返回主页：当前非主页且未找到 H/房子、详情关闭或左上角返回按钮，无法继续。"
            )
            return False

        self.log_warning(f"自动返回主页：{step_limit} 步内未回到主页。")
        return False

    def _recognize_page(self, frame) -> PageRecognition:
        """用该帧 OCR 结果识别页面；OCR 失败等价于旧实现的“信号均未命中”。"""
        try:
            boxes = list(
                self.ocr(
                    frame=frame,
                    threshold=0.2,
                    log=False,
                    name="自动返回主页",
                )
            )
        except Exception:
            return identify_page(frame, [])
        return identify_page(frame, boxes)

    def _click_gacha_detail_close(self, frame, step: int) -> bool:
        """识别抽卡角色详情层，并在识别到的 X 中心点击关闭。"""
        try:
            boxes = list(
                self.ocr(
                    frame=frame,
                    threshold=0.2,
                    log=False,
                    name="自动返回主页-抽卡详情",
                )
            )
        except Exception:
            return False

        recognition = identify_page(frame, boxes)
        close_center = recognition.detail_close_center
        if recognition.page is not PageId.GACHA_DETAIL or close_center is None:
            return False

        center_x, center_y = close_center
        frame_h, frame_w = frame.shape[:2]
        self.log_info(
            f"自动返回主页：第 {step} 步识别到抽卡详情页，点击关闭 X @"
            f"({center_x},{center_y})。"
        )
        self.operate_click(
            center_x / frame_w,
            center_y / frame_h,
            name="自动返回主页-抽卡详情关闭",
            after_sleep=0.0,
        )
        return True

    def _click_top_left_back(self, frame, step: int) -> bool:
        """在左上角 ROI 内用模板匹配“返回”条，命中则点其中心。"""
        try:
            import cv2

            frame_h, frame_w = frame.shape[:2]
            left = 0
            top = 0
            right = int(frame_w * float(self.config.get("左上角返回ROI右百分比", 13)) / 100.0)
            bottom = int(frame_h * float(self.config.get("左上角返回ROI底百分比", 11)) / 100.0)
            if right <= left or bottom <= top:
                return False
            crop = cv2.cvtColor(
                frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY
            )
            template_path = TEMPLATE_DIR / "image" / "back_return_bar_arrow.png"
            template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                self.log_warning(f"自动返回主页：找不到返回模板 {template_path}")
                return False

            threshold = float(self.config.get("左上角返回模板阈值", 0.55))
            best = None
            for scale in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3):
                t_width = max(8, round(template.shape[1] * scale))
                t_height = max(8, round(template.shape[0] * scale))
                if t_width >= crop.shape[1] or t_height >= crop.shape[0]:
                    continue
                resized = cv2.resize(
                    template,
                    (t_width, t_height),
                    interpolation=cv2.INTER_AREA,
                )
                response = template_match_response(crop, resized)
                _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(response)
                if best is None or max_v > best[0]:
                    best = (max_v, max_loc[0], max_loc[1], t_width, t_height)
            if best is None or best[0] < threshold:
                best_score = -1.0 if best is None else best[0]
                self.info_set("左上角返回模板", f"未命中 {best_score:.3f}/{threshold:.3f}")
                return False

            score, loc_x, loc_y, t_width, t_height = best
            center_x = left + loc_x + t_width // 2
            center_y = top + loc_y + t_height // 2
            self.info_set("左上角返回模板", f"{score:.3f}/{threshold:.3f} @{center_x},{center_y}")
            self.log_info(
                f"自动返回主页：第 {step} 步 ROI 命中左上角返回 @({center_x},{center_y})，点击。"
            )
            self.operate_click(
                center_x / frame_w,
                center_y / frame_h,
                name="自动返回主页-左上返回",
                after_sleep=0.0,
            )
            return True
        except Exception as exc:
            self.log_warning(f"自动返回主页：左上角返回点击失败：{exc}")
            return False

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
        """Clear a dimming announcement only when the other two home signals pass."""
        if not home_temporary_announcement_detected(
            left_hits=left_hits,
            required_left_hits=required_left_hits,
            brightness=brightness,
            brightness_threshold=brightness_threshold,
            gacha_ocr_text=gacha_ocr_text,
        ):
            return False

        now = monotonic()
        last_click_at = float(
            getattr(self, "_last_home_announcement_clear_at", 0.0)
        )
        if now - last_click_at < 1.0:
            return True
        self._last_home_announcement_clear_at = now

        clear_x, clear_y = HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT
        self.info_set(
            "主页临时公告",
            f"{context}：亮度 {brightness:.3f}/{brightness_threshold:.3f}",
        )
        self.log_info(
            f"{context}：左列关键词和抽抽乐 OCR 已命中但亮度不足，"
            f"按登录公告流程点击清理位置，brightness={brightness:.3f}, "
            f"x={clear_x:.2%}, y={clear_y:.2%}。"
        )
        self._sleep_after_recognition()
        self.operate_click(clear_x, clear_y, after_sleep=0.2)
        return True

    def open_cartridge_quick_switcher(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
        confirm_quick_switch_page: Callable[[], bool],
    ) -> bool:
        """Open the recent cartridge, click quick-switch, and confirm its page."""
        if not ensure_home():
            return False

        try:
            recent_cartridge_is_pvp = self._recent_cartridge_is_pvp()
        except RuntimeError as exc:
            self.info_set("最近卡带 PVP 模板错误", str(exc))
            self.log_warning(str(exc), notify=True)
            return False

        # Fixed common flow: confirmed home -> classify the recent cartridge
        # -> recognition settle delay
        # -> recent cartridge -> OCR PVP special pages only for a recent PVP cartridge
        # -> recognize the quick-switch icon -> click the recognized center
        # -> confirm the cartridge selection page.
        self._sleep_after_recognition()
        self.info_set("当前阶段", "点击最近卡带")
        self.operate_click(*CARTRIDGE_RECENT_ENTRY_POINT, after_sleep=0.0)
        if recent_cartridge_is_pvp:
            self._handle_recent_cartridge_special_pages()
        self.info_set("当前阶段", "寻找快速切换按钮")
        quick_switch_opened = click_quick_switch()
        if not quick_switch_opened:
            if recent_cartridge_is_pvp:
                handled_after_timeout = self._handle_recent_cartridge_special_pages()
                if not handled_after_timeout:
                    self._save_flow_diagnostic("cartridge_quick_switch_failed")
                    return False
                self.info_set("当前阶段", "特殊页面后重试快速切换按钮")
                quick_switch_opened = click_quick_switch()
            else:
                quick_switch_opened = self._retry_recent_cartridge_entry(
                    ensure_home=ensure_home,
                    click_quick_switch=click_quick_switch,
                )
        if not quick_switch_opened:
            self._save_flow_diagnostic("cartridge_quick_switch_failed")
            return False
        return bool(confirm_quick_switch_page())

    def _retry_recent_cartridge_entry(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
    ) -> bool:
        """Re-click the recent cartridge once after a lost confirmed-home click.

        The first click can be dropped game-side without opening the panel
        (BUG-20260829-07: three identical clicks timed out for 10s each, the
        same click worked right after an app restart). Guarded by a fresh home
        confirmation so the entry point is never pressed over an open panel.
        """
        self.info_set("当前阶段", "重试点击最近卡带")
        if not ensure_home():
            return False
        self._sleep_after_recognition()
        self.info_set("当前阶段", "点击最近卡带")
        self.operate_click(*CARTRIDGE_RECENT_ENTRY_POINT, after_sleep=0.0)
        return click_quick_switch()

    def _save_flow_diagnostic(self, name: str) -> None:
        """Persist a failure frame; report bundles pick up *_failed/_error stems."""
        try:
            self.save_frame(name, self.capture_frame())
        except Exception as exc:
            self.log_warning(f"诊断截图保存失败：{exc}")

    def _recent_cartridge_is_pvp(self) -> bool:
        """Detect whether clicking the recent cartridge may open a PVP page."""
        frame = self.capture_frame()
        result = self._match_recent_pvp_cartridge(frame)
        verdict = "PVP" if result.passed else "非 PVP"
        self.info_set(
            "最近卡带 PVP 模板",
            (
                f"{verdict} m={result.score:.3f} p={result.pixel_score:.3f} "
                f"z={result.zncc_score:.3f} box={result.position}+{result.size}"
            ),
        )
        return result.passed

    def _match_recent_pvp_cartridge(
        self,
        frame: np.ndarray,
    ) -> RecentPvpCartridgeMatch:
        template, mask = self._load_recent_pvp_cartridge_template()
        frame_gray = to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(
            RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
            frame_width,
            frame_height,
        )
        scaled_template = resize_template(template, scale)
        scaled_mask = resize_mask(mask, scale)
        height, width = scaled_template.shape[:2]
        if (
            height < 5
            or width < 5
            or height > frame_height
            or width > frame_width
        ):
            return RecentPvpCartridgeMatch(size=(width, height))

        try:
            response = template_match_response(
                frame_gray,
                scaled_template,
                scaled_mask,
            )
        except cv2.error as exc:
            raise RuntimeError(f"最近卡带 PVP 模板匹配失败：{exc}") from exc

        candidate = best_pixel_valid_match(
            response,
            frame_gray,
            scaled_template,
            scaled_mask,
            template_threshold=RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
            pixel_threshold=RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
            zncc_threshold=RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
        )
        if candidate is not None:
            return RecentPvpCartridgeMatch(
                score=candidate.score,
                pixel_score=candidate.pixel_score,
                zncc_score=candidate.zncc_score,
                position=candidate.location,
                size=(width, height),
            )

        _minimum, score, _minimum_location, location = cv2.minMaxLoc(response)
        x, y = location
        region = frame_gray[y : y + height, x : x + width]
        return RecentPvpCartridgeMatch(
            score=float(score),
            pixel_score=pixel_similarity(region, scaled_template, scaled_mask),
            zncc_score=masked_zncc(region, scaled_template, scaled_mask),
            position=(int(x), int(y)),
            size=(width, height),
        )

    def _load_recent_pvp_cartridge_template(
        self,
    ) -> tuple[np.ndarray, np.ndarray]:
        cached = getattr(self, "_recent_pvp_cartridge_template_cache", None)
        if cached is not None:
            return cached

        path = TEMPLATE_DIR / RECENT_PVP_CARTRIDGE_TEMPLATE_FILE
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"最近卡带 PVP 模板不存在或无法读取：{path}")
        if raw.ndim != 3 or raw.shape[2] < 4:
            raise RuntimeError(f"最近卡带 PVP 模板缺少 Alpha 通道：{path}")

        mask = np.where(raw[:, :, 3] > 0, 255, 0).astype(np.uint8)
        active_pixels = int(np.count_nonzero(mask))
        if active_pixels <= 0 or active_pixels >= mask.size:
            raise RuntimeError(f"最近卡带 PVP 模板 Alpha 遮罩无效：{path}")

        cached = (to_gray(raw), mask)
        self._recent_pvp_cartridge_template_cache = cached
        return cached

    def _handle_recent_cartridge_special_pages(
        self,
        timeout: float = RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
        interval: float = 0.25,
        allow_season_reward: bool | None = None,
    ) -> bool:
        """OCR and dismiss PVP promotion, demotion, and season reward pages."""
        if allow_season_reward is None:
            allow_season_reward = self._is_beijing_monday()
        end_at = PollDeadline(max(0.0, float(timeout)), monotonic)
        handled: set[str] = set()
        action_count = 0

        while True:
            boxes = self._recent_cartridge_ocr_boxes()
            text, action_name, target_box = self._pvp_special_page_action(
                boxes,
                allow_season_reward=allow_season_reward,
            )
            self.info_set("最近卡带特殊页面 OCR", text or "-")

            if action_name and action_name not in handled and target_box is not None:
                point = self._ocr_box_center(target_box)
                if point is not None:
                    frame_width = max(1, int(self.width))
                    frame_height = max(1, int(self.height))
                    self.info_set("当前阶段", f"处理最近卡带{action_name}")
                    self.operate_click(
                        max(0.0, min(1.0, point[0] / frame_width)),
                        max(0.0, min(1.0, point[1] / frame_height)),
                        after_sleep=0.5,
                    )
                    handled.add(action_name)
                    action_count += 1

            if (
                end_at.expired()
                or action_count >= RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
            ):
                break
            self.sleep(max(0.0, float(interval)))

        return bool(handled)

    @classmethod
    def _pvp_special_page_action(
        cls,
        boxes: list,
        *,
        allow_season_reward: bool,
    ) -> tuple[str, str, object | None]:
        """Return a strict paired PVP special-page action from one OCR frame."""
        text = " ".join(
            str(getattr(box, "name", ""))
            for box in boxes
            if getattr(box, "name", "")
        )
        normalized = normalize_ocr_text(text)
        if (
            allow_season_reward
            and "赛季奖励" in normalized
            and "点击画面即可返回" in normalized
        ):
            return text, "赛季奖励", cls._find_ocr_box(boxes, "点击画面即可返回")
        if "恭喜晋级" in normalized and "确认" in normalized:
            return text, "恭喜晋级", cls._find_ocr_box(boxes, "确认")
        if "段位下滑" in normalized and "确认" in normalized:
            return text, "段位下滑", cls._find_ocr_box(boxes, "确认")
        return text, "", None

    @staticmethod
    def _is_beijing_monday(now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        return now.astimezone(BEIJING_TIMEZONE).weekday() == 0

    def _pvp_special_page_ocr_boxes(
        self,
        frame: np.ndarray | None = None,
        *,
        name: str = "PVP 特殊页面",
    ) -> list:
        try:
            if frame is None:
                frame = self.capture_frame()
            config = getattr(self, "config", {})
            threshold = next(
                (
                    float(config[key])
                    for key in (
                        "PVP OCR 阈值",
                        "广场 OCR 阈值",
                        "跑商 OCR 阈值",
                        "跑图 OCR 阈值",
                    )
                    if key in config
                ),
                0.2,
            )
            boxes = self.ocr(
                frame=frame,
                threshold=threshold,
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []
        return list(boxes)

    def _recent_cartridge_ocr_boxes(self) -> list:
        return self._pvp_special_page_ocr_boxes(name="最近卡带特殊页面")

    @staticmethod
    def _find_ocr_box(boxes: list, keyword: str):
        normalized_keyword = normalize_ocr_text(keyword)
        for box in boxes:
            if normalized_keyword in normalize_ocr_text(getattr(box, "name", "")):
                return box
        return None

    @staticmethod
    def _ocr_box_center(box) -> tuple[float, float] | None:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            raw_box = getattr(box, "box", None)
            if raw_box is None or len(raw_box) < 4:
                return None
            values = tuple(raw_box[:4])
        x, y, width, height = (float(value) for value in values)
        return x + width / 2, y + height / 2

    def _check_action_interval(self, action_name: Any, interval: float) -> bool:
        if interval <= 0:
            return True
        with self._action_interval_lock:
            now = time.time()
            last_time = self._last_interval_action_time.get(action_name, 0)
            if now - last_time < interval:
                return False
            self._last_interval_action_time[action_name] = now
            return True

    def run_with_interval(
        self,
        func: Callable,
        interval: float,
        *args,
        action_name=None,
        **kwargs,
    ) -> Any:
        action_name = action_name or getattr(func, "__qualname__", repr(func))
        if not self._check_action_interval(action_name, interval):
            return False
        return func(*args, **kwargs)

    def mark_logged_in(self) -> None:
        if self.scene is not None:
            self.scene.set_logged_in(True)

    def is_main(self) -> bool:
        return bool(self.scene and self.scene.logged_in())

    def wait_main(self, time_out: float = 30, raise_if_not_found: bool = False):
        return self.wait_until(
            self.is_main,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
        )


def task_info_snapshot(task) -> dict:
    """Best-effort thread-safe copy of ``task.info`` for any task object.

    ``BaseBD2Task`` (and subclasses) expose a locked ``info_snapshot``; tasks
    outside that hierarchy — ok-framework tasks such as ``BD2TriggerTask`` or
    plain stubs — only get an unlocked ``dict`` copy.  For those, a concurrent
    mutation can still surface as ``RuntimeError`` mid-copy, which is swallowed
    into an empty snapshot so UI/diagnostics readers stay alive.
    Callers that must know about snapshot failures (diagnostics) perform their
    own strict copy instead of relying on this fallback.
    """
    snapshot = getattr(task, "info_snapshot", None)
    if callable(snapshot):
        return snapshot()
    try:
        return dict(getattr(task, "info", None) or {})
    except RuntimeError:
        return {}
