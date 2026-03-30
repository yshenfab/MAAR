# Agent Group Chat 对照架构计划

本文档描述一个新的多智能体组织方式，用于与当前 `MAAR` 以及单 worker baseline 同时做对比。  
该方案不替代现有 MAAR，而是新增一个新的实验臂。

## 1. 摘要

新的对照架构采用一种**固定职责分工、但职责边界为软分工的群聊式共享候选模式**。

其核心形态如下：

- `3` 个固定 specialist
- 无显式 Team Lead
- 每个 specialist 采用固定职责分工：
  - `architecture`
  - `optimizer_schedule`
  - `efficiency_memory`
- 每轮内部围绕**同一个共享候选**做顺序接力式协作
- 每个 specialist 每轮发言 `2` 次，共 `6` 个轮内 turn
- 每次 turn 只允许做一次增量 `Search/Replace`
- patch 能应用且通过 preflight 后，立即写入共享候选，视作一次“共享 commit”
- 后续 specialist 可以继续叠加，也允许通过新的 patch 对前面改动做局部改写或回滚
- 整轮结束后只训练 **1 个** 最终共享候选
- 若最终共享候选训练 crash，则允许触发一个 `engineer` agent 做一次保守 debug，再重训一次
- 不再保留当前 MAAR 的训练后 coordinator merge

该设计的核心目标是引入一种真正意义上的**训练前多智能体协作**，让候选在轮内自然融合，而不是在轮末再显式合并。

## 2. 架构模式

系统新增一种新的运行模式：

- `baseline`
- `maar`
- `agent_groupchat`

其中 `agent_groupchat` 是本计划关注的新模式。

在配置语义上，`agent_groupchat` 与现有 `maar` 明确解耦：

- `maar` 继续使用 `worker_count`
- `agent_groupchat` 使用 `specialist_roles` 推导 specialist 数量
- 两种架构不共享同一个“人数配置字段”

默认配置如下：

- specialist 配置体系独立于 `worker_count`
- specialist 数量：`3`
- specialist 模型：`glm-4.6v`
- 无 Team Lead
- 无 post-train coordinator
- 每轮 `6` 个 turn
- turn 顺序固定为：
  - `architecture`
  - `optimizer_schedule`
  - `efficiency_memory`
  - 再重复一次相同顺序

## 3. 单轮执行逻辑

每轮执行流程如下：

1. 冻结当前 baseline。
2. 基于 baseline 初始化一个**共享候选 worktree**。
3. `architecture` specialist 读取共享候选与共享上下文，提出一次增量 patch。
4. 若 patch 能应用并通过 preflight，则立即写入共享候选并创建一次共享 commit。
5. `optimizer_schedule` specialist 在更新后的共享候选上继续提出自己的增量 patch。
6. `efficiency_memory` specialist 再在新的共享候选上继续工作。
7. 完成第一轮三人接力后，再重复一轮相同顺序，共得到 `6` 个 turn。
8. 整轮结束后，仅对最终共享候选执行 **1 次** 真实训练。
9. 若该最终候选严格优于当前 baseline，则推进主线；否则丢弃整轮共享候选。

因此，该架构不是“多个独立 worker 各自训练”，而是“多个 specialist 在一轮内部共同打磨一个方案，最后只训练一次”。

若最终共享候选在真实训练时 crash，则执行额外的轮末工程修复流程：

1. orchestrator 保留当前共享候选与 crash 日志；
2. 调用单独的 `engineer` agent；
3. engineer 在尽量保留已接受 specialist 方向的前提下，对当前共享候选做一次最小 debug；
4. 若修复后的 patch 能通过 preflight，则允许再训练一次；
5. engineer 修复后的候选若优于 baseline，则允许直接推进主线。

## 4. Specialist 职责分工

### 4.0 软分工原则

本架构中的 specialist 采用**软分工**，而不是硬隔离。

这意味着：

- 每个 specialist 都有自己的主责领域；
- 但后续 specialist 在必要时可以跨界小改，以整合前面已接受的共享候选；
- specialist 不应被限制为“只能修改自己名下的参数或模块”，否则群聊会退化成排队发言，而不是协作式共创。

