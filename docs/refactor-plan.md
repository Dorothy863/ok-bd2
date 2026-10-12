# 架构重构计划：三层边界与端口适配器

> 基线：`pre-refactor-20261012`（重构前备份 branch/tag）
> 目标：保持实机行为不变，把当前“概念分层、依赖交叉”的结构收敛为可验证的分层 + 端口适配器。

## 1. 目标与非目标

### 目标

1. 表达层（UI / Entry）只依赖业务任务、状态接口和配置模型。
2. 业务层只依赖端口和领域模型，不直接依赖具体截图/OCR/输入实现。
3. 工具/适配器层实现最小操作，不反向依赖业务层。
4. 公共识别模型不再放在 `map_trade` 领域内。
5. 页面识别收敛为共享业务能力：`identify_page(frame) -> PageId`。
6. 增加架构守卫，禁止新增跨层依赖。
7. 每个阶段独立提交，保留回滚点。

### 非目标

- 不重写所有任务状态机。
- 不改变现有用户配置键和任务名称。
- 不把项目拆成多进程或微服务。
- 不在本重构中修改游戏业务判定；只移动边界和接口。

## 2. 目标架构

```text
表达层 Presentation
  main.py / main_debug.py / src/config.py / src/ui
        ↓ 只调用业务任务与状态接口

业务层 Application
  AutoLoginTask / DailyBatchTask / 各玩法任务 / map_trade 领域任务
  PageRecognition / Navigation / Trader / Collector / Progress
        ↓ 只依赖端口

端口 Ports（中性抽象层，实现位置 src/ports/）
  TaskSurface = CapturePort + OcrPort + InputPort + ConfigPort + StatusPort + ProgressPort

适配器 Adapters
  WGC / BitBlt / onnxocr / BD2Interaction / 文件存储
```

视觉管线作为业务内部管道：

```text
Frame → ROI → OCR/Template → Evidence → PageId → Action Decision
```

## 3. 当前违规基线

1. `src/utils` 内部存在组合工具依赖：
   - `task_vision -> image_utils / template_resolution / vision_models`
   - `home_confirmation -> image_utils / ocr_utils`
   - `cartridge_quick_switch -> image_utils`
2. 公共业务依赖 `map_trade`：
   - `task_vision_mixin -> map_trade.models / map_trade.vision`
   - `DailyTask / QuickHuntTask / FreeGachaTask / PVPTask / SquareGoddessTask / AutoLoginTask`
     导入 `map_trade.models`。
3. `BaseBD2Task` 同时承担工具操作、页面业务和状态输出。
4. 表达层方向基本正确，但直接依赖具体任务类，缺少状态读取接口。

## 3.1 依赖审计复核（主 Agent 实测，2026-10-12）

用 AST 扫描 `src/` 实际 import 复核，结论：

1. 跨层违规：**无**。
   - `src/ui` 未引用 `src/utils` / `src/interaction` / `src/scene`。
   - `src/utils` / `src/interaction` / `src/scene` 未反向引用 `src/tasks` / `src/ui`。
2. 公共任务依赖 `map_trade.models`（待 P1 清理）：
   `DailyTask` / `FreeGachaTask` / `PVPTask` / `SquareGoddessTask` /
   `tasks/quick_hunt.py` / `trigger/AutoLoginTask` / `task_vision_mixin`。
   `MapTradeTask` / `MapCollectionTask` / `BD2MapCollectionProbeTask` 引用
   `map_trade.*` 属领域入口，不在清理范围。
3. `task_vision_mixin` 依赖 `map_trade.vision.Vision`（待 P1 下沉），
   这是"公共业务依赖具体领域"的最大单点。
4. 工具层内部有向无环依赖（可接受）：
   `cartridge_quick_switch → image_utils`；`home_confirmation → ocr_utils / image_utils`；
   `task_vision → vision_models / template_resolution`。
