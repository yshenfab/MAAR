# MAAR 项目方法与系统设计说明（中文版）

本文档用于系统性描述本项目当前的方法设计、系统结构、实验设定与关键工程决策。它的目标不是替代最终论文，而是作为后续论文写作时的中文素材底稿，帮助快速组织 `Method`、`System`、`Experimental Setup` 与 `Discussion` 等章节内容。

## 1. 研究目标

本项目研究的问题是：**在相同的实验轮数与相同的训练预算下，不同的智能体组织方式是否会显著影响自动研究系统推进训练主线的能力，从而得到更低的验证集 `val_bpb`。**

围绕这一目标，我们构建了三条实验臂：

- 多 worker、训练后协调的 **MAAR**（Multi-Agent AutoResearch）；
- 单 worker 的 **Autoresearch-style baseline**；
- 固定职责 specialist、训练前协作的 **Agent Group Chat** 对照架构。

这里的“单智能体对照臂”并不等同于简单地关闭两个 worker，而是尽量模拟原始 `autoresearch` 的工作方式：只有一个 worker 持续观察当前主线、提出补丁、运行训练、再根据结果决定保留或丢弃该修改。新增的 `agent_groupchat` 则代表另一种多智能体组织方式，其重点不是训练后的 merge，而是在训练前围绕同一候选进行轮内协作。

因此，本项目的核心对比不是“哪句 prompt 更强”，而是：

- 在相同起点下，`MAAR` 是否能在相同轮数内找到更好的候选；
- 在相同起点下，`agent_groupchat` 这种训练前协作模式是否比 `MAAR` 或单 worker 更有效；
- 在固定训练预算下，哪一种组织方式能更早推进主线 baseline；
- 并行探索、训练后协调与训练前协作三种组织方式，哪一种更能带来稳定收益。

## 2. 问题设定

本项目建立在 `karpathy/autoresearch` 思想之上，但没有直接复用其原始交互流程，而是构建了一个外层 orchestrator，将整个研究过程转化为可复现的回合制实验。

当前问题设定如下：

- 目标任务：在固定训练时间预算内，修改 `train.py` 中的训练逻辑、模型结构或超参数，使验证集 `val_bpb` 尽可能降低。
- 唯一可编辑文件：`train.py`。
- 评价指标：以 `val_bpb` 为唯一主指标，数值越低越好。
- 训练预算：使用固定 `TIME_BUDGET` 的短程训练预算，当前主要使用 `300s`，并扩展了 `600s` 与 `900s` 平行实验臂。
- 接受规则：若某一候选的 `val_bpb` 严格优于当前 baseline，则该候选可以推进主线；否则丢弃。

这一设定实际上将问题转化为一个**固定计算预算下的自动化研究与搜索问题**：系统不是追求“理论上最强模型”，而是追求“在给定 300/600/900 秒预算内，哪种修改最能提升短时训练效率与最终验证指标”。

## 3. 系统总体结构

整个项目采用统一的 orchestrator 外层框架，在同一套 Git worktree、补丁应用、preflight、训练执行器与状态落盘机制之上，同时支持三条实验臂。这样可以把比较重点放在**智能体组织方式**上，而不是基础设施差异上。

### 3.1 Orchestrator

Orchestrator 是整个系统的控制平面，负责：

- 初始化运行目录；
- 冻结与推进 baseline；
- 为 worker / shared candidate 创建独立 Git worktree；
- 调用 worker / coordinator / specialist / engineer 的真实 LLM backend；
- 应用补丁、执行 preflight、调度训练；
- 记录所有实验产物；
- 根据结果更新共享经验文档。

需要强调的是，orchestrator 本身不是一个自由文本生成 agent，而是一个**确定性的 Python 控制层**。它通过统一的 `AgentRunner` 接口调用外部 LLM，但真正负责文件系统、Git、训练命令与状态持久化的是 orchestration 逻辑本身。

