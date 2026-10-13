# 待决事项与能力缺口（Issues）

> 记录时间：2026-10-12 ｜ 仓库：https://github.com/Dorothy863/ok-bd2 ｜ 分支：`main`
>
> 背景：本轮「分层 + 端口」架构重构（`docs/refactor-plan.md`、`REFACTOR_LOG.md` 阶段七）
> 已完成并通过代码级验证（1102 项测试、架构守卫 R1–R8）。本文记录**验收期间**通过
> 「录制真实帧回放」（`scripts/replay_frames.py`：真 OCR + 真 `identify_page` /
> `auto_return_main_home`，仅截图/点击用替身）与依赖审计发现的遗留问题。
>
> 除特别说明外，每条的「证据」都可在本机复现；含账号内文本的帧留在本地
> `probe_outputs/`（已 gitignore），本文只引用其中的界面文案。

## 总览

| 编号 | 标题 | 类型 | 推荐 | 所需 |
| --- | --- | --- | --- | --- |
| ISSUE-1 | 右上角「H/房子」分支恒不命中（死代码） | Bug（规格未实现） | 修 | 实机验证 |
| ISSUE-2 | 共享主页门禁的 gacha 文本为子串匹配 | 设计取舍 | **保持** | — |
| ISSUE-3a | 缺「点击画面即可返回」场景分支 | 能力缺口 | 补 | 实机验证 |
| ISSUE-3b | 缺「非抽卡类详情层」关闭分支 | 能力缺口 | 补 | 实机验证 |
| ISSUE-3c | 缺「PVP 战斗结算页」分支 | 能力缺口 | **暂缓** | 更多真实帧 |
| ISSUE-4 | `map_trade/vision.py` 仍是 re-export 壳 | 清理 | **保留** | — |
| ISSUE-5 | P8 前景拖拽/光标定位未实机验证 | 验证缺口 | 下次实机跑 | 实机（当前被 LIVE-2 挡住） |
| LIVE-1 | 快速狩猎模块无法完成（**非重构引入**，A/B 已证） | 既有缺陷 | 单独排查 | 实机 + 排查 |
| LIVE-2 | 赛季奖励页卡死 广场/PVP/日常（= ISSUE-3a 实机复现） | 能力缺口 | 补分支 | 需先确认触发点 |
| LIVE-3 | 加载页无分支导致一键日常中止 | 健壮性缺口 | 加加载等待 | 实机 |
| LIVE-4 | 一键日常双击启动器 `ModuleNotFoundError` | Bug | **已修**（`493540b`） | — |
| ISSUE-6 | P7 实机验收项 3/4 未完成 | 验证缺口 | 待实机 | 实机 |
| ISSUE-7 | `force_activate` 死分支 | 死代码 | 删或忽略 | — |

---

## ISSUE-1 右上角「H/房子」分支恒不命中

**现象**：`auto_return_main_home` 里「检测到右上角 H 提示就点小房子」这条分支从未生效。

**证据**（本机实测）：

```text
normalize_ocr_text("H") == "h"
HOUSE_HINT_LABEL == "H"        # page_recognition.py
=> normalize_ocr_text("H") == HOUSE_HINT_LABEL  →  False（恒不命中）
```

**位置**：`src/tasks/page_recognition.py` 的 `HOUSE_HINT_LABEL` 与 `build_page_signals`
中的严格等值比较（判定区域仍是右上角 `cx/w > 0.7 且 cy/h < 0.25`）。

**影响**：最初规格「有 H 图标就点小房子，没有就点左上角返回」里的前半句实际未实现；
回主页依赖「抽卡详情关闭 X」或「左上角返回 ROI 模板匹配」。

**推荐**：改为大小写无关比较（`normalized_label == "h"`），**只改比较、不动坐标**
（点击仍用配置里的 `返回主页按钮 X/Y 百分比`，即 2560×1440 的约 (2400,85)，该坐标已在
餐厅实测过）。理由：这是规格要求的分支，且「点小房子」比「点左上角返回」语义更安全
（左上角按钮在主页里是「返回战场」，误点会进战斗）。

**风险与附带发现**：真实主页帧的 OCR 结果里**没有**出现 `H` 标签，所以修好后是否真的会
触发仍需实机确认；若实机出现误点（区域内杂散 `h`），应把判定区域收紧到图标实际位置
（收紧，不是加兜底）。