5. `src/tasks/map_trade/models.py` 已经把 `MatchResult` / `TemplateSpec`
   从 `src.utils.vision_models` re-export，公共模型唯一来源已是 `src.utils.vision_models`；
   P1 主要是改导入点，并把 `Vision` 移出 `map_trade`。

## 4. 依赖规则

### 允许

```text
src/ui → src/tasks
src/tasks → src/utils
src/tasks → src/interaction
src/tasks → src/scene
src/tasks/map_trade → src/tasks/map_trade
src/tasks/map_trade → src/utils
```

### 禁止

```text
src/utils → src/tasks / src/ui / src/config
src/interaction → src/tasks / src/ui / src/config
src/scene → src/tasks / src/ui / src/config
src/ui → src/utils / src/interaction / src/scene
src/tasks/task_vision_mixin → src/tasks/map_trade
非 map_trade 任务 → src.tasks.map_trade.models
```

### 工具内部规则

工具层内部允许有向无环的 DAG，但只允许从基础工具指向组合工具：

```text
基础工具：vision_models / image_utils / ocr_utils / template_resolution / polling
组合工具：task_vision / home_confirmation / cartridge_quick_switch / vision_runtime
```

禁止组合工具被基础工具反向导入，禁止循环依赖。

### 新增契约（2026-10-12）

```text
所有有界轮询 → PollDeadline（src/utils/polling.py）
禁止：while monotonic() <= end_at / while monotonic() < end_at
循环体尾部收尾检查 → deadline.expired()（纯判断）
零等待语义 → 至少采样一次（poll_expired）
```

## 5. 阶段与工作包

### P0 基线保护

- 创建 `pre-refactor-20261012` branch/tag。
- 每个工作包独立分支、独立提交。
- 主 Agent 合并前运行全量测试与架构守卫。

### P0.5 基线绿化（已完成）

进入重构前必须先有绿色基线，否则无法判定回归。

事实核查（2026-10-12，Python 3.14.7）：

1. Python 3.14 把 Windows 上 `time.monotonic()` 的分辨率从 15.6ms 提升到 ~100ns，
   于是 `while monotonic() <= end_at` 在超时为 0 时一次都不进入循环体（旧版本靠
   粗粒度时钟恰好落在同一 tick 才"侥幸"执行一次）。全量测试暴露 15 项失败。
2. 处理方式：新增 `src/utils/polling.py::PollDeadline`，把"零等待也要采样一次"
   显式化；其余情况与 `monotonic() <= end_at` 的迭代次数、时钟读取次数完全一致。
   `clock` 由调用方传入自己模块的 `monotonic`，保留 `patch("<task module>.monotonic")`
   这一既有时间夹具。
3. **契约**：新增的有界轮询一律使用 `PollDeadline`；禁止再写
   `while monotonic() <= end_at` / `while monotonic() < end_at`。
   循环体尾部（`while True` + 收尾判断）的截止检查用纯 `deadline.expired()`，
   不要用带零等待放行的 `poll_expired()`。
4. 已知例外：`src/compat/windows_graphics.py::_capture_can_produce_visible_frame`
   仍是"零超时=不探测"。它是注入给上游 `ok` 库的钩子，超时由库侧决定，本次不动。
5. 同时修复：`BaseBD2Task._click_top_left_back` 原先直接 `cv2.matchTemplate`，
   改走同文件已在用的 `template_match_response`（集中式匹配，已有守卫测试）。
6. 同时回退：`SquareGoddessTask._click_fantasia_square_card` 的小卡面 OCR 搜索，
   恢复"生活玩法游戏卡带 → 固定第二格 → 确认梦幻广场"单一路径（第三轮 review 结论）。

基线证据：`uv run ruff check .` 通过；`uv run python -m unittest discover -s tests -p "test_*.py"`
1034 项全过（修复前 15 项失败）。

### P1 公共视觉模型下沉

**问题**：`TemplateSpec`、`MatchResult` 等公共模型位于 `map_trade.models`，导致公共任务依赖具体领域。

**任务**：

