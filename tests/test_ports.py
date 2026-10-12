"""端口契约测试（P3）：协议必须对得上真实任务类，且不能是空协议。

端口只有一份定义，位于中性层 ``src/ports``（业务层与工具层都引用它），所以这里同时锁住：

- 结构校验：``BaseBD2Task`` 与真实任务子类通过 ``isinstance``（``runtime_checkable``）
  满足各端口，覆盖组合面 ``TaskSurface``。
- 防漂移：协议成员在真实类上必须存在，且**消费面不超过契约面**（``Vision`` 只读
  ``TaskSurface`` 成员；``TaskVisionMixin`` 通过 ``self`` 访问的宿主成员都在
  ``HomeReturnHost`` 里）。
- 抽象层纯度：``src/ports`` 运行时只依赖 stdlib（守卫 R7 负责「不得 import 包外 src.*」）。
- 反向验证：普通对象、只实现单个端口的桩都不满足 ``TaskSurface``，证明协议不是空壳。
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path
from typing import get_type_hints

from ok import Box

from src import ports
from src.ports import (
    CapturePort,
    ConfigPort,
    InputPort,
    OcrBox,
    OcrPort,
    ProgressPort,
    StatusPort,
    TaskSurface,
)
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.SquareGoddessTask import SquareGoddessTask
from src.tasks.task_vision_mixin import HomeReturnHost, TaskVisionMixin
from src.utils.vision_runtime import Vision

ROOT = Path(__file__).resolve().parents[1]
PORTS_PACKAGE_INIT = ROOT / "src" / "ports" / "__init__.py"
VISION_RUNTIME = ROOT / "src" / "utils" / "vision_runtime.py"
TASK_VISION_MIXIN = ROOT / "src" / "tasks" / "task_vision_mixin.py"

# 单一端口（``TaskSurface`` 是它们的组合面；``OcrBox`` 是 ``ocr`` 的返回值面）。
SINGLE_PORTS = (CapturePort, OcrPort, InputPort, ConfigPort, StatusPort, ProgressPort)
# 任务对象应当结构满足的全部端口。
TASK_PORTS = (*SINGLE_PORTS, TaskSurface)
# 必须能通过 ``isinstance`` 结构校验的协议（含宿主业务面与 OCR 结果框）。
ALL_PROTOCOLS = (*TASK_PORTS, HomeReturnHost, OcrBox)


def _members(protocol: type) -> set[str]:
    """协议声明的成员名（含继承）。"""

    attributes = getattr(protocol, "__protocol_attrs__", None)
    if attributes is None:  # Python 3.12 没有这个属性，退回 typing 内部实现
        from typing import _get_protocol_attrs

        attributes = _get_protocol_attrs(protocol)
    return set(attributes)


def _task_instance(task_class: type):
    """构造满足端口协作者的最小实例，只为结构校验。

    沿用仓库既有写法（``object.__new__`` + 注入协作者），不启动 ok 执行器：
    ``config`` 是 ``load_config`` 之后才存在的实例属性。
    """

    task = object.__new__(task_class)
    task.config = {}
    return task


def _attribute_reads(path: Path, expression: str) -> set[str]:
    """收集 ``<expression>.<name>`` 形式的属性读取名（AST，含函数体）。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) == expression
    }