### 3.2 Worker Agents

在 MAAR 与单智能体 baseline 中，worker 是核心探索单元。每个 worker 在同一轮中：

1. 读取当前 baseline 的 `train.py`；
2. 读取当前 run 的共享经验文档 `program_exp.md`；
3. 基于自己的 prompt 生成一个结构化 proposal；
4. 在独立 worktree 中应用 proposal；
5. 通过 preflight 后运行训练；
6. 根据得到的 `val_bpb` 参与本轮比较。

每个 worker 每轮只允许提交一个候选，从而保证同一轮内“每个 agent 只代表一个清晰假设”。

### 3.3 Coordinator

Coordinator 只在 **MAAR** 中被使用，且仅在**同一轮出现多个正向候选**时触发。其职责不是简单选择最佳 worker，而是尝试对多个正向候选做一次语义级合并，并产生一个 merged proposal。合并后的候选会单独运行验证；只有当 merged candidate 严格优于本轮最佳 worker 时，才允许它取代最佳 worker 进入下一轮主线。

因此，coordinator 不是每轮都运行，而是一个**按需触发的高价值模块**。

### 3.4 Agent Group Chat 对照架构

除 MAAR 外，系统还实现了一条新的多智能体对照臂 `agent_groupchat`。它与 MAAR 共享同一个 orchestrator、同一套 Git worktree / preflight / executor / metrics 基础设施，但改变了候选形成方式。

`agent_groupchat` 的核心特征是：

- 不使用多个独立 worker 分别训练；
- 不使用训练后的 coordinator merge；
- 每轮维护一个共享候选 worktree；
- 由 `architecture`、`optimizer_schedule`、`efficiency_memory` 三个 specialist 轮流在该共享候选上进行 `6` 个 turn 的增量修改；
- 整轮结束后只训练 **1** 个最终共享候选；
- 若最终候选 crash，则允许触发一个 `engineer` agent 进行一次保守 debug，并再验证一次。

因此，`agent_groupchat` 代表的是一种**训练前协作**范式，而 MAAR 代表的是一种**训练后协调**范式。

## 4. MAAR 单轮执行流程

MAAR 采用同步回合制执行，而不是异步滚动更新。每轮的执行流程如下。

### 4.1 冻结 baseline

每轮开始时，系统冻结一个统一的 `baseline_commit`。所有 worker 都从这一 commit 出发，避免不同 worker 在不同起点上工作，从而保证候选结果可直接比较。

### 4.2 多 worker 并行提出候选

每个 worker 读取当前主线与共享经验文档，生成一个结构化 proposal。proposal 的执行契约不是“直接返回整份代码”，而是：

- `motivation`
- `idea_summary`
- `search_block`
- `replace_block`

也就是说，LLM 并不直接控制完整文件，而是只能提出一次 Search/Replace 形式的局部修改。

### 4.3 补丁应用与预检

proposal 会被应用到对应 worker 的独立 Git worktree 中。应用成功后进入 preflight。preflight 的目标是用尽量低的成本，在训练前筛掉明显无效或高风险的候选。

当前 preflight 至少包含以下内容：

- 只允许修改 `train.py`；
- Python 语法合法；
- 必要导入可用；
- 拒绝明显危险的 runtime side effects；
- 在 MAAR 中保留少量更严格但仍然较保守的结构一致性检查。

### 4.4 训练执行

通过 preflight 的候选会被提交到对应 worker 分支，然后进入训练执行阶段。训练执行器会：

- 启动 `train.py`；
- 重定向日志到独立 `run.log`；
- 解析 `val_bpb`、`peak_vram_mb`、`training_seconds`、`total_seconds`；
- 处理 crash、timeout、非零退出与缺失 summary 的情况。

### 4.5 本轮裁决

当所有 worker 完成后，系统会从本轮结果中筛出严格优于 baseline 的候选，形成 `positive_results`。

