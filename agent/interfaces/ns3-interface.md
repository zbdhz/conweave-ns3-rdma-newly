# ns-3 接口：现有入口与待实现适配契约

本文路径均相对仓库根目录。当前可执行入口是 `tools/native_build.py`、
`tools/native_smoke.py` 和编译后的 `build/scratch/network-load-balance`。
下文的 `run_trial`、分层 adapter 和统一结果对象是**待实现契约**，不是现成 SDK。
任务、参数白名单及字段约束以同目录 [contracts.md](./contracts.md) 为准。

## 1. 当前可用入口

在仓库根目录执行真实构建和 smoke 命令：

```bash
python3 tools/native_build.py --jobs 8
python3 tools/native_smoke.py
```

构建脚本默认使用 `~/miniforge3/envs/conweave-build/bin/python`（Python 2.7）、
`/usr/bin/gcc` 和 `/usr/bin/g++`；可用 `--python2`、`--cc`、`--cxx` 覆盖。
它先配置，再完整构建已限定的模块，避免首次只构建 scratch target 时缺生成头文件。
每次保存 `.native/build-<时间>/configure.log`、`build.log` 和 `manifest.json`；
配置失败时不会继续构建，尚未执行的步骤没有对应运行日志。

smoke 默认运行 ECMP 与 ConWeave 两个小案例，可用真实选项
`--binary` 指定可执行文件，或 `--output-dir` 指定一个尚不存在的证据目录。
默认结果位于 `.native/smoke-<时间>/summary.json`，每个案例保留输入、日志和结果。
它检查 QP 完成、声明字节量、路由观测及 ConWeave INIT 回复；
**不证明拥塞控制性能提升、乱序恢复、重路由或 PFC 行为已验证。**

## 2. 直接调用二进制

不需要通过 Waf 启动每次实验。先准备新的 `.native/trial-manual-001/`，
其中放置完整 `config.txt`、拓扑文件及 `flows.txt`，再从仓库根目录运行：

```bash
repo_root="$(pwd -P)"
cd .native/trial-manual-001
LD_LIBRARY_PATH="$repo_root/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$repo_root/build/scratch/network-load-balance" config.txt > run.log 2>&1
```

二进制只接收配置文件路径；配置中的输入与输出路径相对**进程 cwd**，
不是相对配置文件所在目录。每个 trial 使用独立 cwd，禁止覆盖其他实验。
配置解析使用空白分词，因此文件名使用简单相对路径；外层 trial 目录可以含空格。
后续 Python runner 应以参数列表启动进程，显式设置 cwd、环境、超时和资源预算，
不生成任意 shell 命令。`LD_LIBRARY_PATH` 指向本次构建的 `build/`。
仅保存 executable hash 不足以锁定动态库，也应记录所用共享库和构建 manifest。

## 3. 输入格式与限制

流文件第一行是精确流数，之后每行 **5 列**：

```text
2
0 16 3 1000000 0.001000000
1 16 3 1000000 0.001000000
```

依次为 `source_host destination_host priority_group payload_bytes start_seconds`。
必须按开始时刻非递减排序，端点必须是主机、字节量为正且可由当前字段表示。
源／目的端口由仿真程序分配，**没有 destination-port 输入列**；
`traffic_gen/README.md` 中含端口的旧说明与真实解析不一致。
预检还应限制每主机生成的 QP 数，避免自动分配的 `uint16_t` 端口回绕。

完整配置可以参考 `tools/native_smoke.py` 的 `write_inputs()`，不能只靠以下片段运行：

```text
TOPOLOGY_FILE leaf_spine_128_100G_OS2.txt
FLOW_FILE flows.txt
CC_MODE 1
LB_MODE 0
ENABLE_PFC 1
ENABLE_IRN 0
RANDOM_SEED 1
```

首版参数模式固定 DCQCN＋ECMP＋PFC，不改变可靠性、ACK、MTU、流量或拓扑。
`RANDOM_SEED` 同时用于 C `srand` 和 ns-3 `SeedManager::SetSeed`；
当前配置没有独立的 ns-3 run 编号接口。流量生成随机种子需另外管理，
已有生成器没有显式 seed 选项；保存实际 flow 文件及 hash，不能只保存 seed。
`LOAD` 当前只读取和打印，不改变输入流量；改负载必须生成新的任务流量并冻结它，
不能在同一调参任务中删流、减字节或改启动时序来提高分数。

现有可优先复用的两个注册拓扑：

| 仓库输入文件 | 主机数 | 代码中登记的最大 BDP |
|---|---:|---:|
| `config/leaf_spine_128_100G_OS2.txt` | 128 | 104000 bytes |
| `config/fat_k8_100G_OS2.txt` | 256 | 156000 bytes |

当前 Python/C++ 按名字查表，C++ 还校验最大 BDP、每条链路 `1000ns`。
复制拓扑到 trial 时保留其注册文件名。不能仅重命名任意拓扑冒充支持。
`config/fat_k4_100G_OS2.txt` 虽存在，尚未登记；任意拓扑、异构延迟或速率
需要先实现并验证适配。新 adapter 同时检查拓扑连通性、端点、PG 和端口数组边界。

## 4. 参数映射及单位

初版先开放四个 DCQCN 核心参数，ECN 仅在任务白名单明确允许时加入：

