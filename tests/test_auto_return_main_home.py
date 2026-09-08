"""回归：auto_return_main_home 对“H/房子”信号的多帧复核与安全停止。"""

import unittest

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task


class AutoReturnMainHomeTest(unittest.TestCase):
    def _task(self, home_results):
        task = object.__new__(BaseBD2Task)
        task.config = {"自动返回主页最大步数": 1}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        results = iter(home_results)
        task._home_scan = lambda _frame: next(results)
        clicks = []
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y))
        return task, clicks

    def test_transient_gacha_miss_on_home_does_not_click_house(self):
        # 首页“我的小屋/H”在，但抽抽乐第一帧漏识别 -> 复核帧确认是主页 -> 停止、不点
        task, clicks = self._task([(False, True), (True, True)])
        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)

    def test_house_clicked_only_after_stable_non_home_rechecks(self):
        task, clicks = self._task([(False, True), (False, True), (False, True)])
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))  # 步数上限结束
        self.assertEqual(1, len(clicks))

    def test_unstable_house_hint_stops_without_click(self):
        task, clicks = self._task([(False, True), (False, False)])
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)


if __name__ == "__main__":
    unittest.main()