- 如果没有正向候选：本轮不推进 baseline；
- 如果只有一个正向候选：直接与 baseline 比较，若更优则推进；
- 如果有多个正向候选：触发 coordinator 进行一次 merge 尝试。

### 4.6 Coordinator 合并与回退

Coordinator 读取本轮前若干个最优候选的：

- proposal
- diff
- `val_bpb`
- `idea_summary`
- `idea_family`

然后生成一个 merged proposal。系统在独立 merge worktree 中验证 merged candidate。

裁决规则是严格的：

- 若 merged candidate 崩溃、补丁失败或不优于最佳 worker，则丢弃 merged；
- 若 merged candidate 严格优于最佳 worker，则 merged candidate 成为新的主线 baseline；
- 若数值持平，则默认保留最佳单一 worker，避免“零收益复杂化”。

### 4.7 更新共享经验文档

轮次结束后，orchestrator 根据 worker/coordinator 的 proposal 与实验结果，更新共享经验文档 `program_exp.md`。这一步是多轮实验能够积累“方向性经验”的关键。

## 5. 共享经验记忆

为了让多轮实验具备某种“研究连续性”，系统引入了显式共享记忆，而不是依赖模型供应商的隐式会话状态。

### 5.1 `program_exp.md`

每个 run 都维护一个共享经验文档：

```text
runs/<run_tag>/program_exp.md
```

这个文档的目标不是保存代码，也不是记录完整日志，而是记录**极简的方向性经验**：

- 哪些机制曾改善结果；
- 哪些机制曾让结果变差；
- 哪些方向曾导致 crash 或无意义的失败。

### 5.2 为什么由 orchestrator 单写

我们没有让 worker、coordinator 或 specialist 直接编辑 `program_exp.md`，而是由 orchestrator 统一写入，原因有三点：

1. 某些角色不是每轮都会触发，如果它们是唯一 writer，会导致很多轮没有经验沉淀；
2. 由 orchestrator 统一写入更容易控制文档长度、格式与去重策略；
3. 这样可以避免 agent 自由改写历史，减少记忆污染。

因此，agent 只额外输出极简的 `idea_summary`，而是否写入、写入到哪一类经验、如何压缩重复模式，全部由 orchestrator 规则化完成。

### 5.3 固定先验与在线经验

除了在线积累的经验外，系统还支持向 fresh run 注入少量固定先验。这些先验来自于长跑中暴露出的“低价值但高风险”的失败模式，例如：

- 明显的 depth inflation 导致 OOM；
- 结构改动破坏 optimizer 假设；
- norm / attention kernel 路径改动破坏 FlashAttention 兼容性。

这些先验只注入 MAAR 与 `agent_groupchat` 这两条多智能体实验臂，不注入 baseline 臂，以避免污染对照设置。

### 5.4 `groupchat_memory.md`

`agent_groupchat` 在 `program_exp.md` 之外，还显式维护另一份团队级记忆：

```text
runs/<run_tag>/groupchat_memory.md
```

其作用不是记录单个 proposal 是否成功，而是记录**群聊接力本身的团队协作经验**，例如：

- 哪些 specialist 的组合顺序曾形成有效共享候选；
- 哪些类型的 turn 经常被后续 specialist 改写或回滚；
- 哪些“accepted 但最终无益”的模式不应在后续轮次被误当成强正向信号；
- 某类 crash 是否与共享接口变更、结构改写或训练动力学失配有关。

与 `program_exp.md` 相同，`groupchat_memory.md` 也由 orchestrator 单写。区别在于：

- `groupchat_log.jsonl` 在每个 turn 后即时追加；
- `groupchat_memory.md` 只在**每轮结束后**统一汇总更新；
- specialist 在每次 turn 中同时读取 `program_exp.md` 与 `groupchat_memory.md`，以获得方向性经验和团队级协作上下文。

## 6. Agent 设计

