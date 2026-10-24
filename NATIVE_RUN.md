# ConWeave 本机运行说明

2026-10-24 在此机器实测：可以直接编译和运行，无需 Docker。仓库基于 ns-3.19，
本次验证基线为 `236a801`。原版源码和下述越界修复后的源码都通过了 ECMP / ConWeave 小规模仿真。

## 当前环境

| 项目 | 已验证配置 |
| --- | --- |
| 系统 | Ubuntu 22.04.5 LTS，x86_64 |
| C/C++ 编译器 | `/usr/bin/gcc`、`/usr/bin/g++`，11.4.0 |
| 旧版 Waf 的解释器 | `~/miniforge3/envs/conweave-build/bin/python`，Python 2.7.15 |
| 新增辅助脚本的解释器 | 系统 `/usr/bin/python3`，3.10.12，仅使用标准库 |
| 仿真程序 | `build/scratch/network-load-balance` |

Conda 环境只用于运行旧 Waf 构建脚本，C++ 使用系统编译器。辅助脚本会显式选择解释器，
因此无需激活环境，也无需修改系统 `python` 链接。ns-3 仿真使用 CPU，不要求 GPU、CUDA 或实体 RDMA 网卡。

## 直接运行

本机环境和二进制已经准备好。修改 C++ 后重新构建，然后运行验证：

```bash
cd /home/gtx/rdma_project/conweave-ns3-rdma-newly
python3 tools/native_build.py
python3 tools/native_smoke.py
```

如果只想再次验证已有二进制，执行第二条 Python 命令即可。预期输出：

```text
PASS ecmp: 16 flows, 16000000 payload bytes
PASS conweave: 16 flows, 16000000 payload bytes
PASS: .../.native/smoke-<时间戳>/summary.json
```

脚本失败时返回非零退出码并保留日志；每次使用新目录，不覆盖旧结果。`native_smoke.py`
会为子进程设置本仓库 `build/` 的动态库搜索路径，直接启动本机 C++ 程序。

构建默认使用 8 个并行任务，可通过 `--jobs 4` 调整。自定义解释器或编译器：

```bash
python3 tools/native_build.py --python2 /path/to/python2.7 --cc /usr/bin/gcc --cxx /usr/bin/g++ --jobs 4
python3 tools/native_smoke.py --output-dir /tmp/conweave-native-check
```

`--output-dir` 必须是尚不存在的目录。

## 测试内容与证据

使用仓库原有的 `config/leaf_spine_128_100G_OS2.txt`：128 台主机、16 台交换机、
100 Gbit/s 链路、每链路 1000 ns 延迟。流量只有四个跨 ToR 通信对：
`0→16`、`1→17`、`2→18`、`3→19`，每对四条 QP，共 16 条流；每条流 1,000,000 字节，
统一从仿真时间 1 ms 开始。随机种子为 1，拥塞控制为 DCQCN，PFC 开启，IRN 关闭。

| 案例 | LB_MODE | 修复后结果 |
| --- | --- | --- |
| ECMP | 0 | 退出码 0，16/16 条流完成，16,000,000 字节 |
| ConWeave | 9 | 退出码 0，16/16 条流完成，16,000,000 字节；多个源 ToR 上行端口有流量，观察到 INIT 回复 |

脚本核验 FCT 文件中的源/目的主机、端口和流大小，检查 QP 无重复、完成数、正 FCT、
日志完成计数和上行计数单调性。这里的字节量是仿真模型确认完成的 QP 大小，不是实际应用数据内容的逐字节校验。

这次验证证明本机可以运行该分支的 RDMA 仿真。负载较小，未触发重路由、乱序入队和 VOQ flush，
也未验证 PFC 触发、IRN、故障恢复或论文级规模实验；不能据此比较算法性能。

本次证据保留在本地（`.native/` 已加入 Git 忽略）：

- 原版源码首次完整构建日志：`.native/build.log`。
- 原版源码验证：`.native/smoke-20261024-153648-710467/summary.json`。
- 修复后构建记录：`.native/build-20261024-153755-392774/manifest.json`。
- 修复后验证：`.native/smoke-20261024-153832-640456/summary.json`。

每个 smoke 目录的 `ecmp/` 和 `conweave/` 子目录包含拓扑、`flows.txt`、`config.txt`、
`run.log`、`fct.txt`、`uplink.txt` 和 `result.json`。输入和二进制哈希记录在 JSON 中，
但二进制哈希不包含动态链接的共享库；后续重建后应生成新的验证记录。

## 本次修改

- 新增 `tools/native_build.py`：检查工具链、执行配置及完整构建，保存版本、命令、退出码和日志。
- 新增 `tools/native_smoke.py`：生成固定流量，运行两个案例并核对完成结果。
- 新增 `tools/native-build-linux-64.lock`：本次 Conda 构建环境的显式包清单。
- 修复 `scratch/network-load-balance.cc` 的 host/IP 映射初始化：把两条映射赋值移入现有 host 分支。
  原代码对全部 144 个节点访问只覆盖主机的 128 项 `serverAddress`，交换机索引会越界读取；
  修复后只对主机建立映射。
- 添加本说明、README 入口和 `.native/` 忽略规则。

## 重建 Conda 环境

本机已完成创建，不必重复执行。若以后环境被删除，可在本仓库目录使用显式清单恢复 Linux x86_64 环境：

```bash
~/miniforge3/bin/conda create -n conweave-build --file tools/native-build-linux-64.lock -y
```

这份清单记录 Conda 解释器依赖；系统 GCC/G++ 和系统开发库仍由主机提供。

构建脚本实际执行的 Waf 命令为：

```bash
~/miniforge3/envs/conweave-build/bin/python ./waf configure \
  --build-profile=optimized --disable-python --disable-examples --disable-tests \
  --enable-modules=point-to-point,applications,flow-monitor
~/miniforge3/envs/conweave-build/bin/python ./waf build -j8
```

所选模块及依赖足以构建 `network-load-balance`。关闭的是 ns-3 的 Python bindings、
内置示例及内置测试；没有运行 ns-3 全量测试套件。首次构建应使用完整 `waf build`，
只指定 scratch target 可能跳过生成头文件的步骤。

## 后续自定义实验

可参考 smoke 留下的输入文件，在新的实验目录中修改流量和参数，并在该目录运行：

```bash
LD_LIBRARY_PATH=/home/gtx/rdma_project/conweave-ns3-rdma-newly/build \
  /home/gtx/rdma_project/conweave-ns3-rdma-newly/build/scratch/network-load-balance config.txt
```

配置中的相对路径以当前实验目录为基准，输出文件也写入该目录。当前 C++ 配置解析器按空白切分文件名，
建议配置内部使用不含空格的相对文件名。自定义拓扑还需检查源码的 `topo2bdpMap` 和 1000 ns 链路延迟假设，
不能仅替换拓扑文本就默认兼容。

原始 `run.py` / `autorun.sh` 和论文绘图流程尚未验证：它们额外涉及 NumPy、Matplotlib、
Python 2 / Python 3 调用和较大的实验负载。当前提供的原生构建与仿真入口可以直接使用。