因此，职责分工的作用是**提供默认关注点**，而不是形成严格边界。

### 4.1 `architecture`

负责：

- 模型结构
- attention 机制
- MLP 结构
- residual / normalization / embedding 相关设计

其职责是提出结构层面的方向，但不负责主导训练 schedule 或显存预算问题。不过在必要时，它仍然可以为了整合共享候选而做少量跨界修改。

### 4.2 `optimizer_schedule`

负责：

- optimizer 选择与超参数
- learning rate schedule
- warmup / warmdown / decay
- batch geometry、梯度累积与其他训练策略

其职责是围绕当前共享候选做训练动力学层面的调整。不过在必要时，它仍然可以对结构候选做局部修正，以保证训练动力学可行。

### 4.3 `efficiency_memory`

负责：

- 短预算训练效率
- 显存压力与结构膨胀风险
- benchmark 适配
- 已知高风险模式规避

其职责不是简单保守，而是把共享候选往“更适合当前 budget 和 GPU”的方向推。不过在必要时，它也可以为了保证整体候选可训练而改动前面 specialist 的结构或调度选择。

## 5. 共享候选与改写权限

一轮中只有一个共享候选。每个 specialist 都在它的当前状态上工作。

默认接受规则：

- patch 能成功应用
- patch 通过当前模式下的 preflight

满足以上条件后，该 patch 就被接受，并：

- 写入共享候选
- 创建一次共享 commit
- 记录到群聊日志

后续 specialist 的权限如下：

- 可以继续叠加新的增量修改
- 可以通过新的 `Search/Replace` 对前面已接受的改动做**局部改写**
- 可以在必要时对前面某段逻辑做**局部回滚**

也就是说，代码历史是追加式的，但共享候选的最终内容允许在轮内被后续 specialist 修正。

默认 turn 成本规则如下：

- 每个 turn 无论 `accepted` 还是 `rejected`，都消耗一个固定 turn 配额；
- rejected turn 不会立即重试；
- 下一位 specialist 总是基于“上一份 accepted 的共享候选状态”继续工作。

这样做的原因是：

- 让 `agent_groupchat` 的探索失误与 MAAR 一样具有真实成本；
- 避免把单轮 turn 机制变成无限重试；
- 保持实验对比的公平性与可解释性。

## 6. 显式持久记忆

该架构不依赖隐式连续会话。默认规则是：

- 每个 specialist 的每次调用都是**新会话**
- 跨轮上下文只通过显式文档传递

默认维护两类共享文档：

### 6.1 `program_exp.md`

沿用当前 MAAR 的方向性经验文档，记录：

- 什么机制可能改善结果
- 什么机制可能恶化结果
- 哪些方向容易 crash 或低效

### 6.2 `groupchat_memory.md`

新增群聊协作层面的显式持久记忆，记录：

- 最近若干轮哪些 specialist 的修改被保留
- 哪些类型的 patch 经常被后续 specialist 改写或回滚
- 哪些接力模式更有效
- 当前共享候选最重要的团队级状态摘要

默认读取规则：

- specialist：读取 `program_exp.md + groupchat_memory.md`
- 不存在额外 leader 记忆

默认写入规则：

- 由 orchestrator 单写
- specialist 不直接自由编辑

默认更新节奏如下：

- `groupchat_log.jsonl` 在每个 turn 结束后立即追加；
- `groupchat_memory.md` 只在**每轮结束后**由 orchestrator 汇总更新；
- rejected turn 会记录到 `groupchat_log.jsonl`，并可在轮末以压缩摘要形式进入 `groupchat_memory.md`；
- 不在每个 turn 后立即重写 `groupchat_memory.md`，以避免短期噪声主导后续 specialist。

## 7. 与现有 MAAR 的区别

当前 `MAAR` 的核心流程是：

- 独立 worker 各自产出候选
- 每个候选各自训练
- 若有多个正向候选，再由 coordinator 在训练后做一次 merge 尝试