### 6.1 MAAR Worker

MAAR 的 worker prompt 采取相对开放的设计。它允许 worker：

- 修改模型结构；
- 修改优化器与调度；
- 修改 batch、depth、window pattern、activation 等；
- 在共享经验指导下探索不同机制，而不是持续重复同一类成功方向。

其核心思想是：**在保持执行契约与安全边界的前提下，尽量保留研究自由度**。

### 6.2 Baseline Worker

单智能体 baseline 臂不使用与 MAAR 完全相同的 prompt，而是使用更接近原始 `autoresearch/program.md` 风格的 `autoresearch_original` prompt profile。它强调：

- 自治研究员式的实验 framing；
- 以当前主线为基础不断尝试与回退；
- 更贴近原始 `autoresearch` 的单 worker 叙事。

因此，目前 MAAR 与 baseline 并不是“只差 worker 数量”，还存在一定 prompt/profile 差异。这个差异更接近“真实系统对比”，而不是最严格的控制变量实验。

### 6.3 Coordinator

Coordinator 的 prompt 重点不在于自由探索，而在于：

- 判断多个正向候选是否真的可组合；
- 避免在没有组合价值时瞎做平均；
- 在可以合并时提出一份更强的 merged proposal；
- 额外提供一条简短 `curator_note`，帮助系统在经验文档中做更高层的归纳。

### 6.4 Agent Group Chat Specialist 与 Engineer

`agent_groupchat` 中的 specialist 采用**软分工**，而不是硬隔离。三类 specialist 的主责分别是：

- `architecture`：负责结构、attention、MLP、residual / normalization / embedding 相关方向；
- `optimizer_schedule`：负责 optimizer、学习率调度、warmup / warmdown、batch geometry 等训练动力学；
- `efficiency_memory`：负责显存压力、短预算适配、吞吐与结构膨胀风险。

这里的“主责”意味着默认关注点，而不是严格边界。后续 specialist 在必要时可以跨界小改，以整合前序 turn 已接受的共享候选。

为防止 specialist 只是在“顺序盲改”，系统会把前序 accepted turn 的 `idea_summary`、`motivation` 与共享 commit 一起提供给后续 specialist，使其更接近真实群聊中的显式上下文传递。

在 `agent_groupchat` 中还引入了单独的 `engineer` 角色。其职责是：当最终共享候选在真实训练时 crash，读取当前代码、crash log 与前序 turn 上下文，做一次**尽量保留 specialist 方向的保守 debug**。这使 `engineer` 更像轮末工程修复器，而不是第二个 coordinator。

## 7. 安全性与可复现性设计

尽管系统允许真实 LLM 自主提出修改，但整个执行层具有较强的工程约束，以保证实验可回放、可审计、可中止。

### 7.1 Search/Replace 补丁契约

系统不接受整份 `train.py` 重写，而只接受 `Search/Replace` 格式的补丁。这种做法有三点好处：

- 易于审查；
- 易于判断越权修改；
- 易于自动生成标准 git diff。

### 7.2 Git worktree 隔离

MAAR 的每个 worker、coordinator，以及 `agent_groupchat` 的 shared candidate / engineer 都在独立 Git worktree 中工作。这样能够保证：

- 同一轮多个候选互不污染；
- 每个候选都有独立 commit；
- 主线 baseline 可以被清晰推进与回退。

### 7.3 训练环境与代理隔离

项目已经显式修复了长跑过程中“继承本地代理环境变量”导致的 LLM 请求中断问题。现在实验 launcher 会清空本地代理环境变量，仅作用于实验进程及其子进程，不影响用户的 VS Code / Codex 全局环境。

### 7.4 运行产物完整落盘

每次运行都会生成完整的实验目录，包括：