**验收方式**：实机停在餐厅页 → 跑 `uv run python scripts/verify_auto_return.py identify`
确认 `house_hint` 是否被识别；再跑 `... return` 确认点击行为。

---

## ISSUE-2 共享主页门禁的 gacha 文本为子串匹配

**现象**：`src/utils/home_confirmation.py::home_gacha_ocr_matches` 用子串判定，
实测 `home_gacha_ocr_matches("抽抽乐点数") is True`；而 `page_recognition`（页面分类）
用精确等值。同一概念存在两套严格度。

**证据**（真实帧 + 真实 OCR，按生产 ROI/阈值复算 `home_confirmation_passes`）：

| 帧 | 左列票 | p95 亮度 | gacha ROI 文本 | 门禁结果 |
| --- | --- | --- | --- | --- |
| 真实主页帧 | 3/2 | 255/185 | `抽抽乐` | **True** ✓ |
| 抽卡详情帧 ×2 | 0/2 | 56/185 | `抽抽` | False |

**结论**：详情页没有被误判为主页，靠的是**左列关键词票 0/2 与亮度 56/185**，
与 gacha 文本是否子串匹配无关。反过来，真实帧上该 ROI 的 OCR 只读到 `抽抽`（半个词），
说明这条 ROI 的 OCR 并不稳定；改成精确匹配等于要求「必须读全」，会把**漏判**风险推给
日常任务。

**推荐**：**保持现状**。页面分类的全帧精确判定已由 `identify_page` 独立承担，
两者用途不同，不属于重复实现。仅当实机出现新的误判证据时再改。

---

## ISSUE-3 自动返回主页缺少真实场景分支

三张帧都是探针**因为流程失败**才存下来的（`probe_outputs/`，本地）。决策回放
（`scripts/replay_frames.py --replay`）确认：这三种屏幕下 `auto_return_main_home`
没有任何可用分支，直接告警返回 False。

### ISSUE-3a 赛季奖励页

- 帧：`cartridge_quick_switch_failed.png`（2560×1440）
- OCR 界面文案：`赛季奖励 / 白金I / 积分1745 / 点击画面即可返回。`
- 现象：回放输出 `自动返回主页：当前非主页且未找到 H/房子、详情关闭或左上角返回按钮，无法继续。`
- **推荐：补**。正向证据为屏幕自带的提示文本「点击画面即可返回。」——检测到该提示时
  点击提示框中心（用 OCR 框坐标，避免固定坐标假设）。

### ISSUE-3b 非抽卡类详情层

- 帧：`map_trade_return_home_error.png`（2560×1440）
- OCR 界面文案：`付费 / 免费 / X / 服装 / 消耗的SP 3个 / 冷却时间7回合 / …`（含角色与技能说明文本）
- 现象：右上 `X` 位于 (1893,430)，与抽卡详情层的关闭按钮**位置完全一致**，但页面判定
  要求 `GACHA_DETAIL_KEYWORDS` 三关键词（`抽抽乐点数`/`抽1次`/`抽10次`）全部命中，
  此处不满足 → `PageId.UNKNOWN` → 不点 X → 放弃。
- **推荐：补**。正向证据改为「右上关闭区检测到 `X` **且**顶栏同时出现 `付费` 与 `免费`」
  （3 张真实帧共享该特征：2 张抽卡详情 + 本帧服装详情）。仍要求正向证据，不是无条件点 X。

### ISSUE-3c PVP 战斗结算页

- 帧：`pvp_auto_battle_failed.png`（2560×1440）
- OCR 界面文案：`第158赛季 / 未进入名次|胜利分1635 / X / …`
- **推荐：暂缓**。目前只有一帧，且正确动作未知（点 X？等待结算？点确认？）。
  凭一帧凑规则等于瞎点，风险大于收益。下次实机遇到时多抓几帧再定。

**验收方式**：先用 `scripts/replay_frames.py` 在真实帧上确认新规则命中，再由使用者在
客户端跑一次一键日常确认没有误点。

---

## ISSUE-4 `map_trade/vision.py` 仍是 re-export 壳

**现状**：`src/tasks/map_trade/vision.py` 只 re-export `src/utils/vision_runtime.py` 的
`Vision` / `normalize_text` / `parse_used_limit`；仍有 17 个领域模块与 6 个测试文件从旧路径导入。

**推荐：保留**。原因：

