"""页面识别服务：把散落在任务基类里的页面判定收敛为可复用业务能力。

判定只读取同一帧的 OCR 标签与像素坐标，不发起任何点击。调用方（
``BaseBD2Task``）按 ``PageId`` 决定动作；证据不足时页面对 ``PageId.UNKNOWN``，
调用方不得据此盲点。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.utils.ocr_utils import normalize_ocr_text

# 主页标签：OCR 文本归一化后必须“恰好等于”该标签才算主页，
# 因此“抽抽乐点数”“付费抽抽乐”这类包含关系不算主页。
HOME_GACHA_LABEL = "抽抽乐"
# 抽卡角色详情层：同一帧同时命中三个关键词才算该页面。
# 命中判断沿用旧实现的“原始标签拼接后子串命中”语义。
GACHA_DETAIL_KEYWORDS = ("抽抽乐点数", "抽1次", "抽10次")
# 右上角“H/房子”提示：保持旧实现的严格等值比较（归一化结果为小写，
# 所以该分支历史语义为不命中）；本工作包只做结构收敛，不改变实机行为。
HOUSE_HINT_LABEL = "H"
HOUSE_HINT_MIN_X_RATIO = 0.7
HOUSE_HINT_MAX_Y_RATIO = 0.25
# PVP 赛季奖励结算层：整屏没有 X / 确认按钮，屏幕自带
# “点击画面即可返回。”提示，因此唯一的动作是点击该提示文字的框中心。
# 两个标签必须同帧命中才认定，避免在别的页面上误点。
REWARD_OVERLAY_TITLE_LABEL = "赛季奖励"
REWARD_OVERLAY_TAP_LABEL = "点击画面即可返回"
# 抽卡详情层关闭按钮（X）的标签与命中区域。
DETAIL_CLOSE_LABELS = ("X", "\u00d7", "\u2715")
DETAIL_CLOSE_MIN_X_RATIO = 0.45
DETAIL_CLOSE_MAX_X_RATIO = 0.95
DETAIL_CLOSE_MIN_Y_RATIO = 0.10
DETAIL_CLOSE_MAX_Y_RATIO = 0.65


class PageId(str, Enum):
    """可确认的页面标识；无法确认时必须落到 ``UNKNOWN``。"""

    HOME = "home"
    GACHA_DETAIL = "gacha_detail"
    REWARD_OVERLAY = "reward_overlay"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageSignals:
    """一帧 OCR 结果中与页面判定相关的证据。"""

    home_label_seen: bool
    gacha_detail_keywords: tuple[str, ...]
    house_hint_center: tuple[int, int] | None
    detail_close_center: tuple[int, int] | None
    reward_overlay_tap_center: tuple[int, int] | None

    @property
    def is_home(self) -> bool:
        return self.home_label_seen

    @property
    def is_gacha_detail(self) -> bool:
        return len(self.gacha_detail_keywords) == len(GACHA_DETAIL_KEYWORDS)

    @property
    def has_house_hint(self) -> bool:
        return self.house_hint_center is not None

    @property
    def has_reward_overlay(self) -> bool:
        return self.reward_overlay_tap_center is not None


@dataclass(frozen=True)
class PageRecognition:
    """页面判定结果：``page`` 以及支撑它的同帧证据。"""

    page: PageId
    signals: PageSignals

    @property
    def has_house_hint(self) -> bool:
        return self.signals.has_house_hint

    @property
    def detail_close_center(self) -> tuple[int, int] | None:
        return self.signals.detail_close_center

    @property
    def reward_overlay_tap_center(self) -> tuple[int, int] | None:
        return self.signals.reward_overlay_tap_center


def _box_center(box) -> tuple[int, int] | None:
    try:
        x, y, width, height = int(box.x), int(box.y), int(box.width), int(box.height)
    except Exception:
        return None
    return x + width // 2, y + height // 2


def _in_house_hint_region(
    center: tuple[int, int],
    frame_width: int,
    frame_height: int,
) -> bool:
    center_x, center_y = center
    return (
        center_x / frame_width > HOUSE_HINT_MIN_X_RATIO
        and center_y / frame_height < HOUSE_HINT_MAX_Y_RATIO
    )


def _in_detail_close_region(
    center: tuple[int, int],
    frame_width: int,
    frame_height: int,
) -> bool:
    center_x, center_y = center
    return (
        DETAIL_CLOSE_MIN_X_RATIO * frame_width
        <= center_x
        <= DETAIL_CLOSE_MAX_X_RATIO * frame_width
        and DETAIL_CLOSE_MIN_Y_RATIO * frame_height
        <= center_y
        <= DETAIL_CLOSE_MAX_Y_RATIO * frame_height
    )


def reward_overlay_tap_box(boxes) -> object | None:
    """返回 PVP 赛季奖励结算层的“点击画面即可返回。”提示框。

    只有同帧同时出现 ``赛季奖励`` 与 ``点击画面即可返回`` 才认定该层，
    因此这里既是识别也是唯一的关闭目标来源。
    """
    joined = " ".join(str(getattr(box, "name", "") or "") for box in boxes)
    normalized = normalize_ocr_text(joined)
    if normalize_ocr_text(REWARD_OVERLAY_TITLE_LABEL) not in normalized:
        return None
    if normalize_ocr_text(REWARD_OVERLAY_TAP_LABEL) not in normalized:
        return None
    normalized_tap = normalize_ocr_text(REWARD_OVERLAY_TAP_LABEL)
    for box in boxes:
        if normalized_tap in normalize_ocr_text(getattr(box, "name", "")):
            return box
    return None


def build_page_signals(frame, boxes) -> PageSignals:
    """把同一帧的截图尺寸与 OCR 框转换为页面信号（纯函数，不点击）。"""
    frame_height, frame_width = frame.shape[:2]
    joined_labels = " ".join(str(getattr(box, "name", "") or "") for box in boxes)
    gacha_detail_keywords = tuple(
        keyword for keyword in GACHA_DETAIL_KEYWORDS if keyword in joined_labels
    )

    home_label_seen = False
    house_hint_center: tuple[int, int] | None = None
    detail_close_center: tuple[int, int] | None = None
    reward_overlay_box = reward_overlay_tap_box(boxes)
    for box in boxes:
        label = str(getattr(box, "name", "") or "")
        normalized_label = normalize_ocr_text(label).strip()
        if normalized_label == HOME_GACHA_LABEL:
            home_label_seen = True
            continue
        if normalized_label == HOUSE_HINT_LABEL:
            center = _box_center(box)
            if (
                house_hint_center is None
                and center is not None
                and _in_house_hint_region(center, frame_width, frame_height)
            ):
                house_hint_center = center
            continue
        if detail_close_center is None and label.strip().upper() in DETAIL_CLOSE_LABELS:
            center = _box_center(box)
            if center is not None and _in_detail_close_region(center, frame_width, frame_height):
                detail_close_center = center

    return PageSignals(
        home_label_seen=home_label_seen,
        gacha_detail_keywords=gacha_detail_keywords,
        house_hint_center=house_hint_center,
        detail_close_center=detail_close_center,
        reward_overlay_tap_center=(
            _box_center(reward_overlay_box) if reward_overlay_box is not None else None
        ),
    )


def identify_page(frame, boxes) -> PageRecognition:
    """判定当前页面：主页优先，其次抽卡角色详情层，其余一律 ``UNKNOWN``。"""
    signals = build_page_signals(frame, boxes)
    if signals.is_home:
        page = PageId.HOME
    elif signals.is_gacha_detail:
        page = PageId.GACHA_DETAIL
    elif signals.has_reward_overlay:
        page = PageId.REWARD_OVERLAY
    else:
        page = PageId.UNKNOWN
    return PageRecognition(page=page, signals=signals)
