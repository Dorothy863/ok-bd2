# 架构守卫规则（R1-R6）

> P6「架构守卫」的规则来源与例外台账。执行者是 `tests/test_architecture_layers.py`：它用
> `ast` 解析 `src/` 的真实 import（`Import` / `ImportFrom` / 相对导入）与 while 守卫，
> **不做正则粗扫字符串**（字符串、注释、docstring 里的 `import` 不算依赖）。
>
> 依据：`docs/refactor-plan.md` 第 4 节（依赖规则）、3.1（依赖审计复核）、P0.5 新增契约。
> 规则编号与例外清单在本文件与测试文件里必须一致，改动任何一侧都要同步另一侧。

## 1. 分层与方向

```text
表达层   src/ui
任务层   src/tasks（含领域包 src/tasks/map_trade）
驱动层   src/utils、src/interaction、src/scene
```

允许方向（来自 refactor-plan 第 4 节）：

```text
src/ui              → src/tasks
src/tasks           → src/utils / src/interaction / src/scene
src/tasks/map_trade → src/tasks/map_trade / src/utils
```

禁止方向：

```text
src/utils / src/interaction / src/scene → src/tasks / src/ui      （R2）
src/ui                                  → src/utils / src/interaction / src/scene（R1）
src/tasks/task_vision_mixin             → src/tasks/map_trade     （R4）
非 map_trade 领域任务                    → src.tasks.map_trade.models（R3）
基础工具                                 → 组合工具（含环）（R5）
任何 src/**                              → `while monotonic() <= end_at` 形态（R6）
```

## 2. 规则编号

| 编号 | 含义 | 禁止方向 | 守卫方法（`tests/test_architecture_layers.py`） |
|---|---|---|---|
| R1 | 表达层不得依赖工具/驱动层 | `src/ui` → `src/utils` / `src/interaction` / `src/scene` | `test_r1_ui_does_not_import_tool_or_driver_layers` |
| R2 | 工具/驱动层不得依赖上层 | `src/utils` / `src/interaction` / `src/scene` → `src/tasks` / `src/ui` | `test_r2_driver_layers_do_not_import_tasks_or_ui` |
| R3 | 公共模型来源唯一 | 非 map_trade 领域任务 → `src.tasks.map_trade.models` | `test_r3_non_domain_tasks_do_not_import_map_trade_models` |
| R4 | 公共视觉基座隔离领域 | `src/tasks/task_vision_mixin.py` → `src.tasks.map_trade` | `test_r4_task_vision_mixin_does_not_import_map_trade` |
| R5 | 工具层 DAG（方向 + 无环） | 基础工具 → 组合工具；`src/utils` 内部 import 环 | `test_r5_basic_tools_do_not_import_composed_tools`、`test_r5_tool_layer_has_no_import_cycle` |
| R6 | 统一有界轮询契约 | 比较 `monotonic()` 与截止时间的 while 守卫 | `test_r6_no_unregistered_monotonic_deadline_while_guards` |

## 3. 逐条细则

### R1 表达层方向

`src/ui/**` 不得 import `src/utils.*`、`src/interaction.*`、`src/scene.*`。

- 判定：文件路径位于 `src/ui/`，且命中模块（含 `from src.utils import image_utils` 解析出的
  `src.utils.image_utils`）以 `src.utils` / `src.interaction` / `src.scene` 开头。
- 允许：`src/ui` → `src/tasks`。
- 现状：0 违规。

### R2 工具/驱动层方向

`src/utils/**`、`src/interaction/**`、`src/scene/**` 不得 import `src/tasks.*`、`src.ui.*`。

- 判定：文件路径位于三个驱动层包内，且命中模块以 `src.tasks` / `src.ui` 开头。
- 现状：0 违规。

### R3 公共模型来源

非 map_trade 领域任务不得 import `src.tasks.map_trade.models`（`MatchResult` / `TemplateSpec`
等公共模型的唯一来源是 `src.utils.vision_models`，见 refactor-plan P1）。

- 判定：命中模块为 `src.tasks.map_trade.models` 或其子模块；**允许的领域入口不算违规**
  （白名单见下）。
- 白名单（领域入口，非例外）：
  - `src/tasks/MapTradeTask.py`
  - `src/tasks/MapCollectionTask.py`
  - `src/tasks/BD2MapCollectionProbeTask.py`
  - `src/tasks/map_trade/**`（领域包内部自然可以使用自己的模型）
- 现状：7 处违规，全部登记在 EXCEPTIONS（P1 清理中）。

### R4 公共基座隔离

