# Agent 数据契约 v0.1

本文件约定 skill 之间传递的数据。当前采用 JSON 文档，尚未提供 JSON Schema 校验器或运行 SDK。
`schema_version` 固定为字符串 `"0.1"`；`task_id` 关联同一任务的全部产物。路径默认相对仓库根目录，
执行器应在创建实验目录前解析为绝对路径。时间、大小、速率注明单位；未知值使用 `null`，同时说明原因。

## TaskSpec：需求分析输出

| 字段 | 类型与含义 |
| --- | --- |
| `schema_version`, `task_id` | 字符串；契约版本与任务标识 |
| `mode` | v0.1 为 `parameter_only`；机制开发另立任务 |
| `request` | 用户原始需求，保留便于核对 |
| `topology` | 对象：`name`、`file`；未知时可为 null，不凭空生成既有文件 |
| `workload` | 对象：`pattern`、`flow_file`、`workload_seed`、`generator`；已有流量优先指定文件，待生成则文件为 null |
| `fixed` | 不允许本轮搜索改变的原生配置键及值；未选择的算法字段可暂缺 |
| `tunable` | 允许搜索的原生配置键数组 |
| `objective` | `metric`、`direction`（minimize/maximize）、`flow_scope`；阶段内固定口径 |
| `constraints` | 完整性、工作量一致性和用户指定的性能边界；区分硬约束和偏好 |
| `budget` | `max_candidates`、`timeout_s_per_trial`；其他预算按需补充，未指定的默认值写入 assumptions |
| `reproducibility` | `simulation_seeds`、`validation_seeds`、`flow_hash`、`source_revision`；运行前补齐实际快照 |
| `assumptions` | 对象数组，每项记录 `field`、`value`、`reason`；明确哪些来自默认建议 |
| `open_questions` | 对象数组，每项记录 `field`、`question`、`blocks_execution`；不确定性显式保留 |

`workload.generator` 是待生成流量的描述，不是已实现的函数。incast 示例定义发送主机、接收主机、
优先级组、每轮每发送端的流大小、轮数和到达时间。对应配置文件生成器仍待开发。
先确定实际输入流集合，再计算预期流数与字节数；更换参数不能悄悄修改它们。

`constraints` 推荐结构：`integrity` 保存 `completion_ratio: 1.0`、`same_workload: true`；
`performance` 保存用户约定的指标边界数组；空数组表示尚无额外硬性能限制。
`objective.flow_scope` 首版可用 `all_input_flows`；若使用短流子集，应写出固定大小界限，
其余流仍参与完整性对账。

示例：[dcqcn-incast-task.json](../examples/dcqcn-incast-task.json)。

## AlgorithmDecision：算法选择输出

| 字段 | 类型与含义 |
| --- | --- |
| `schema_version`, `task_id` | 与 TaskSpec 一致 |
| `cc`, `lb` | 对象：`name`、`code`；代码必须使用当前后端映射 |
| `reliability` | 对象：`pfc`、`irn` 布尔值 |
| `skill` | 项目内所选底层 `SKILL.md` 路径；未提供对应 skill 时为 null，并在 next_action 说明缺口 |
| `status` | `planned` / `supported` / `needs_validation` / `unsupported` |
| `rationale` | 选择理由数组，关联需求或观测 |
| `alternatives` | 替代机制及未选原因数组；不要求穷举 |
| `required_capabilities` | 需要的信号、可靠性或拓扑能力数组 |
| `evidence` | 证据对象数组：`kind`、`path`、`claim`；区分 source、smoke、performance |
| `next_action` | 下一步验证或参数规划动作 |

`supported` 表示所列证据范围内可用，不等于该算法在目标业务上最优；目标拓扑/流量尚未验证时，
通常选择 `needs_validation`。源代码有枚举值只构成 source 证据。
选择结果必须满足 TaskSpec 的 fixed；修改约束需生成新 TaskSpec，不能静默覆盖。

## ParameterPlan：底层算法 skill 输出

| 字段 | 类型与含义 |
| --- | --- |
| `schema_version`, `task_id` | 与 TaskSpec 一致 |
| `baseline` | 原生配置键到基线值的对象，例如 `RATE_AI: "40Mb/s"` |
| `search_space` | 原生配置键到候选值数组的对象；只包含 TaskSpec.tunable 允许的字段 |
| `fixed` | 本轮保持不变的原生配置对象，与 TaskSpec.fixed 一致 |
| `candidate_budget` | 最多提出的候选参数组数，不含基线；实际进程次数还需乘 seed 数并计入复验 |
| `simulation_seeds`, `validation_seeds` | 调参与独立复验使用的网络种子数组；两组不重叠 |
| `rationale` | 参数选择、预期影响及可推翻假设的证据数组 |
| `evidence_required` | 执行和评估前需要的输入、遥测及完整性证据数组 |

这份计划不必包含所有 ns-3 配置项。适配器需要以一个冻结、记录来源的完整配置模板为基础，
合并 fixed 和候选参数，输出完整 `config.txt`。已有 smoke 模板可作为起点，但不是通用渲染接口。
同一搜索中的基线和候选复用相同模板、流量文件及配对 seed；未列入 search_space 的字段不得随候选变化。
复验 seed 用于网络随机性复验；若要验证新流量或新拓扑，需要另外冻结对应 TaskSpec。

## TrialResult：拟实现的适配器输出

此结构尚不是 `native_smoke.py` 的输出格式；smoke 的 `summary.json` 是现有独立格式。

| 字段 | 类型与含义 |
| --- | --- |
| `schema_version`, `task_id`, `trial_id` | 契约、任务、单次运行标识 |
| `status` | success / invalid_config / build_failed / run_failed / timed_out / incomplete / unsupported |
| `config` | 实际生效的完整配置、种子、输入哈希、源码/补丁及二进制与共享库快照信息 |
| `artifacts` | 实验目录、输入、配置、stdout/stderr、FCT、监控文件路径 |
| `integrity` | 预期/完成流数与字节数、QP 重复/缺失检查、完整性是否通过 |
| `metrics` | 指标对象，单位体现在名称中；未测得的值为 null |
| `diagnostics` | 对象：`feasible`、`observations`、`limitations`；feasible 为 true/false/null |
| `error` | 失败时的原因对象：`stage`、`message`、`exit_code`；无错误为 null |

`success` 需要进程成功且完整性检查通过；性能未达目标仍可为 success，但 `feasible` 为 false。
进程成功退出而流未完成使用 incomplete；超时保留已有文件，但不把残存已完成流的均值用于排名。
合法性错误为 invalid_config；已知后端缺能力为 unsupported；运行异常为 run_failed。

首批指标建议：`makespan_ns`、`mean_fct_ns`、`p95_fct_ns`、`mean_fct_slowdown`，
以及分别注明统计对象的反馈计数、PFC 事件数。makespan 定义为被测流最后完成时刻减首次开始时刻，
要求被测集合全部完成。slowdown 是每流 FCT 除以原始记录中的 standalone FCT，再求均值，
需验证分母为正；它依赖后端的基准时间模型。
队列、暂停时长、吞吐等指标必须在观测口径实现后输出；缺测不能填 0。

## 执行前与交付时

规划阶段允许未知值和有依据的假设。执行前，输入文件、算法能力、参数单位、评价集合、
运行截止时间及预算必须具体化；对会改变实验含义的缺失项进行澄清。
配置合法性和结果校验由拟实现的确定性适配器完成，LLM 不能通过改评分规则让失败方案成为优胜者。
输出建议时关联真实 TrialResult；尚未执行时交付 ParameterPlan，并明确其规划状态。
