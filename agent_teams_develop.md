# Agent Group Chat 开发日志与推进路线

本文件用于记录 `agent_groupchat` 对照架构的开发进展、阶段目标、问题、决策与实验安排。  
它与主线 [develop.md](/home/yzy/yzycode/Multi-agent-AutoResearch/develop.md) 并行维护，避免把 MAAR 主线和新对照架构的推进过程混在一起。

## 当前目标

先完成一个可运行的 `agent_groupchat` 最小闭环：

- 新增 `architecture_mode=agent_groupchat`
- 在单轮内维护一个共享候选 worktree
- 让 `3` 个固定 specialist 按顺序对同一个共享候选做 `6` 次增量修改
- 每次成功 patch + preflight 后立即形成一次共享 commit
- 整轮只训练 `1` 次最终共享候选
- 维护 `groupchat_memory.md` 与 `groupchat_log.jsonl`

## 当前定义

- 架构名称：`agent_groupchat`
- 配置语义与 `maar` 分离：
  - `maar` 使用 `worker_count`
  - `agent_groupchat` 使用独立的 specialist 配置体系
- specialist 固定角色：
  - `architecture`
  - `optimizer_schedule`
  - `efficiency_memory`
- specialist 默认模型：
  - `glm-4.6v`
- 每轮固定：
  - `6` 个 turn
  - turn 顺序为 `architecture -> optimizer_schedule -> efficiency_memory` 重复两次
- 不启用 Team Lead
- 不启用训练后 coordinator merge

## 与现有实验臂的关系

- **baseline**
  - 单 worker
  - 无 coordinator
- **MAAR**
  - 多 worker
  - 训练后 coordinator merge
- **agent_groupchat**
  - 多 specialist
  - 训练前围绕同一共享候选顺序接力
  - 无训练后 merge

## 建议开发顺序

### 阶段 0：模式接入与配置骨架

目标：

- 在运行配置中新增 `agent_groupchat` 模式
- 为 specialist 角色、轮内 turn 数、群聊记忆路径预留配置位
- 不复用 `worker_count`，而是引入独立的 specialist 配置入口

完成标准：

- 不改主逻辑也能区分 `baseline / maar / agent_groupchat`
- `agent_groupchat` 的 specialist 数量可由 `specialist_roles` 派生，而不是借用 MAAR 的 worker 语义

### 阶段 1：共享候选 worktree 流程

目标：

- 新增共享候选 worktree 的初始化与重置逻辑
- 轮内所有 specialist 都基于同一个共享候选工作

完成标准：

- 一轮内可以连续接受多个 patch，并始终只维护一个共享候选

### 阶段 2：轮内接力协议

目标：

- 固定 `6` 个 turn 的执行顺序
- 每个 turn 都能发起 proposal
- patch 成功 + preflight 通过后立即提交共享 commit

完成标准：

- 每轮结束前不会启动训练
- 只有最终共享候选进入训练

### 阶段 3：显式持久记忆

目标：

- 新增 `groupchat_memory.md`
- 新增 `groupchat_log.jsonl`
- specialist 调用时读取 `program_exp.md + groupchat_memory.md`

完成标准：

- 下一轮 specialist 能读取上一轮的群聊记忆摘要

### 阶段 4：结果统计与对比

目标：

- summary 中补充 `train_jobs_executed`
- 明确记录每轮 accepted/rejected turn
- 为后续和 MAAR/baseline 做相同轮数、相同训练次数对比做准备

完成标准：

- 可以明确回答：
  - 一共训练了多少次
  - 一轮里接受了多少个微提交
  - 是否出现改写/回滚

## 近期优先事项

- [x] 新增 `agent_groupchat` 模式配置
- [x] 设计共享候选 round state
- [x] 设计 `groupchat_log.jsonl`
- [x] 设计 `groupchat_memory.md`
- [x] 接入固定 specialist prompt profile
- [x] 打通 mock/replay 单轮接力
- [x] 打通轮末 engineer fallback
- [ ] 打通真实 LLM 单轮接力
- [ ] 跑一次 `bench300` 的短程 smoke

## 关键注意事项

- 该架构的价值在于“训练前协作”，不要偷偷退化成“伪 MAAR”
- 整轮只训练 `1` 次是它与当前 MAAR 的关键差异之一
- 共享候选允许被后续 specialist 局部改写或回滚，但必须通过新的 `Search/Replace` 明确表达
- 群聊记忆必须显式可审计，不依赖 provider session
- specialist 采用软分工：有主责领域，但允许必要的跨界整合
- `groupchat_memory.md` 应只在每轮结束后统一更新，不要在每个 turn 后立即重写
- rejected turn 也应消耗固定 turn 配额，不能在轮内无限重试

## 日志模板

后续每推进一个小阶段，建议按下面格式补记录：