- 确认 `src/utils/vision_models.py` 是公共模型唯一来源。
- 非 map_trade 模块改为导入 `src.utils.vision_models`。
- `src/tasks/map_trade/models.py` 只保留领域模型；如必须保持兼容，只做 re-export。
- 去掉 `task_vision_mixin` 对 `map_trade.vision/models` 的依赖。

**验收**：

```text
非 map_trade 任务中不存在 from src.tasks.map_trade.models import ...
task_vision_mixin 不导入 map_trade
```

**写入范围**：

```text
src/utils/vision_models.py
src/tasks/map_trade/models.py
src/tasks/task_vision_mixin.py
src/tasks/DailyTask.py
src/tasks/QuickHuntTask.py
src/tasks/FreeGachaTask.py
src/tasks/PVPTask.py
src/tasks/SquareGoddessTask.py
src/tasks/quick_hunt.py
src/tasks/trigger/AutoLoginTask.py
tests/test_*.py（仅相关导入）
```

### P2 页面识别服务

**问题**：主页判断、抽卡详情判断和自动返回流程写在 `BaseBD2Task` 内。

**任务**：

- 新增 `src/tasks/page_recognition.py`。
- 定义 `PageId`：`HOME`、`GACHA_DETAIL`、`UNKNOWN`（后续可扩展）。
- 提供 `identify_page(frame, boxes)` 和页面信号构建器。
- `BaseBD2Task.auto_return_main_home()` 只消费识别结果。
- 保持 OCR 精确匹配：主页必须是“抽抽乐”，不能匹配“抽抽乐点数/付费抽抽乐”。

**验收**：

```text
BaseBD2Task 中不再散落主页/抽卡详情关键词匹配
抽卡详情准确产出 GACHA_DETAIL
主页准确产出 HOME
无法确认时产出 UNKNOWN，不盲点
```

**写入范围**：

```text
src/tasks/page_recognition.py
src/tasks/BaseBD2Task.py
tests/test_page_recognition.py
tests/test_auto_return_main_home.py
```

### P3 端口抽象

**问题**：业务任务直接知道 WGC、onnxocr、BD2Interaction 等实现。

**任务**：

- 新增 `src/tasks/ports.py`，用 `typing.Protocol` 定义：
  - `CapturePort.capture_frame()`
  - `OcrPort.ocr(...)`
  - `TemplatePort.match(...)`
  - `InputPort.click/scroll/drag(...)`
  - `ProgressPort.load/save(...)`
- 先让 `BaseBD2Task` 适配这些协议，不要求一次性改完所有任务。
- 任务通过端口方法调用，不直接判断具体 adapter 类型。

**验收**：

```text
端口协议有类型定义和测试
BaseBD2Task 中的 capture/ocr/click 通过端口或明确 adapter 转发
不改变现有任务 run() 行为
```

**写入范围**：

```text
src/ports/（中性抽象层，端口唯一定义点）
src/utils/vision_runtime.py
src/tasks/task_vision_mixin.py
tests/test_ports.py
tests/test_architecture_layers.py（R7）
```

### P4 BaseBD2Task 拆分

**问题**：BaseBD2Task 同时是工具、页面业务和状态输出。

**任务**：

- 最小操作：capture、ocr、click、scroll、drag、show info。
- 业务共享：页面识别、自动返回主页、任务生命周期。
- 将页面业务委托给 P2 的 PageRecognition。
- 保留兼容方法，避免一次性改所有子类。

**验收**：

```text
BaseBD2Task 不再新增 OCR 关键词分支
自动返回主页只调用 PageRecognition + InputPort
现有任务无需大规模改写
```

### P5 表达层接口收敛（核查结论：已达成，无需新增抽象）

主 Agent 实测（2026-10-12，合并 P1/P2/P6 后）：

- `src/ui → src/utils` / `src/interaction` / `src/scene`：**0 处**（守卫 R1 = 0）。
- UI 读取任务状态**已经**统一走 `src/tasks/BaseBD2Task.task_info_snapshot(task)`；
  AST 扫描 `src/ui` 中 13 处 `.info` 全部是 `logger.info(...)`，
  对任务状态字段的直读为 **0 处**，`getattr(..., "info")` 亦为 0 处。
