---
name: tune-dcqcn
description: 根据拓扑、固定流量和算法选择，为本仓库 CC_MODE=1 生成有预算、可复现的 DCQCN 参数计划；用于离线参数优化，不承担新协议设计或硬件调参。
---

# DCQCN 参数规划

读取 [公共契约](../../../interfaces/contracts.md) 中的 `TaskSpec`、`AlgorithmDecision`、`ParameterPlan`，以及 [ns-3 接口](../../../interfaces/ns3-interface.md)。本技能接收前两者，输出 `ParameterPlan`；执行与固定评价由接口层负责。

## 机制与适用范围

`CC_MODE=1` 使用拥塞反馈更新 alpha，再乘性降速；拥塞消退后按快速恢复、普通增速和快速增速阶段恢复速率。参数影响响应时间、恢复速度及振荡，不能只用一个平均 FCT 判断优劣。

本分支的周期更新为 `alpha ← (1-g) × alpha + g × I`，I 表示该周期是否收到拥塞反馈；
降速检查收到反馈时使用 `rate ← max(min_rate, rate × (1-alpha/2))`。
`RP_TIMER` 驱动恢复阶段，`RATE_AI/HAI` 改变目标速率的增量。更快恢复可能缩短空闲，也可能再次推高队列；
用反馈、速率和完成时间共同验证这一假设。alpha 周期与 g 共同影响平滑时间尺度，首轮固定周期。

当前模拟器在 ACK/NACK 中携带拥塞标志，并可能因 ECN 或乱序产生该标志；独立 `ReceiveCnp()` 路径会退出。现有 CNP 计数不能直接当作真实 RoCE 独立 CNP 报文模型或硬件证据。

以 `AlgorithmDecision.cc.name/code` 确认 DCQCN/1，沿用其 `lb`、`reliability` 和 `TaskSpec.fixed`。`unsupported` 不进入可执行候选；`planned/needs_validation` 保留待验证说明。参数优化保持算法公式、流量、拓扑、LB、PFC/IRN、MTU、ACK、缓冲和测量、排空、截止条件不变。

## 四个首版参数

| 原生配置键 | 作用 | 当前 100Gbps 基线 | 建议初筛值 |
|---|---|---|---|
| `EWMA_GAIN` | alpha 平滑增益 g | `0.00390625`，即 1/256 | 1/512、1/256、1/128 |
| `RP_TIMER` | 恢复/增速计时器，μs | `300` | 150、300、600 |
| `RATE_AI` | 普通增速步长 | `40Mb/s` | `20Mb/s`、`40Mb/s`、`80Mb/s` |
| `RATE_HAI` | 快速增速步长 | `100Mb/s` | `50Mb/s`、`100Mb/s`、`200Mb/s` |

四项均已由文本配置传到 CC1 的实际更新逻辑。表中数值是当前 100Gbps 配置的起点，**不是已测最优值或跨拓扑推荐**。异构速率、不同 RTT 或不同流量必须重新核对适用域，不能直接套用表格。

`RateOnFirstCnp` 虽然是 `RdmaHw` attribute，目前没有接入文本配置；不要放进已支持搜索空间。`U_TARGET`、`MULTI_RATE`、`DCTCP_RATE_AI` 不作为 CC1 的有效搜索维度。

## 生成计划

1. 提取拓扑速率/RTT/过订阅、流量大小/突发/fan-in、目标和预算，检查是否存在真正的拥塞场景。缺少信息时写明假设或交回上层补充，不能将未知能力记为已支持。
2. 保留输入基线。仅当使用本仓库当前 100Gbps profile 时，可采用上表起点；`baseline` 是原生键值对象，例如：

   ```json
   {
     "EWMA_GAIN": 0.00390625,
     "RP_TIMER": 300,
     "RATE_AI": "40Mb/s",
     "RATE_HAI": "100Mb/s"
   }
   ```

3. 从 `TaskSpec.tunable` 选择允许变化的四项子集，生成 `search_space`，键为上述原生键，值为有限候选列表。保留基线候选；3×3×3×3 是可能组合，不要求穷举81组。按预算先筛少量组合，再局部细化。
4. `fixed` 记录其余原生键及值。当前 profile 可参考 `ALPHA_RESUME_INTERVAL=1`、`RATE_DECREASE_INTERVAL=4`、`FAST_RECOVERY_TIMES=1`、`CLAMP_TARGET_RATE=0`、`MIN_RATE=100Mb/s`、`RATE_BOUND=1`、`HAS_WIN=0`、`VAR_WIN=0`；以任务实际冻结值为准。不能通过关闭速率限制、扩大缓冲或更改流量取得虚假收益。
5. 检查 `0<g≤1`、恢复间隔为正、速率步长合法且有界；未搜索项也要固定。使用相同流量文件及 hash 比较候选，分别记录仿真种子和未参与选参的验证种子。
6. 输出契约要求的字段：`schema_version`、`task_id`、`baseline`、`search_space`、`fixed`、`candidate_budget`、`simulation_seeds`、`validation_seeds`、`rationale`、`evidence_required`。`rationale` 解释适用假设、候选方向及潜在副作用，不预写提升结论。

`candidate_budget` 按公共契约计数，不能把种子重复和最终复验当作免费实验；完整仿真调用量须满足 `TaskSpec.budget`。若预算不足，缩小候选空间。搜索种子与验证种子分开，最终复验前冻结选定参数。

## 必要证据与评价

在 `evidence_required` 中至少要求：

- 输入和完成流身份/大小对账、完成 payload、未完成流、退出/超时状态；完整性不通过不进入性能排名。
- 基线与候选使用同一拓扑/流量、配对种子、测量窗口及 drain；保留源码/二进制、配置和输入 hash。
- 流量规模足够时报告 FCT/slowdown 尾部和重复种子的波动，同时记录有效吞吐与任务完成时间；小样本不夸大 P99。
- 确认拥塞确实触发的反馈与速率变化证据。真实 MMU 队列、PFC 实际暂停和丢包/重传可逐步补充；当前未采到的指标标记缺失，不填零。若某项是用户的硬约束，补齐观测后才能判断达标。
- 留出种子复验、最好可行配置及失败/退化案例；预算耗尽只说明已测范围，不宣称全参数空间无解。

当前 `native_smoke.py` 只证明固定16流的原生运行、完成性和路径流量；它没有证明拥塞控制已充分触发，不能据此选出最优参数、验证拥塞收益或晋升为硬件验证。

## 后续 ECN 联合调优

仅在任务允许时，再将 `KMIN_MAP`、`KMAX_MAP`、`PMAX_MAP` 纳入独立阶段。每种实际链路速率都要有映射；K 输入为十进制 kB，`ConfigEcn()` 乘 **1000** 转字节，`BUFFER_SIZE` 则乘 `1024×1024`。统一单位后再渲染原生配置。

ECN读取出口共享队列，PFC依据入口共享池/headroom，不能拿两个数字直接比较作安全证明。固定 ECN 搜 CC、固定 CC 搜 ECN，再以小预算复验组合；组合结果必须重新取得证据。