1. 它不违反任何分层规则（`tasks → utils` 是允许方向；守卫 R4 只约束 `task_vision_mixin`）。
2. 重构要拿到的收益已经拿到：公共实现落在 `src/utils/vision_runtime.py`，
   公共层不再反向依赖领域包（守卫 R3/R4 违规数为 0），领域内部如何引用不再影响分层。
3. 删除需要改 23 个文件的 import，属纯 churn，且会让旧模块路径（含上游历史引用）失效。

若确实希望「没有别名模块」，可作为独立清理提交，与业务改动分开。

---

## ISSUE-5 P8 前景拖拽/光标定位未实机验证

**现状**：P8 把前景拖拽与绝对光标定位从任务层收口进
`src/interaction/BD2Interaction.py`（`drag_foreground` / `set_foreground_cursor`），
已用「逐行 diff 确认搬迁语句与顺序一致」「`_input_lock` 为 RLock 且 `operate` 已持锁」
两张方式验证等价性，但真实 `SetCursorPos` / `mouse_event` 在游戏内的表现、光标恢复时序、
滚轮定位在真实分辨率下的表现**未实机验证**（单测是 mock）。

**推荐**：下次实机验收时顺带跑一次 PVP 与广场女神像的拖动路径（两处都调用 `drag_client`）。

---

## ISSUE-6 P7 实机验收项 3/4 未完成

计划 P7 的四项实机验收：主页识别、抽卡详情关闭返回主页、一键日常逐模块
「执行前自动返回主页」开关、至少一个完整日常子任务成功路径。

- 第 1、2 项已有**离线真实帧证据**（见 `REFACTOR_LOG.md` 7.10）：
  真实主页帧 → `home` 且决策回放 True 零点击；真实抽卡详情帧 → 点
  `自动返回主页-抽卡详情关闭 @(1893,430)`。
- 第 3 项已有回归测试锁定（`test_auto_return_home_before_child_can_be_enabled/disabled`），
  但未在实机复跑。
- 第 4 项必须实机执行。

**推荐**：客户端在跑时执行 `scripts/verify_auto_return.py identify|return` 与一次
一键日常，作为本轮的实机验收记录。

---

## ISSUE-7 `force_activate` 死分支

**证据**：全仓库（`src/`）与依赖包 `ok` 内均无 `force_activate` 定义，
只有 `src/tasks/device_ops_mixin.py` 中 4 处 `hasattr(interaction, "force_activate")` 判断：

```text
src/tasks/device_ops_mixin.py:246  if interaction is not None and hasattr(interaction, "force_activate"):
src/tasks/device_ops_mixin.py:247      interaction.force_activate()
src/tasks/device_ops_mixin.py:289  if hasattr(interaction, "force_activate"):
src/tasks/device_ops_mixin.py:290      interaction.force_activate()
```

**影响**：分支恒为假，实际总是走 `try_activate`（`drag_client` 与 `scroll_client` 各一处）。

**推荐**：删除这 4 行，或在注释里标明「保留以兼容未来的输入实现」。属低优先级死代码，
不影响行为；因为改动落在已验收的输入路径上，建议与实机验证一起做。

---

---

# 实机验证记录（2026-10-13，客户端在线）

## 已通过项

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 主页识别 | ✓ | 真实主页帧 `identify` → `home`（`home_label_seen=True`）；标题页（`TOUCH TO START`）→ `unknown`，无误判 |
| 非主页自动返回主页 | ✓ | 小屋房间编辑页 → `ROI 命中左上角返回 @(145,66)` → 点击后 `after = home`，`auto_return_main_home() -> True`。**该坐标与离线回放在同一张屏上的预测一致** |
| 已是主页时零点击 | ✓ | `auto_return_main_home() -> True`，`info[自动返回主页] = 已是主页（第1步）` |
| 逐模块「执行前自动返回主页」 | ✓ | 一键日常日志中每个子任务前均有 `自动返回主页 已是主页（第1步）` |
| 完整日常子任务 | ✓ | 一键日常第二趟：`完成 ['公会签到','小屋签到','一键收菜']`，`失败 []`；含主页临时公告亮度不足时的清理分支（亮度 55→253 后通过） |

## 新增问题（实机发现，均已用重构前备份 c37d4ee 做 A/B 对照）

### LIVE-1 快速狩猎模块无法完成（**非本次重构引入**）