- 因此 P5 计划里“定义只读状态接口或 adapter”**已经由 `task_info_snapshot` 承担**，
  不再新增一层接口模块（避免为抽象而抽象）。

结论：P5 不派发子 Agent，仅保留守卫 R1 与本节记录作为回归依据。

#### 原始计划文本（保留备查）

### P5 表达层接口收敛

**问题**：UI 直接依赖具体任务类和状态字段。

**任务**：

- 定义只读状态接口或 adapter，UI 通过接口读取 `task.info`。
- `src/config.py` 作为组合根保持允许 import 全层。
- UI 不直接 import `src.utils`、`src.interaction`、`src.scene`。

**验收**：

```text
src/ui 中不存在 utils/interaction/scene import
UI 只依赖业务任务和状态接口
```

### P6 架构守卫

- 新增 `tests/test_architecture_layers.py`。
- 使用 AST 扫描 import。
- 对已有例外维护显式 `EXCEPTIONS` 列表，并在重构阶段逐项删除。
- 守卫失败必须报出文件、import 和规则编号。

#### P7 离线前置验证（2026-10-12，客户端不在线时执行）

工具：`scripts/replay_frames.py`（对**录制的真实客户端帧**回放真流程：真实 onnxocr 引擎 +
真实 `identify_page` / `auto_return_main_home`，只有“截图”返回录制帧、“点击”只记录坐标）。

在 `probe_outputs/` 的 8 张真实 2560×1440 客户端帧上实测：

| 帧 | `identify_page` | 决策回放 |
| --- | --- | --- |
| `bd2_probe_20260928_195413.png`（主页） | `home`（`home_label_seen=True`） | **True，零点击** ✓（P7 第 1 项：主页识别） |
| `bd2_probe_20261012_202157.png`（抽卡详情） | `gacha_detail`（三关键词齐全） | 点击 `自动返回主页-抽卡详情关闭` **@(1893,430)** ✓（P7 第 2 项） |
| `bd2_probe_20261012_203215.png`（抽卡详情） | `gacha_detail` | 同上 @(1893,430) ✓ |
| `bd2_probe_20260928_194516.png`（小屋房间编辑） | `unknown` | 左上返回模板命中 @(145,66) ✓（真实像素模板匹配可用） |
| `pvp_hub_entry_failed.png`（卡带列表） | `unknown` | 左上返回模板命中 @(168,66) ✓ |

**关键副产物**：两张抽卡详情帧的 OCR 标签里同时存在 `抽抽乐点数` 与 `付费抽抽乐`。
旧的全帧子串判定会把它们当成主页；重构后的精确匹配正确判为 `gacha_detail`——
即 P0.5 修的那个 bug 在**真实像素**上得到验证。

**发现的能力缺口（3 张真实失败帧，需用户决策，不在本重构范围）**

| 帧 | 屏幕内容（OCR） | 为什么失败 |
| --- | --- | --- |
| `cartridge_quick_switch_failed.png` | `赛季奖励 / 白金I / 积分1745 / 点击画面即可返回。` | 屏幕自带“点击画面即可返回”提示，但 `auto_return_main_home` 没有该分支 → 放弃 |
| `pvp_auto_battle_failed.png` | `第158赛季 / 未进入名次|胜利分1635 / X / TAB` | 战斗结算页，无任何匹配分支 |
| `map_trade_return_home_error.png` | `校园女王 / 食艾玛 / 服装 / …`，右上 `X @(1893,430)` | 非抽卡类详情层：X 位置满足区域规则，但缺 `GACHA_DETAIL_KEYWORDS` 三关键词 → `UNKNOWN` → 不点 X |

注：这些帧来自 `probe_outputs/`（本地、已 gitignore；含账号内文本，不入库）。
启用任何新分支都必须实机验证，因此列入待决策而非直接实现。

