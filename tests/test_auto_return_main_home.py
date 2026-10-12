"""回归：自动返回主页只消费页面识别结果，并保持既有动作顺序。"""

import unittest

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.page_recognition import PageId, PageRecognition, PageSignals

FRAME = np.zeros((1440, 2560, 3), dtype=np.uint8)


class _Box:
    def __init__(self, name, x, y, width, height):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


def _signals(**overrides):
    values = {
        "home_label_seen": False,
        "gacha_detail_keywords": (),
        "house_hint_center": None,
        "detail_close_center": None,
    }
    values.update(overrides)
    return PageSignals(**values)


def _recognition(page=PageId.UNKNOWN, **signal_overrides):
    return PageRecognition(page=page, signals=_signals(**signal_overrides))


class AutoReturnMainHomeTest(unittest.TestCase):
    def _task(self, boxes):
        task = object.__new__(BaseBD2Task)
        task.ocr = lambda **_kwargs: boxes
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        return task, clicks

    def _looping_task(self, recognitions):
        task = object.__new__(BaseBD2Task)
        task.config = {"自动返回主页最大步数": 2, "返回主页步间等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        warnings = []
        task.log_warning = lambda message, *args, **kwargs: warnings.append(message)
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: FRAME
        scans = iter(recognitions)
        task._recognize_page = lambda _frame: next(scans)
        clicks = []
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        return task, clicks, warnings

    def test_recognize_page_does_not_treat_gacha_detail_labels_as_home(self):
        task, _clicks = self._task(
            [
                _Box("抽抽乐点数", 211, 1337, 138, 34),
                _Box("付费抽抽乐", 1605, 1294, 152, 37),
                _Box("抽1次：", 1941, 1292, 123, 45),
            ]
        )

        self.assertIs(PageId.UNKNOWN, BaseBD2Task._recognize_page(task, FRAME).page)

    def test_recognize_page_accepts_exact_gacha_home_label(self):
        task, _clicks = self._task([_Box("抽抽乐", 171, 1342, 88, 37)])

        self.assertIs(PageId.HOME, BaseBD2Task._recognize_page(task, FRAME).page)

    def test_recognize_page_without_labels_is_unknown(self):
        task, _clicks = self._task([])

        self.assertIs(PageId.UNKNOWN, BaseBD2Task._recognize_page(task, FRAME).page)

    def test_gacha_detail_close_clicks_ocr_x_center(self):
        boxes = [
            _Box("抽抽乐点数", 211, 1337, 138, 34),
            _Box("抽1次", 1944, 1289, 104, 48),
            _Box("抽10次", 2227, 1292, 128, 45),
            _Box("X", 1875, 410, 37, 40),
        ]
        task, clicks = self._task(boxes)

        self.assertTrue(BaseBD2Task._click_gacha_detail_close(task, FRAME, 1))
        self.assertEqual(1, len(clicks))
        self.assertAlmostEqual(1893 / 2560, clicks[0][0])
        self.assertAlmostEqual(430 / 1440, clicks[0][1])

    def test_gacha_detail_close_requires_page_signals_and_x(self):
        task, clicks = self._task(
            [
                _Box("抽抽乐点数", 211, 1337, 138, 34),
                _Box("X", 1875, 410, 37, 40),
            ]
        )

        self.assertFalse(BaseBD2Task._click_gacha_detail_close(task, FRAME, 1))
        self.assertEqual([], clicks)

    def test_gacha_detail_close_requires_in_region_x(self):
        task, clicks = self._task(
            [
                _Box("抽抽乐点数", 211, 1337, 138, 34),
                _Box("抽1次", 1944, 1289, 104, 48),
                _Box("抽10次", 2227, 1292, 128, 45),
                _Box("X", 100, 1300, 40, 40),
            ]
        )

        self.assertFalse(BaseBD2Task._click_gacha_detail_close(task, FRAME, 1))
        self.assertEqual([], clicks)

    def test_auto_return_uses_gacha_detail_close_then_home(self):
        task, clicks, _warnings = self._looping_task(
            [_recognition(), _recognition(page=PageId.HOME, home_label_seen=True)]
        )
        closed = []
        task._click_gacha_detail_close = lambda *_args, **_kwargs: closed.append(True) or True
        task._click_top_left_back = lambda *_args, **_kwargs: False

        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([True], closed)
        self.assertEqual([], clicks)

    def test_auto_return_clicks_house_hint_before_detail_actions(self):
        task, clicks, _warnings = self._looping_task(
            [
                _recognition(house_hint_center=(2020, 100)),
                _recognition(page=PageId.HOME, home_label_seen=True),
            ]
        )
        task._click_gacha_detail_close = lambda *_args, **_kwargs: self.fail("不应走详情关闭")
        task._click_top_left_back = lambda *_args, **_kwargs: self.fail("不应走左上返回")

        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual(1, len(clicks))
        self.assertAlmostEqual(0.9375, clicks[0][0])
        self.assertAlmostEqual(0.059, clicks[0][1])
        self.assertEqual("自动返回主页-房子", clicks[0][2]["name"])

    def test_auto_return_does_not_click_when_page_is_unknown(self):
        task, clicks, warnings = self._looping_task([_recognition(), _recognition()])
        task._click_gacha_detail_close = lambda *_args, **_kwargs: False
        task._click_top_left_back = lambda *_args, **_kwargs: False

        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)
        self.assertIn(
            "自动返回主页：当前非主页且未找到 H/房子、详情关闭或左上角返回按钮，无法继续。",
            warnings,
        )

    def test_auto_return_gives_up_after_step_limit(self):
        task, clicks, warnings = self._looping_task([_recognition(), _recognition()])
        task._click_gacha_detail_close = lambda *_args, **_kwargs: False
        task._click_top_left_back = lambda *_args, **_kwargs: True

        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)
        self.assertIn("自动返回主页：2 步内未回到主页。", warnings)

    def test_auto_return_reports_capture_failure(self):
        task, _clicks, warnings = self._looping_task([])

        def _boom():
            raise RuntimeError("capture down")

        task.capture_frame = _boom

        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertIn("自动返回主页：截图失败：capture down", warnings)


if __name__ == "__main__":
    unittest.main()