`src/tasks/task_vision_mixin.py` 不得 import `src.tasks.map_trade`（任意子模块）。

- 判定：文件路径等于 `src/tasks/task_vision_mixin.py`，且命中模块以 `src.tasks.map_trade` 开头。
- 理由：它是公共视觉基座，被大量非 map_trade 任务复用；一旦依赖领域实现，公共业务就被
  具体领域绑架（P0.5 审计第 3 条）。
- 现状：2 处违规，全部登记在 EXCEPTIONS（P1 清理中）。

### R5 工具层 DAG

工具层内部允许有向无环 DAG，但只允许“基础工具 ← 组合工具”的方向。

```text
基础工具：vision_models / image_utils / ocr_utils / template_resolution / polling
组合工具：task_vision / home_confirmation / cartridge_quick_switch
```

- R5a（方向）：基础工具不得 import 组合工具（`test_r5_basic_tools_do_not_import_composed_tools`）。
  现状 0 违规；P0.5 审计记录的既有合法边为
  `cartridge_quick_switch → image_utils`、`home_confirmation → ocr_utils / image_utils`、
  `task_vision → vision_models / template_resolution`。
- R5b（无环）：`src/utils` 内部 import 图不得有环（`test_r5_tool_layer_has_no_import_cycle`）。
  边按“最深匹配模块”解析，自环不计；函数内延迟导入同样参与判定（延迟导入不是绕开环的理由，
  确需保留时按例外登记）。现状 0 环。

### R6 轮询契约

禁止任何 `src/**` 再出现 **零等待时会一次都不采样** 的轮询守卫，即
`while monotonic() <= end_at` / `while monotonic() < end_at` 形态；统一改用
`src/utils/polling.py::PollDeadline`。

- 判定：while 守卫中出现“调用 `monotonic()`（含 `time.monotonic()`）与某个非字面量表达式比较”
  的 `Compare` 节点。
- **不在 R6 范围**（避免误报）：
  - 普通状态判断（不是循环守卫），例如 `if monotonic() < self._match_pause_until`、
    `if monotonic() >= next_home_retry_at`、`if time.monotonic() < self._startup_deadline`。
  - 与字面常量比较的固定时长循环，例如 `while monotonic() < 5:`。
  - 循环体内部/收尾处的截止判断。
- 契约细节（refactor-plan P0.5）：
  - 循环守卫用 `while not end_at.poll_expired():`（零等待至少采样一次）。
  - `while True:` + 收尾判断用纯 `deadline.expired()`。
  - `clock` 由调用方传入本模块的 `monotonic`，保留 `patch("<task module>.monotonic")` 时间夹具。
- 现状：2 处命中，均为已登记的合法写法（见 EXCEPTIONS）。

## 4. 已知例外 EXCEPTIONS

例外登记在 `tests/test_architecture_layers.py::EXCEPTIONS`，每条必须带
**规则编号 / 文件 / 命中细节 / 原因 / 清理计划**。守卫只在“完全相同”时放行：
换文件、换模块、换守卫表达式都会重新失败。

| 编号 | 文件 | 命中细节 | 原因 | 清理计划 |
|---|---|---|---|---|
| R6 | `src/tasks/FreeGachaTask.py` | `monotonic() < end_at` | `_click_skip_until_back_page` 是**点击时间窗**而非“等待并确认”轮询：窗口按 `[start, start+duration)` 计，用 `<=` 会多出一次点击（测试断言 3.0s/0.2s 恰好 15 次点击），且零时长=不点击是期望行为 | 不清理（语义正确）；改动需同步点击次数断言 |
| R6 | `src/compat/windows_graphics.py` | `time.monotonic() < deadline` | `_capture_can_produce_visible_frame` 是注入给上游 `ok` 库的钩子（`_capture_can_produce_frame`），超时由库侧给定，不属于本仓库 `_wait_*` 轮询契约 | 不清理（跟随上游契约） |

维护规则：

1. **新增违规不许直接进 EXCEPTIONS**：先改代码方向；本阶段确实无法清理的，才登记，并写清原因与清理计划。
2. **清理完成后必须删除登记项**。`test_every_exception_still_covers_a_real_violation` 会在
   条目过期（已无对应真实违规）时失败，避免“陈旧例外掩盖新违规”的假绿。
3. `test_every_exception_is_documented` 保证每条例外都有规则编号、路径、命中细节、原因与清理计划。

当前 2 条，均为语义正确、**永久保留**的 R6 例外。