### P7 合并与实机验证

按顺序合并：

```text
P1 公共视觉模型
→ P2 页面识别
→ P6 架构守卫
→ P3/P4 BaseBD2Task/端口
→ P5 UI 收敛
```

每阶段：

```text
uv run ruff check .
uv run python -m unittest discover -s tests -p "test_*.py"
```

主 Agent 在每个合并点执行真实验证：

0. 工具：`uv run python scripts/verify_auto_return.py identify`（只识别当前页面，不点击）；
   `... return`（识别 → 自动返回主页 → 复验并打印 before/after 与信号）。
1. 主页识别。
2. 抽卡详情自动关闭并返回主页。
3. 一键日常逐模块“执行前自动返回主页”开关。
4. 至少一个完整日常子任务成功路径。

## 6. Subagent 分工

| Agent | 分支 | 写入范围 | 禁止修改 | 交付 |
|---|---|---|---|---|
| A | `refactor/vision-models` | 公共 vision 模型、相关任务 import、相关测试 | BaseBD2Task 页面识别 | commit + 测试输出 |
| B | `refactor/page-recognition` | page_recognition.py、BaseBD2Task、相关测试 | map_trade 领域模型 | commit + 测试输出 |
| C | `test/architecture-guard` | tests/test_architecture_layers.py、docs/architecture-rules.md | 业务代码 | commit + 架构扫描报告 |

## 7. 风险与回滚

- 风险：公共模型移动导致 import 破坏。回滚：保留 `map_trade.models` re-export。
- 风险：BaseBD2Task 拆分改变任务行为。回滚：保留旧方法作为兼容 wrapper。
- 风险：子 Agent 并行冲突。控制：各自独立分支和写入范围；主 Agent 按顺序合并且每次运行全量测试。
- 回滚点：`pre-refactor-20261012` tag/branch。

---

## 8. 进展与遗留（滚动更新）

### 8.1 已完成并合并到 `main`

| 工作包 | 分支 | 提交 | 主 Agent 复核结论 |
| --- | --- | --- | --- |
| P0.5 基线绿化 | 直接提交 `main` | `80000b9` / `387dd72` / `16574c6` | ruff 通过；1034 项全过（此前 15 项失败） |
| P1 公共视觉模型下沉 | `refactor/vision-models` | `2ecb360` / `ed3ed02` / `71b8cfb` | 独立 AST 审计：非领域任务 → map_trade 归零；`task_vision_mixin` 无 map_trade 依赖；`Vision` 迁移 diff 仅 4 处（docstring/import/`parents[3]→[2]`/`MAP_TRADE_REFERENCE→HD_720`，同值同对象）；ruff + 1034 项全过 |
| P2 页面识别服务 | `refactor/page-recognition` | `2314a46` / `232f536` | 逐行核对判定规则与原实现等价（精确等值、关键词子串、区域闭区间、优先级 HOME > GACHA_DETAIL > UNKNOWN）；写入范围仅 4 文件；ruff + 1056 项全过 |
| P6 架构守卫 | `test/architecture-guard` | `fc8fc32` / `bb0f363` | 我自行注入 R1/R6 违规验证守卫**非假绿**（报出规则编号+文件+行号+语句）后复原；P1 合并后删除 9 条过期例外；ruff + 1073 项全过 |
| P3 端口抽象 | `refactor/ports` | `310ed1f` / `6a7001f` / `bc4843f` / `5b02c23` | 首版因 R2 禁止 `utils → tasks` 而在 `vision_runtime` 产生镜像协议，复审判定**两份定义不可接受**，打回改为中性层 `src/ports/` 并删镜像；我独立核对 `task_vision_mixin` / `vision_runtime` 的函数体与默认值**逐条 AST 比对为 0 差异**（仅签名注解变化），并自己注入 R7 违规验证守卫非假绿；ruff + 1093 项 + 守卫 18 项全过 |
| P4 `BaseBD2Task` 拆分 | `refactor/base-task-split` | `cbbf1bc` / `7cddcf3` / `23ab2d2` | 我独立比对拆分前/后的 107 个函数 AST：`missing`/`extra` 为同名方法的归属迁移，**函数体差异 0 处**；`BaseBD2Task` 1054 → 219 行；MRO 正确（两个 mixin 在 `BaseTask` 之前、无 `__init__`、`_pvp_special_page_action` 仍为 classmethod）、mixin 无反向上依赖；ruff + 1080 项全过 |