- `run.json`
- `results.tsv`
- `experiments.jsonl`
- 每轮的 `round.json`
- 每个 worker / coordinator / specialist / engineer 的 `proposal.json`
- `candidate.diff`
- `run.log`
- `metrics.json`
- `agent_request.json`
- `agent_output_*.txt` 与错误文件

这些产物保证了系统能够进行完整复盘，而不是只保留一个最终分数。

## 8. Benchmark 构造

### 8.1 从 `autoresearch` 到 `autoresearch-3090`

原始 `autoresearch` 的默认设定更贴近高算力环境。为使其可在本地 3090 环境下稳定运行，我们首先构建了 `autoresearch-3090`，主要进行显存与预算适配，例如：

- 降低 `EVAL_TOKENS`
- 降低 `TOTAL_BATCH_SIZE`
- 降低 `DEVICE_BATCH_SIZE`

这一步的目标不是优化模型，而是得到一个可运行的 3090 适配基线。

### 8.2 为什么又构造 `bench300/600/900`

在真实测试中我们发现，原始 3090 适配版为 agent 留下的改进空间不够明显，不利于观察多智能体系统是否真的更快地产生收益。因此又构建了三套共享 benchmark 家族：

- `autoresearch-3090-bench300`
- `autoresearch-3090-bench600`
- `autoresearch-3090-bench900`

三者共享同一套“中度弱化”的初始代码，只在 `TIME_BUDGET` 上分别设置为 300、600、900 秒。

### 8.3 中度弱化 benchmark 的作用

中度弱化不是为了“故意做坏系统”，而是为了构造一个更有改进空间、同时又不显得明显不合理的起点。当前弱化主要集中在训练效率相关因素上，例如：

- 更保守或不够合理的 `WINDOW_PATTERN`
- 更大的 `TOTAL_BATCH_SIZE`
- 过长的 warmup / warmdown
- 非零的 `FINAL_LR_FRAC`

这样构造的 benchmark 更适合观察：在固定轮数与固定训练预算下，多智能体系统是否能更快推进主线。

## 9. Agent Group Chat 对照架构

### 9.1 定义

`agent_groupchat` 是本项目新增的第三条实验臂，用于和 MAAR、单智能体 baseline 同时对比。它的目标不是替代 MAAR，而是回答另一类问题：

> 如果多个 specialist 不先各自训练，而是在训练前围绕同一候选进行显式接力协作，是否能形成更有效的候选？

它与 MAAR 的最大差异不在于模型供应商或训练环境，而在于候选形成机制：MAAR 是“先独立探索，后协调”；`agent_groupchat` 是“先协作形成候选，再统一训练”。

### 9.2 轮内共享候选接力

每轮开始时，系统冻结当前 baseline，并创建一个共享候选 worktree。随后按照固定顺序执行 `6` 个 turn：

1. `architecture`
2. `optimizer_schedule`
3. `efficiency_memory`
4. `architecture`
5. `optimizer_schedule`
6. `efficiency_memory`

每个 turn 只允许执行一次增量 `Search/Replace`。若 patch 能成功应用且通过 preflight，则：

- 立即写入共享候选；
- 形成一次新的共享 commit；
- 追加一条 `groupchat_log.jsonl` 记录。

若某个 turn 失败，则不会立即重试；失败同样消耗该 turn 的预算，下一位 specialist 基于上一份 accepted 的共享状态继续。这保证了 `agent_groupchat` 的探索失误具有真实成本，而不是被 turn 机制无限吞噬。

### 9.3 轮末训练与 Engineer Fallback

整轮结束后，系统只对最终共享候选执行一次真实训练。由此可见，`agent_groupchat` 与 MAAR 在训练预算的使用方式上明显不同：

- MAAR 一轮可能训练多个 worker 候选，必要时再训练一个 merged candidate；
- `agent_groupchat` 一轮默认只训练一个最终共享候选。

如果最终共享候选在训练阶段 crash，则触发 `engineer` fallback：

