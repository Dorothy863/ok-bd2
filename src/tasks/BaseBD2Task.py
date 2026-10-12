"""BaseBD2Task：状态输出、生命周期与多继承组合根。

工具/设备操作见 :mod:`src.tasks.device_ops_mixin`，卡带/主页业务见
:mod:`src.tasks.cartridge_home_mixin`；本模块只保留 ``__init__``、``info_*``
状态输出、任务生命周期与 ``task_info_snapshot``。既有的模块级导入点
（``src/ui``、``src/tasks/PVPTask.py`` 与测试）通过下面的兼容再导出保持可用。
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import numpy as np
from ok import BaseTask, Logger, og

from src.scene.BD2Scene import BD2Scene
from src.scene.ScreenPosition import ScreenPosition
from src.tasks.cartridge_home_mixin import (
    BEIJING_TIMEZONE,
    CARTRIDGE_RECENT_ENTRY_POINT,
    PROJECT_ROOT,
    RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS,
    RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
    RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
    RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
    RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
    RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
    TEMPLATE_DIR,
    CartridgeHomeMixin,
    RecentPvpCartridgeMatch,
)
from src.tasks.device_ops_mixin import (
    GREEN_MASK_TOLERANCE,
    PROBE_OUTPUT_DIR,
    DeviceOpsMixin,
)
from src.tasks.task_notifications import log_task_completion
from src.utils.image_utils import green_mask_from_template

logger = Logger.get_logger(__name__)

# 拆分前可导入的模块级名字继续保留（``__all__`` 同时声明再导出意图）。
__all__ = [
    "BEIJING_TIMEZONE",
    "CARTRIDGE_RECENT_ENTRY_POINT",
    "GREEN_MASK_TOLERANCE",
    "PROBE_OUTPUT_DIR",
    "PROJECT_ROOT",
    "RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS",
    "RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS",
    "RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD",
    "RECENT_PVP_CARTRIDGE_TEMPLATE_FILE",
    "RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD",
    "RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD",
    "TEMPLATE_DIR",
    "BaseBD2Task",
    "RecentPvpCartridgeMatch",
    "green_mask_from_template",
    "task_info_snapshot",
]

# Shared lock for task instances whose ``__init__`` never ran (object.__new__);
# see ``BaseBD2Task._task_info_lock``.
_INFO_FALLBACK_LOCK = threading.RLock()


class BaseBD2Task(DeviceOpsMixin, CartridgeHomeMixin, BaseTask):
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visible = False
        self.scene: BD2Scene | None = None
        self.default_box = ScreenPosition(self)
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()
        # ``task.info`` is a plain dict shared between the TaskExecutor worker
        # thread (info_set/info_clear/log_* and DailyBatchTask child resets)
        # and UI/diagnostics readers.  All mutations go through the mutators
        # below, which serialize on this lock so readers can take a consistent
        # copy via ``info_snapshot``/``task_info_snapshot``.  ``run_task_by_class``
        # rebinds ``task.info`` as a raw attribute, so every locked section must
        # re-read the attribute instead of caching the dict.
        self._info_lock = threading.RLock()
        self._last_home_announcement_clear_at = 0.0
        self._recent_pvp_cartridge_template_cache: (
            tuple[np.ndarray, np.ndarray] | None
        ) = None
        self.default_config.update(
            {
                "识别成功后等待秒数": 1.0,
                "执行前自动返回主页": True,
                # 自动返回主页按钮位置（右上角“小房子”，格鲁菲餐厅实测 2560x1440 的约 (2400,85)）。
                "返回主页按钮 X 百分比": 93.75,
                "返回主页按钮 Y 百分比": 5.9,
                "主页确认自动返回主页次数": 2,
                "自动返回主页最大步数": 6,
                "返回主页步间等待秒数": 2.5,
                "左上角返回模板阈值": 0.55,
                "左上角返回ROI右百分比": 13,  # 2560x1440 下约 x<333
                "左上角返回ROI底百分比": 11,  # 2560x1440 下约 y<158

            }
        )
        self.config_description.update(
            {
                "识别成功后等待秒数": "识别成功后，执行下一步点击或切换操作前等待多久。",
                "执行前自动返回主页": "任务开始前是否先尝试自动返回主页；关闭后直接执行任务。",
                "返回主页按钮 X 百分比": "自动返回主页按钮的横向百分比。",
                "返回主页按钮 Y 百分比": "自动返回主页按钮的纵向百分比。",
                "主页确认自动返回主页次数": "主页确认超时后，自动点击返回主页按钮的最大次数。",
            }
        )

    def log_completion(self, message: str) -> None:
        """Publish a completion popup unless this task is running as a batch child."""

        log_task_completion(self, message)

    def _task_info_lock(self) -> threading.RLock:
        """The per-instance info lock, or a shared fallback for odd instances.

        Tests routinely build tasks via ``object.__new__`` without running
        ``__init__``; those instances have no ``_info_lock`` yet still go through
        these overrides, so they fall back to one process-wide lock instead of
        raising ``AttributeError``.
        """
        lock = self.__dict__.get("_info_lock")
        return _INFO_FALLBACK_LOCK if lock is None else lock

    def info_clear(self) -> None:
        with self._task_info_lock():
            super().info_clear()

    def info_incr(self, key, inc=1):
        with self._task_info_lock():
            return super().info_incr(key, inc)

    def info_add_to_list(self, key, item):
        with self._task_info_lock():
            return super().info_add_to_list(key, item)

    def info_set(self, key, value):
        with self._task_info_lock():
            return super().info_set(key, value)

    def info_add(self, key, count=1):
        with self._task_info_lock():
            return super().info_add(key, count)

    def info_snapshot(self) -> dict:
        """A consistent point-in-time copy of ``self.info``.

        The copy is taken under the same lock as every mutator, so it can never
        observe a half-applied update or hit "dictionary changed size during
        iteration".  ``self.info`` is re-read inside the lock because
        ``run_task_by_class`` rebinds the attribute as a whole.
        """
        with self._task_info_lock():
            return dict(getattr(self, "info", None) or {})

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor | None:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    @staticmethod
    def submit_periodic_task(delay: float, task: Callable, *args, **kwargs):
        if og.my_app is None:
            return None
        return og.my_app.submit_periodic_task(delay, task, *args, **kwargs)

    def run_with_interval(
        self,
        func: Callable,
        interval: float,
        *args,
        action_name=None,
        **kwargs,
    ) -> Any:
        action_name = action_name or getattr(func, "__qualname__", repr(func))
        if not self._check_action_interval(action_name, interval):
            return False
        return func(*args, **kwargs)

    def mark_logged_in(self) -> None:
        if self.scene is not None:
            self.scene.set_logged_in(True)

    def is_main(self) -> bool:
        return bool(self.scene and self.scene.logged_in())

    def wait_main(self, time_out: float = 30, raise_if_not_found: bool = False):
        return self.wait_until(
            self.is_main,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
        )


def task_info_snapshot(task) -> dict:
    """Best-effort thread-safe copy of ``task.info`` for any task object.

    ``BaseBD2Task`` (and subclasses) expose a locked ``info_snapshot``; tasks
    outside that hierarchy — ok-framework tasks such as ``BD2TriggerTask`` or
    plain stubs — only get an unlocked ``dict`` copy.  For those, a concurrent
    mutation can still surface as ``RuntimeError`` mid-copy, which is swallowed
    into an empty snapshot so UI/diagnostics readers stay alive.
    Callers that must know about snapshot failures (diagnostics) perform their
    own strict copy instead of relying on this fallback.
    """
    snapshot = getattr(task, "info_snapshot", None)
    if callable(snapshot):
        return snapshot()
    try:
        return dict(getattr(task, "info", None) or {})
    except RuntimeError:
        return {}