合并后 `main` 实测：`src/**` 106 个文件；R1/R2/R3/R4/R5 = 0 违规；R6 仅 2 条在册例外（语义正确）。

### 8.2 待办与验收（P7）

| 项 | 内容 | 阻塞 |
| --- | --- | --- |
| P7 实机验收 | 主页识别 / 抽卡详情关闭返回主页 / 一键日常逐模块「执行前自动返回主页」开关 | 需要游戏客户端在跑且用户在场；已备好 `scripts/verify_auto_return.py` |
| P3 端口抽象 | **已完成**：`src/ports/` 唯一定义 + R7 守卫（`src/ports` 不得依赖包外 `src.*`） | 无 |
| P8 输入适配器收口 | **已完成**（`refactor/input-adapter`）：前景拖拽与绝对光标定位收口进 `BD2Interaction.drag_foreground` / `set_foreground_cursor`；新增守卫 R8 | 无 |
| P4 `BaseBD2Task` 拆分 | **已完成**：`device_ops_mixin`（设备操作）+ `cartridge_home_mixin`（卡带/主页业务），基类只留状态输出与生命周期 | 无 |
| P5 表达层收敛 | 核查结论：**已达成**，UI 只经 `task_info_snapshot` 读状态（见 P5 小节实测）；不新增抽象 | 无 |

### 8.3 审计发现与决策记录

**P8 复核要点（主 Agent 独立验证）**

- 逐行 diff 原始 `drag_client.action` 与新的任务层 + `BD2Interaction.drag_foreground`：
  `steps = max(6, round(duration / 0.03))`、`SetCursorPos` → `sleep(0.03)` →
  `LEFTDOWN` → `for` 分步 `SetCursorPos` + `sleep(duration / steps)` → `finally LEFTUP`
  **整块语句与顺序一致**；`operate(action, block=True, restore_cursor=True)` 与
  `sleep(after_sleep)` 逐字未变。
- `_input_lock` 是 `threading.RLock`，且 `BD2Interaction.operate` 本就持该锁，
  因此适配器内新增的 `with self._input_lock` 是**可重入的同把锁**，未引入新锁语义。
- 我自行在 `src/tasks/` 注入函数级 `import win32api` 探针，确认 R8 报出
  规则编号 + 文件 + 行号 + 语句后删除探针复原。
- 已知（且刻意保留的）唯一差异：`executor.interaction is None` 时旧实现会退化成
  裸 win32 拖拽（客户端坐标当屏幕坐标，本身是错误路径），新实现直接抛
  `AttributeError`。生产路径与测试均不覆盖该分支，按「不加兜底」保留。
- 实机未验证部分：真实 `SetCursorPos`/`mouse_event` 前景拖拽手感与光标恢复时序、
  滚轮定位在真实分辨率下的表现——需要在 P7 实机验收时顺带跑一次 PVP / 广场的拖动。

**已决策（本轮主 Agent 实测结论）**

1. **主页标签存在三处定义，两套匹配语义——有意保留，不强行统一**
   - `src/tasks/page_recognition.py::HOME_GACHA_LABEL`：全帧 OCR 标签的**精确等值**判定（供 `identify_page` 用）。
   - `src/utils/home_confirmation.py::HOME_GACHA_OCR_KEYWORD`：固定小 ROI（gacha 入口）的**子串容错**判定（供三信号门禁用）。
   - `src/tasks/FreeGachaTask.py:502` 内联了一次 `抽抽乐` 字面量（点击入口用）。
   - 理由：两者是**不同用途、不同严格度**的策略（全帧分类要求精确，小 ROI 容错是为了 OCR 只识别部分文字的实测情况）；且守卫 R2 禁止 `src/utils → src/tasks`，唯一定义只能放 utils。
   - 结论：不在本轮强行合并（会产生跨层策略倒置）；作为可选清理项记录：把字面量统一到 `src/utils/home_confirmation.py`，`page_recognition` 只引用字面量、保留自己的精确比较。