def _is_type_checking_test(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_import_targets(path: Path) -> set[str]:
    """收集模块运行时真正会执行的 import 目标（跳过 ``if TYPE_CHECKING:`` 分支）。"""

    targets: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking_test(child.test):
                continue
            if isinstance(child, ast.Import):
                targets.update(alias.name for alias in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module:
                targets.add(child.module)
            visit(child)

    visit(ast.parse(path.read_text(encoding="utf-8")))
    return targets


class _CaptureOnlyStub:
    """只实现抓帧的桩：满足 ``CapturePort``，但不是 ``TaskSurface``。"""

    def capture_frame(self):
        return None


class _FullTaskSurfaceStub:
    """把六个端口全部实现的桩，用于证明 ``TaskSurface`` 不是空协议。"""

    def __init__(self) -> None:
        self.config: dict[str, object] = {}

    def capture_frame(self):
        return None

    def ocr(self, **_kwargs):
        return []

    def operate_click(self, x, y, *, after_sleep=0.0):
        return None

    def drag_client(self, start, end, *, duration=0.7, after_sleep=0.0):
        return None

    def scroll_client(
        self, relative_point, scroll_amount, *, count=1, interval=0.1, after_sleep=0.0
    ):
        return None

    def info_set(self, key, value):
        return None

    def log_info(self, message, *, notify=False):
        return None

    def log_warning(self, message, *, notify=False):
        return None

    def sleep(self, timeout):
        return None


class PortsContractTest(unittest.TestCase):
    def test_every_protocol_is_runtime_checkable_and_rejects_plain_object(self) -> None:
        """``runtime_checkable`` 生效：普通对象不被误判为任何端口。"""

        for protocol in ALL_PROTOCOLS:
            with self.subTest(protocol=protocol.__name__):
                self.assertNotIsInstance(object(), protocol)

    def test_port_members_exist_on_real_task_classes(self) -> None:
        """协议成员必须是真实任务类上的属性（hasattr 反射，防止协议漂移）。"""

        for task_class in (BaseBD2Task, SquareGoddessTask):
            task = _task_instance(task_class)
            for port in TASK_PORTS:
                for member in sorted(_members(port)):
                    with self.subTest(task=task_class.__name__, port=port.__name__, member=member):
                        self.assertTrue(
                            hasattr(task, member),
                            f"{port.__name__}.{member} 在 {task_class.__name__} 上不存在",
                        )

    def test_port_callables_accept_declared_keywords(self) -> None:
        """协议声明的关键字参数必须能被真实实现接收（inspect 反射，防止参数漂移）。"""

        task = _task_instance(BaseBD2Task)
        for port in TASK_PORTS:
            for member in sorted(_members(port)):
                declared = getattr(port, member, None)
                if not callable(declared):
                    continue
                real = getattr(task, member, None)
                with self.subTest(port=port.__name__, member=member):
                    self.assertTrue(callable(real), f"{member} 在真实任务上不可调用")
                    real_parameters = inspect.signature(real).parameters
                    for name in inspect.signature(declared).parameters:
                        if name == "self":
                            continue
                        self.assertIn(name, real_parameters, f"{member} 不接受参数 {name}")

    def test_real_tasks_satisfy_every_port(self) -> None:
        """``BaseBD2Task`` 与真实任务子类在结构上满足每个端口（含组合面）。"""

        for task_class in (BaseBD2Task, SquareGoddessTask):
            task = _task_instance(task_class)
            for port in TASK_PORTS:
                with self.subTest(task=task_class.__name__, port=port.__name__):
                    self.assertIsInstance(task, port)

    def test_task_surface_is_not_vacuous(self) -> None:
        """组合面不空：只实现抓帧的桩不算 ``TaskSurface``，六个端口齐全才算。"""

        capture_only = _CaptureOnlyStub()
        self.assertIsInstance(capture_only, CapturePort)
        self.assertNotIsInstance(capture_only, TaskSurface)
        self.assertIsInstance(_FullTaskSurfaceStub(), TaskSurface)

    def test_ports_document_that_they_are_call_surface_only(self) -> None:
        """契约说明必须写明「只描述被调用面，不代表接口稳定承诺」。"""

        self.assertIn("不代表接口稳定承诺", ports.__doc__ or "")
        for protocol in (TaskSurface, HomeReturnHost):
            with self.subTest(protocol=protocol.__name__):
                self.assertIn("不代表接口稳定承诺", protocol.__doc__ or "")

    def test_ocr_box_matches_the_real_ok_box(self) -> None:
        """``OcrBox`` 必须能被真实适配器返回值满足（``ok`` 框架的 ``Box``）。"""

        self.assertIsInstance(Box(1, 2, 3, 4, name="示例"), OcrBox)

    def test_vision_consumes_only_task_surface_members(self) -> None:
        """消费面 ≤ 契约面：``Vision`` 只读 ``self.task`` 的 ``TaskSurface`` 成员。"""

        consumed = _attribute_reads(VISION_RUNTIME, "self.task")
        self.assertTrue(consumed, "AST 未扫到 self.task 调用面，扫描可能已失效")
        self.assertLessEqual(consumed, _members(TaskSurface))
        self.assertEqual(
            {"capture_frame", "config", "drag_client", "info_set", "ocr", "operate_click", "sleep"},
            consumed,
        )

    def test_task_vision_mixin_self_calls_stay_inside_the_host_contract(self) -> None:
        """mixin 通过 ``self`` 访问的非下划线宿主成员必须都在 ``HomeReturnHost`` 里。"""

        accessed = {
            name
            for name in _attribute_reads(TASK_VISION_MIXIN, "self")
            if not name.startswith("_")
        }
        self.assertTrue(accessed, "AST 未扫到 self 调用面，扫描可能已失效")
        self.assertLessEqual(accessed, _members(HomeReturnHost))

    def test_home_return_host_adds_only_real_host_methods(self) -> None:
        """``HomeReturnHost`` 只在端口之上补真实存在的主页业务方法。"""

        task = _task_instance(BaseBD2Task)
        self.assertTrue(_members(TaskSurface) <= _members(HomeReturnHost))
        self.assertEqual(
            _members(HomeReturnHost) - _members(TaskSurface),
            {"attempt_return_home", "clear_temporary_home_announcement_if_needed"},
        )
        for member in sorted(_members(HomeReturnHost)):
            with self.subTest(member=member):
                self.assertTrue(hasattr(task, member), f"{member} 在 BaseBD2Task 上不存在")
        self.assertIsInstance(task, HomeReturnHost)

    def test_ports_package_is_a_stdlib_only_abstraction_layer(self) -> None:
        """``src/ports`` 运行时只 import stdlib；第三方类型库只允许出现在 TYPE_CHECKING 内。"""

        runtime_targets = _runtime_import_targets(PORTS_PACKAGE_INIT)
        self.assertTrue(runtime_targets, "AST 未扫到 import，扫描可能已失效")
        roots = {target.split(".")[0] for target in runtime_targets}
        non_stdlib = sorted(root for root in roots if root not in sys.stdlib_module_names)
        self.assertEqual([], non_stdlib, f"src/ports 运行时引入了非 stdlib 依赖：{non_stdlib}")
        self.assertNotIn("numpy", roots)

    def test_vision_and_mixin_declare_the_port_on_duck_typed_positions(self) -> None:
        """鸭子类型位置必须标注到端口，而不是继续留在无标注状态。"""

        self.assertIs(get_type_hints(Vision.__init__)["task"], TaskSurface)

        expected_self_annotations = {
            "_quick_vision": TaskSurface,
            "_status_set": TaskSurface,
            "_click_reference": TaskSurface,
            "_wait_loading_or_template": TaskSurface,
            "_wait_for_template": TaskSurface,
            "_wait_for_template_or_ocr": TaskSurface,
            "_wait_for_ocr_keywords": TaskSurface,
            "_match": TaskSurface,
            "_passes": TaskSurface,
            "_ocr_text": TaskSurface,
            "_home_p95_threshold": TaskSurface,
            "_wait_for_home_confirmation": HomeReturnHost,
            "_auto_return_home_after_timeout": HomeReturnHost,
        }
        for name, expected in expected_self_annotations.items():
            method = getattr(TaskVisionMixin, name)
            with self.subTest(method=name):
                self.assertIs(get_type_hints(method)["self"], expected)


if __name__ == "__main__":
    unittest.main()