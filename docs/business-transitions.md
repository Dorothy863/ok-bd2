# P9：业务动作状态迁移与后置条件验证

## 目标

业务层不能把“动作已执行”当成“目标状态已到达”。每一步必须显式声明：

```text
前置状态 → 执行动作 → 等待预期后置状态 → 验证 → 下一步
```

实现位于 `src/tasks/business_transition.py`，任务统一通过
`BaseBD2Task.verified_transition()` 调用。

## 契约

`TransitionSpec` 包含：

- `name`：业务动作名，用于状态面板与失败诊断；
- `precondition_states`：可选前置状态集合；不满足时不执行动作；
- `expected_states`：动作后允许的后置状态集合；
- `timeout_seconds`：每次动作后的观察窗口；
- `interval_seconds`：状态采样间隔；
- `max_action_attempts`：动作有界重试次数。

`TransitionResult` 保留：

- 前置状态与实际观测状态；
- 动作派发次数；
- 完整状态观测轨迹；
- 耗时与异常信息；
- `ok` 与可直接写入状态面板的 `summary`。

## 强制规则

1. 动作派发后，未观察到 `expected_states` 不得执行下一步。
2. 重试必须有界，并重新验证同一后置条件。
3. 页面状态使用集合而非单一脆弱目标，例如抽卡确认后允许结果页或物品详情页。
4. 后置状态不匹配时必须保留实际状态/OCR，不能只报告“失败”。
5. 恢复动作属于业务决策，不放进通用迁移器；迁移器只判断“是否到达预期状态”。
6. 禁止用全局盲点或无限等待掩盖未验证的转场。

## 已迁移

| 业务动作 | 期望后置状态 | 位置 |
| --- | --- | --- |
| 一键收菜入口 | 经营管理弹窗 `dialog` | `DailyTask.run_business_collect` |
| 抽卡结果连续跳过 | 抽抽乐券详情 `ticket_detail` | `FreeGachaTask._click_skip_until_back_page` |
| 关闭券详情并返回 | 抽卡页 `gacha_page` | `FreeGachaTask._handle_result_until_back` |
| 点击 PVP 舞台 | 自动战斗入口 `launch_button` 或设置菜单 `menu` | `PVPTask._start_auto_battle` |
| 点击自动战斗入口 | 设置菜单 `menu` | `PVPTask._start_auto_battle` |

## 待迁移

- `SquareGoddessTask`：最近卡带 → 快速切换页 → 生活卡带 → 梦幻广场。
- `DailyTask`：公会、小屋入口的重复点击逻辑。
- `auto_return_main_home`：目前已有手写状态循环，后续可收敛到同一迁移契约。
- `MapTradeTask` / `MapCollectionTask`：长途导航中的点击与页面确认。

迁移顺序应按真实失败频率决定，不为了形式统一一次性重写全部任务。

## 验证要求

- `tests/test_business_transition.py` 覆盖成功、前置不满足、重试、动作未派发、
  观测异常、超时和证据字段。
- 被迁移业务保留原有定向回归。
- 验收仍以实机流程为准；单测只证明契约和回归护栏。

## 实机验证（2026-10-28）

迁移后的完整「一键完成日常」实机通过：

- 公会、小屋、酒馆：完成
- 快速狩猎：完成
- 免费抽抽乐：完成
- 广场女神像：完成
- 自动 PVP：完成
- 跑商：按配置跳过
- 收尾返回主页：已回到主页
- 最终状态：`一键完成日常完成。已执行 5 项，跳过 1 项。`

说明：免费抽与 PVP 当天已无可执行次数时，只验证了“正常结束”路径；
本轮迁移前已分别实机验证过抽卡结果详情和 PVP 舞台/自动战斗菜单动作。
后续仍应在下一次可抽卡/有 AP 时复验迁移后的成功动作链。