### YYYY-MM-DD：阶段标题

#### 本次目的

- 

#### 关键改动

- 

#### 遇到的问题

- 

#### 决策

- 

#### 验证

- 

## 初始记录

### 2026-03-23：确认 agent_groupchat 架构方向

#### 本次目的

- 把原先的 `Team Lead + specialist` 方案重构为真正更接近“agent 群聊”的对照架构

#### 决策

- 不采用显式 Team Lead
- 采用固定职责分工的 `3` 个 specialist
- 每轮内部围绕一个共享候选顺序接力
- 每个 specialist 每轮发言 `2` 次，共 `6` 个 turn
- patch + preflight 通过后立即写入共享候选
- 整轮结束后只训练 `1` 次最终共享候选
- 保留显式持久记忆：
  - `program_exp.md`
  - `groupchat_memory.md`

#### 当前状态

- 计划文档已收敛
- 尚未开始代码实现

### 2026-03-23：补充 agent_groupchat 关键实现边界

#### 本次目的

- 在开始开发前，把会影响实现边界与实验公平性的细节进一步写死

#### 决策

- specialist 采用**软分工**，不是硬隔离
- `groupchat_log.jsonl` 在每个 turn 后立即追加
- `groupchat_memory.md` 只在每轮结束后由 orchestrator 汇总更新
- rejected turn 也消耗 turn 配额，不允许同 turn 即时重试
- `agent_groupchat` 不复用 `worker_count`，而是采用独立的 specialist 配置体系

#### 影响

- 该架构会更像真实的训练前协作，而不是 rigid pipeline
- 群聊记忆不会被轮内短期噪声污染
- 与 MAAR 的探索成本对比会更公平
- 配置层面不会把 `maar` 与 `agent_groupchat` 的人数语义混在一起

#### 当前状态

- 计划文档已根据上述边界更新
- 仍未开始代码实现

### 2026-03-24：阶段 0 配置骨架落地

#### 本次目的

- 先让系统在配置与持久化层面“认识” `agent_groupchat`
- 暂时不进入共享候选 worktree 与轮内接力主逻辑

#### 关键改动

- 新增独立目录 [agent_teams](/home/yzy/yzycode/Multi-agent-AutoResearch/agent_teams)
- 新增 `AgentGroupChatConfig`
- 在 `RunConfig` 中加入：
  - `architecture_mode`
  - `agent_groupchat`
- 在 `RunState` / `run.json` 中落盘：
  - `architecture_mode`
  - `agent_groupchat` 默认配置
- 在 run layout 中预留：
  - `groupchat_memory.md`
  - `groupchat_log.jsonl`

#### 决策

- `agent_groupchat` 不复用 `worker_count` 作为人数语义
- specialist 数量由 `specialist_roles` 派生
- 当前阶段仍保留 `worker_count` 以兼容现有初始化逻辑，但在语义上与 groupchat 配置解耦

#### 验证

- 新增阶段 0 配置测试
- 已通过：
  - `python3 -m unittest tests.test_agent_teams_stage0 tests.test_stage1 -v`

### 2026-03-24：阶段 1 共享候选 worktree 骨架落地

#### 本次目的

- 为 `agent_groupchat` 提供独立的 shared candidate branch/worktree
- 先打通初始化与 reset 逻辑，为后续轮内接力做准备

#### 关键改动

- 在 run topology 中新增：
  - `shared_candidate_branch`
  - `shared_candidate_worktree`
- `agent_groupchat` 初始化时额外创建：
  - `autoresearch/<run_tag>/shared-candidate`
  - `workspaces/shared-candidate`
- `sync_all_to_baseline()` 现在也会重置 shared candidate worktree

### 2026-03-26：轮末 engineer fallback 落地

#### 本次目的

- 当最终共享候选在真实训练中 crash 时，不直接判整轮失败
- 改为让一个单独的 `engineer` agent 在尽量保留 specialist 方案的前提下做一次保守 debug，并允许重训一次

#### 关键改动

- `AgentGroupChatConfig` 新增：
  - `engineer_model_name`
  - `engineer_prompt_profile`
- `ActorRole` 新增：
  - `ENGINEER`
- `RoundState` 新增：
  - `groupchat_engineer_result`
- `agent_groupchat` runner 在 final candidate crash 时新增 engineer repair 路径
- summary 现在会区分：
  - 原始 `groupchat_result`
  - engineer 修复后的 `groupchat_engineer_result`
  - 真实训练次数

#### 决策

- engineer 默认模型使用 `glm-4.7`
- engineer 只允许做一次单补丁保守修复
- engineer 的目标是修 crash，不是重新设计整轮方案

#### 验证

- 新增 runner 级测试，覆盖：
  - final crash -> engineer repair -> retrain success
