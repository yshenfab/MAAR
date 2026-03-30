# 多智能体 Autoresearch Orchestrator 开发计划（合并版 V1）

本文件合并了 `discuss.md` 与旧版 `development_plan.md` 的方案分歧，作为当前唯一的实现基线。核心决策已经锁定：`autoresearch/` 是唯一目标仓库；系统采用回合制同步裁决；补丁执行契约为 `Search/Replace`；当同一轮出现多个正向候选时引入 Coordinator 做一次合并尝试；V1 demo 必须接入真实 LLM，而不是仅依赖 mock agent。

## 1. Summary

- 以本地 `autoresearch/` clone 为唯一 Git source of truth，构建回合制多 worker 自动研究系统。
- V1 闭环能力包括：baseline 管理、worker worktree、真实 LLM 驱动的 worker/coordinator 提案、`train.py` 补丁应用、训练调度、结果记录、回合裁决、Coordinator 合并与回退。
- V1 增加一个轻量的跨轮经验文档 `program_exp.md`：由 orchestrator 单写，worker/coordinator 只读并贡献简短 idea 摘要，用于在同一 run 内累积“什么方向更可能变好/变差”的经验。
- V1 必须内置可插拔的真实 LLM 接入层，并保留 mock/replay backend 仅用于测试和离线调试。
- V1 只需保留后续对比实验所需的完整运行产物与统计数据；严格 benchmark harness 和严谨对照协议延后到主流程稳定后再做。
- V1 明确不做：异步抢占、Reflector 记忆系统、跨轮递归合并、`train.py` 之外的代码修改。

### 1.1 对比口径锁定

- 本项目的系统名称是 **MAAR**，其核心形态是：
  - `orchestrator + 多个 workers + 一个按需触发的 coordinator`
- 在本项目的实验语境里，原始 `autoresearch` 的工作方式等价于：
  - 只有一个 worker 在工作
  - 没有 coordinator
  - 没有多 worker 同轮竞争与裁决
- 因此后续对比实验的核心问题不是“哪个 prompt 更强”，而是：
  - **多智能体架构 MAAR 是否能在相同轮数下，比单 worker 的 Autoresearch-style 流程更快或更稳定地把 `val_bpb` 降低**
- 为了保证对比更公平，本项目中的单智能体对照臂会尽量复用同一套外层 orchestrator、同一训练环境、同一目标代码基线，只把架构收缩为：
  - `worker_count = 1`
  - `coordinator = disabled`
  - 无多 worker 协作与合并
- 为了避免起点漂移，后续主要 benchmark 采用“共享 benchmark family”：
  - `bench300`
  - `bench600`
  - `bench900`
- 这三条 benchmark 臂共享同一份“中度弱化”的初始代码，只在 `TIME_BUDGET` 上区分：
  - `300s`
  - `600s`
  - `900s`
- 每个 benchmark repo 都应自带一个固定 baseline ref，保证 fresh run 不会误从 repo 当前 `HEAD` 漂移启动

## 2. 总体架构

系统由六个核心子系统组成：

- **Master Orchestrator**：负责运行初始化、轮次调度、状态持久化、最终裁决。
- **LLM Agent Adapter Layer**：统一封装真实 LLM 的调用、上下文组装、结构化输出校验、失败重试和调用日志留存。
- **Worker Agents**：多个并行的真实 LLM 候选生成器。每个 worker 在独立 `git worktree` 中基于同一轮 baseline 生成一个候选修改。
- **Coordinator Agent**：由真实 LLM 驱动，仅在同一轮出现至少两个正向候选时触发，尝试将前两名候选的思想合并为一个 merged proposal。
- **Program Experience Memory**：一个由 orchestrator 维护的精简共享经验文档，记录同一 run 内哪些“修改方向”曾改善、恶化或破坏训练；该文档只记录 idea，不记录代码。
- **Training Executor**：统一封装 GPU 资源申请、训练进程拉起、日志采集、指标解析、超时与崩溃处理。

## 3. 仓库与工作区模型

- `autoresearch/` 的本地 clone 是唯一目标仓库，也是唯一需要提交候选 commit 的本地 Git 仓库。
- 每次运行创建一个专属 `run_tag`，并以此生成运行上下文目录 `runs/<tag>/`。
- 基线使用一个专用 baseline 分支维护。
- 每个 worker 使用一个独立 `git worktree` 和独立分支，例如 `autoresearch/<tag>/worker-1`。
- Coordinator 使用独立 merge worktree，例如 `autoresearch/<tag>/merge`。
- 运行产物全部写入 `runs/<tag>/`，包括状态文件、实验日志、解析结果和 diff，不污染 `autoresearch/` 仓库中的研究代码。
- `program_exp.md` 放在 `runs/<tag>/program_exp.md`，不放进目标 repo worktree，也不参与候选 commit。
- `program_exp.md` 是“每个 run / 每个实验臂独立”的上下文，不跨 run 共享，避免对后续对比实验造成知识泄漏。
- 当前方案依赖本地 Git clone，不依赖 GitHub API、远程分支所有权或 push 权限。

