"""回归：页面识别服务的主页精确匹配、抽卡详情信号与 UNKNOWN 不盲点。"""

import unittest

import numpy as np

from src.tasks.page_recognition import (
    GACHA_DETAIL_KEYWORDS,
    HOME_GACHA_LABEL,
    PVP_BATTLE_KEYWORDS,
    PageId,
    build_page_signals,
    identify_page,
)

FRAME = np.zeros((1440, 2560, 3), dtype=np.uint8)


class _Box:
    def __init__(self, name, x, y, width, height):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _BoxWithoutCoordinates:
    def __init__(self, name):
        self.name = name


def _detail_boxes(**overrides):
    labels = {
        "抽抽乐点数": (211, 1337, 138, 34),
        "抽1次": (1944, 1289, 104, 48),
        "抽10次": (2227, 1292, 128, 45),
    }
    labels.update(overrides)
    return [_Box(name, *geometry) for name, geometry in labels.items()]


class PageRecognitionTest(unittest.TestCase):
    def test_page_id_defines_required_pages(self):
        names = {page.name for page in PageId}
        self.assertLessEqual(
            {
                "HOME",
                "HOME_ANNOUNCEMENT",
                "CARD_COLLECTION",
                "DETAIL_OVERLAY",
                "GACHA_DETAIL",
                "UNKNOWN",
            },
            names,
        )

    def test_exact_home_label_is_home(self):
        recognition = identify_page(FRAME, [_Box("抽抽乐", 171, 1342, 88, 37)])

        self.assertIs(PageId.HOME, recognition.page)
        self.assertTrue(recognition.signals.is_home)

    def test_home_label_is_matched_after_normalization(self):
        recognition = identify_page(FRAME, [_Box(f" {HOME_GACHA_LABEL} ", 171, 1342, 88, 37)])

        self.assertIs(PageId.HOME, recognition.page)

    def test_card_collection_title_marks_card_collection_page(self):
        recognition = identify_page(
            FRAME,
            [_Box("游戏卡珍藏集", 317, 37, 288, 45)],
        )

        self.assertIs(PageId.CARD_COLLECTION, recognition.page)
        self.assertTrue(recognition.signals.card_collection_seen)

    def test_dimmed_home_announcement_is_not_home(self):
        boxes = [
            _Box("抽抽乐", 172, 1344, 80, 36),
            _Box("我的小屋", 163, 244, 120, 34),
            _Box("格鲁TALK", 163, 384, 264, 34),
            _Box("7天内不再显示", 1755, 304, 200, 35),
            _Box("前往查看", 1744, 1054, 131, 45),
        ]

        recognition = identify_page(FRAME, boxes)

        self.assertIs(PageId.HOME_ANNOUNCEMENT, recognition.page)
        self.assertTrue(recognition.signals.home_announcement_seen)

    def test_bright_home_with_announcement_text_stays_home(self):
        boxes = [
            _Box("抽抽乐", 172, 1344, 80, 36),
            _Box("我的小屋", 163, 244, 120, 34),
            _Box("格鲁TALK", 163, 384, 264, 34),
            _Box("7天内不再显示", 1755, 304, 200, 35),
            _Box("前往查看", 1744, 1054, 131, 45),
        ]

        recognition = identify_page(np.full_like(FRAME, 255), boxes)

        self.assertIs(PageId.HOME, recognition.page)
        self.assertFalse(recognition.signals.home_announcement_seen)

    def test_gacha_point_label_is_not_home(self):
        recognition = identify_page(FRAME, [_Box("抽抽乐点数", 211, 1337, 138, 34)])

        self.assertIs(PageId.UNKNOWN, recognition.page)
        self.assertFalse(recognition.signals.is_home)

    def test_paid_gacha_label_is_not_home(self):
        recognition = identify_page(FRAME, [_Box("付费抽抽乐", 1605, 1294, 152, 37)])

        self.assertIs(PageId.UNKNOWN, recognition.page)
        self.assertFalse(recognition.signals.is_home)

    def test_detail_labels_alone_produce_gacha_detail(self):
        recognition = identify_page(FRAME, _detail_boxes())

        self.assertIs(PageId.GACHA_DETAIL, recognition.page)
        self.assertEqual(GACHA_DETAIL_KEYWORDS, recognition.signals.gacha_detail_keywords)

    def test_gacha_detail_requires_every_keyword(self):
        for missing in GACHA_DETAIL_KEYWORDS:
            with self.subTest(missing=missing):
                boxes = [box for box in _detail_boxes() if box.name != missing]
                self.assertIs(PageId.UNKNOWN, identify_page(FRAME, boxes).page)

    def test_gacha_detail_keywords_keep_raw_substring_semantics(self):
        recognition = identify_page(
            FRAME,
            [_Box(label, 211, 1337, 138, 34) for label in ("抽抽乐点数", "抽1次", "抽10次可选")],
        )

        self.assertIs(PageId.GACHA_DETAIL, recognition.page)

    def test_home_takes_priority_over_gacha_detail_labels(self):
        boxes = [*_detail_boxes(), _Box("抽抽乐", 171, 1342, 88, 37)]

        self.assertIs(PageId.HOME, identify_page(FRAME, boxes).page)

    def test_detail_close_center_comes_from_in_region_x_label(self):
        recognition = identify_page(FRAME, [*_detail_boxes(), _Box("X", 1875, 410, 37, 40)])

        self.assertIs(PageId.GACHA_DETAIL, recognition.page)
        self.assertEqual((1893, 430), recognition.detail_close_center)

    def test_non_gacha_detail_x_marks_generic_detail_overlay(self):
        recognition = identify_page(
            FRAME,
            [
                _Box("抽抽乐券", 659, 508, 133, 45),
                _Box("拥有626个", 664, 559, 152, 37),
                _Box("X", 1875, 410, 37, 40),
            ],
        )

        self.assertIs(PageId.DETAIL_OVERLAY, recognition.page)
        self.assertEqual((1893, 430), recognition.detail_close_center)

    def test_detail_close_region_bounds_are_inclusive(self):
        recognition = identify_page(FRAME, [*_detail_boxes(), _Box("X", 1132, 410, 40, 40)])

        self.assertEqual((1152, 430), recognition.detail_close_center)

    def test_detail_close_center_ignores_out_of_region_x_label(self):
        recognition = identify_page(FRAME, [*_detail_boxes(), _Box("X", 100, 1300, 40, 40)])

        self.assertIs(PageId.GACHA_DETAIL, recognition.page)
        self.assertIsNone(recognition.detail_close_center)

    def test_box_without_coordinates_is_skipped(self):
        recognition = identify_page(
            FRAME,
            [*_detail_boxes(), _BoxWithoutCoordinates("X"), _BoxWithoutCoordinates("H")],
        )

        self.assertIs(PageId.GACHA_DETAIL, recognition.page)
        self.assertIsNone(recognition.detail_close_center)
        self.assertFalse(recognition.has_house_hint)

    def test_house_hint_keeps_normalized_strict_comparison(self):
        """旧实现用归一化文本与 "H" 严格比较，收敛后保持同一语义。"""
        recognition = identify_page(FRAME, [_Box("H", 2000, 80, 40, 40)])

        self.assertIs(PageId.UNKNOWN, recognition.page)
        self.assertIsNone(recognition.signals.house_hint_center)
        self.assertFalse(recognition.has_house_hint)

    def test_pvp_battle_requires_all_keywords(self):
        boxes = [
            _Box("阵形设置", 1420, 1040, 120, 40),
            _Box("BATTLE", 1370, 1320, 160, 60),
            _Box("自动战斗", 1810, 1300, 140, 50),
        ]

        recognition = identify_page(FRAME, boxes)

        self.assertIs(PageId.PVP_BATTLE, recognition.page)
        self.assertEqual(PVP_BATTLE_KEYWORDS, recognition.signals.pvp_battle_keywords)

        for missing in PVP_BATTLE_KEYWORDS:
            with self.subTest(missing=missing):
                partial = [box for box in boxes if box.name != missing]
                self.assertIs(PageId.UNKNOWN, identify_page(FRAME, partial).page)

    def test_unknown_without_any_label(self):
        recognition = identify_page(FRAME, [])

        self.assertIs(PageId.UNKNOWN, recognition.page)
        self.assertEqual((), recognition.signals.gacha_detail_keywords)
        self.assertIsNone(recognition.detail_close_center)

    def test_build_page_signals_is_pure_and_reusable(self):
        boxes = [*_detail_boxes(), _Box("X", 1875, 410, 37, 40)]

        first = build_page_signals(FRAME, boxes)
        second = build_page_signals(FRAME, boxes)

        self.assertEqual(first, second)
        self.assertEqual(GACHA_DETAIL_KEYWORDS, first.gacha_detail_keywords)
        self.assertEqual((1893, 430), first.detail_close_center)

class ModalDialogRecognitionTest(unittest.TestCase):
    """可取消弹窗（例如“鲜血鸡尾酒不足 / 取消 / 补充”）必须能被识别。"""

    def _box(self, name, x, y, width, height):
        return _Box(name=name, x=x, y=y, width=width, height=height)

    def test_cancel_label_marks_modal_dialog(self):
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        boxes = [
            self._box("鲜血鸡尾酒不足，无法进行战斗。是否补充鲜血鸡尾酒?", 800, 600, 900, 60),
            self._box("取消", 1070, 1067, 180, 28),
            self._box("补充", 1380, 1067, 180, 28),
        ]

        recognition = identify_page(frame, boxes)

        self.assertIs(PageId.MODAL_DIALOG, recognition.page)
        self.assertEqual((1160, 1081), recognition.modal_cancel_center)

    def test_longer_label_containing_cancel_is_not_a_button(self):
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        boxes = [self._box("取消体力消耗", 1070, 1067, 180, 28)]

        recognition = identify_page(frame, boxes)

        self.assertIs(PageId.UNKNOWN, recognition.page)
        self.assertIsNone(recognition.modal_cancel_center)



if __name__ == "__main__":
    unittest.main()
