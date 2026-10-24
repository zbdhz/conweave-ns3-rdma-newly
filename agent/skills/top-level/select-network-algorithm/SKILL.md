---
name: select-network-algorithm
description: 根据 RDMA 任务约束和当前 ns-3 后端能力选择拥塞控制、负载均衡及可靠性组合，并路由到底层机制 skill。
---

# 分析并选择网络算法

输入 [TaskSpec](../../../interfaces/contracts.md)，输出同一契约中的 AlgorithmDecision。
选择依据依次是用户固定项、后端能力、目标所需信号、已有实验结果和验证成本。
对尚无实测证据的效果给出假设与试验，不把源码中存在的算法列为已经验证的方案。

## 当前选择边界

先读 [ns-3 接口](../../../interfaces/ns3-interface.md) 的能力和证据范围，再使用下表。

| 组合 | 当前证据 | v0.1 用途 |
| --- | --- | --- |
| DCQCN（1）＋ECMP（0），PFC 开启 | 本机固定流量 smoke 已通过 | 参数调优首个基线 |
| DCQCN（1）＋ConWeave（9），PFC 开启 | 本机固定流量 smoke 已通过；重排及拥塞重路由未验证 | 用户研究多路径时的后续候选 |
| HPCC（3）／TIMELY（7）／DCTCP（8） | 源码存在；目标组合和场景需要验证 | 需求需要时单独评估 |
| 非 DCQCN＋ConWeave | 当前 C++ 显式拒绝 | unsupported；需机制开发才能扩展 |
| IRN 路线 | 源码存在，当前本机 smoke 未覆盖 | 另行验证可靠性，不能默认继承 PFC 结果 |

ECMP 在原 run.py 中名为 fecmp，原生 LB_MODE 同为 0。依据源码映射填写 code，
不要将这些数字视为其他 ns-3 后端的统一枚举。验证能力时同时检查目标拓扑和流量，
已有 smoke 不代表任意场景通过。

## 选择过程

1. 保留 TaskSpec.fixed 中的显式选择。用户要求 DCQCN 参数调优时，选择 DCQCN 底层 skill；
   用其他算法作对照可以提出，但不能静默改变优化对象。
2. 参数模式优先固定 LB、可靠性和缓冲；若瓶颈可能来自路径碰撞、接收端容量或链路故障，
   解释 DCQCN 调参的作用范围，并列出能区分原因的实验。
3. 需要更换机制时，结合信号条件评估候选：例如 HPCC 需要其遥测路径，TIMELY 需要 RTT 反馈，
   ConWeave 涉及路径选择和目的 ToR 重排；检查本分支具体实现及组合限制。
4. 选择一个可复现基线，给出适用原因和有必要的替代项。候选尚未在目标场景验证时，
   使用 needs_validation，并指定下一步最小验证。

不要为了优化指标而修改用户规定的业务量、目标定义或可靠性。若目标无法满足，
交付当前能力缺口和可选后续范围，不把参数搜索自动升级为协议改写。

## 输出与交接

AlgorithmDecision 中 cc/lb 使用 name 和 code，reliability 使用 pfc/irn 布尔值；
选择 DCQCN 时 skill 指向 `agent/skills/bottom-level/tune-dcqcn/SKILL.md`，缺少对应 skill 时填 null 并说明缺口。证据注明 source、smoke 或 performance，
将真实路径和能够支持的结论一并记录。源码存在但没有对应 skill 的算法应列出待补实现，不能调用虚构 skill。

首版 DCQCN 决策交给 [DCQCN skill](../../bottom-level/tune-dcqcn/SKILL.md) 生成 ParameterPlan。
机制选择由需求和证据解释；性能优劣交由同场景、同预算的后续实验判断。