## 4. 核心数据结构

### 4.1 RunConfig

运行级配置最少包含：

- `run_tag`
- `worker_count`
- `target_repo_path`
- `execution_slots`
- `agent_command_template`
- `worker_agent_backend`
- `coordinator_agent_backend`
- `worker_model_name`
- `coordinator_model_name`
- `agent_timeout_seconds`
- `agent_max_retries`
- `train_command`
- `artifact_root`
- `max_rounds`
- `continuous`

### 4.2 CoordinatorConfig

Coordinator 配置最少包含：

- `enabled`
- `trigger_min_improvements=2`
- `top_k=2`
- `validate_with_priority=true`

### 4.3 ExperimentProposal

Worker 和 Coordinator 都必须输出统一的结构化补丁：

```python
class ExperimentProposal:
    motivation: str
    idea_summary: str
    search_block: str
    replace_block: str
```

Coordinator 在该结构之外额外记录元数据：

- `merge_rationale`
- `source_candidates`

约束：

- `idea_summary` 必须是一句极简自然语言，只描述“方向/假设”，不包含代码、变量名改写建议或大段实现细节。
- `idea_summary` 旨在给 `program_exp.md` 提供原始素材，不直接参与补丁执行。

### 4.4 ExperimentResult

每次候选实验的结果最少包含：

- `round_id`
- `worker_id` 或 `coordinator`
- `baseline_commit`
- `candidate_commit`
- `status`
- `val_bpb`
- `peak_vram_mb`
- `training_seconds`
- `total_seconds`
- `diff_path`
- `log_path`

### 4.5 RoundState

每一轮的状态最少包含：

- `round_id`
- `baseline_commit`
- `worker_results`
- `positive_results`
- `merge_result`
- `selected_result`

### 4.6 Program Experience Memory

- 每个 run 维护一个共享文档：`runs/<tag>/program_exp.md`
- 文档结构保持极简，建议仅保留三段：
  - `Positive Directions`
  - `Negative Directions`
  - `Open Notes`
- 每条记录只写一条 idea，不写代码，不贴 diff，不复述大段日志
- 每条记录应尽量包含最低限度的证据标签，例如：
  - `improved once`
  - `worse once`
  - `crashed once`
- 文档必须保持短小，默认总条目数受限，避免上下文无限膨胀
- 该文档由 orchestrator 单写；worker 和 coordinator 不直接编辑文件，只通过 `idea_summary` 与实验结果间接贡献内容
- 默认策略是“单次即记”：
  - 单次 `improved` 就可写入正向经验
  - 单次 `worse` 或 `crash` 也直接写入负向经验
- orchestrator 在写入时附带最小证据标签，而不是等待重复出现后再记录
- 为避免 memory 把 worker 锚定在单一机制上，orchestrator 默认按“idea family”归并重复经验，并在同一家族多次成功后追加一条显式的 `diversify` 提示，鼓励后续 round 改试不同机制

## 5. Agent 契约与补丁执行

### 5.1 真实 LLM 接入与引擎选型要求

- V1 demo 必须支持至少一种真实 LLM backend，且 Worker 与 Coordinator 都必须能由真实 LLM 驱动。
- Mock backend、replay backend 或固定脚本 proposal 只能用于单元测试、集成测试和离线排障，不能作为最终 demo 的默认执行路径。
- 首个实现优先采用可插拔 `AgentRunner` 抽象，并优先复用轻量开源 agent engine，而不是从零自研完整 agent runtime。
- 当前默认首选 `PydanticAI` 作为真实 LLM 接入引擎：它是 Python 原生、强调 Pydantic 结构化输出与校验、模型提供方无关、MIT 许可，最贴合 `ExperimentProposal` 这类强结构输出场景。
- 当前默认次选 `OpenAI Agents SDK`：它同样强调轻量、多智能体工作流、provider-agnostic 和 MIT 许可；若后续更需要内建 handoff、session 和 tracing，可切换到该方案。
- `smolagents` 保留为可选第三方案：它足够轻量且开源，但其 `CodeAgent`/工具执行范式更偏“agent 自己做多步工具调用与代码执行”，与本项目由 orchestrator 严格掌控补丁应用和训练执行的边界不完全一致，因此不作为默认首选。
- `Agno` 暂不作为 V1 默认方案：能力完整，但更偏运行时、服务化与生产管理，超出当前 demo 的最小需求。
- 每次 agent 调用都必须保存足够的可审计信息：模型标识、prompt 输入、原始输出、解析后的 proposal、重试次数、耗时和失败原因。