- 已通过：
  - `python3 -m unittest tests.test_agent_teams_stage2 tests.test_live_agent_teams tests.test_agent_teams_stage0 tests.test_stage6 -v`

### 2026-03-26：基于真实 smoke 收紧 engineer 红线

#### 观察

- 真实 smoke 中，最终共享候选先因为半截 SwiGLU 改动 crash：
  - `forward()` 读取了 `self.c_gate`
  - 但 `__init__` 并未定义 `c_gate`
- engineer 补上 `c_gate` 后，仍保留了 value embedding / attention 张量几何不一致的结构改动
- 结果 engineer 重训再次因 shape/view 路径不一致而 crash
- 后续真实 smoke 又暴露出第二类问题：
  - specialist 把单层 `ve_gate` 改成了 `Sequential`
  - 但初始化逻辑仍按 `.weight` 直接访问
  - engineer 修正初始化后，残留结构又在优化器阶段暴露出 `None` gradient 问题

#### 决策

- 把以下内容写入 engineer prompt 的硬红线：
  - 不允许保留未在 `__init__` 中完整定义的新模块属性读取
  - 不允许保留半截门控或半截结构迁移
  - 不允许随意改 `value_embeds` 维度、`n_head/n_kv_head/head_dim` 关系或 attention/value 的张量几何
  - 不允许把单层模块容器化后却不同时修正初始化和 `.weight/.bias` 访问路径
  - 不允许保留大概率拿不到梯度的新增参数
  - 不允许为修一个 crash 再叠加第二个无关结构改动
- 后续又进一步收紧为：
  - specialist 不应随意改变 `value_embeds`、`ve_gate`、norm helper、attention projection 这类共享组件的表示方式
  - engineer 在处理共享接口不一致时，必须默认扫描 `init_weights`、`estimate_flops`、`forward` 等同名调用点，而不是只修第一处报错
- engineer 默认应优先回滚最小坏片段，而不是扩展一个已经部分损坏的架构改动

#### 验证

- 新增 prompt 级回归测试，确认上述红线已进入 `agent_groupchat_engineer` system prompt

#### 决策

- 阶段 1 只建立共享候选底座，不开始接入 turn-level proposal 执行
- 现有 `merge` worktree 仍然保留，以避免影响当前 MAAR 主线

#### 验证

- 新增 shared candidate 初始化与 reset 测试
- 已通过：
  - `python3 -m unittest tests.test_agent_teams_stage0 tests.test_stage1 -v`

### 2026-03-24：阶段 2 轮内接力最小闭环

#### 本次目的

- 先打通 `agent_groupchat` 的 replay/mock 单轮接力
- 让固定 `6` 个 turn 能围绕 shared candidate 顺序执行
- 整轮结束后只训练 `1` 次最终共享候选

#### 关键改动

- 新增 [agent_teams/runner.py](/home/yzy/yzycode/Multi-agent-AutoResearch/agent_teams/runner.py)
- 新增 groupchat turn 结果结构与状态：
  - `GroupChatTurnResult`
  - `GroupChatTurnStatus`
- `RoundState` 现可记录：
  - `groupchat_turns`
  - `groupchat_result`
- `RunLayout` 新增：
  - `groupchat/`
  - turn 级 artifact 目录
- 每个 accepted turn 立即形成 shared commit
- 整轮结束后才运行一次最终训练

#### 决策

- 当前阶段只支持 replay/mock 单轮接力，不接真实长跑入口
- turn 失败时会恢复到上一份 accepted 的 shared commit
- 现有 MAAR 的 `WorkerRoundRunner` 与 live runner 先不改

#### 验证

- 新增阶段 2 测试：
  - accepted turn 累积后只训练一次
  - rejected turn 后共享候选会恢复到上一份 accepted state
- 已通过：
  - `python3 -m unittest tests.test_agent_teams_stage2 tests.test_agent_teams_stage0 tests.test_stage1 tests.test_stage4 -v`

### 2026-03-24：阶段 3 显式持久记忆落地

#### 本次目的

- 让 `groupchat_memory.md` 从空文件变成真正的轮末记忆
- 确保下一轮 specialist 能读取上一轮的 team memory

#### 关键改动

- 新增 [agent_teams/memory.py](/home/yzy/yzycode/Multi-agent-AutoResearch/agent_teams/memory.py)
- `groupchat_memory.md` 现在有独立模板与轮末写入逻辑
- `AgentGroupChatRoundRunner` 在每轮结束后会调用 `GroupChatMemoryStore.record_round()`
- `StateStore.initialize_run_files()` 在 `agent_groupchat` 模式下会初始化 `groupchat_memory.md`

#### 决策