> 历史：本轮最初登记 11 条，另 9 条 R3/R4 属“公共任务依赖 map_trade 领域模型”，
> 由工作包 P1（`refactor/vision-models`）清理。P1 合并当天即从守卫与本文档中删除；
> `test_every_exception_still_covers_a_real_violation` 会在例外过期时失败，
> 以此强制清理陈旧例外，避免“陈旧例外掩盖新违规”。

## 5. 运行方式与失败信息

```powershell
uv run ruff check .
uv run python -m unittest tests/test_architecture_layers.py -v      # 只跑守卫（17 项）
uv run python -m unittest discover -s tests -p "test_*.py"          # 全量
```

守卫失败信息必须包含**规则编号、违规文件（仓库相对路径）、具体 import 语句**，例如：

```text
[架构守卫] R1 触发 1 处未登记违规 —— 表达层方向：src/ui 不得 import src/utils / src/interaction / src/scene
  - src/ui/common.py:1: from src.utils.image_utils import to_gray
    命中 src.utils.image_utils
  修复方式：调整依赖方向。确属本阶段无法清理的历史违规，必须在 tests/test_architecture_layers.py 的 EXCEPTIONS 登记（规则编号/文件/命中细节/原因/清理计划），并同步 docs/architecture-rules.md。
```

R6 的“语句”一栏是守卫本身，附所属函数，例如：

```text
[架构守卫] R6 触发 1 处未登记违规 —— 轮询契约：不得再写比较 monotonic() 与截止时间的 while 守卫，统一用 PollDeadline
  - src/tasks/_guard_probe_tmp.py:5: while monotonic() <= end_at:
    命中 monotonic() <= end_at；probe
```

## 6. 反向验证（证明守卫不是假绿）

守卫里 R1 / R2 / R5 当前都是 0 违规，如果扫描器坏掉会“看起来全绿”，所以有两层保险：

1. **自检测试**（用合成源码片段，不依赖业务代码现状）：
   - `test_import_detector_catches_synthetic_violations`：构造 `src/ui → src.utils`、
     相对导入、`import src.scene.*`、`src.utils → src.tasks`、`src.interaction → src.ui`、
     `NonDomainTask → map_trade.models`、`task_vision_mixin → map_trade.vision`、
     `image_utils → task_vision` 等片段，断言每条都被识别、报出正确文件与语句。
   - `test_import_detector_ignores_allowed_directions`：反向确认允许方向的片段不误报。
   - `test_while_guard_detector_catches_synthetic_violations` /
     `test_while_guard_detector_ignores_state_checks`：R6 只认循环守卫，不误报状态判断。
   - `test_tool_layer_cycle_detector_catches_synthetic_cycle`：构造两模块互相 import，
     断言 R5b 报出环。
   - `test_scan_covers_source_tree`：确认真的扫到了 `src/`（文件数量与已知文件）。
   - `test_known_exceptions_suppress_only_their_own_violation`：确认例外只放行它自己那条，
     同规则的新违规照样失败。
2. **真实反向验证**（手工，一次性）：
   - 临时在 `src/ui/common.py` 顶部插入 `from src.utils.image_utils import to_gray`；
   - 跑 `uv run python -m unittest tests/test_architecture_layers.py`，确认
     `test_r1_ui_does_not_import_tool_or_driver_layers` 失败且报出 R1 + 文件 + 语句；
   - `git checkout -- src/ui/common.py` 撤销，再跑一次确认恢复全绿；
   - 追加验证 R6：在 `src/tasks/` 下临时建含 `while monotonic() <= end_at:` 的探针文件，
     确认 R6 失败后删除探针文件。

## 7. 基线证据（2026-10-12，基线提交 `16574c6`）

- `uv run ruff check .`：通过。
- `uv run python -m unittest discover -s tests -p "test_*.py"`：全过（含本守卫 17 项）。
- 扫描规模：`src/**` 104 个 `.py`，其中 94 个含 import。
- 规则扫描结果（P1 合并后复跑）：R1 = 0、R2 = 0、R3 = 0、R4 = 0、R5 方向 = 0、R5 环 = 0；R6 = 2（在册例外）。

## 8. 边界：本守卫不检查什么

- 不检查 `src/tasks → src/config` 等 refactor-plan 第 4 节列出但未编号为 R1-R6 的方向。
- 不检查 `src/compat`、`src/diagnostics`、`src/globals.py` 等未列层的依赖方向。
- 不检查运行时动态导入（`importlib`、`__import__`、字符串导入）。
- 不检查 `tests/`、`scripts/`、`main.py` 的依赖方向。
- 方向规则对所有 import 生效（含函数内/延迟导入）；R5b 环检测同样包含延迟导入。