### 5.2 统一执行契约

- 执行层只接受 `Search/Replace` 形式的补丁。
- 不允许 Worker 或 Coordinator 直接返回整份 `train.py` 作为执行输入。
- 不要求 agent 直接生成可执行 diff；diff 由系统在补丁落地后自动生成。

### 5.3 Agent 调用与输出校验

- Worker prompt 允许存在不同 profile：
  - `maar_wide`：用于 MAAR，强调更宽的探索范围和跨机制搜索
  - `autoresearch_original`：用于单 worker baseline，尽量贴近原始 `autoresearch/program.md` 的研究 framing
- Coordinator 保持独立 prompt profile，继续偏向合并、提炼和高层语义总结

- Worker 和 Coordinator 的 prompt 必须基于结构化上下文组装，而不是将整个仓库无差别拼接进上下文。
- Worker 和 Coordinator 的 prompt 都要包含当前 `program_exp.md` 的内容快照，作为“本 run 已知经验”上下文的一部分。
- 输出必须可解析为结构化 proposal；若解析失败、字段缺失或输出越权修改请求，则触发有限次重试。
- 超过 `agent_max_retries` 后仍失败，则记录为 agent failure，不进入补丁应用阶段。
- 多个实验臂在对比实验中应尽量使用同一真实 LLM 模型族或同一具体模型版本，避免因模型能力差异掩盖系统差异。
- `program_exp.md` 只允许提供方向性经验，不允许替代 `train.py` 原文上下文。
- orchestrator 本身不是“另一个自由写作 agent”；它是 Python 控制层，但通过 `AgentRunner` 接入 worker/coordinator LLM，因此完全可以负责读取、汇总和写回 `program_exp.md`。
- Coordinator 可以贡献额外的 `idea_summary` 或 merged rationale，但不应成为唯一 writer；否则在“无 Coordinator 触发”的轮次中，经验文档将无法稳定更新。
- MAAR 允许在 fresh run 的 `program_exp.md` 中注入少量固定先验，用来记录已经在对照臂中反复验证为“低价值且高风险”的失败模式；这些固定先验只作用于 MAAR，不注入单 worker baseline 臂。

### 5.4 修改范围限制

- V1 中唯一允许修改的文件是 `train.py`。
- 对 `prepare.py`、`program.md`、依赖文件、运行脚本的修改一律视为越权。

### 5.5 Smart Patcher 行为

- 先尝试严格字符串匹配。
- 严格匹配失败后，允许一次有限的 whitespace-tolerant 匹配。
- 若出现零匹配、多匹配或无法安全替换，直接判定 proposal failure。
- 每次成功应用补丁后自动生成标准 git diff，供日志与审阅使用。

### 5.6 Preflight

补丁成功后进入训练前，必须执行轻量预检：

- 目标文件存在且仅 `train.py` 被修改。
- Python 语法有效。
- 必要导入不报错。

预检失败不进入训练队列，直接记录失败结果。

## 6. 资源调度与训练执行

### 6.1 资源模型

- 资源层按 token pool 抽象实现。
- V1 默认 `execution_slots=1`，即单 GPU 串行验证。
- Worker 实验与 Coordinator 验证共用同一资源池。
- 接口层保留后续扩展到多 GPU 的能力，但 V1 不依赖多 GPU 才能工作。

### 6.2 执行器职责

- 注入运行所需环境变量。
- 启动训练命令，默认执行 `uv run train.py`。
- 将 stdout/stderr 重定向到独立 `run.log`。
- 监控超时、异常退出、OOM、缺失 summary 行等失败情况。
- 解析 `val_bpb`、`peak_vram_mb`、`training_seconds`、`total_seconds`。
- 无论成功还是失败，都必须释放执行槽。

## 7. 回合制执行流

### 7.1 轮次开始

- Master 冻结一个 `baseline_commit` 作为本轮统一起点。
- 所有 worker worktree 都重置到该 baseline。
- 每个 worker 在自己的 worktree 中生成且仅生成一个候选 proposal。
- Master 在本轮开始前读取并注入当前 `program_exp.md`，让所有 worker 基于同一份经验上下文启动。