- `groupchat_memory.md` 只在每轮结束后统一更新
- 当前阶段记录的是“保留过的 turn、被拒绝的 turn、以及 relay 级别总结”
- 先不做更复杂的 family-level 压缩，等真实 short smoke 后再决定是否需要进一步抽象

#### 验证

- 新增测试：
  - 轮末会把 accepted/rejected turn 写入 `groupchat_memory.md`
  - 第二轮 specialist 请求能读到第一轮的 groupchat memory
- 已通过：
  - `python3 -m unittest tests.test_agent_teams_stage2 tests.test_agent_teams_stage0 tests.test_stage1 tests.test_stage4 -v`

#### 当前状态

- 阶段 0、阶段 1、阶段 2、阶段 3 已完成最小闭环
- 当前 `agent_groupchat` 具备：
  - 独立配置与持久化
  - shared candidate worktree
  - replay/mock 单轮接力
  - `groupchat_log.jsonl`
  - 轮末 `groupchat_memory.md`
- 尚未完成：
  - 真实 LLM live smoke
  - summary / 对比统计整合

### 2026-03-24：阶段 4A 固定 specialist prompt profile 落地

#### 本次目的

- 让 `agent_groupchat` 的 specialist 真正拥有独立于 MAAR worker 的 prompt/profile
- 为后续真实 LLM live runner 做准备

#### 关键改动

- `AgentGroupChatConfig` 新增：
  - `specialist_prompt_profile`
- 新增默认 profile：
  - `agent_groupchat_specialist`
- `build_agent_runner()` 现在在 `ActorRole.SPECIALIST` 下优先使用：
  - `agent_groupchat.specialist_model_name`
  - `agent_groupchat.specialist_prompt_profile`
- specialist system prompt 现在按角色区分：
  - `architecture`
  - `optimizer_schedule`
  - `efficiency_memory`
- `agent_teams.__init__` 改为懒加载，避免配置层与 runner 层再次形成包级环依赖

#### 决策

- specialist 共享相同执行契约，但系统提示要体现不同主责重点
- 真实 live runner 接入前，先把 profile 和配置语义分离完成

#### 验证

- 已通过：
  - `python3 -m unittest tests.test_stage6 tests.test_agent_teams_stage0 tests.test_agent_teams_stage2 -v`
  - `python3 -m py_compile agent_teams/__init__.py agent_teams/config.py agent_teams/runner.py agent_teams/memory.py orchestrator/agents.py orchestrator/__init__.py orchestrator/persistence.py tests/test_agent_teams_stage0.py tests/test_agent_teams_stage2.py tests/test_stage6.py`

### 2026-03-24：阶段 4B live runner 与 launcher 接入

#### 本次目的

- 让 `agent_groupchat` 具备独立于 MAAR 的真实运行入口
- 在真实入口中补上 round-level summary 和 `program_exp.md` 写回

#### 关键改动

- 新增 [agent_teams/live.py](/home/yzy/yzycode/Multi-agent-AutoResearch/agent_teams/live.py)
  - `run_agent_groupchat_experiment()`
- 新增 [scripts/run_glm_agent_groupchat.py](/home/yzy/yzycode/Multi-agent-AutoResearch/scripts/run_glm_agent_groupchat.py)
- `AgentGroupChatRoundRunner` 现在在轮末同时写回：
  - `program_exp.md`
  - `groupchat_memory.md`
- `agent_groupchat` summary 现在会记录：
  - `specialist_count`
  - `specialist_roles`
  - `turn_order`
  - `train_jobs_executed`
  - `accepted_turn_count`
  - `rejected_turn_count`

#### 决策

- 真实入口保持独立，不复用 `run_glm_multi_agent.py`
- 当前阶段先支持 fresh run，不先做 resume
- `train_jobs_executed` 统计 groupchat 每轮的最终训练次数，不把 baseline measurement 混进去

#### 验证

- 新增 [tests/test_live_agent_teams.py](/home/yzy/yzycode/Multi-agent-AutoResearch/tests/test_live_agent_teams.py)
- 已通过：
  - `python3 -m unittest tests.test_live_agent_teams tests.test_agent_teams_stage2 tests.test_agent_teams_stage0 tests.test_stage6 -v`
  - `python3 -m py_compile agent_teams/__init__.py agent_teams/config.py agent_teams/runner.py agent_teams/memory.py agent_teams/live.py orchestrator/agents.py orchestrator/memory.py orchestrator/persistence.py scripts/run_glm_agent_groupchat.py tests/test_live_agent_teams.py`

#### 当前状态

- `agent_groupchat` 已具备真实入口与独立 launcher
- 下一步最合适的是跑一次 `bench300` 的真实短程 smoke，验证：
  - specialist prompt 是否能产生差异化 proposal
  - groupchat 单轮 6 turn 是否稳定
  - live artifacts 和 summary 是否符合预期