1. 保留当前共享候选与 crash log；
2. 调用单独的 `engineer` agent；
3. engineer 在尽量保留 specialist 思路的前提下做一次局部 debug；
4. 若修复候选通过 preflight，则再训练一次；
5. 若 engineer 修复后的候选优于当前 baseline，则可以直接推进主线。

因此，`engineer` 的作用是提高训练前协作系统的容错性，而不是替代 specialist 完成新的研究探索。

### 9.4 显式群聊上下文

为了使 `agent_groupchat` 真正接近“群聊式协作”而非简单串行编辑，后续 specialist 在每次 turn 中不仅会读取当前共享 `train.py`，还会读取前序 accepted turn 的结构化摘要。该摘要至少包括：

- `turn_index`
- `specialist_role`
- `idea_summary`
- `motivation`
- `shared_commit_after`

这使后续 specialist 能明确知道“前一个人想做什么”，从而选择配合、修正、局部回滚或补全，而不是只看一份被改过的代码继续盲目叠加。

### 9.5 与 MAAR 的关键区别

`agent_groupchat` 与 MAAR 的核心区别可概括为：

- MAAR：训练后协调
- `agent_groupchat`：训练前协作

更具体地说：

- MAAR 的 worker 彼此独立工作，`agent_groupchat` 的 specialist 轮内共享同一候选；
- MAAR 的组合发生在 round 末的 coordinator merge，`agent_groupchat` 的组合发生在 round 内的连续共享 commit；
- MAAR 更适合并行探索多个互斥假设，`agent_groupchat` 更适合围绕一个候选做逐步打磨与局部修正；
- MAAR 的主要记忆是 `program_exp.md`，`agent_groupchat` 额外引入了 `groupchat_memory.md` 来描述团队级协作经验。

因此，`agent_groupchat` 不是“弱化版 MAAR”，而是与 MAAR 正交的另一种多智能体组织方式。

## 10. 单智能体对照臂

### 10.1 定义

本项目中的单智能体对照臂并不是原始 `autoresearch` 的原封不动复刻，而是一个 **Autoresearch-style baseline**：

- 单 worker；
- 无 coordinator；
- 使用接近原始 `autoresearch` 风格的 prompt；
- 仍运行在统一的 orchestrator、Git worktree、训练执行器与日志系统之上。

### 10.2 与原始 `autoresearch` 的相同点

- 核心目标一致：不断修改 `train.py`，让 `val_bpb` 下降；
- 单智能体连续推进主线的研究叙事一致；
- 使用 keep/discard 风格的实验 loop。

### 10.3 与原始 `autoresearch` 的不同点

- 原始 `autoresearch` 更像交互式 agent loop，而本项目 baseline 仍然受 orchestrator 外层框架控制；
- 原始版本没有统一的 `Search/Replace` 契约，而本项目使用结构化 proposal；
- 原始版本没有 MAAR 或 `agent_groupchat` 那种多智能体路径；
- 本项目 baseline 的运行产物与状态落盘更完整，更利于比较与复盘。

因此，这个 baseline 更准确地说是：**在统一实验框架下，尽量模拟原始 autoresearch 单 worker 行为的对照臂。**

## 11. 关键设计选择与动机

### 11.1 为什么采用回合制同步裁决

项目最终没有采用异步滚动更新，而是采用了回合制同步裁决。这样做的好处是：

- 所有候选在同一轮使用同一起点；
- 候选结果可以直接比较；
- coordinator 与 shared candidate 的输入更清晰；
- 状态机更稳定，更容易复现与解释。

### 11.2 为什么 coordinator 按需触发

如果每轮都调用 coordinator，会带来额外的 token 成本和验证开销，而且很多轮根本没有足够信息进行合并。因此 coordinator 只在“存在多个正向候选”时触发，兼顾收益与成本。

### 11.3 为什么使用固定训练时间预算

