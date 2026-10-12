"""Bounded polling deadlines that always sample at least once."""

from __future__ import annotations

from collections.abc import Callable


class PollDeadline:
    """有界轮询截止时间，等价于 ``while monotonic() <= end_at``。

    Windows 上 ``time.monotonic()`` 的分辨率自 Python 3.14 起由 15.6ms 提升到
    ~100ns。旧写法在超时为 0 时不再进入循环体（旧版本靠粗粒度时钟恰好落在同一
    tick 才"侥幸"执行一次），于是 ``_wait_*`` 轮询在零等待配置下一次都不采样屏幕。
    这里把"零等待也要采样一次"显式化，其余情况与 ``monotonic() <= end_at`` 完全
    一致（迭代次数与时钟读取次数不变）。

    ``clock`` 由调用方传入自己模块的 ``monotonic``，让
    ``patch("<task module>.monotonic")`` 这类时间夹具继续生效。
    """

    __slots__ = ("_checked", "_clock", "_deadline", "_grace", "_started")

    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._started = clock()
        wait_seconds = max(0.0, float(seconds))
        self._deadline = self._started + wait_seconds
        # 零等待时首次判断不与起算时刻比较，保证至少采样一次。
        self._grace = wait_seconds <= 0.0
        self._checked = False

    def started_at(self) -> float:
        """截止起算时刻，供需要“已等待多久”的调用方复用同一次采样。"""
        return self._started

    def poll_expired(self) -> bool:
        """循环守卫用：零等待时首次返回 False，保证至少采样一次。"""
        now = self._clock()
        if self._grace and not self._checked:
            self._checked = True
            return False
        return now > self._deadline

    def expired(self, now: float | None = None) -> bool:
        """纯截止判断，等价于 ``monotonic() >= end_at``。

        ``now`` 用于复用调用方已采样的时间，避免重复读取时钟。
        """
        return (self._clock() if now is None else now) > self._deadline

    def remaining(self) -> float:
        """剩余秒数，已过期时为 0.0。"""
        return max(0.0, self._deadline - self._clock())
