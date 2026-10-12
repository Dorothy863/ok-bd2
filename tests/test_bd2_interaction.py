import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import win32con
from ok.device.intercation import PostMessageInteraction

from src.interaction.BD2Interaction import (
    CLICK_MODE_BACKGROUND,
    BD2Interaction,
)
from src.tasks.BaseBD2Task import BaseBD2Task


def make_interaction():
    """Build a BD2Interaction without running its device ``__init__``."""
    interaction = object.__new__(BD2Interaction)
    interaction._input_lock = threading.RLock()
    interaction._operating = False
    interaction._click_mode_provider = None
    interaction.cursor_position = (1, 2)
    calls = []
    interaction._restore_cursor = lambda: calls.append("restore_cursor")
    interaction.block_input = lambda: calls.append("block_input")
    interaction.unblock_input = lambda: calls.append("unblock_input")
    return interaction, calls


class BD2InteractionOperateTest(unittest.TestCase):
    def test_operate_reraises_callback_exception_and_cleans_up(self):
        interaction, calls = make_interaction()
        error = ValueError("boom")

        def failing_callback():
            raise error

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ):
            with self.assertRaises(ValueError) as caught:
                interaction.operate(failing_callback, block=True)

        self.assertIs(error, caught.exception)
        self.assertFalse(interaction._operating)
        self.assertIn("block_input", calls)
        self.assertIn("restore_cursor", calls)
        self.assertIn("unblock_input", calls)

    def test_operate_returns_callback_result_and_cleans_up(self):
        interaction, calls = make_interaction()

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ) as get_cursor_pos:
            result = interaction.operate(lambda: 42, block=True)

        self.assertEqual(42, result)
        self.assertFalse(interaction._operating)
        get_cursor_pos.assert_called_once_with()
        self.assertIn("block_input", calls)
        self.assertIn("restore_cursor", calls)
        self.assertIn("unblock_input", calls)

    def test_nested_operate_keeps_outer_layer_state_isolated(self):
        interaction, calls = make_interaction()
        states_after_inner = []

        def outer_callback():
            inner_result = interaction.operate(lambda: "inner", block=True)
            states_after_inner.append((inner_result, interaction._operating))
            raise RuntimeError("outer boom")

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ) as get_cursor_pos:
            with self.assertRaises(RuntimeError):
                interaction.operate(outer_callback)

        self.assertEqual([("inner", True)], states_after_inner)
        self.assertFalse(interaction._operating)
        get_cursor_pos.assert_called_once_with()
        self.assertEqual(1, calls.count("restore_cursor"))


class BD2InteractionForegroundInputTest(unittest.TestCase):
    """P8：前景输入（拖拽 / 绝对光标定位）实现收口在适配器里。"""

    def test_drag_foreground_positions_cursor_presses_steps_and_releases(self):
        interaction, _calls = make_interaction()
        timeline = []

        with (
            patch(
                "src.interaction.BD2Interaction.win32api.SetCursorPos",
                side_effect=lambda point: timeline.append(("cursor", point)),
            ),
            patch(
                "src.interaction.BD2Interaction.win32api.mouse_event",
                side_effect=lambda *args: timeline.append(("mouse", args[0])),
            ),
            patch(
                "src.interaction.BD2Interaction.time.sleep",
                side_effect=lambda seconds: timeline.append(("sleep", seconds)),
            ),
        ):
            interaction.drag_foreground((100, 200), (160, 260), duration=0.09)

        steps = [("cursor", (100 + 10 * index, 200 + 10 * index)) for index in range(1, 7)]
        expected = [("cursor", (100, 200)), ("sleep", 0.03)]
        expected.append(("mouse", win32con.MOUSEEVENTF_LEFTDOWN))
        for entry in steps:
            expected.extend([entry, ("sleep", 0.015)])
        expected.append(("mouse", win32con.MOUSEEVENTF_LEFTUP))
        self.assertEqual(expected, timeline)

    def test_drag_foreground_releases_button_when_cursor_move_fails(self):
        interaction, _calls = make_interaction()
        events = []
        cursor_calls = []

        def set_cursor_pos(point):
            cursor_calls.append(point)
            events.append(("cursor", point))
            if len(cursor_calls) == 3:
                raise RuntimeError("cursor move failed")

        with (
            patch(
                "src.interaction.BD2Interaction.win32api.SetCursorPos",
                side_effect=set_cursor_pos,
            ),
            patch(
                "src.interaction.BD2Interaction.win32api.mouse_event",
                side_effect=lambda *args: events.append(("mouse", args[0])),
            ),
            patch("src.interaction.BD2Interaction.time.sleep"),
        ):
            with self.assertRaises(RuntimeError):
                interaction.drag_foreground((0, 0), (60, 0), duration=0.09)

        self.assertEqual(("cursor", (0, 0)), events[0])
        self.assertEqual(("mouse", win32con.MOUSEEVENTF_LEFTDOWN), events[1])
        self.assertEqual(("mouse", win32con.MOUSEEVENTF_LEFTUP), events[-1])

    def test_set_foreground_cursor_positions_the_real_cursor(self):
        interaction, _calls = make_interaction()

        with patch(
            "src.interaction.BD2Interaction.win32api.SetCursorPos",
        ) as set_cursor_pos:
            interaction.set_foreground_cursor((1234, 567))

        set_cursor_pos.assert_called_once_with((1234, 567))

    def test_foreground_input_methods_take_the_input_lock(self):
        interaction, _calls = make_interaction()
        started = threading.Event()

        def worker():
            started.set()
            interaction.set_foreground_cursor((1234, 567))

        with patch(
            "src.interaction.BD2Interaction.win32api.SetCursorPos",
        ) as set_cursor_pos:
            with interaction._input_lock:
                thread = threading.Thread(target=worker)
                thread.start()
                self.assertTrue(started.wait(1.0))
                time.sleep(0.05)
                set_cursor_pos.assert_not_called()
            thread.join(1.0)

        self.assertFalse(thread.is_alive())
        set_cursor_pos.assert_called_once_with((1234, 567))


class BD2InteractionClickModeTest(unittest.TestCase):
    def test_background_mode_routes_click_through_post_message_without_cursor_move(self):
        interaction, _calls = make_interaction()
        interaction.set_click_mode_provider(lambda: CLICK_MODE_BACKGROUND)

        with (
            patch.object(PostMessageInteraction, "click", autospec=True) as post_click,
            patch("src.interaction.BD2Interaction.SetCursorPos") as set_cursor_pos,
        ):
            interaction.click(320, 540, move_back=True, down_time=0.02)

        post_click.assert_called_once_with(
            interaction,
            320,
            540,
            move_back=False,
            name=None,
            down_time=0.02,
            move=True,
            key="left",
        )
        set_cursor_pos.assert_not_called()

    def test_operate_click_does_not_block_input_in_background_mode(self):
        task = object.__new__(BaseBD2Task)
        task._executor = SimpleNamespace(
            interaction=SimpleNamespace(background_click_enabled=lambda: True),
        )
        task._check_action_interval = lambda *_args: True
        task.click = lambda *_args, **_kwargs: True
        task.info_set = lambda *_args: None
        task.sleep = lambda *_args: None
        operate_options = []

        def operate(action, block=True, restore_cursor=True):
            operate_options.append((block, restore_cursor))
            return action()

        task.operate = operate
        task.operate_click(0.25, 0.75)

        self.assertEqual([(False, False)], operate_options)


if __name__ == "__main__":
    unittest.main()