| 真实配置键 | 含义／输入单位 | 预检要求 |
|---|---|---|
| `EWMA_GAIN` | alpha 更新增益，无量纲 | 有限值，首版限定 `0 < g <= 1` |
| `RP_TIMER` | 恢复计时器，微秒 | 正值，记录量化后的实际时长 |
| `RATE_AI` | 加性增速，DataRate 字符串，如 `40Mb/s` | 统一换算 bit/s，正值且不超任务范围 |
| `RATE_HAI` | 快速加性增速，同上 | 同上；检查与 AI 的任务关系约束 |
| `KMIN_MAP`／`KMAX_MAP` | `条目数 rate_bps threshold_kB ...` | 每个阈值输入是整数十进制 kB，内部乘 1000 |
| `PMAX_MAP` | `条目数 rate_bps probability ...` | `0 <= p <= 1`，速率键覆盖实际端口 |

v0.1 的 fixed/baseline/search_space 保留原生配置键及单位。若后续业务层以 bytes 描述 ECN 阈值，
渲染到现有后端前检查能否精确转成整数 kB，不能静默取整。
检查 `Kmin <= Kmax`，不把出口 ECN 阈值与入口 PFC 数字直接比较。
映射当前按**链路速率**配置，并非可任意指定每端口／每 PG。
`BUFFER_SIZE` 的输入实际按 `1024*1024` 转换为 bytes，与 ECN 十进制 kB 不同。
`ALPHA_RESUME_INTERVAL`、`RATE_DECREASE_INTERVAL` 也使用微秒，首轮保持基线值。
改变参数不代表目标实体 RNIC 支持该旋钮；这里只声明仿真配置能力。

## 5. 待实现的 adapter

拟定 Python 调用形态，不提供尚不存在的 CLI：

```text
run_trial(task, parameters) -> TrialResult
```

| 步骤 | 职责 |
|---|---|
| `validate` | 校验契约、能力、拓扑／流量、种子、参数白名单、单位和冻结输入 hash；拒绝未知键 |
| `render` | 从完整基线配置生成候选；保存实际配置、固定输入与 hash，不改 workload；建立独立 cwd |
| `run` | 复用已验证构建，必要时调用真实构建入口；运行 binary，记录 argv／cwd／库版本／资源／退出码 |
| `collect` | 固定解析器核对完成流与字节，计算指标，保存缺失原因；LLM 不从日志猜测数值 |

`TrialResult` 必须包含以下顶层字段，具体类型以 `contracts.md` 为准：

| 字段 | 内容 |
|---|---|
| `schema_version`、`task_id`、`trial_id` | 契约版本、冻结任务与唯一试验标识 |
| `status` | `success / invalid_config / build_failed / run_failed / timed_out / incomplete / unsupported` |
| `config` | 实际配置及参数映射，包含单位、seed 和冻结输入标识 |
| `artifacts` | 配置、输入、构建 manifest、库／二进制 hash、原始输出与日志路径 |
| `integrity` | 预期／完成流数与字节、重复／缺失 QP、完成事件及检查结果 |
| `metrics` | 已计算且有单位的数值；未实现、缺失或不适用指标为 `null` |
| `diagnostics` | 日志引用、缺失指标原因、反馈／模型边界，不混写成测得性能 |
| `error` | 失败类型与原因；无错误时为 `null` |

`success` 只表示运行与完整性通过，不表示优化目标达成。
退出正常但少流、字节不符或缺完成证据，状态为 `incomplete`；超时是 `timed_out`，
不能删除未完成流后照常参与性能排名。错误配置、能力不支持和构建失败分开归因。

## 6. 指标与停止语义的已知边界

- `fct.txt` 每行 8 列：src、dst、sport、dport、payload bytes、开始时刻、FCT、
  无竞争参考 FCT；后三项使用 ns。以 QP ACK 完成记账，不是实际 payload 内容校验。
- 流集合 makespan 用最后完成时刻减固定起点计算，不能直接用进程耗时或模拟器退出时刻。
  开放环输入只能支持流指标，不能声称 collective 完成时间或训练 JCT。
- 旧 `run.py` 分析默认丢弃前 5ms，短 incast 可能全部被过滤；新解析器应从原始 FCT
  按冻结窗口计算，包含所有应完成流。小样本不要夸大 p99 精度。
- 当前拥塞通知主要随 ACK/NACK 携带，不等同独立 RoCE CNP 时序；
  `cnp.txt` 区分 ECN 与乱序通知。没有通知不能直接解释为没有拥塞。
- 常规队列监控被 `#if(false)` 关闭；ConWeave VOQ 不是物理出口队列。
  未补可靠采集前，物理队列峰值等指标置 `null`。
- PFC 文件仅记事件，缺优先级和完整暂停区间；帧数不能换称暂停时长。
  DCQCN 多处直接改速率，只监听 `ChangeRate()` 会漏采；完整速率轨迹亦需补接口。
- 程序每 100us 检查全部流完成并提前停止，另设 `FLOWGEN_STOP_TIME + 10s`
  的仿真时间上限；这不是 wall-clock timeout。runner 需外加统一墙钟上限，
  保留退出日志，并核对流数／字节，不能仅凭返回码 0 判通过。

首版验收是“冻结输入→生成合法配置→自动运行→完整性门控→基线对照→证据包”。
后续性能结论必须来自实际拥塞场景、配对基线和独立留出验证；
smoke 的 PASS 只作为构建及基本完成路径可用的起点。
