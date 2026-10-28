"""业务动作状态迁移：执行动作，并验证动作后的预期页面状态。

本模块只处理业务层契约，不执行截图、OCR 或点击。调用方负责提供：

- ``observe``：读取当前业务状态；
- ``action``：执行一个业务动作；
- ``sleep``：等待下一次采样。

核心约束：

1. 可选前置状态不满足时，不执行动作；
2. 动作返回 ``False`` 视为动作未派发；
3. 动作派发后必须观察到期望状态才算成功；
4. 动作次数和每次等待时间都有上界；
5. 结果保留观测轨迹，便于日志和失败诊断。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

from src.utils.polling import PollDeadline

StateT = TypeVar("StateT", bound=Hashable)


@dataclass(frozen=True)
class TransitionSpec(Generic[StateT]):
    """一个业务动作的状态迁移契约。"""

    name: str
    expected_states: frozenset[StateT]
    precondition_states: frozenset[StateT] | None = None
    timeout_seconds: float = 5.0
    interval_seconds: float = 0.25
    max_action_attempts: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("transition name must not be empty")
        if not self.expected_states:
            raise ValueError("expected_states must not be empty")
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.max_action_attempts < 1:
            raise ValueError("max_action_attempts must be >= 1")

    @classmethod
    def create(
        cls,
        name: str,
        expected_states: Collection[StateT],
        *,
        precondition_states: Collection[StateT] | None = None,
        timeout_seconds: float = 5.0,
        interval_seconds: float = 0.25,
        max_action_attempts: int = 1,
    ) -> "TransitionSpec[StateT]":
        return cls(
            name=name,
            expected_states=frozenset(expected_states),
            precondition_states=(
                None
                if precondition_states is None
                else frozenset(precondition_states)
            ),
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            max_action_attempts=max_action_attempts,
        )


@dataclass(frozen=True)
class TransitionResult(Generic[StateT]):
    """动作迁移结果，包含成功判定和完整观测证据。"""

    spec: TransitionSpec[StateT]
    precondition_state: StateT | None
    precondition_ok: bool
    action_attempts: int
    action_dispatched: bool
    observed_state: StateT | None
    observations: tuple[StateT | None, ...]
    elapsed_seconds: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.precondition_ok
            and self.action_dispatched
            and self.observed_state in self.spec.expected_states
            and self.error is None
        )

    @property
    def expected_description(self) -> str:
        return ", ".join(sorted(str(state) for state in self.spec.expected_states))

    @property
    def observed_description(self) -> str:
        return "-" if self.observed_state is None else str(self.observed_state)

    @property
    def summary(self) -> str:
        if self.ok:
            outcome = "通过"
        elif not self.precondition_ok:
            outcome = "前置状态不匹配"
        elif self.error:
            outcome = f"异常:{self.error}"
        elif not self.action_dispatched:
            outcome = "动作未派发"
        else:
            outcome = "后置状态不匹配"
        return (
            f"{self.spec.name}: {outcome}; "
            f"expected={self.expected_description}; "
            f"observed={self.observed_description}; "
            f"attempts={self.action_attempts}"
        )


def execute_transition(
    spec: TransitionSpec[StateT],
    *,
    observe: Callable[[], StateT | None],
    action: Callable[[], bool | None],
    sleep: Callable[[float], None],
    clock: Callable[[], float] = monotonic,
) -> TransitionResult[StateT]:
    """执行动作并等待预期状态；动作可按契约有界重试。"""

    started_at = clock()
    observations: list[StateT | None] = []
    precondition_state: StateT | None = None
    precondition_ok = True
    action_attempts = 0
    action_dispatched = False
    observed_state: StateT | None = None
    error: str | None = None

    if spec.precondition_states is not None:
        try:
            precondition_state = observe()
            observations.append(precondition_state)
            precondition_ok = precondition_state in spec.precondition_states
            if not precondition_ok:
                return TransitionResult(
                    spec=spec,
                    precondition_state=precondition_state,
                    precondition_ok=False,
                    action_attempts=0,
                    action_dispatched=False,
                    observed_state=precondition_state,
                    observations=tuple(observations),
                    elapsed_seconds=max(0.0, clock() - started_at),
                )
        except Exception as exc:  # noqa: BLE001 - 结果必须携带诊断
            return TransitionResult(
                spec=spec,
                precondition_state=None,
                precondition_ok=False,
                action_attempts=0,
                action_dispatched=False,
                observed_state=None,
                observations=(),
                elapsed_seconds=max(0.0, clock() - started_at),
                error=f"observe precondition: {type(exc).__name__}: {exc}",
            )

    for attempt in range(1, spec.max_action_attempts + 1):
        action_attempts = attempt
        try:
            dispatched = action()
        except Exception as exc:  # noqa: BLE001 - 结果必须携带诊断
            error = f"action: {type(exc).__name__}: {exc}"
            break

        if dispatched is False:
            if attempt < spec.max_action_attempts:
                if spec.interval_seconds > 0:
                    sleep(spec.interval_seconds)
                continue
            break

        action_dispatched = True
        deadline = PollDeadline(spec.timeout_seconds, clock)
        while not deadline.poll_expired():
            try:
                observed_state = observe()
            except Exception as exc:  # noqa: BLE001 - 保留最后一次状态观测
                error = f"observe postcondition: {type(exc).__name__}: {exc}"
                break
            observations.append(observed_state)
            if observed_state in spec.expected_states:
                return TransitionResult(
                    spec=spec,
                    precondition_state=precondition_state,
                    precondition_ok=True,
                    action_attempts=attempt,
                    action_dispatched=True,
                    observed_state=observed_state,
                    observations=tuple(observations),
                    elapsed_seconds=max(0.0, clock() - started_at),
                )
            if spec.interval_seconds > 0:
                sleep(spec.interval_seconds)

        if error is not None:
            break
        if attempt < spec.max_action_attempts and spec.interval_seconds > 0:
            sleep(spec.interval_seconds)

    return TransitionResult(
        spec=spec,
        precondition_state=precondition_state,
        precondition_ok=True,
        action_attempts=action_attempts,
        action_dispatched=action_dispatched,
        observed_state=observed_state,
        observations=tuple(observations),
        elapsed_seconds=max(0.0, clock() - started_at),
        error=error,
    )