- 现象（当前 `main`）：进入狩猎场菜单、识别米饭 90/90、点击 (2216,1320) 后，
  在计数区找不到 OCR 目标 `^MIN$`，8 秒超时 → `未找到可点击 OCR 目标：狩猎场-MIN`
  → 模块失败 → 一键日常中止（后续 抽抽乐/广场/PVP 全部没跑）。
- A/B 对照：同一时间用重构前备份 `c37d4ee` 跑同一套一键日常，**快速狩猎同样失败**
  （失败步骤因瞬时状态不同：先卡在主页确认 0/2、p95=46 的暗屏，随后确认主页但仍在
  `快速狩猎结果 失败`）。
- 结论：**与本次分层/端口重构无关**，是既有问题（可能与本季 UI/配置有关），需单独排查。
- 影响：一键日常目前跑到第二个模块就中止，后面 3 个模块永远轮不到。

### LIVE-2 ISSUE-3a 实机复现（赛季奖励页卡死）

- 现象：`广场女神像` 任务进入「最近卡带特殊页面」处理分支后，
  OCR 反复读到 `赛季奖励 / 积分1651 / 白金 / 点击画面即可返回。`（约 10 次），
  无任何可用动作 → `未能从主页打开卡带快速切换页面` → `未能进入梦幻广场`。
- 关键证据：任务把该失败帧保存为 `probe_outputs/cartridge_quick_switch_failed.png`
  —— **正是离线分析用的那一帧**，离线结论得到实机确证。
- 影响：该页会同时挡住 广场女神像 / 自动PVP / 一键日常，且是反复出现的（10-12 与 10-13
  两次会话都遇到）。
- 附带实测：我在该页上点击画面中心一次**没有**把它消掉（`identify` 仍为 `unknown`），
  所以 ISSUE-3a 的修复方案不能简单写成“点屏幕中心”，需要先确认真正的触发点。

### LIVE-3 加载页没有分支，会让一键日常直接中止

- 现象：第一趟一键日常在 19:45:03 与 19:45:42~47 两次遇到**只显示 `BROWN DUST I` logo
  的加载屏**；`identify_page` 判 `unknown` → 自动返回主页四个分支都不命中 →
  `自动返回主页未成功，跳过执行该子任务` → 整个一键日常中止。
- 分析：加载屏没有左上角返回按钮、没有 H 提示，所以现有分支天然覆盖不到；
  而 `task_vision_mixin._wait_loading_or_template` 那套「等 loading 消失」的逻辑
  并不在 `auto_return_main_home` 里。
- 结论：属「逐模块执行前自动返回主页」这一新门的**健壮性缺口**（同一份代码在
  19:47 那趟同页面上顺利跑完，说明是瞬时状态触发的），建议给 `auto_return_main_home`
  加“加载屏等待”，或让模块自身的等待逻辑承担这段时间。
- 需实机验证，故未直接改。

### LIVE-4（已修复）一键日常双击启动器一直是坏的

- `scripts/run_daily.py` 是全部 runner 中唯一漏了 `sys.path` 兜底的，
  而 `run_daily.bat`（双击启动器）正是调它 → 直接 `ModuleNotFoundError: No module named 'src'`，
  启动器只会打印“执行失败，退出码：1”。
- 已修：补 `sys.path.insert(0, repo_root)`，与其它 runner 一致（提交 `493540b`）。
- 验证：修复后 `uv run python scripts/run_daily.py` 正常启动并跑完模块一。

## 仍未完成的实机项

- **P8 前景拖拽的真实生效**（ISSUE-5）：需要进入 广场女神像 / PVP 的拖动场景，
  但这两个流程当前都被 LIVE-2 的赛季奖励页挡住，因此本轮未取到证据。
  待该页可消掉后，跑 `scripts/run_square_goddess.py` 或 自动PVP 补验。

---

## 附：本轮已完成且无待办的项

- 分层与端口：`src/ports`（唯一端口定义）+ 守卫 R1–R8 全过，违规数为 0。
- `BaseBD2Task` 1054 → 219 行（`device_ops_mixin` + `cartridge_home_mixin`），
  107 个函数 AST 比对函数体差异 0 处。
- 零等待轮询缺陷（Python 3.14 高精度时钟暴露）已修，统一走 `PollDeadline`。
- 输入适配器旁路已收口（任务/工具层不再直接 import `win32api`）。
- 梦幻广场小卡面 OCR 已按第三轮 review 回退为固定第二格流程。