### 7.2 Worker 实验

- Worker 基于本轮 baseline 生成 proposal。
- 系统在对应 worktree 应用补丁。
- 补丁通过预检后，在 worker 分支上提交候选 commit。
- 训练执行结束后，记录结果到本轮状态与运行产物目录。

### 7.3 正向候选筛选

- 本轮所有 worker 完成后，筛出 `val_bpb` 严格优于 baseline 的候选。
- 这些候选构成 `positive_results`。
- 轮次结束后，Master 根据 proposal 的 `idea_summary` 与实验结果更新 `program_exp.md`。
- 更新策略默认由 orchestrator 决定，而不是让 agent 直接自由改写整个经验文档。
- 即使本轮没有触发 Coordinator，Master 也照常根据 worker 结果更新 `program_exp.md`。

### 7.4 无 Coordinator 情况

出现以下任一情况时，不触发 Coordinator：

- `CoordinatorConfig.enabled` 为 false。
- 正向候选数量少于 `trigger_min_improvements`。

此时直接在 worker 结果中选择本轮最优候选。

## 8. Coordinator 合并策略

### 8.1 触发条件

- 仅当本轮至少有两个正向候选时触发。
- 默认只取 `val_bpb` 最好的前两个候选作为 Coordinator 输入。

### 8.2 输入内容

Coordinator 接收以下信息：

- 本轮 `baseline_commit`
- 前两个正向候选的 `proposal`
- 对应 `motivation`
- 自动生成的 `candidate.diff`
- 已解析的 metrics

### 8.3 合并基底

- Coordinator 必须基于“本轮 baseline”生成 merged proposal。
- 不允许在最佳 worker 候选上再次叠加其他补丁。
- 不做 merged-on-merged 递归合并。

### 8.4 合并验证

- 系统在独立 merge worktree 中应用 merged proposal。
- 通过预检后，提交 merge candidate commit。
- 合并验证与 worker 训练共享同一调度器。
- 如果资源调度器支持优先级，Coordinator 验证优先于下一轮 worker 训练；否则仍在当前轮次内串行完成。

### 8.5 快速回退

若出现以下任一情况，则 merged candidate 直接丢弃：

- 补丁应用失败
- 预检失败
- 训练崩溃
- `val_bpb` 不严格优于本轮最佳 worker

若 merged candidate 的 `val_bpb` 严格优于本轮最佳 worker，则 merged candidate 成为本轮最终晋升对象。

若分数完全相同，优先保留单一 worker 候选，避免引入零收益复杂度。

## 9. 轮次裁决与基线推进

- 每轮最多晋升一个结果：最佳 worker 或 merged candidate。
- 若本轮没有候选严格优于 baseline，则 baseline 保持不变。
- 若某个候选胜出，则将其对应 commit 设为新的 baseline。
- 轮次结束后，所有 worker worktree 和 merge worktree 都同步重置到新的 baseline，再进入下一轮。

## 10. 状态持久化与审计

`runs/<tag>/` 目录下至少保留：

- `run.json`
- `results.tsv`
- `experiments.jsonl`
- `program_exp.md`

每个 worker 的实验目录至少保留：

- `proposal.json`
- `agent_request.json`
- `agent_response.txt`
- `candidate.diff`
- `run.log`
- `metrics.json`

Coordinator 目录额外保留：

- `coordinator_input.json`
- `merge_proposal.json`
- `agent_request.json`
- `agent_response.txt`
- `merged.diff`
- `merge.log`

## 11. 后续对比实验设计（Post-V1）

本节不是 V1 主流程落地的阻塞项。V1 只需要保证运行产物足够完整，能够在后续补建 benchmark harness 时复现和比较结果。

### 11.1 对比目标

- 对比对象是原始 `autoresearch` 所代表的单智能体自治研究流程；在本项目中，将其具体实现为“只有一个 worker、没有 coordinator”的单智能体对照臂。
- 目标不是证明某个 prompt 更强，而是验证多智能体架构 **MAAR** 在相同研究预算下，是否比单 worker 流程更快或更稳定地找到更低的 `val_bpb`。
- 当前最核心的对比维度先固定为：
  - 相同轮数下的 best-so-far `val_bpb`
  - 相同轮数下是否更早出现首次严格优于基线的候选

### 11.2 对比实验臂

