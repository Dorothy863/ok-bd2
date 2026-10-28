"""P9：业务动作迁移与后置条件验证。"""

import unittest

from src.tasks.business_transition import (
    TransitionSpec,
    execute_transition,
)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BusinessTransitionTest(unittest.TestCase):
    def test_waits_for_expected_postcondition(self):
        clock = _Clock()
        state = {"value": "HOME"}

        def wait_step(seconds):
            clock.sleep(seconds)
            state["value"] = "HOME"

        result = execute_transition(
            TransitionSpec.create("返回主页", ["HOME"], timeout_seconds=1.0),
            observe=lambda: state["value"],
            action=lambda: state.update(value="LOADING"),
            sleep=wait_step,
            clock=clock,
        )

        self.assertTrue(result.ok)
        self.assertEqual("HOME", result.observed_state)
        self.assertEqual(("LOADING", "HOME"), result.observations)
        self.assertEqual(1, result.action_attempts)

    def test_precondition_mismatch_never_runs_action(self):
        clock = _Clock()
        called = []

        result = execute_transition(
            TransitionSpec.create(
                "打开菜单",
                ["MENU"],
                precondition_states=["HOME"],
            ),
            observe=lambda: "BATTLE",
            action=lambda: called.append(True),
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.ok)
        self.assertFalse(result.precondition_ok)
        self.assertEqual([], called)
        self.assertIn("前置状态不匹配", result.summary)

    def test_retries_action_until_expected_state(self):
        clock = _Clock()
        attempts = []
        state = {"value": "HOME"}

        def action():
            attempts.append(len(attempts) + 1)
            state["value"] = "WRONG" if len(attempts) == 1 else "MENU"
            return True

        result = execute_transition(
            TransitionSpec.create(
                "打开菜单",
                ["MENU"],
                timeout_seconds=0.0,
                interval_seconds=0.1,
                max_action_attempts=2,
            ),
            observe=lambda: state["value"],
            action=action,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertTrue(result.ok)
        self.assertEqual([1, 2], attempts)
        self.assertEqual(2, result.action_attempts)

    def test_action_false_is_not_reported_as_dispatched(self):
        clock = _Clock()

        result = execute_transition(
            TransitionSpec.create("点击入口", ["OPEN"]),
            observe=lambda: "HOME",
            action=lambda: False,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.ok)
        self.assertFalse(result.action_dispatched)
        self.assertIn("动作未派发", result.summary)

    def test_observation_exception_is_preserved(self):
        clock = _Clock()

        def observe():
            raise RuntimeError("ocr down")

        result = execute_transition(
            TransitionSpec.create("打开菜单", ["MENU"]),
            observe=observe,
            action=lambda: True,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.ok)
        self.assertIn("ocr down", result.error or "")
        self.assertIn("observe postcondition", result.error or "")

    def test_timeout_reports_expected_and_observed_states(self):
        clock = _Clock()

        result = execute_transition(
            TransitionSpec.create(
                "进入战斗",
                ["BATTLE"],
                timeout_seconds=0.0,
            ),
            observe=lambda: "LOBBY",
            action=lambda: True,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.ok)
        self.assertEqual("LOBBY", result.observed_state)
        self.assertEqual(
            "进入战斗: 后置状态不匹配; expected=BATTLE; observed=LOBBY; "
            "attempts=1",
            result.summary,
        )


if __name__ == "__main__":
    unittest.main()
