"""架构守卫：用 AST 扫描 src/ 的真实 import 与 while 守卫，断言分层规则 R1-R6。

规则定义、允许/禁止方向、已知例外与清理计划见 docs/architecture-rules.md；
本文件里的规则编号必须与文档保持一致。

设计要点：

- 只解析 AST，不做正则匹配，避免把字符串/注释里的 ``import`` 当成真实依赖。
- ``ImportFrom`` 同时登记 ``X`` 与 ``X.<alias>`` 候选目标，因此
  ``from src.utils import image_utils``、``from src.utils.image_utils import x``
  与相对导入都能被识别。
- 真实存在的历史违规集中在 ``EXCEPTIONS``：必须带规则编号、文件、命中细节、原因
  与清理计划；清理完成后守卫会报“过期条目”，强制删除登记项，避免假绿。
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"

# 表达层：``*_PACKAGE`` 用于判断“谁在 import”，``*_MODULES`` 用于判断“import 了谁”。
UI_LAYER_PACKAGE = "src/ui"
# 驱动层（工具/交互/场景）。
DRIVER_LAYER_PACKAGES = ("src/utils", "src/interaction", "src/scene")
DRIVER_LAYER_MODULES = ("src.utils", "src.interaction", "src.scene")
# 驱动层禁止依赖的上层。
HIGHER_LAYER_PACKAGES = ("src/tasks", "src/ui")
HIGHER_LAYER_MODULES = ("src.tasks", "src.ui")

TASK_LAYER = "src/tasks"
MAP_TRADE_PACKAGE = "src/tasks/map_trade"
MAP_TRADE_MODELS = "src.tasks.map_trade.models"

# R3 允许的领域入口：它们本来就要编排 map_trade，不算违规（见 refactor-plan 3.1/4）。
MAP_TRADE_DOMAIN_ENTRIES = (
    "src/tasks/MapTradeTask.py",
    "src/tasks/MapCollectionTask.py",
    "src/tasks/BD2MapCollectionProbeTask.py",
)

TASK_VISION_MIXIN = "src/tasks/task_vision_mixin.py"

TOOL_PACKAGE = "src/utils"
# R5 基础工具：只允许被组合工具依赖。
R5_BASIC_TOOLS = (
    "src.utils.vision_models",
    "src.utils.image_utils",
    "src.utils.ocr_utils",
    "src.utils.template_resolution",
    "src.utils.polling",
)
# R5 组合工具：可以依赖基础工具，不得被基础工具反向依赖。
R5_COMPOSED_TOOLS = (
    "src.utils.task_vision",
    "src.utils.home_confirmation",
    "src.utils.cartridge_quick_switch",
)

RULES: dict[str, str] = {
    "R1": "表达层方向：src/ui 不得 import src/utils / src/interaction / src/scene",
    "R2": "驱动层方向：src/utils / src/interaction / src/scene 不得 import src/tasks / src/ui",
    "R3": "公共模型来源：非 map_trade 领域任务不得 import src.tasks.map_trade.models",
    "R4": "公共基座隔离：src/tasks/task_vision_mixin.py 不得 import src.tasks.map_trade",
    "R5": "工具层 DAG：基础工具不得反向依赖组合工具，src/utils 内部不得出现 import 环",
    "R6": "轮询契约：不得再写比较 monotonic() 与截止时间的 while 守卫，统一用 PollDeadline",
}


@dataclass(frozen=True, order=True)
class Violation:
    """一处违规：规则编号 + 相对文件路径 + 具体语句。"""

    rule: str
    path: str
    lineno: int
    statement: str
    detail: str
    context: str = ""


@dataclass(frozen=True)
class KnownViolation:
    """已登记的真实违规（例外），必须可被逐项清理。"""

    rule: str
    path: str
    detail: str
    reason: str
    cleanup: str

    def covers(self, violation: Violation) -> bool:
        return (
            self.rule == violation.rule
            and self.path == violation.path
            and self.detail == violation.detail
        )

    def label(self) -> str:
        return f"{self.rule} {self.path} [{self.detail}]"


R6_KEEP_CLICK_WINDOW = (
    "不清理：语义正确，保留点击时间窗写法；如需改动必须同步 FreeGachaTask 的点击次数断言。"
)
R6_KEEP_UPSTREAM_HOOK = "不清理：跟随上游 ok 库契约，若上游改动钩子签名再评估。"

EXCEPTIONS: tuple[KnownViolation, ...] = (
    # ---- R6：语义正确、不属于“零等待不采样”轮询契约的历史写法 ----
    KnownViolation(
        rule="R6",
        path="src/tasks/FreeGachaTask.py",
        detail="monotonic() < end_at",
        reason=(
            "_click_skip_until_back_page 是“点击时间窗”而不是“等待并确认”轮询："
            "窗口按 [start, start+duration) 计，用 <= 会多出一次点击"
            "（测试断言 3.0s/0.2s 恰好 15 次点击），且零时长=不点击是期望行为。"
        ),
        cleanup=R6_KEEP_CLICK_WINDOW,
    ),
    KnownViolation(
        rule="R6",
        path="src/compat/windows_graphics.py",
        detail="time.monotonic() < deadline",
        reason=(
            "_capture_can_produce_visible_frame 是注入给上游 ok 库的钩子"
            "（_capture_can_produce_frame），超时由库侧给定，不属于本仓库 _wait_* 轮询契约。"
        ),
        cleanup=R6_KEEP_UPSTREAM_HOOK,
    ),
)


@dataclass(frozen=True)
class ImportEdge:
    """一条解析后的 import 边；``target`` 是候选模块全名（含 ``from X import a`` 的 ``X.a``）。"""

    path: str
    module: str
    target: str
    lineno: int
    statement: str


@dataclass(frozen=True)
class WhileGuard:
    """比较 monotonic() 与截止时间的 while 守卫。"""

    path: str
    module: str
    lineno: int
    statement: str
    detail: str
    function: str


def module_name(path: Path) -> str:
    """把仓库内文件路径换算成点分模块名（``src/ui/common.py`` -> ``src.ui.common``）。"""

    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(path: str, module: str) -> str:
    if path.endswith("__init__.py"):
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _import_from_base(path: str, module: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = _package_of(path, module)
    parts = package.split(".") if package else []
    keep = len(parts) - (node.level - 1)
    prefix = parts[: max(0, keep)]
    return ".".join(part for part in (*prefix, node.module or "") if part)


def parse_imports(path: str, module: str, source: str) -> tuple[ImportEdge, ...]:
    """解析一段源码里的 import；``path`` 是仓库相对路径，``module`` 是它自己的模块名。"""

    tree = ast.parse(source)
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            statement = ast.unparse(node)
            for alias in node.names:
                edges.append(ImportEdge(path, module, alias.name, node.lineno, statement))
        elif isinstance(node, ast.ImportFrom):
            base = _import_from_base(path, module, node)
            if not base:
                continue
            statement = ast.unparse(node)
            edges.append(ImportEdge(path, module, base, node.lineno, statement))
            for alias in node.names:
                edges.append(
                    ImportEdge(path, module, f"{base}.{alias.name}", node.lineno, statement)
                )
    return tuple(edges)


def collect_imports(root: Path = SRC_DIR) -> tuple[ImportEdge, ...]:
    edges: list[ImportEdge] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        edges.extend(parse_imports(relative, module_name(path), source))
    return tuple(edges)


def _is_monotonic_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "monotonic"
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "monotonic"
    return False


def _monotonic_deadline_compares(test: ast.expr) -> list[ast.Compare]:
    """循环守卫里“monotonic() 与截止时间比较”的判定。

    ``if monotonic() < self._match_pause_until`` 这类状态判断不是循环守卫，不在范围内；
    ``while monotonic() < 5`` 这类与常量比较也不是截止时间，同样不报。
    """

    found: list[ast.Compare] = []
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if not any(_is_monotonic_call(operand) for operand in operands):
            continue
        bounds = [operand for operand in operands if not _is_monotonic_call(operand)]
        if not any(not isinstance(operand, ast.Constant) for operand in bounds):
            continue
        found.append(node)
    return found


class _WhileGuardVisitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str) -> None:
        self.path = path
        self.module = module
        self.scope: list[str] = []
        self.guards: list[WhileGuard] = []

    def _visit_scoped(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_While(self, node: ast.While) -> None:
        for compare in _monotonic_deadline_compares(node.test):
            self.guards.append(
                WhileGuard(
                    path=self.path,
                    module=self.module,
                    lineno=node.lineno,
                    statement=f"while {ast.unparse(compare)}:",
                    detail=ast.unparse(compare),
                    function=".".join(self.scope),
                )
            )
        self.generic_visit(node)


def parse_while_guards(path: str, module: str, source: str) -> tuple[WhileGuard, ...]:
    visitor = _WhileGuardVisitor(path, module)
    visitor.visit(ast.parse(source))
    return tuple(visitor.guards)


def collect_while_guards(root: Path = SRC_DIR) -> tuple[WhileGuard, ...]:
    guards: list[WhileGuard] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        guards.extend(parse_while_guards(relative, module_name(path), source))
    return tuple(guards)


def tool_layer_modules(root: Path = SRC_DIR) -> tuple[str, ...]:
    return tuple(sorted(module_name(path) for path in (root / "utils").rglob("*.py")))


def _in_package(path: str, package: str) -> bool:
    return path.startswith(package + "/")


def _targets(target: str, module: str) -> bool:
    return target == module or target.startswith(module + ".")


def _targets_any(target: str, modules: tuple[str, ...]) -> bool:
    return any(_targets(target, module) for module in modules)


def _is_map_trade_domain_file(path: str) -> bool:
    """R3 白名单：map_trade 包自身与三个领域入口。"""

    return _in_package(path, MAP_TRADE_PACKAGE) or path in MAP_TRADE_DOMAIN_ENTRIES


def matches_r1(edge: ImportEdge) -> bool:
    return _in_package(edge.path, UI_LAYER_PACKAGE) and _targets_any(
        edge.target, DRIVER_LAYER_MODULES
    )


def matches_r2(edge: ImportEdge) -> bool:
    if not any(_in_package(edge.path, layer) for layer in DRIVER_LAYER_PACKAGES):
        return False
    return _targets_any(edge.target, HIGHER_LAYER_MODULES)


def matches_r3(edge: ImportEdge) -> bool:
    return not _is_map_trade_domain_file(edge.path) and _targets(edge.target, MAP_TRADE_MODELS)


def matches_r4(edge: ImportEdge) -> bool:
    return edge.path == TASK_VISION_MIXIN and _targets(edge.target, "src.tasks.map_trade")


def matches_r5(edge: ImportEdge) -> bool:
    return edge.module in R5_BASIC_TOOLS and _targets_any(edge.target, R5_COMPOSED_TOOLS)


IMPORT_RULES = {
    "R1": matches_r1,
    "R2": matches_r2,
    "R3": matches_r3,
    "R4": matches_r4,
    "R5": matches_r5,
}


def rule_violations(rule: str, edges: tuple[ImportEdge, ...]) -> tuple[Violation, ...]:
    """按规则筛选违规；同一语句只报一次，取最短命中目标作为细节。"""

    hits: dict[tuple[str, int, str], ImportEdge] = {}
    matcher = IMPORT_RULES[rule]
    for edge in edges:
        if not matcher(edge):
            continue
        key = (edge.path, edge.lineno, edge.statement)
        current = hits.get(key)
        if current is None or len(edge.target) < len(current.target):
            hits[key] = edge
    return tuple(
        Violation(rule, edge.path, edge.lineno, edge.statement, edge.target)
        for edge in sorted(hits.values(), key=lambda item: (item.path, item.lineno, item.target))
    )


def r6_violations(guards: tuple[WhileGuard, ...]) -> tuple[Violation, ...]:
    return tuple(
        Violation(
            "R6",
            guard.path,
            guard.lineno,
            guard.statement,
            guard.detail,
            guard.function,
        )
        for guard in sorted(guards, key=lambda item: (item.path, item.lineno, item.detail))
    )


def _deepest_target(target: str, modules: tuple[str, ...]) -> str | None:
    matched = [module for module in modules if _targets(target, module)]
    if not matched:
        return None
    return max(matched, key=len)


def tool_layer_graph(
    edges: tuple[ImportEdge, ...], modules: tuple[str, ...]
) -> tuple[dict[str, set[str]], dict[tuple[str, str], list[ImportEdge]]]:
    """构建 src/utils 内部 DAG（去掉自环），并保留每条边对应的 import 语句。"""

    known = set(modules)
    graph: dict[str, set[str]] = {module: set() for module in modules}
    statements: dict[tuple[str, str], list[ImportEdge]] = {}
    for edge in edges:
        if edge.module not in known or not _in_package(edge.path, TOOL_PACKAGE):
            continue
        target = _deepest_target(edge.target, modules)
        if target is None or target == edge.module:
            continue
        graph[edge.module].add(target)
        statements.setdefault((edge.module, target), []).append(edge)
    return graph, statements


def find_cycles(graph: dict[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    cycles: dict[tuple[str, ...], None] = {}
    for start in sorted(graph):
        stack: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for target in sorted(graph.get(node, ())):
                if target == start:
                    cycles.setdefault(path + (start,), None)
                elif target not in path:
                    stack.append((target, path + (target,)))
    return tuple(cycles)


def r5_cycle_violations(
    edges: tuple[ImportEdge, ...], modules: tuple[str, ...]
) -> tuple[Violation, ...]:
    graph, statements = tool_layer_graph(edges, modules)
    violations: dict[tuple[str, int, str, str], Violation] = {}
    for cycle in find_cycles(graph):
        description = " -> ".join(cycle)
        for source, target in zip(cycle, cycle[1:]):
            for edge in statements.get((source, target), []):
                key = (edge.path, edge.lineno, edge.statement, target)
                violations.setdefault(
                    key,
                    Violation(
                        "R5",
                        edge.path,
                        edge.lineno,
                        edge.statement,
                        target,
                        f"import 环：{description}",
                    ),
                )
    return tuple(
        sorted(violations.values(), key=lambda item: (item.path, item.lineno, item.detail))
    )


def collect_all_violations(
    edges: tuple[ImportEdge, ...],
    guards: tuple[WhileGuard, ...],
    modules: tuple[str, ...],
) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    for rule in IMPORT_RULES:
        violations.extend(rule_violations(rule, edges))
    violations.extend(r6_violations(guards))
    violations.extend(r5_cycle_violations(edges, modules))
    return tuple(violations)


def unresolved_violations(violations: tuple[Violation, ...]) -> tuple[Violation, ...]:
    return tuple(
        violation
        for violation in violations
        if not any(known.covers(violation) for known in EXCEPTIONS)
    )


def format_report(rule: str, violations: tuple[Violation, ...]) -> str:
    lines = [f"[架构守卫] {rule} 触发 {len(violations)} 处未登记违规 —— {RULES[rule]}"]
    for violation in violations:
        lines.append(f"  - {violation.path}:{violation.lineno}: {violation.statement}")
        detail = f"命中 {violation.detail}"
        if violation.context:
            detail = f"{detail}；{violation.context}"
        lines.append(f"    {detail}")
    lines.append(
        "  修复方式：调整依赖方向。确属本阶段无法清理的历史违规，必须在"
        " tests/test_architecture_layers.py 的 EXCEPTIONS 登记（规则编号/文件/命中细节/原因/"
        "清理计划），并同步 docs/architecture-rules.md。"
    )
    return "\n".join(lines)


class ArchitectureLayerGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.imports = collect_imports()
        cls.guards = collect_while_guards()
        cls.tool_modules = tool_layer_modules()

    def assert_no_unregistered(self, rule: str, violations: tuple[Violation, ...]) -> None:
        unresolved = unresolved_violations(violations)
        if unresolved:
            self.fail(format_report(rule, unresolved))

    # ---- R1 ~ R6：真实源码断言 ----

    def test_r1_ui_does_not_import_tool_or_driver_layers(self) -> None:
        self.assert_no_unregistered("R1", rule_violations("R1", self.imports))

    def test_r2_driver_layers_do_not_import_tasks_or_ui(self) -> None:
        self.assert_no_unregistered("R2", rule_violations("R2", self.imports))

    def test_r3_non_domain_tasks_do_not_import_map_trade_models(self) -> None:
        self.assert_no_unregistered("R3", rule_violations("R3", self.imports))

    def test_r4_task_vision_mixin_does_not_import_map_trade(self) -> None:
        self.assert_no_unregistered("R4", rule_violations("R4", self.imports))

    def test_r5_basic_tools_do_not_import_composed_tools(self) -> None:
        self.assert_no_unregistered("R5", rule_violations("R5", self.imports))

    def test_r5_tool_layer_has_no_import_cycle(self) -> None:
        self.assert_no_unregistered("R5", r5_cycle_violations(self.imports, self.tool_modules))

    def test_r6_no_unregistered_monotonic_deadline_while_guards(self) -> None:
        self.assert_no_unregistered("R6", r6_violations(self.guards))

    # ---- 例外登记：不许假绿，也不许留过期条目 ----

    def test_every_exception_still_covers_a_real_violation(self) -> None:
        current = collect_all_violations(self.imports, self.guards, self.tool_modules)
        stale = [known for known in EXCEPTIONS if not any(known.covers(v) for v in current)]
        if stale:
            self.fail(
                "[架构守卫] EXCEPTIONS 有 %d 条过期条目（违规已清理，或文件/命中细节写错）：\n%s\n"
                "  处理：删除对应条目，并同步 docs/architecture-rules.md 的例外清单。"
                % (
                    len(stale),
                    "\n".join(f"  - {known.label()}" for known in stale),
                )
            )

    def test_every_exception_is_documented(self) -> None:
        for known in EXCEPTIONS:
            with self.subTest(exception=known.label()):
                self.assertIn(known.rule, RULES)
                self.assertTrue(known.path.startswith("src/"))
                self.assertTrue(known.detail.strip())
                self.assertTrue(known.reason.strip(), "例外必须写清原因")
                self.assertTrue(known.cleanup.strip(), "例外必须写清清理计划")

    def test_rules_and_exceptions_are_documented(self) -> None:
        document = (ROOT / "docs" / "architecture-rules.md").read_text(encoding="utf-8")
        for rule in RULES:
            with self.subTest(rule=rule):
                self.assertIn(f"| {rule} |", document)
        for known in EXCEPTIONS:
            with self.subTest(exception=known.label()):
                self.assertIn(f"| {known.rule} | `{known.path}` | `{known.detail}` |", document)

    def test_scan_covers_source_tree(self) -> None:
        scanned = {edge.path for edge in self.imports}
        for expected in (
            "src/ui/quest_ui.py",
            "src/tasks/DailyTask.py",
            "src/utils/task_vision.py",
        ):
            with self.subTest(path=expected):
                self.assertIn(expected, scanned)
        self.assertGreaterEqual(len(scanned), 90, "src/ 扫描结果异常偏少，守卫可能已失效")
        for expected in ("src/tasks/FreeGachaTask.py", "src/compat/windows_graphics.py"):
            with self.subTest(path=expected):
                self.assertIn(expected, {guard.path for guard in self.guards})

    # ---- 自检：守卫本身必须在样例上真的会失败 ----

    def test_import_detector_catches_synthetic_violations(self) -> None:
        samples = (
            (
                "R1",
                "src/ui/common.py",
                "src.ui.common",
                "from src.utils import image_utils\n",
                "src.utils",
            ),
            (
                "R1",
                "src/ui/tab.py",
                "src.ui.tab",
                "from ..utils.vision_models import MatchResult\n",
                "src.utils.vision_models",
            ),
            (
                "R1",
                "src/ui/tab.py",
                "src.ui.tab",
                "import src.scene.BD2Scene\n",
                "src.scene.BD2Scene",
            ),
            (
                "R2",
                "src/utils/image_utils.py",
                "src.utils.image_utils",
                "import src.tasks.DailyTask\n",
                "src.tasks.DailyTask",
            ),
            (
                "R2",
                "src/interaction/BD2Interaction.py",
                "src.interaction.BD2Interaction",
                "from src.ui.common import helper\n",
                "src.ui.common",
            ),
            (
                "R3",
                "src/tasks/NewTask.py",
                "src.tasks.NewTask",
                "from src.tasks.map_trade import models\n",
                MAP_TRADE_MODELS,
            ),
            (
                "R4",
                "src/tasks/task_vision_mixin.py",
                "src.tasks.task_vision_mixin",
                "from .map_trade.vision import Vision\n",
                "src.tasks.map_trade.vision",
            ),
            (
                "R5",
                "src/utils/image_utils.py",
                "src.utils.image_utils",
                "from src.utils import task_vision\n",
                "src.utils.task_vision",
            ),
        )
        for rule, path, module, source, detail in samples:
            with self.subTest(rule=rule, source=source.strip()):
                edges = parse_imports(path, module, source)
                violations = rule_violations(rule, edges)
                self.assertEqual(1, len(violations), f"{rule} 未识别违规: {source.strip()}")
                self.assertEqual(detail, violations[0].detail)
                self.assertEqual(path, violations[0].path)
                self.assertEqual(source.strip(), violations[0].statement)

    def test_import_detector_ignores_allowed_directions(self) -> None:
        samples = (
            (
                "R1",
                "src/ui/common.py",
                "src.ui.common",
                "from src.tasks.DailyTask import DailyTask\n",
            ),
            (
                "R2",
                "src/utils/image_utils.py",
                "src.utils.image_utils",
                "from src.utils.polling import PollDeadline\n",
            ),
            (
                "R3",
                "src/tasks/MapTradeTask.py",
                "src.tasks.MapTradeTask",
                "from src.tasks.map_trade.models import TemplateSpec\n",
            ),
            (
                "R3",
                "src/tasks/map_trade/navigator.py",
                "src.tasks.map_trade.navigator",
                "from src.tasks.map_trade.models import MatchResult\n",
            ),
            (
                "R3",
                "src/tasks/map_trade/vision.py",
                "src.tasks.map_trade.vision",
                "from . import models\n",
            ),
            (
                "R4",
                "src/tasks/DailyTask.py",
                "src.tasks.DailyTask",
                "from src.tasks.BaseBD2Task import BaseBD2Task\n",
            ),
            (
                "R5",
                "src/utils/task_vision.py",
                "src.utils.task_vision",
                "from src.utils.image_utils import to_gray\n",
            ),
        )
        for rule, path, module, source in samples:
            with self.subTest(rule=rule, source=source.strip()):
                edges = parse_imports(path, module, source)
                self.assertEqual((), rule_violations(rule, edges))

    def test_known_exceptions_suppress_only_their_own_violation(self) -> None:
        """例外只放行自己登记的那一条（R3/R4 的 P1 例外已随 P1 合并删除）。

        用仍然在册的 R6 例外验证：同文件同守卫在册→放行；换个文件→必须仍报违规。
        """
        registered = parse_while_guards(
            "src/tasks/FreeGachaTask.py",
            "src.tasks.FreeGachaTask",
            "def probe(end_at):\n    while monotonic() < end_at:\n        pass\n",
        )
        self.assertEqual((), unresolved_violations(r6_violations(registered)))
        unregistered = parse_while_guards(
            "src/tasks/NewTask.py",
            "src.tasks.NewTask",
            "def probe(end_at):\n    while monotonic() < end_at:\n        pass\n",
        )
        self.assertEqual(1, len(unresolved_violations(r6_violations(unregistered))))

    def test_while_guard_detector_catches_synthetic_violations(self) -> None:
        source = "def poll(end_at):\n    while monotonic() <= end_at:\n        sample()\n"
        guards = parse_while_guards("src/tasks/Demo.py", "src.tasks.Demo", source)
        self.assertEqual(1, len(guards))
        self.assertEqual("monotonic() <= end_at", guards[0].detail)
        self.assertEqual("while monotonic() <= end_at:", guards[0].statement)
        self.assertEqual("poll", guards[0].function)

    def test_while_guard_detector_ignores_state_checks(self) -> None:
        source = (
            "class Task:\n"
            "    def poll(self, end_at):\n"
            "        while not end_at.poll_expired():\n"
            "            if monotonic() < self._match_pause_until:\n"
            "                sample()\n"
            "            if monotonic() >= self._startup_deadline:\n"
            "                break\n"
            "        while monotonic() < 5:\n"
            "            sample()\n"
        )
        self.assertEqual((), parse_while_guards("src/tasks/Demo.py", "src.tasks.Demo", source))

    def test_tool_layer_cycle_detector_catches_synthetic_cycle(self) -> None:
        sources = {
            "src/utils/a_util.py": "from src.utils.b_util import helper\n",
            "src/utils/b_util.py": "from src.utils.a_util import helper\n",
        }
        edges: list[ImportEdge] = []
        for path, source in sources.items():
            module = module_name(ROOT / path)
            edges.extend(parse_imports(path, module, source))
        violations = r5_cycle_violations(tuple(edges), ("src.utils.a_util", "src.utils.b_util"))
        self.assertEqual(2, len(violations))
        self.assertEqual(
            {"src/utils/a_util.py", "src/utils/b_util.py"},
            {violation.path for violation in violations},
        )
        self.assertIn("import 环", violations[0].context)


if __name__ == "__main__":
    unittest.main()
