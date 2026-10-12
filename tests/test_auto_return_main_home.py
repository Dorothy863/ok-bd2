"""回归：自动返回主页对抽卡角色详情层的关闭处理。"""

import unittest

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task


class _Box:
    def __init__(self, name, x, y, width, height):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class AutoReturnMainHomeTest(unittest.TestCase):
    def _task(self, boxes):
        task = object.__new__(BaseBD2Task)
        task.ocr = lambda **_kwargs: boxes
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        return task, clicks

    def test_gacha_detail_close_clicks_ocr_x_center(self):
        boxes = [
            _Box("抽抽乐点数", 211, 1337, 138, 34),
            _Box("抽1次", 1944, 1289, 104, 48),
            _Box("抽10次", 2227, 1292, 128, 45),
            _Box("X", 1875, 410, 37, 40),
        ]
        task, clicks = self._task(boxes)
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)

        self.assertTrue(BaseBD2Task._click_gacha_detail_close(task, frame, 1))
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
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)

        self.assertFalse(BaseBD2Task._click_gacha_detail_close(task, frame, 1))
        self.assertEqual([], clicks)

    def test_auto_return_uses_gacha_detail_close_then_home(self):
        task = object.__new__(BaseBD2Task)
        task.config = {"自动返回主页最大步数": 2, "返回主页步间等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        scans = iter(((False, False), (True, False)))
        task._home_scan = lambda _frame: next(scans)
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        closed = []
        task._click_gacha_detail_close = lambda *_args, **_kwargs: closed.append(True) or True
        task._click_top_left_back = lambda *_args, **_kwargs: False

        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([True], closed)


if __name__ == "__main__":
    unittest.main()