新的 `agent_groupchat` 则是：

- 固定 specialist 在训练前围绕同一个共享候选顺序接力
- 候选在轮内自然融合
- 整轮只训练一次最终共享候选
- 不执行训练后 coordinator merge

因此两者的本质区别是：

- `MAAR`：训练后协调
- `agent_groupchat`：训练前协作

## 8. 保持不变的部分

为了让对比更公平，以下设置默认与当前 MAAR 保持一致：

- 相同 benchmark family：`bench300 / bench600 / bench900`
- 相同 baseline 起点解析机制
- 相同 `Search/Replace` 执行契约
- 相同 patcher / executor / runtime / proxy isolation
- 相同训练环境与 GPU 资源调度
- 相同 `program_exp.md`
- 相同 MAAR fixed priors
- 相同训练预算与评价指标

新的实验臂只改变**多智能体如何在训练前协作形成候选**，而不改变底层执行框架。

## 9. 运行产物扩展

为了便于后续论文分析，`agent_groupchat` 模式下每轮至少需要额外记录：

- 每个 turn 的 specialist proposal
- 每个 turn 的 `accepted / rejected` 状态
- 若被接受，对应的共享 commit id
- 若被拒绝，失败原因
- 整轮的最终共享候选
- `groupchat_memory.md` 的轮后更新结果

建议新增一个逐条追加的日志文件，例如：

- `groupchat_log.jsonl`

其每条记录至少包含：

- `turn_index`
- `specialist_role`
- `proposal_path`
- `status`
- `shared_commit`
- `failure_reason`

## 10. 对比统计口径

由于该模式每轮只训练 `1` 次，而当前 MAAR 每轮可能训练多个独立候选，因此后续对比必须同时记录两套口径：

- **相同轮数**
  - 比较相同 round 数下的 best-so-far `val_bpb`
- **相同训练次数**
  - 比较相同真实训练 job 数下的 best-so-far `val_bpb`

因此 summary 中至少需要显式统计：

- `round_count`
- `train_jobs_executed`
- `best_so_far_val_bpb`
- `accepted_turn_commits`
- `rejected_turns`
- `rewrite_or_rollback_events`

## 11. 测试要求

实现该模式后，至少需要覆盖以下测试场景：

- `agent_groupchat` 模式能正确启用共享候选路径
- 不触发 Team Lead
- 不触发 post-train coordinator
- 每轮按固定 specialist 顺序执行 `6` 个 turn
- 某个 turn patch 失败时，共享候选保持不变
- 后续 specialist 能基于上一个 accepted state 继续
- 后续 specialist 能对前面已接受的改动做局部改写或回滚
- rejected turn 会消耗 turn 配额，且不会触发同 turn 重试
- 整轮结束后只执行 `1` 次训练
- `groupchat_memory.md` 能在下一轮被 specialist 读取
- round state 和 summary 能正确记录真实训练次数与群聊日志统计
- 现有 `baseline` 和 `maar` 模式行为不受影响

## 12. 当前默认假设

- 该方案是新的对照架构，不替代现有 MAAR
- 正式名称采用 `agent_groupchat`
- `agent_groupchat` 与 `maar` 的人数配置语义分离：
  - `maar` 使用 `worker_count`
  - `agent_groupchat` 使用 `specialist_roles`
- 固定 specialist 角色为：
  - `architecture`
  - `optimizer_schedule`
  - `efficiency_memory`
- specialist 模型统一为 `glm-4.6v`
- 不引入 Team Lead
- 不引入 post-train coordinator
- 每轮固定 `6` 个 turn
- specialist 采用软分工：有主责领域，但允许必要的跨界小改与整合
- 每次调用都是新会话，跨轮只依赖显式持久记忆
- `groupchat_log.jsonl` 每 turn 追加，`groupchat_memory.md` 每轮结束后统一更新
- rejected turn 也消耗 turn 配额，不进行同 turn 重试
- 当前仍维持单次 `Search/Replace` 契约
- 当前不扩展到多段补丁或多文件修改
