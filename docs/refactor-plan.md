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

端口 Ports
  CapturePort / OcrPort / TemplatePort / InputPort / ProgressPort

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
基础工具：vision_models / image_utils / ocr_utils / template_resolution
组合工具：task_vision / home_confirmation / cartridge_quick_switch
```

禁止组合工具被基础工具反向导入，禁止循环依赖。

## 5. 阶段与工作包

### P0 基线保护

- 创建 `pre-refactor-20261012` branch/tag。
- 每个工作包独立分支、独立提交。
- 主 Agent 合并前运行全量测试与架构守卫。

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
src/tasks/ports.py
src/tasks/BaseBD2Task.py
tests/test_ports.py
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