2. **输入适配器旁路**：已由 P8 派发收口（见 8.2）。

**三信号主页门禁的真实帧实测（2026-10-12，回答“是否收紧为精确匹配”）**

用真实帧 + 真实 OCR 按生产 ROI/阈值复算 `home_confirmation_passes`：

| 帧 | 左列票 | p95 亮度 | gacha ROI 文本 | 门禁 |
| --- | --- | --- | --- | --- |
| 真实主页 | 3/2 | 255/185 | `抽抽乐` | **True** ✓ |
| 抽卡详情 ×2 | 0/2 | 56/185 | `抽抽` | False（被另两信号拦下） |
| 其它失败帧 | 0/2 | 56~254 | 空/无关 | False |

结论：详情页误判风险**不是**由 gacha 子串容错造成的——左列票 0/2 与 p95 56/185
已经把门禁挡住了。反过来，真实帧上该 ROI 的 OCR 只读到 `抽抽`（部分文字），说明这条
ROI 的 OCR 并不稳定，改成精确匹配等于要求“必须读全”，会把漏判风险推给日常任务。
**推荐：保持子串容错**（页面分类的全帧精确判定已由 `identify_page` 独立承担）。

**需用户决策（有实测证据，改动涉及实机行为）**

1. **右上角「H/房子」分支是死代码**
   - 证据：`normalize_ocr_text("H") == "h"`，而 `page_recognition.HOUSE_HINT_LABEL == "H"` 且判定为严格等值 → 实测 `normalize_ocr_text("H") == HOUSE_HINT_LABEL` 为 `False`。
   - 影响：该分支从未命中；实际回主页靠「左上角返回 ROI 模板匹配」兜住（这正是你现在能正常回主页的原因）。
   - 两个选项：① 保持现状（不改行为，继续走左上角返回）；② 改为大小写无关比较，启用这条从未在实机验证过的点房子分支——**必须**配套实机验证。
2. **共享主页门禁仍是子串匹配**
   - 证据：`home_confirmation.home_gacha_ocr_matches("抽抽乐点数")` 返回 `True`。
   - 影响：`_wait_for_home_confirmation` 路径（日常/小屋/快速狩猎/广场女神像/map_trade）在固定小 ROI 上接受“包含抽抽乐”的文本；理论上抽卡详情/券详情页若在该 ROI 出现同类文本，会误判为主页。
   - 风险：改成精确匹配会让门禁**变严**，可能使现在能跑通的日常任务在实机上失败（例如 OCR 返回 “抽抽乐·” 这类多余字符）。属于游戏业务判定，**不在本重构范围内擅自修改**，需你决定 + 实机验证。


1. **右上角「H/房子」分支实机不可达（历史语义，非本轮引入）**：
   `normalize_ocr_text()` 会把标签小写化，而旧代码与字面量 `"H"` 严格比较，恒为 False。
   P2 按「不改变游戏行为」原样保留并加测试锁定，**没有**顺手改成小写比较——
   那会在实机上首次启用一条从未触发的点房子分支。若要启用，应作为独立工作包
   显式修复并实机验证。
2. **`src/compat/windows_graphics.py` 的零超时探测**：仍是「零超时=不探测」，
   属上游 `ok` 库注入钩子，已在守卫中登记为永久例外。
3. **`map_trade/vision.py` 现为 re-export 薄壳**：方向 `tasks → utils` 合法，
   领域侧 17 处导入可后续机械替换为 `src.utils.vision_runtime`，本轮未做以控制 diff。