固定时间预算是整个 benchmark 的定义基础。它使系统比较的是“在给定预算内谁学得更快”，而不是“理论上谁的最终最优点更高”。这与原始 `autoresearch` 的核心思想是一致的。

### 11.4 为什么引入共享经验文档

没有共享经验时，多轮实验会不断重复近似失败方向。引入 `program_exp.md` 的目的，是让系统在保留探索自由度的同时，具备最小限度的跨轮经验积累能力。

### 11.5 为什么 MAAR-only strict guard 与 fixed priors 不作用于 baseline

这些机制的目标是增强多智能体系统的效率与稳定性。如果把它们同步注入 baseline，对照实验就会被污染，难以回答“多智能体组织方式是否真的有价值”。因此，这些增强默认只属于 MAAR 与 `agent_groupchat`，不属于 baseline。

### 11.6 为什么新增 Agent Group Chat

在只有 MAAR 与单 worker baseline 的情况下，我们只能回答“并行 worker + 训练后 coordinator 是否优于单 worker”。但这无法回答另一个关键问题：**多智能体收益究竟来自并行探索，还是来自更好的协作组织方式。**

因此，新增 `agent_groupchat` 的动机在于提供第三种对照组织方式：

- 若它优于 baseline 但劣于 MAAR，说明训练后协调可能更适合当前问题；
- 若它接近或优于 MAAR，说明训练前协作在某些 benchmark 上更有潜力；
- 若它明显不稳定，则也能反向说明“显式协作”并不天然优于独立并行探索。

这使实验问题从二元比较扩展为对**多智能体组织结构本身**的比较。

## 12. 当前局限与后续工作

尽管系统已经具备真实 LLM、真实训练环境和长跑能力，但目前仍存在若干限制：

1. **当前对比还不是最严格的控制变量实验**  
   MAAR、baseline 与 `agent_groupchat` 之间除了 agent 数量不同，还存在 prompt/profile 与组织流程差异。

2. **Coordinator 的收益仍不稳定**  
   在部分实验中，coordinator 能提升最佳 worker；但在另一些实验中，它更像“重复最佳 worker”的选择器，而不是真正创造新组合。

3. **Agent Group Chat 目前稳定性不足**  
   真实 smoke 与长跑结果表明，`agent_groupchat` 已经能形成共享候选并完整跑通训练链路，但仍然存在较高 crash 率、较强 engineer 依赖以及群聊记忆中噪声累积的问题。

4. **当前 benchmark 更强调短时训练效率**  
   尤其在 `bench300` 下，系统可能倾向于选择更小、更省显存的模型，而不是更高容量的结构。

5. **多智能体系统目前没有完整的硬参数上限**  
   虽然已经有固定先验和 prompt 警告来避免明显愚蠢的结构膨胀，但还没有彻底静态限制所有可能导致 OOM 的形状修改。

6. **长跑稳定性仍受到外部 API 质量影响**  
   虽然已经解决了本地代理继承问题，但 provider timeout、rate limit 和模型波动仍会影响实验流畅度。

后续工作可沿四个方向继续推进：

- 构建更严格的控制变量对比实验；
- 进一步提升 coordinator 的真正合并能力；
- 降低 `agent_groupchat` 的 crash 率与 engineer 依赖；
- 在 `300/600/900` 三套 benchmark 上系统比较 MAAR、`agent_groupchat` 与单 worker baseline 的相对优势。

## 13. 一句话总结

本项目提出并实现了一个统一的自动研究实验框架，在同一套 orchestrator、Git worktree、结构化 proposal、训练执行器与共享记忆机制之上，对比了三种组织方式：单智能体 `autoresearch` 风格 baseline、训练后协调的 MAAR，以及训练前协作的 `agent_groupchat`。项目的核心目标不是直接训练出全局最优模型，而是验证：**在相同实验轮数与相同预算下，不同智能体组织结构是否会显著影响自动研究系统推进研究主线的效率与稳定性。**
