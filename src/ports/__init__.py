"""任务端口协议：只描述「被调用面」，不代表接口稳定承诺。

P3「端口抽象」之前，工具层与公共视觉基座都按鸭子类型使用任务对象：
``src/utils/vision_runtime.py`` 的 ``Vision(task)`` 直接调用 ``task.capture_frame()`` /
``task.ocr(...)`` / ``task.operate_click(...)`` / ``task.drag_client(...)`` /
``task.config`` / ``task.info_set(...)`` / ``task.sleep(...)``，
``src/tasks/task_vision_mixin.py`` 把同一批方法当隐式接口用。

端口放在中性层 ``src/ports``（既不在 ``src/tasks`` 也不在 ``src/utils``）：业务层
（``src/tasks``）与适配器 / 工具层（``src/utils``）都直接引用这里的**同一定义**，
``Vision(task)`` 与 ``TaskVisionMixin`` 因此不需要镜像协议。

架构约束（守卫 R7）：本包是纯抽象层，不得 import 包外任何 ``src.*`` 模块；运行时只依赖
stdlib（``typing`` / ``collections.abc``），第三方类型库（``numpy``）只在
``if TYPE_CHECKING:`` 里用于类型标注。

约定：

- **协议只描述被调用面，不代表接口稳定承诺**：成员由真实调用点决定，调用点增删成员时
  协议必须同步；``tests/test_ports.py`` 用反射断言协议成员确实存在于真实任务类上，
  并用 AST 断言消费面不超过契约面，避免协议漂移成「写了但没人用」的空接口。
- 协议是结构类型：``BaseBD2Task`` 及其子类不需要继承端口即可满足，运行时行为不变。
- 本模块不注册实现、不做转发：抓帧实现在 ``src/compat/*``（WGC / BitBlt），OCR 与输入
  实现来自 ``ok`` 框架（onnxocr / executor）与 ``src/interaction/BD2Interaction.py``。
- 通过 ``getattr(self.task, "vision_threshold_key", ...)`` 这类带默认值读取的可选配置键
  （``vision_runtime.py`` 的 ``vision_threshold_key`` / ``ocr_threshold_key``）刻意不进契约：
  它们是可选能力，不是被调用面。

未纳入的成员（都不发明调用面）：

- ``main_viewport`` / ``capture_method_name``：由 ``src/tasks/device_ops_mixin.py:26`` /
  ``:30`` 真实提供（P4 从 ``BaseBD2Task`` 拆出），但被标注的两个模块没有调用点，已知调用点
  是 ``device_ops_mixin.capture_frame``、``BD2ProbeTask``、``BD2InputTestTask``。
- 计划里写的 ``TemplatePort.match`` 与 ``ProgressPort.load/save``：模板匹配是
  ``src/utils/task_vision.py`` 的纯函数（frame + spec + config），任务对象上没有
  ``match`` 调用点；任务对象真实提供的进度 / 等待方法只有 ``sleep``。

下面各协议的 ``file:line`` 是 P3 收口提交（``refactor/ports``）时的调用点位置。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np


@runtime_checkable
class OcrBox(Protocol):
    """OCR 结果框的只读面；真实实现是 ``ok`` 框架的 ``Box``。

    消费点：``src/utils/vision_runtime.py::Vision.ocr_boxes`` 与
    ``src/tasks/task_vision_mixin.py::TaskVisionMixin._ocr_text``。
    证据代码用 ``getattr(box, "score", ...)`` / ``getattr(box, "box", ...)`` 做兜底读取，
    这两个可选字段不属于契约。
    """

    name: str
    x: float
    y: float
    width: float
    height: float
    confidence: float


@runtime_checkable
class CapturePort(Protocol):
    """抓帧端口。

    调用点：``src/utils/vision_runtime.py:332``（``Vision.capture``）与
    ``src/tasks/task_vision_mixin.py`` 各 ``_wait_*`` 循环里的 ``self.capture_frame()``
    （:155 / :183 / :217 / :264 / :291 / :314 / :345 / :369）。
    实现：``src/tasks/device_ops_mixin.py:34`` → ``next_frame()`` → ``src/compat/*``。
    """

    def capture_frame(self) -> np.ndarray:
        """返回一帧客户端画面（BGR ``ndarray``）。"""
        ...


@runtime_checkable
class OcrPort(Protocol):
    """OCR 端口。

    调用点：``vision_runtime.py:1229``（``Vision.ocr_boxes`` 的
    ``self.task.ocr(frame=..., threshold=..., target_height=..., log=False, name=...)``）
    与 ``task_vision_mixin.py:495``（``TaskVisionMixin._ocr_text`` 的 ``self.ocr(...)``）。
    实现：``ok`` 框架 ``BaseTask.ocr``（onnxocr）。

    这里只声明这两个调用点用到的关键字参数；``ok`` 的 ``ocr`` 还接受
    ``screenshot`` / ``lib`` 等参数，不在本端口契约内。
    """

    def ocr(
        self,
        *,
        frame: np.ndarray,
        threshold: float = 0,
        target_height: int = 0,
        log: bool = False,
        name: str | None = None,
    ) -> list[OcrBox]:
        """对给定帧做 OCR，返回文字框列表（按 y 排序由 ``ok`` 负责）。"""
        ...


@runtime_checkable
class InputPort(Protocol):
    """输入端口（点击 / 拖动 / 滚轮）。

    调用点：

    - ``operate_click``：``vision_runtime.py:351``（``click_reference``）、
      ``:361``（``click_client``）、``task_vision_mixin.py:140``（``_click_reference``）；
    - ``drag_client``：``vision_runtime.py:376``（``drag_reference``）；
    - ``scroll_client``：``src/tasks/device_ops_mixin.py:273`` 定义，当前不被任何模块调用
      （全仓库暂无调用点），按 P4「最小操作：capture / ocr / click / scroll / drag /
      show info」保留在端口里，P4 路由滚轮时不必再改契约。

    实现：``src/tasks/device_ops_mixin.py`` 的 ``operate_click`` / ``drag_client`` /
    ``scroll_client``（P4 从 ``BaseBD2Task`` 拆出）→ ``ok`` executor 与
    ``src/interaction/BD2Interaction.py``。
    """

    def operate_click(self, x: float, y: float, *, after_sleep: float = 0.0) -> Any:
        """按相对坐标点击（x / y 为 0-1 相对值），``after_sleep`` 为点击后等待秒数。"""
        ...

    def drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        """在客户端坐标上从 ``start`` 拖到 ``end``。"""
        ...

    def scroll_client(
        self,
        relative_point: tuple[float, float],
        scroll_amount: int,
        *,
        count: int = 1,
        interval: float = 0.1,
        after_sleep: float = 0.0,
    ) -> None:
        """在相对坐标处发送 ``count`` 次滚轮事件（``scroll_amount`` 为单次滚动量）。"""
        ...


@runtime_checkable
class ConfigPort(Protocol):
    """配置端口：两个模块都只用 ``.get(key, default)``。

    调用点：``vision_runtime.py:338``（``threshold_for``）/ ``:1219``（``ocr_boxes``）、
    ``task_vision_mixin.py:153`` / ``:363`` / ``:461`` / ``:497`` / ``:513``；
    ``config`` 还会作为 mapping 直接传给 ``task_vision.match_template`` /
    ``passes_match``（``vision_runtime.py:737`` / ``:871``）。
    实现：``ok`` 框架 ``Config(dict)``，由 ``BaseTask.load_config`` 注入。
    """

    config: Mapping[str, Any]


@runtime_checkable
class StatusPort(Protocol):
    """状态与日志端口。

    调用点：``info_set``（``vision_runtime.py:1360``、``task_vision_mixin.py`` 各循环，
    如 ``:103`` / ``:157`` / ``:376``）、``log_info``（``task_vision_mixin.py:195`` /
    ``:231`` / ``:278`` / ``:325`` / ``:405`` / ``:420``）、``log_warning``
    （``task_vision_mixin.py:473`` / ``:481``，以 ``notify=True`` 调用）。
    实现：``ok`` 框架 ``BaseTask.info_set`` / ``log_info`` / ``log_warning``。
    """

    def info_set(self, key: str, value: Any) -> None:
        """写入一条任务状态（UI 侧通过 ``task_info_snapshot`` 读取）。"""
        ...

    def log_info(self, message: str, *, notify: bool = False) -> None:
        """记录一条信息日志。"""
        ...

    def log_warning(self, message: str, *, notify: bool = False) -> None:
        """记录一条告警日志。"""
        ...


@runtime_checkable
class ProgressPort(Protocol):
    """进度 / 等待端口：当前只有 ``sleep``。

    调用点：``vision_runtime.py:1105``（``wait_template``）/ ``:1142``
    （``click_stable_template``）/ ``:1154``（作为 ``sleep=`` 回调传给
    ``stabilize_template_match``）/ ``:1314``（``wait_ocr``），
    ``task_vision_mixin.py`` 各 ``_wait_*`` 循环（如 ``:170`` / ``:297`` / ``:390``）。
    任务对象上没有其它真实的进度方法，所以不发明 ``load`` / ``save`` 之类的成员。
    """

    def sleep(self, timeout: float) -> Any:
        """等待 ``timeout`` 秒（返回 ``ok`` 框架的返回值，调用方不消费）。"""
        ...


@runtime_checkable
class TaskSurface(CapturePort, OcrPort, InputPort, ConfigPort, StatusPort, ProgressPort, Protocol):
    """组合端口：``Vision(task)`` 这类「拿到任务对象就用」位置的声明类型。

    只描述被调用面，不代表接口稳定承诺：成员由调用点决定，不承诺接口稳定。
    ``BaseBD2Task`` 与真实任务子类在结构上满足它，见 ``tests/test_ports.py``；
    满足协议不要求继承，运行时行为不变。
    """