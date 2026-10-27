"""卡带/主页业务 mixin：返回主页、页面识别决策与最近卡带 PVP 处理。

页面判定由 :mod:`src.tasks.page_recognition` 提供，这里只消费识别结果并决策动作；
最小操作（截图/OCR/点击）见 :mod:`src.tasks.device_ops_mixin`。
方法体自 ``BaseBD2Task`` 原样搬移，行为等价。
"""

import time
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable

import cv2
import numpy as np

from src.tasks.page_recognition import (
    PageId,
    PageRecognition,
    identify_page,
    reward_overlay_tap_box,
)
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT,
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    home_temporary_announcement_detected,
)
from src.utils.image_utils import (
    best_pixel_valid_match,
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

CARTRIDGE_RECENT_ENTRY_POINT = (0.7875, 0.9111111111111111)
# PVP 战斗设置页的左上返回按钮：该页按钮图形与普通返回条不同，
# 左上 ROI 模板在 2560x1440 实机帧只能得到 0.376，不能作为唯一入口。
PVP_BATTLE_BACK_REFERENCE_POINT = (133, 66)
RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS = 3.0
RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS = 3
RECENT_PVP_CARTRIDGE_TEMPLATE_FILE = "cartridge-image2-left-lower-cutout.png"
RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD = 0.85
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"


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


class CartridgeHomeMixin:
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
        识别到 PVP 战斗设置页时先关信息层、再点该页左上角返回，回到 PVP 大厅后
        才点右上角主页按钮；其余用 ROI+模板匹配点左上角返回键（仅非主页时执行，
        主页上它是“返回战场”）。有界循环直到主页或步数用尽。
        """
        step_limit = (
            int(self.config.get("自动返回主页最大步数", 6))
            if max_steps is None
            else int(max_steps)
        )
        settle = float(self.config.get("返回主页步间等待秒数", 2.5))
        no_action_steps = 0
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

            if recognition.page is PageId.HOME_ANNOUNCEMENT:
                self.log_info(
                    f"自动返回主页：第 {step} 步识别到登录奖励/更新活动遮罩，"
                    f"点击公告关闭点 @({HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[0]},"
                    f"{HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[1]})。"
                )
                self.operate_click(
                    *HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
                    name="自动返回主页-活动遮罩关闭",
                    after_sleep=settle,
                )
                no_action_steps = 0
                continue

            if recognition.has_house_hint:
                self.log_info(
                    f"自动返回主页：第 {step} 步检测到右上角 H，点房子回主页。"
                )
                x = float(self.config.get("返回主页按钮 X 百分比", 93.75)) / 100.0
                y = float(self.config.get("返回主页按钮 Y 百分比", 5.9)) / 100.0
                self.operate_click(x, y, name="自动返回主页-房子", after_sleep=settle)
                no_action_steps = 0
                continue

            if recognition.reward_overlay_tap_center is not None:
                tap_x, tap_y = recognition.reward_overlay_tap_center
                frame_height, frame_width = frame.shape[:2]
                self.log_info(
                    f"自动返回主页：第 {step} 步识别到赛季奖励结算层，"
                    f"点击“点击画面即可返回”提示 @({tap_x},{tap_y})。"
                )
                self.operate_click(
                    tap_x / max(1, frame_width),
                    tap_y / max(1, frame_height),
                    name="自动返回主页-赛季奖励关闭",
                    after_sleep=settle,
                )
                no_action_steps = 0
                continue

            if recognition.modal_cancel_center is not None:
                cancel_x, cancel_y = recognition.modal_cancel_center
                frame_height, frame_width = frame.shape[:2]
                self.log_info(
                    f"自动返回主页：第 {step} 步识别到可取消弹窗，"
                    f"点击“取消”@({cancel_x},{cancel_y})。"
                )
                self.operate_click(
                    cancel_x / max(1, frame_width),
                    cancel_y / max(1, frame_height),
                    name="自动返回主页-取消弹窗",
                    after_sleep=settle,
                )
                no_action_steps = 0
                continue

            if recognition.page is PageId.PVP_BATTLE:
                frame_height, frame_width = frame.shape[:2]
                close_center = recognition.detail_close_center
                if close_center is not None:
                    action_x, action_y = close_center
                    action_name = "自动返回主页-PVP信息层关闭"
                    action_desc = "关闭信息层"
                else:
                    action_x, action_y = PVP_BATTLE_BACK_REFERENCE_POINT
                    action_name = "自动返回主页-PVP战斗返回"
                    action_desc = "点击左上角返回"
                self.log_info(
                    f"自动返回主页：第 {step} 步识别到 PVP 战斗页，"
                    f"{action_desc} @({action_x},{action_y})。"
                )
                self.operate_click(
                    action_x / max(1, frame_width),
                    action_y / max(1, frame_height),
                    name=action_name,
                    after_sleep=settle,
                )
                no_action_steps = 0
                continue

            if self._click_gacha_detail_close(frame, step):
                no_action_steps = 0
                self.sleep(settle)
                continue

            if self._click_top_left_back(frame, step):
                no_action_steps = 0
                self.sleep(settle)
                continue

            if step >= step_limit:
                self.log_warning(
                    "自动返回主页：当前非主页且未找到 H/房子、详情关闭或左上角返回按钮，无法继续。"
                )
                return False

            # 没有任何可执行动作：可能是加载/转场的一瞬，也可能停在某个深层页面
            # （例如 PVP 大厅）。先等一步；连续两步都没动作，就升级为点游戏自带的
            # 右上角「返回主页」按钮（与 AutoLoginTask 的“游戏内返回主页”同一条路）。
            no_action_steps += 1
            if no_action_steps >= 2:
                self.log_info(
                    f"自动返回主页：第 {step} 步仍无可执行动作，"
                    "点右上角返回主页按钮升级处理。"
                )
                if self.attempt_return_home(name=f"自动返回主页 第{step}步"):
                    no_action_steps = 0
                continue

            self.log_info(
                f"自动返回主页：第 {step} 步未匹配到可执行动作（可能正在加载/转场），"
                f"等待 {settle:g}s 后重试。"
            )
            self.sleep(settle)

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
            # 最近卡带首次点击可能被转场吞掉。PVP 卡带在点击后没有特殊页面，
            # 只代表需要继续找快速切换，不能据此认定点击已成功；统一重试一次。
            quick_switch_opened = self._retry_recent_cartridge_entry(
                ensure_home=ensure_home,
                click_quick_switch=click_quick_switch,
                recent_cartridge_is_pvp=recent_cartridge_is_pvp,
            )
        if not quick_switch_opened:
            self._save_flow_diagnostic("cartridge_quick_switch_failed")
            return False
        return bool(confirm_quick_switch_page())

    def _retry_recent_cartridge_entry(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
        *,
        recent_cartridge_is_pvp: bool = False,
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
        if recent_cartridge_is_pvp:
            self._handle_recent_cartridge_special_pages()
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
    ) -> bool:
        """OCR and dismiss PVP promotion, demotion, and season reward pages.

        三种页面都按**同帧内容**识别，不看星期：赛季奖励结算层只在真实出现时才处理
        （原先按“北京时间周一”放行，导致周二及之后页面一直清不掉、流程卡死）。
        """
        end_at = PollDeadline(max(0.0, float(timeout)), monotonic)
        handled: set[str] = set()
        action_count = 0

        while True:
            boxes = self._recent_cartridge_ocr_boxes()
            text, action_name, target_box = self._pvp_special_page_action(boxes)
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
    ) -> tuple[str, str, object | None]:
        """Return a strict paired PVP special-page action from one OCR frame."""
        text = " ".join(
            str(getattr(box, "name", ""))
            for box in boxes
            if getattr(box, "name", "")
        )
        normalized = normalize_ocr_text(text)
        reward_box = reward_overlay_tap_box(boxes)
        if reward_box is not None:
            return text, "赛季奖励", reward_box
        if "恭喜晋级" in normalized and "确认" in normalized:
            return text, "恭喜晋级", cls._find_ocr_box(boxes, "确认")
        if "段位下滑" in normalized and "确认" in normalized:
            return text, "段位下滑", cls._find_ocr_box(boxes, "确认")
        return text, "", None


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