- **单智能体基线臂（Autoresearch-style）**：只允许一个真实 LLM worker 串行修改并验证 `train.py`，不启用 coordinator，不存在多 worker 竞争。
- **多智能体实验臂（MAAR）**：使用本项目的回合制 orchestrator、多个 worker 和一个按需触发的 Coordinator。
- 两个实验臂必须使用同一份初始代码快照、同一数据缓存、同一训练命令、同一评估口径和同一可编辑文件范围。

### 11.3 公平性约束

- 两个实验臂必须共享相同的 `prepare.py`、`TIME_BUDGET`、`evaluate_bpb`、初始 `train.py` 和 pinned validation shard。
- 两个实验臂必须运行在相同硬件条件下，默认都使用单个执行槽。
- 两个实验臂必须使用同一真实 LLM 模型或至少同一模型族与版本策略。
- 主比较维度是相同总墙钟时间和相同训练实验预算；`val_bpb` 始终作为唯一主效果指标。
- LLM token 消耗和 API 成本需要记录，但默认作为次级分析指标，而不是主公平性约束。
- 若引入 `program_exp.md`，则单智能体臂与多智能体臂都必须采用相同的“每 run 独立 memory”规则；禁止跨实验臂共享同一份经验文档。

### 11.4 评测方式

- 记录并绘制每个实验臂的 best-so-far `val_bpb` 随墙钟时间变化曲线。
- 记录并绘制每个实验臂的 best-so-far `val_bpb` 随已完成实验数变化曲线。
- 记录最终最优 `val_bpb`、首次超过 baseline 的时间、keep rate、crash rate、proposal failure rate、Coordinator 成功晋升次数。
- 若存在多轮独立重复实验，则以中位数和分位区间汇总，而不是只看单次最好结果。

### 11.5 运行协议

- 每个实验臂都从同一个初始 commit 开始。
- 在同一个总时长预算内运行，例如固定若干小时或固定若干完成实验轮次。
- 每个实验臂都保留完整运行产物：候选 proposal、diff、日志、指标、agent 输入输出和晋升历史。
- 多智能体实验臂若要宣称优于单智能体，至少需要在多数重复实验中表现出更快的 best-so-far 改进速度或更低的最终 `val_bpb`。

## 12. 测试计划

### 12.1 工作区与基线

- baseline/worktree 初始化正确。
- worker 分支与 merge 分支命名正确。
- 回合结束后所有 worktree 能正确同步到新 baseline。

### 12.2 补丁器

- 精确匹配成功。
- whitespace-tolerant 匹配成功。
- 无匹配、重复匹配、多文件修改、越权修改被正确拒绝。

### 12.3 调度与执行

- 单槽串行执行正常。
- 异常退出、超时、OOM 后执行槽会释放。
- 缺失 summary 行时结果被判定为 crash。

### 12.4 Agent 接入

- 真实 LLM backend 能返回合法 proposal 并通过结构化解析。
- 输出格式错误、超时、空响应、非零退出或 API 异常时能正确重试和失败落盘。
- Mock/replay backend 与真实 backend 共享同一 `AgentRunner` 接口。

### 12.5 Worker 裁决

- 全失败。
- 全部不提升。
- 单一胜者。
- 分数相同情况下的稳定 tie-break。

### 12.6 Coordinator

- 少于两个正向候选时不触发。
- 两个以上正向候选时按 top-2 触发。
- merged 补丁失败时回退到最佳 worker。
- merged 跑崩时回退到最佳 worker。
- merged 优于最佳 worker 时晋升 merged。
- merged 等于或差于最佳 worker 时丢弃 merged。

### 12.7 日志与持久化

- `results.tsv` 记录完整。
- `experiments.jsonl` 与目录产物一致。
- 每个实验的 diff、proposal、metrics、log 路径可回溯。
- 每次 agent 调用的输入输出、模型标识和重试信息可回溯。

### 12.8 结果导出与后续对比准备

- 系统能导出构建 best-so-far 曲线所需的原始时间序列数据。
- 单次运行的配置、晋升历史、实验结果和 agent 输入输出可用于后续离线对比。

## 13. 当前默认假设

- 唯一可编辑文件是 `train.py`。
- `Search/Replace` 是唯一执行真相，git diff 仅用于审阅和回溯。
- Coordinator 每轮最多尝试一次合并。
- Coordinator 默认只消费本轮最好的两个正向候选。
- 当前默认按单执行槽设计，多 GPU 仅作为接口预留。
- 真实 LLM 是 demo 和最终对比实验的必选依赖，mock backend 仅用于测试。
- 严格 benchmark 暂缓到主流程稳定之后；V1 只要求保留后续公平对比所需数据。
