# autoresearch 多智能体并发重构计划书

## 1. 系统总体架构

本系统基于 `karpathy/autoresearch` 构建水平并发的多智能体自动化研究框架。系统由三个核心组件构成：

- **主控脚本 (Master Script)**：系统的中枢神经。负责目录初始化、进程生命周期管理、全局 GPU 任务队列维护以及智能体之间的产物传递。
- **工作智能体 (Worker Agents)**：多个并行的探索单元（基于开源 Agent 引擎封装）。在各自独立的软隔离沙盒中运行，探索不同的架构或超参数假设空间。
- **协调智能体 (Coordinator Agent)**：负责审查和合并代码的高级决策单元。读取各 Worker 的报告与差异文件（Diff），生成综合最优代码。

## 2. 环境隔离机制：软隔离与目录映射

采用基于文件目录与进程的软隔离策略，兼顾开发敏捷性与运行安全。

- **工作区克隆**：主控脚本在启动时，以原始 `autoresearch` 目录为基准，复制出 $N$ 个独立的工作目录（如 `workspace_worker_1`, `workspace_worker_2`）。
- **进程绑定**：主控脚本为每个工作目录启动一个独立的 Agent 进程。
- **环境隔离**：所有工作目录共享宿主机的底层 Python 虚拟环境（包含 `uv` 和 PyTorch），以节省磁盘空间并消除重复编译开销。绝对禁止 Agent 执行跨目录访问指令。

## 3. 核心通信与防溢出机制：Git Diff 标准输出

为彻底解决长文件传递导致的大模型上下文溢出（Context OOM）和幻觉问题，严格约束 Worker Agent 的代码输出格式。

- **禁用全量覆盖**：Worker Agent 完成本地沙盒的代码修改与调试后，禁止向主控脚本返回完整的 `train.py`。
- **强制 Diff 输出**：要求 Worker Agent 必须输出标准的 Git Diff 格式文本。
- **产物打包**：Worker 提交给主控脚本的最终产物结构严格定义为：`[Markdown 实验报告 (包含修改动机)]` + `[标准 Git Diff 片段]` + `[局部验证指标 val_bpb]`。

## 4. 算力调度：全局 FIFO 队列与进程锁 (Mutex)

为防止多个并发运行的 Agent 同时拉起模型训练导致单节点 GPU 显存溢出（OOM），在主控脚本中实现强制的同步调度层。

- **全局锁 (Mutex Lock)**：系统初始化一个全局进程锁，映射至目标 GPU。
- **请求排队 (FIFO Queue)**：当任意 Worker Agent 完成代码修改并需要验证性能时，必须将“训练执行请求”推入全局 FIFO 队列，其主线程随即进入挂起（Blocked）状态。
- **获取与释放**：
  - 主控脚本的调度器持续监控队列，为队首请求分配进程锁。
  - 拿到锁的 Agent 被唤醒，执行 `uv run train.py`。
  - 运行 5 分钟后测试结束，该 Agent 释放进程锁，调度器唤醒队列中的下一个 Agent。

## 5. 代码合并与择优回退策略 (Merge & Fallback)

每一轮并发探索结束时，触发协调与合并流程，作为系统实现单调递增优化的关键防御网。

- **语义级审查**：主控脚本收集所有 Worker 的产物（报告 + Diff），将其打包发送给 Coordinator Agent。Coordinator 负责解析各方修改动机，解决物理与数学逻辑冲突，输出合并后的 `train.py`。
- **基线验证**：主控脚本为 Coordinator Agent 分配 GPU 锁，运行合并后的代码，获取验证指标 $val\_bpb_{merged}$。
- **严格回退机制 (Strict Fallback)**：
  - 若合并代码发生编译错误、梯度爆炸（NaN），或 $val\_bpb_{merged} \ge \min(val\_bpb_{workers})$，系统判定合并失败。触发**熔断操作**：抛弃 Coordinator 的合并结果，将当前轮次跑分最优的单一 Worker 的 `train.py` 提升为新的全局 Baseline。
  - 若 $val\_bpb_{merged} < \min(val\_bpb_{workers})$，则合并结果成为下一轮的全局 Baseline。
- **状态同步**：主控脚本将新的全局 Baseline 同步覆盖至所有 `workspace_worker_n` 目录，清空队列，启动下一轮并发探索。