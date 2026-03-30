# 开发日志与推进路线

本文件用于记录多智能体 `autoresearch` orchestrator 的开发进度、阶段目标、实验安排、问题与决策。  
更新原则：

- 每完成一个可验证的小阶段，就补一条日志。
- 每遇到一个会影响架构或实现顺序的问题，就记录“现象 + 原因 + 决策”。
- 不把这里写成长篇设计文档；设计以 `development_plan.md` 为准，这里只记录推进过程和执行顺序。

## 当前目标

先完成一个可运行的 V1 主流程：

- 基于 `autoresearch/` 创建 worker worktree
- 运行回合制实验 loop
- 应用 `Search/Replace` 补丁
- 运行训练并解析 `val_bpb`
- 做 worker 裁决与 coordinator 合并
- 接入真实 LLM backend

严格 benchmark 暂时不作为主线阻塞项，先保证流程可跑、日志完整、结果可复盘。

## 当前对比口径

- **MAAR**：本项目的多智能体系统，由 `orchestrator + 多个 workers + 一个按需触发的 coordinator` 组成
- **Autoresearch-style baseline**：在本项目中的单智能体对照臂，等价于“只有一个 worker 在工作”，不启用 coordinator
- 后续我们想验证的不是“哪句 prompt 更强”，而是：
  - 在相同轮数下，MAAR 是否能比单 worker 的 Autoresearch-style 流程得到更好的 best-so-far `val_bpb`
  - 在相同轮数下，MAAR 是否更容易更早找到严格优于当前主线的候选
- 为了减少混杂因素，单 / 多智能体实验臂尽量共享：
  - 同一训练环境
  - 同一代码基线
  - 同一模型族
  - 同一运行产物记录方式

## 建议开发顺序

### 阶段 0：项目骨架与约定

目标：

- 建立 orchestrator 代码目录结构
- 明确配置文件、状态文件、运行产物目录
- 确定 `RunConfig` / `RoundState` / `ExperimentResult` 等核心 schema

完成标准：

- 能清楚回答“代码放哪里、日志放哪里、每轮状态写到哪里”
- 运行目录 `runs/<tag>/` 结构固定

建议产物：

- `orchestrator/` 或等价源码目录
- 配置与数据模型
- 基础 README 或注释说明

### 阶段 1：Git 工作区与状态持久化

目标：

- 实现 baseline 分支、worker worktree、merge worktree 的创建和重置
- 能在每轮开始时把所有 worktree 同步到同一个 baseline commit
- 能把运行信息写入 `run.json`、`results.tsv`、`experiments.jsonl`

完成标准：

- 不依赖 LLM，也能完成一次“初始化 -> 创建 worktree -> 重置 -> 收尾”
- 路径、分支名、commit 来源都可追踪

建议实验：

- 用假数据模拟一个 run，验证 worktree 生命周期
- 多次重置同一个 worker，确认不会污染 baseline

### 阶段 2：补丁器与预检

目标：

- 实现 `Search/Replace` 补丁应用器
- 限制只能修改 `train.py`
- 加入语法检查和最小预检

完成标准：

- 能稳定处理“精确匹配 / 空白容忍匹配 / 无匹配 / 多匹配 / 越权修改”
- 成功应用后可自动导出 git diff

建议实验：

- 人工构造 5 到 10 个补丁样例覆盖正常和异常路径
- 验证 diff 输出是否足够用于审阅与回放

### 阶段 3：训练执行器与日志解析

目标：

- 封装训练命令执行
- 采集 `run.log`
- 解析 `val_bpb`、`peak_vram_mb`、`training_seconds`、`total_seconds`
- 正确处理 crash、timeout、空输出和异常退出

完成标准：

- 不依赖真实 LLM，也能用手工补丁跑通“一次候选实验”
- 执行槽在成功和失败时都能正确释放

建议实验：

- 跑一次原始 `train.py` baseline
- 人工制造一个语法错误补丁，确认 crash 路径能落盘
- 人工制造一个不会提升的安全小改动，确认结果能被正确记录

### 阶段 4：回合制 worker loop

目标：

- 把“基于 baseline 生成候选 -> 应用补丁 -> 提交候选 commit -> 执行训练 -> 记录结果”串成一轮
- 支持多个 worker，但默认单执行槽串行验证
- 完成本轮最佳 worker 的裁决

完成标准：

- 在 mock/replay proposal 下，能完整跑完至少 1 到 2 轮
- `positive_results` 和 `selected_result` 逻辑正确

建议实验：

- 先用 1 worker 跑通
- 再用 2 workers 模拟“一个提升、一个不提升”
- 再模拟“两者都不提升”和“两者都 crash”

### 阶段 5：Coordinator 合并路径

目标：

- 在同一轮至少两个正向候选时触发 coordinator
- 收集 top-2 候选的 proposal、motivation、diff、metrics
- 在 merge worktree 中应用 merged proposal 并验证
- 实现 merged 优先晋升与失败回退

完成标准：

- 少于两个正向候选时不触发
- merged 优于最佳 worker 时能晋升
- merged 等于、差于或崩溃时能回退到最佳 worker

建议实验：

- 用 replay proposal 人工构造一个“可合并成功”的路径
- 构造一个“merged 补丁失败”的路径
- 构造一个“merged 跑分不如最佳 worker”的路径

### 阶段 6：真实 LLM API 接入

目标：

- 在主流程已经被 mock/replay 跑通后，再接入真实 LLM
- 优先接入轻量开源 agent engine，并保留统一 `AgentRunner` 抽象
- 先接 worker，再接 coordinator

接入时机：

- 阶段 1 到阶段 5 完成基本验证后开始
- 不建议在 worktree、补丁器、执行器还不稳定时就接真实 LLM，否则问题很难定位

推荐顺序：

1. 先接 `PydanticAI`
2. 打通一个真实 worker proposal
3. 再接真实 coordinator proposal
4. 最后补 mock/replay 与真实 backend 的统一切换

完成标准：

- 真实 LLM 能稳定返回结构化 proposal
- 调用日志、失败重试、原始输出都能留档
- 至少能完成一次“真实 worker -> 训练 -> 裁决”的小规模 run

建议实验：

- `1 worker + 1 round + 真实 LLM`
- `1 worker + 2 rounds + 真实 LLM`
- `2 workers + coordinator + 真实 LLM` 小规模试跑

### 阶段 7：稳定化与长跑准备

目标：

- 把日志、错误信息、运行恢复能力补齐
- 让系统适合连续跑多个 round
- 为后续对比实验保留足够数据

完成标准：

- 运行被中断后能够知道停在哪一轮
- 每个实验的 proposal、diff、metrics、agent 输入输出都能回溯
- 常见失败原因能快速定位

建议实验：

- 跑一次 3 到 5 轮的短程真实实验
- 观察 proposal failure rate、crash rate、keep rate
- 看 coordinator 是否真的带来收益，而不只是增加复杂度

### 阶段 8：后续工作

暂不阻塞主线，但后面要做：

- 与原始单智能体 `autoresearch` 的对比实验
- 更系统的 benchmark harness
- 可视化分析脚本
- 经验总结与 lesson memory

## 近期优先事项

- [x] 建立 orchestrator 目录和核心 schema
- [x] 实现 worktree manager
- [x] 实现 patcher 与 preflight
- [x] 实现 training executor 与日志解析
- [x] 跑通 mock/replay 单轮实验
- [x] 实现 coordinator merge 路径
- [x] 接入真实 LLM worker
- [x] 接入真实 LLM coordinator
- [x] 做一次真实 GLM 联网 smoke run
- [x] 新增可重复的真实 multi-worker pilot 脚本
- [x] 做一次 2 workers 的真实小规模试跑
- [x] 做一次 2 workers 的多轮短程试跑
- [x] 把 preflight / executor 绑定到可解析的运行时环境配置层
- [x] 为 autoresearch 准备真实可用的 Python 运行环境
- [x] 跑通真实 autoresearch 的数据准备
- [x] 同步创建 `autoresearch-3090` 副本
- [x] 确立 3090 上可长期使用的 baseline 配置
- [x] 建立单智能体 baseline 臂入口与 `autoresearch-3090-baseline`
- [x] 将默认智谱模型切换到 `glm-4.7`
- [x] 建立真实多智能体 launcher
- [x] 为危险 runtime side effects 增加 preflight 拦截
- [x] 设计并接入每个 run 独立的 `program_exp.md` 共享经验文档
- [x] 修复长跑继承本地代理环境变量导致的 LLM 请求中断问题
- [x] 将单 worker baseline 中最无价值的高风险失败模式整理成 MAAR 固定先验
- [ ] 提高真实 worker proposal 的稳定性，减少函数体重写和缩进错误

## 实验安排建议

### 第一批实验：纯本地无 LLM 验证

目的：

- 验证 orchestration 本身，而不是验证模型研究效果

建议内容：

- baseline 跑通
- 手工补丁跑通
- replay proposal 跑通
- coordinator 回退逻辑跑通

### 第二批实验：真实 LLM 小规模试跑

目的：

- 验证真实 proposal 的质量和稳定性

## 近期运行时决策

### 2026-03-20：默认禁止实验脚本继承本地代理变量

现象：

- 长跑在前几轮正常后开始连续出现 `proposal_failed`
- 错误从 `timed out` 演变为 `Connection refused`
- 智谱官方 endpoint 仍然是 `https://open.bigmodel.cn/...`，但进程环境中存在：
  - `HTTP_PROXY=http://127.0.0.1:7891`
  - `HTTPS_PROXY=http://127.0.0.1:7891`

原因判断：

- 实验脚本继承了本地代理环境变量
- 该代理依赖 SSH / VS Code 转发或本地代理进程
- 用户断开连接后，服务器侧继续长跑时再访问 `127.0.0.1:7891`，于是出现 `Connection refused`

决策：

- 对所有实验 launcher 默认清空代理环境变量，只影响 launcher 进程及其子进程，不修改用户全局 shell / VS Code / Codex 环境
- agent HTTP client 额外显式禁用系统代理读取，避免遗漏
- 训练子进程和 preflight import-check 子进程也统一使用清洗后的环境

影响范围：

- MAAR 多智能体入口
- 单 worker baseline 入口
- 长跑 launcher
- smoke / pilot / runtime probe 脚本

预期结果：

- 用户关闭 SSH / VS Code 会话后，长跑不再因为本地代理消失而中断

### 2026-03-21：把 baseline 中低价值高风险失败模式沉淀为 MAAR 固定先验

现象：

- 单 worker baseline 长跑里出现了多类“非常昂贵但收益很差”的修改：
  - 直接增大 `DEPTH` 导致 OOM
  - 改变 value-embedding / optimizer 相关张量形状导致 `Muon` 路径崩溃
  - 轻率改动 norm / attention kernel 路径导致 FlashAttention 兼容性错误

决策：

- 这些经验不写回 baseline 臂，避免污染对照
- 仅把它们作为 **MAAR-only 固定先验** 注入 fresh run 的初始 `program_exp.md`
- 同时在 `maar_wide` prompt 中增加一句简短高风险提醒：
  - `depth inflation`
  - `embedding inflation`
  - `shape-changing optimizer interactions`
  - `norm / attention kernel path swaps`

作用：

- 让 MAAR 从第一轮开始就避开最愚蠢、最没价值的高风险改动
- 仍然保留探索空间，不引入硬参数阈值或静态封锁

建议内容：

- 1 worker，1 round
- 1 worker，2 rounds
- 2 workers，1 round
- 2 workers + coordinator，1 round

重点观察：

- proposal 可解析率
- patch 成功率
- crash 率
- 有效提升率

## 最新进展

### 2026-03-16：阶段 6 完成，接入真实 GLM backend

完成内容：

- 在 `orchestrator/agents.py` 中新增 `ZhipuChatAgentRunner`
- 走智谱官方 `chat/completions` 接口，不额外引入第三方依赖
- 保留原有 `AgentRunner` / `ReplayAgentRunner` 抽象，不改 round runner 主流程
- 支持 worker 和 coordinator 两种 prompt 结构
- 支持 `ZHIPUAI_API_KEY`、`ZHIPUAI_BASE_URL`、`ZHIPUAI_MODEL` 环境变量
- 支持失败重试和结构化 JSON 校验
- 在每次真实 agent 调用后落盘 `agent_request.json`、`agent_response_*.json`、`agent_output_*.txt`、`agent_error_*.txt`

验证结果：

- 新增 `tests/test_stage6.py`
- 已通过阶段 6 单测
- 尚未执行真实联网 smoke run

当前判断：

- 真实 backend 这一层已经具备接入条件
- 下一步不需要继续扩 agent 框架，而是要做一次真实 `1 worker + 1 round` 短程 smoke run

### 2026-03-16：真实 GLM 联网 smoke run 与修正

完成内容：

- 新增 `tests/test_env.py`，补 `.env.local` / `.env.example` 加载优先级回归测试
- 修正 `orchestrator/env.py` 中 `.env.local` 被 `.env.example` 空值覆盖的问题
- 扩展 `PreflightChecker`，支持 `import_check_command` 与 `check_imports=False`
- 收紧 `ZhipuChatAgentRunner` prompt，明确禁止 logging-only / timestamp-only / formatting-only 提案
- 新增 `scripts/live_glm_smoke.py`，用于执行真实 worker、coordinator 与最小一轮集成 smoke
- 让 live smoke 脚本支持重复执行并把结果汇总到 `runs/live-glm-smoke/summary.json`

验证情况：

- 本地完整回归：`python3 -m unittest tests.test_env tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 -v`
- 结果：`28` 个测试全部通过
- 真实联网 smoke 已执行
- direct worker：成功返回结构化 proposal，且在干净 `autoresearch` clone 上通过 patch + syntax-only preflight
- direct coordinator：成功返回结构化 proposal，且在干净 `autoresearch` clone 上通过 patch + syntax-only preflight
- 最小一轮集成 smoke：成功跑完 `proposal -> patch -> preflight -> execute -> adjudicate`，本轮结果为 `discard`，没有产生提升，但链路已跑通

遇到的问题：

- 第一轮真实联网前发现环境加载顺序错误，导致真实 `ZHIPUAI_API_KEY` 未被读取
- 第一版 live prompt 在极简 toy `train.py` 上生成了无效的 traceability 改动，导致 preflight 失败
- 真实 `autoresearch` proposal 验证时发现当前解释器缺少项目依赖，说明 import-check 不能硬编码绑定系统 `python3`
- live smoke 脚本第一次复跑时因固定 `run_tag` 撞上旧目录而失败

决策：

- 保留 `.env.local` 优先于 `.env.example` 的加载策略
- preflight 在真实 smoke/早期验证阶段允许 `check_imports=False`，但正式训练前仍应绑定真实项目环境
- 真实 agent prompt 明确收紧，先提高 proposal 的可执行性，再继续扩提示信息
- 真实 smoke 与单元测试分层：单元测试保证状态机和回退逻辑，联网 smoke 只验证 agent 真能进入 pipeline

下一步：

- 做一次真实 `2 workers + 1 round` 短程试跑
- 尝试真实 coordinator 触发路径，而不是只做 direct coordinator proposal
- 明确真实 `autoresearch` 训练环境应该如何提供给 preflight 和 executor

### 2026-03-16：真实 multi-worker pilot 脚本与短程试跑

完成内容：

- 新增 `scripts/live_glm_pilot.py`
- 新增一个更适合真实 agent 安全修改的 toy `train.py` 场景，优先鼓励修改顶层常量，而不是重写函数体
- 进一步收紧 `ZhipuChatAgentRunner` prompt，显式要求保留前导空格和缩进
- 用真实 GLM 跑了 `1` 个 single-worker trial 和 `2` 个 two-worker + coordinator trial

验证情况：

- 本地完整回归：`python3 -m unittest tests.test_env tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 -v`
- 结果：`28` 个测试全部通过
- 真实 pilot 汇总文件：`runs/live-glm-pilot/summary.json`
- 第一轮 pilot 调整后结果：
  - `single-worker`：仍有一次因模型重写函数体导致运行时 crash
  - `two-workers-trial-1`：两个 worker 都给出正向候选，`val_bpb=0.935`，coordinator 被真实触发，但 merged 结果与最佳 worker 持平，因此被丢弃
  - `two-workers-trial-2`：一个 worker 给出正向候选并被保留，另一个 worker 无提升

遇到的问题：

- 即使 prompt 已经收紧，single-worker 场景下模型仍可能偏向改函数体，导致缩进或结构错误
- two-worker 场景里两个 worker 容易收敛到相同提案，因此 coordinator 更像“确认共识”而不是产生新合并收益

决策：

- pilot 脚本继续保留“顶层常量更安全”的 toy 场景，用来验证 orchestrator 本身的真实多 worker 行为
- 先接受“coordinator 已触发但未带来额外收益”的结果，这对当前阶段仍然是有效信号
- 下一步重点从“能不能触发 coordinator”转向“能不能在多轮短跑中维持稳定正向候选”

下一步：

- 跑 `2 workers + 2-3 rounds` 的短程真实试跑
- 看 coordinator 在多轮设置下是否仍能稳定触发
- 开始把 preflight / executor 的运行环境向真实 `autoresearch` 训练环境收敛

### 2026-03-16：multi-worker pilot 支持多轮短跑

完成内容：

- 扩展 `scripts/live_glm_pilot.py`，支持 `--rounds`
- 执行了一次真实 `2 workers + 2 rounds` 的短程试跑

验证情况：

- 真实命令：`env PYTHONPATH=. python3 scripts/live_glm_pilot.py --two-worker-trials 1 --rounds 2`
- 结果摘要：
  - `single-worker`：本次 1 轮直接得到正向候选，`val_bpb` 从 `1.0` 降到 `0.935`
  - `two-workers-trial-1` 第 1 轮：一个 worker 因缩进错误触发 `preflight_failed`，另一个 worker 无提升
  - `two-workers-trial-1` 第 2 轮：worker-1 给出正向候选并被保留，`val_bpb` 从 `1.0` 降到 `0.935`
- 汇总文件仍写入 `runs/live-glm-pilot/summary.json`

遇到的问题：

- 多 worker + 多轮下，真实 agent 仍会偶发输出带错误缩进的函数体补丁
- coordinator 在这次多轮短跑中没有触发，说明多 worker 的有效分歧还不稳定

决策：

- 把“真实 proposal 稳定性”单独提升为近期优先事项
- 继续使用 multi-round pilot 作为真实 agent 质量回归手段

下一步：

- 继续优化 prompt，尽量把真实 worker 收敛到修改顶层常量或完整代码块
- 做一次 `2 workers + 3 rounds` 的短程试跑，观察 keep rate 和 proposal failure rate
- 尽快把 preflight / executor 绑到真实 `autoresearch` 训练环境，而不是长期停留在 toy pilot

### 2026-03-16：运行时解析层与 autoresearch 环境探针

完成内容：

- 新增 `orchestrator/runtime.py`
- 把运行时选择从脚本硬编码提升为 `RunConfig -> RuntimeResolution`
- 支持以下解析顺序：
  - 显式 `runtime_python_command`
  - 目标仓库下的 `.venv/bin/python`
  - 目标仓库下的 `venv/bin/python`
  - `uv run python`
  - 最后回退到系统 `python3`
- 新增 `build_preflight_checker(config)` 和 `build_training_executor(config)`
- 扩展 `RunConfig`，支持：
  - `runtime_python_command`
  - `preflight_import_check_command`
  - `preflight_check_imports`
  - `train_timeout_seconds`
- 新增 `tests/test_runtime.py`
- 新增 `scripts/check_autoresearch_runtime.py`
- 顺手修复了 `scripts/live_glm_smoke.py`、`scripts/live_glm_pilot.py`、`scripts/check_autoresearch_runtime.py` 对 `PYTHONPATH` 的依赖，脚本现在可直接从仓库根目录执行

验证情况：

- 本地完整回归：`python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 -v`
- 结果：`32` 个测试全部通过
- 真实 runtime probe：`python3 scripts/check_autoresearch_runtime.py`
- 当前 probe 结果：
  - `autoresearch` 目前解析到 `source=system`
  - `python_command=['python3']`
  - `train_command=['python3', 'train.py']`
  - import probe 失败，缺少 `torch`

遇到的问题：

- 当前机器上没有 `autoresearch/.venv/bin/python`
- 当前机器上也没有 `uv`
- 因此对于真实 `autoresearch`，runtime 只能退回系统 `python3`，这还不足以支持正式 preflight import-check 和真实训练

决策：

- 把“运行时选择能力”与“真实环境安装”拆开处理
- 先把解析逻辑、builder 和探针脚本做扎实，再单独进入真实 `autoresearch` 环境准备
- 在真实环境尚未装好之前，pilot 继续用 toy repo，真实 `autoresearch` 只做 probe，不直接跑训练

下一步：

- 为 `autoresearch` 准备可用环境，优先方案是安装 `uv` 并建立项目环境
- 环境就绪后，用 runtime probe 再确认 `source` 不再是 `system`
- 然后再尝试第一次真实 `autoresearch` preflight / train smoke run

### 2026-03-16：autoresearch conda 环境启动与运行时优先级修正

完成内容：

- 将运行时自动解析顺序调整为优先使用 `autoresearch/.conda-env/bin/python`，避免误选此前失败遗留的半成品 `.venv`
- 扩展 `tests/test_runtime.py`，新增 `.conda-env` 与 `.venv` 并存时的优先级回归测试
- 创建 `autoresearch/.conda-env`
- 在该 conda 环境中完成基础 Python 运行时与轻量依赖安装：
  - `python=3.10`
  - `pip`
  - `numpy`
  - `requests`
  - `tiktoken`
  - `pandas`
  - `pyarrow`
  - `matplotlib`
  - `rustbpe`
- 确认当前 `scripts/check_autoresearch_runtime.py` 已解析到 `.conda-env`

验证情况：

- `python3 -m unittest tests.test_runtime -v`
- `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 -v`
- 结果：`34` 个测试全部通过
- `python3 scripts/check_autoresearch_runtime.py`
- 当前 probe 结果：
  - `python_command=['/home/yzy/yzycode/Multi-agent-AutoResearch/autoresearch/.conda-env/bin/python']`
  - `train_command=['/home/yzy/yzycode/Multi-agent-AutoResearch/autoresearch/.conda-env/bin/python', 'train.py']`
  - import probe 仍失败，当前缺少 `torch`
- 额外检查确认：`~/.cache/autoresearch` 目前还不存在，后续真实训练前仍需执行 `prepare.py`

遇到的问题：

- 用 `python3.10 -m venv autoresearch/.venv` 创建环境时，当前系统缺少 `ensurepip` 支持，因此留下了一个不可用但可执行的 `.venv`
- `autoresearch` 不能通过 `pip install -e ./autoresearch` 直接 editable 安装，因为仓库是 flat layout，包含多个顶层模块文件
- `torch` 的 GPU 版安装体积很大，当前选择继续使用 conda 方式安装，耗时较长

决策：

- 把 `.conda-env` 作为当前真实 `autoresearch` 的标准运行环境，不再依赖 `.venv`
- 在 `torch` 安装完成前，不在真实 `autoresearch` 上执行正式 preflight / train smoke run
- 数据准备延后到 `torch` 和 `kernels` 都可导入之后再做，避免下载完数据后仍无法训练

下一步：

- 等待并完成 `torch` GPU 版安装
- 安装并验证 `kernels`
- 用 `.conda-env` 重跑 runtime probe，确认 `import torch` 和 `import kernels` 都成功
- 然后执行第一次真实 `autoresearch` `prepare.py` / `train.py` smoke run

### 2026-03-16：固定真实 autoresearch 运行时并完成首次真机 smoke

完成内容：

- 放弃继续下载新的 `torch` 环境，改为复用机器上现成的 `w3c` conda 环境
- 在项目内新增私有依赖目录 `autoresearch/.orchestrator-site`
- 将 `rustbpe`、`tiktoken`、`kernels` 及其所需轻量依赖安装到该私有目录
- 在 `.env.local` / `.env.example` 中新增：
  - `AUTORESEARCH_RUNTIME_PYTHON`
  - `AUTORESEARCH_RUNTIME_PYTHONPATH`
- 扩展 `orchestrator/runtime.py`，支持从环境变量自动组装运行时命令
- 扩展 `tests/test_runtime.py`，补充环境变量驱动的运行时解析测试
- 扩展 `scripts/check_autoresearch_runtime.py`，把 probe 模块覆盖面扩大到 `rustbpe`、`tiktoken`、`kernels`
- 用真实运行时执行了 `prepare.py`，完成 `~/.cache/autoresearch` 数据与 tokenizer 准备
- 在原始 `autoresearch` 上执行了一次真实 `train.py` 启动验证
- 在临时 worktree `/tmp/autoresearch-3090-smoke` 上执行了一次 3090 缩配 smoke run，并成功产出完整 summary

验证情况：

- 本地完整回归：`python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 -v`
- 结果：`35` 个测试全部通过
- `python3 scripts/check_autoresearch_runtime.py`
- 当前 probe 结果：
  - `source=env`
  - `python_command=['env', 'PYTHONPATH=.../.orchestrator-site', '/home/yzy/.conda/envs/w3c/bin/python']`
  - import probe 成功覆盖 `torch,numpy,requests,rustbpe,tiktoken,kernels`
- 真实 `prepare.py` 已完成：
  - 数据目录：`~/.cache/autoresearch/data`
  - tokenizer 目录：`~/.cache/autoresearch/tokenizer`
- 原始 `autoresearch/train.py` 已在真实 GPU 上跑到训练阶段，但默认配置在 `3090 24GB` 上 OOM

### 2026-03-17：首次真实 `autoresearch-3090` 多智能体试跑

完成内容：

- 在 `autoresearch-3090` 上执行了第一次真实 `2 workers + 1 round + coordinator enabled` 的多智能体实验
- 真实命令：
  - `python3 scripts/run_glm_multi_agent.py --target-repo autoresearch-3090 --worker-count 2 --rounds 1 --run-tag live-multi-agent-20260317-2303 --train-timeout-seconds 900 --agent-timeout-seconds 90 --agent-max-retries 3`
- 运行产物：
  - `runs/glm-multi-agent/live-multi-agent-20260317-2303/summary.json`

验证情况：

- baseline 测量成功：
  - `val_bpb=1.178007`
  - `peak_vram_mb=11701.0`
- `worker-1`：
  - proposal / patch / preflight / train 全部成功
  - `val_bpb=1.177456`
  - 相比 baseline 有严格提升，因此被保留并晋升为新的 baseline
- `worker-2`：
  - proposal / patch / preflight 成功
  - 训练阶段 crash
  - `failure_reason=command exited with code 1`
- 本轮结果：
  - `selected_actor_id=worker-1`
  - `selected_status=keep`
  - `baseline_after=1.177456`
  - coordinator 未触发，因为只有一个正向候选

遇到的问题：

- `worker-2` 生成了一个“定期保存 best checkpoint，再在结尾回读 best checkpoint”的 proposal
- 该 proposal 假设在 300s 时间预算内一定能触发中途评估并写出 `best_model_step_<step>.pt`
- 实际训练仅跑到约 `543` step，未达到其设定的 `eval_interval=1000`
- 结果是在结尾执行 `torch.load('best_model_step_0.pt')` 时直接触发 `FileNotFoundError`
- 这类错误说明：
  - 当前 prompt 还允许模型引入不必要的 checkpoint / 文件 I/O 副作用
  - 仅靠语法检查和 import-check 还不足以拦截“语义上明显危险”的 proposal

决策：

- 对真实 worker prompt 继续收紧：
  - 禁止重写、复制或迁移主训练循环
  - 禁止引入 `torch.save`、`torch.load`、`open(...)`、`exit(...)` 等新的 side effects
  - 禁止假设一个可选分支一定会先运行
- 在 preflight 中新增 diff 级别的危险模式拦截，优先拒绝 checkpoint / 文件 I/O / 进程退出类新增代码

下一步：

- 对 prompt + preflight guard 做本地回归
- 再跑一次真实 `2 workers + 1 round`，验证是否能把这类危险 proposal 前置消灭

### 2026-03-17：危险 side effects guard 与第二次真实多智能体试跑

完成内容：

- 在 `orchestrator/agents.py` 中继续收紧真实 worker prompt：
  - 禁止重写、复制或迁移主训练循环
  - 禁止引入 checkpoint 文件、`torch.save`、`torch.load`、`open(...)`、`exit(...)`
  - 禁止依赖一个可能从未执行的中间分支来保证程序末尾正确性
- 在 `orchestrator/preflight.py` 中新增 diff 级别的危险模式检查
  - 当前会拒绝新增的 `torch.save(`、`torch.load(`、`open(`、`exit(`、`sys.exit(`
- 新增回归测试：
  - `tests.test_stage2.Stage2Tests.test_preflight_rejects_risky_runtime_side_effects`
  - `tests.test_stage6.Stage6Tests.test_zhipu_runner_prompt_explicitly_forbids_checkpoint_side_effects`
- 本地完整回归：
  - `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_live_baseline tests.test_live_multi -v`
  - 结果：`43` 个测试全部通过

第二次真实试跑：

- 真实命令：
  - `python3 scripts/run_glm_multi_agent.py --target-repo autoresearch-3090 --worker-count 2 --rounds 1 --run-tag live-multi-agent-20260317-guarded --train-timeout-seconds 900 --agent-timeout-seconds 90 --agent-max-retries 3`
- 运行产物：
  - `runs/glm-multi-agent/live-multi-agent-20260317-guarded/summary.json`

验证情况：

- baseline 测量成功：
  - `val_bpb=1.177361`
  - `peak_vram_mb=11701.0`
- `worker-1`：
  - 合法执行，但无提升
  - `val_bpb=1.182217`
  - `status=discard`
- `worker-2`：
  - 合法执行，但无提升
  - `val_bpb=1.182179`
  - `status=discard`
- 本轮结果：
  - `selected_actor_id=""`
  - `selected_status=""`
  - `baseline_after=1.177361`
  - coordinator 未触发，因为没有正向候选

当前判断：

- 新 guard 至少达成了一个重要目标：第二轮没有再出现上一轮那种 checkpoint 假设错误导致的运行时 crash
- 目前的主要问题已经从“proposal 会把 run 炸掉”转成了“proposal 虽然安全，但提升率仍然偏低”
- 下一阶段的优化方向应继续聚焦在真实 prompt 质量，而不是再扩大执行层复杂度

### 2026-03-18：继续收紧 prompt，并确认混合模型架构已具备配置入口

完成内容：

- 在 `orchestrator/agents.py` 中继续把真实 prompt 收紧到“局部、小改动”风格：
  - 更明确要求优先替换已有表达式、常量或分支条件
  - 明确限制 replacement 尽量保持在单个现有 block 内
  - 禁止新增 `for/while` 循环、嵌套训练 pass、重复的大段训练/评估代码
  - 禁止改动数据加载、tokenizer、路径、cache、device placement、runtime bootstrap
  - 明确把“超参、schedule 常量、eval cadence、optimizer 设置、已有 update/eval 表达式”定义为安全目标
- 扩展 `tests/test_stage6.py`，验证新的 prompt 约束文本确实存在
- 在 `orchestrator/README.md` 中补充了混合模型运行示例：
  - worker 用 `glm-4.5-air`
  - coordinator 用 `glm-4.7`

验证情况：

- 定向回归：
  - `python3 -m unittest tests.test_stage6 -v`
  - 结果：全部通过

当前判断：

- 现在的代码层已经支持混合模型架构，不需要额外实现新 runner
- 具体做法就是：
  - `--model glm-4.5-air`
  - `--coordinator-model glm-4.7`
- 因此接下来是否试混合架构，已经不是“能不能做”的问题，而是“是否值得为 coordinator 单独支付更高模型成本”的实验设计问题

### 2026-03-17：固定 3090 对比臂并跑通真实单智能体 baseline

完成内容：

- 在 `autoresearch-3090/` 中把 3090 训练配置固化为一个干净本地 commit：`90243dd` (`Add 3090 baseline training profile`)
- 为 `autoresearch-3090/.gitignore` 增加 `.orchestrator-site/`、`.conda-env*` 忽略规则，确保实验 repo 可保持 clean
- 从 `autoresearch-3090/` 克隆出 `autoresearch-3090-baseline/`
- 在 `autoresearch-3090-baseline/` 中新增：
  - `run_single_agent_baseline.py`
  - `BASELINE_ARM.md`
- 在 `autoresearch-3090-baseline/` 中提交本地 convenience commit：`47f1ac8` (`Add single-agent baseline runner`)
- 新增 `orchestrator/live_baseline.py`
  - baseline 首次测量
  - `worker_count=1` / `coordinator=false` 的真实单智能体轮次入口
- 新增共享入口 `scripts/run_glm_single_baseline.py`
- 新增 `tests/test_live_baseline.py`
- 修复 `PreflightChecker` 的语法校验漏洞：从 `ast.parse()` 升级为 `compile(..., "exec")`
- 收紧 `ZhipuChatAgentRunner` prompt，禁止把 `break` / `continue` / `return` 引入错误作用域

对比臂约定：

- 多智能体实验臂：
  - 目标 repo：`autoresearch-3090/`
  - agent backend：共享 `ZhipuChatAgentRunner`
  - runtime：共享 `AUTORESEARCH_RUNTIME_PYTHON`
  - data/tokenizer cache：共享 `~/.cache/autoresearch`
  - worker 数：`N > 1`
  - coordinator：开启
- 单智能体 baseline 臂：
  - 目标 repo：`autoresearch-3090-baseline/`
  - agent backend：同一套 `ZhipuChatAgentRunner`
  - runtime：同一套 Python 与 `.orchestrator-site`
  - data/tokenizer cache：同一份 `~/.cache/autoresearch`
  - worker 数：`1`
  - coordinator：关闭
- 公平性边界：
  - 训练比较只看 `train.py` / `prepare.py`
  - baseline repo 中新增的 launcher / markdown 文件只用于运行和记录，不进入可编辑搜索空间

验证情况：

- 本地回归：
  - `python3 -m unittest tests.test_stage2 tests.test_stage6 tests.test_live_baseline -v`
  - `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_live_baseline -v`
  - 当前共通过 `38` 个测试
- 真实单智能体 run #1：
  - 命令：`python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1 --run-tag live-single-agent-baseline-20260317-1 --train-timeout-seconds 900`
  - 汇总：`runs/glm-single-baseline/live-single-agent-baseline-20260317-1/summary.json`
  - baseline `val_bpb = 1.177375`
  - baseline `peak_vram_mb = 11701.0`
  - worker proposal 训练前未被挡下，最终在执行阶段因 `SyntaxError: 'break' outside loop` crash
- 真实单智能体 run #2（修复 preflight 后）：
  - 命令：`python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1 --run-tag live-single-agent-baseline-20260317-2 --train-timeout-seconds 900`
  - 汇总：`runs/glm-single-baseline/live-single-agent-baseline-20260317-2/summary.json`
  - baseline `val_bpb = 1.178085`
  - candidate `val_bpb = 1.178154`
  - candidate 合法执行完成，但没有严格优于 baseline，因此被 `discard`
  - 这次 worker 训练同样保持 `peak_vram_mb = 11701.0`

遇到的问题：

- 第一次真实单智能体 run 暴露出一个真实工程漏洞：
  - `ast.parse()` 不能拦截 `break outside loop`
  - 导致明显坏补丁漏过 preflight，白白消耗一次真实训练
- 单智能体 baseline 的固定前置成本比 toy pilot 明显更高：
  - 在真正进入 round 之前，必须先做一次约 `300s` 的 baseline 测量
- 即使 prompt 已经收紧，真实 worker 仍可能给出“局部看起来合理，但最终无收益”的 proposal

决策：

- 单智能体 baseline 臂不再依赖原始 `autoresearch` 的手工 agent 使用方式，统一改为复用外层 orchestrator 的真实 GLM backend
- 后续单 / 多智能体对比默认都从 clean git commit 出发，不允许继续基于 dirty worktree 直接开跑
- preflight 现在必须拦住“能过 AST 但不能过编译”的控制流错误；这类错误不再允许进入训练阶段
- 真实对比实验时，baseline 首次测量应作为两个实验臂共享的固定成本显式记录，而不是隐含忽略

下一步：

- 给 `autoresearch-3090/` 增加真实多智能体 launcher，和单智能体 baseline 使用同一级别的运行入口
- 继续提高真实 worker proposal 的稳定性，重点减少“局部合法但方向错误”的修改
- 视需要加入 baseline 测量缓存或复用机制，减少重复 run 的固定开销

### 2026-03-17：切换默认 GLM 模型到 4.7，并补齐真实多智能体入口

完成内容：

- 将默认智谱模型从 `glm-4-flash` 切换为 `glm-4.7`
  - `orchestrator/agents.py`
  - `.env.example`
  - `.env.local`
  - 相关测试和 README 示例
- 新增 `orchestrator/live_multi.py`
  - 真实多智能体实验入口
  - baseline 首次测量
  - 多 worker round 执行
  - 可选 coordinator 汇总
- 新增 `scripts/run_glm_multi_agent.py`
- 新增 `tests/test_live_multi.py`
- 更新 `orchestrator/README.md`，补单 / 多智能体两套真实入口命令

验证情况：

- 本地定向回归：
  - `python3 -m unittest tests.test_env tests.test_stage6 tests.test_live_baseline tests.test_live_multi -v`
- 本地完整回归：
  - `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_live_baseline tests.test_live_multi -v`
  - 当前共通过 `40` 个测试
- 真实 GLM-4.7 smoke：
  - 命令：`env PYTHONPATH=. python3 scripts/live_glm_smoke.py`
  - `agent_request.json` 中已确认真实请求模型为 `glm-4.7`
  - `round_smoke` 成功进入真实 GLM-4.7 路径，但 worker proposal 因 `HTTP 429 / code 1302` 被记为 `proposal_failed`
  - `worker_direct` 与 `coordinator_direct` 两条 direct smoke 这次都出现了 read timeout

遇到的问题：

- 切到 `glm-4.7` 后，当前账号在这次 smoke 中出现了两类外部限制：
  - `read operation timed out`
  - `HTTP 429` / `code 1302` 速率限制
- 这说明当前阻塞点已经不是本地 orchestrator 逻辑，而是外部 API 稳定性和请求节流

决策：

- 默认模型先统一使用 `glm-4.7`，后续真实实验不再继续沿用 `glm-4-flash`
- 多智能体真实 launcher 先落地并通过本地回归，再等待智谱 API 请求窗口恢复后跑第一次真实 `autoresearch-3090` 多 worker 实验
- 真实对比实验前，需要考虑加入更保守的请求节流或重试间隔，避免被 `1302` 速率限制直接打断

下一步：

- 在 `glm-4.7` 速率限制恢复后，运行第一次真实 `autoresearch-3090` 多智能体 round
- 若 `1302` 仍频繁出现，给 agent backend 增加 provider-level backoff
- 开始记录单 / 多智能体真实 run 的可比摘要表，为后续最终对比试验做准备
- 临时 3090 smoke 配置：
  - `prepare.py`: `TIME_BUDGET=60`, `EVAL_TOKENS=1 * 524288`
  - `train.py`: `TOTAL_BATCH_SIZE=2**16`, `DEPTH=4`, `DEVICE_BATCH_SIZE=32`
- 临时 smoke 结果：
  - `val_bpb: 1.203798`
  - `training_seconds: 60.1`
  - `total_seconds: 94.0`
  - `peak_vram_mb: 3681.3`
  - `num_steps: 920`

遇到的问题：

- 机器上现成的多个 conda 环境虽然有可用 GPU `torch`，但不少组合无法命中 `kernels-community/flash-attn3` 的预编译变体
- `mapf-gpt` (`torch 2.5 + cu124`) 与 `lns2-rl` / `mapf-gpt-ddg` 的 `torch` 版本都无法通过 `kernels` 变体选择
- 原始 `autoresearch` 默认配置是面向 H100 的，在 `3090 24GB` 上会在首轮训练时 OOM

决策：

- 当前真实运行时固定为：
  - Python: `/home/yzy/.conda/envs/w3c/bin/python`
  - extra `PYTHONPATH`: `/home/yzy/yzycode/Multi-agent-AutoResearch/autoresearch/.orchestrator-site`
- 运行时发现不再依赖仓库内 `.conda-env`，统一走 `.env.local` 中的外部 Python 配置
- 将“3090 上的可用 baseline 缩配”提升为新的主线问题；在它明确之前，不直接用原始默认配置跑 orchestrator 长程实验
- 临时 smoke worktree 只用于平台验证，不作为正式 baseline 历史

下一步：

- 先确定一版 3090 上稳定可复现的 baseline 配置，尽量只修改 `train.py`
- 然后把这版 baseline 接入 orchestrator，完成第一次真实 `worker_count=1` 的 end-to-end run
- 在 baseline 稳定后，再继续回到多 worker + coordinator 的真实 `autoresearch` 长跑

### 2026-03-17：3090 baseline 候选与 `autoresearch-3090` 副本

完成内容：

- 在临时 worktree `/tmp/autoresearch-3090-smoke` 上继续做 3090 显存 profiling
- 验证了 `DEPTH=8`、`DEVICE_BATCH_SIZE=64` 仍会把峰值显存推到 `22.8GB`，不符合目标
- 验证了以下候选配置可以把峰值显存压到约 `11.7GB`：
  - `prepare.py`: `EVAL_TOKENS = 10 * 524288`
  - `train.py`: `TOTAL_BATCH_SIZE = 2**17`, `DEPTH = 8`, `DEVICE_BATCH_SIZE = 32`
- 将以上配置正式写入主仓库 `autoresearch/prepare.py` 与 `autoresearch/train.py`
- 新建独立副本 `autoresearch-3090/`
- 将同样的 3090 配置同步写入 `autoresearch-3090/prepare.py` 与 `autoresearch-3090/train.py`
- 同步复制了私有运行时依赖目录到 `autoresearch-3090/.orchestrator-site`

验证情况：

- 3090 smoke 失败候选：
  - `TOTAL_BATCH_SIZE = 2**18`
  - `DEPTH = 8`
  - `DEVICE_BATCH_SIZE = 64`
  - 结果：`peak_vram_mb = 22804.5`
- 3090 smoke 通过候选：
  - `TOTAL_BATCH_SIZE = 2**17`
  - `DEPTH = 8`
  - `DEVICE_BATCH_SIZE = 32`
  - 结果：
    - `val_bpb: 1.520465`
    - `training_seconds: 60.0`
    - `total_seconds: 97.8`
    - `peak_vram_mb: 11701.0`
    - `num_steps: 118`
- 已确认 `autoresearch/prepare.py` 与 `autoresearch-3090/prepare.py` 完全一致
- 已确认 `autoresearch/train.py` 与 `autoresearch-3090/train.py` 完全一致

遇到的问题：

- 若只把 `DEVICE_BATCH_SIZE` 从 `128` 降到 `64`，在 `3090 24GB` 上仍然会 OOM 边缘运行，峰值达到 `22.8GB`
- 虽然 `11.7GB` 已经满足“约 12-16GB”的目标区间，但这还是基于 `60s` smoke，而不是完整 `300s` 长跑

决策：

- 当前把 `TOTAL_BATCH_SIZE = 2**17`, `DEPTH = 8`, `DEVICE_BATCH_SIZE = 32` 视为 3090 baseline 候选
- `prepare.py` 保持 `MAX_SEQ_LEN = 2048` 和 `TIME_BUDGET = 300` 不变，只缩小 `EVAL_TOKENS`，以避免 3090 版最终评估过长
- 后续单智能体基线和多智能体实验都可以优先指向 `autoresearch-3090/`

下一步：

- 用 `autoresearch-3090/` 做一次更接近正式设置的真实长程验证
- 然后把 orchestrator 的真实 target 从原始 `autoresearch/` 切到 `autoresearch-3090/`
- 在 `worker_count=1` 下先跑出单智能体基线，再扩回多 worker

### 第三批实验：连续运行

目的：

- 验证系统能否持续跑多个 round

建议内容：

- 2 workers，3 到 5 rounds
- 记录每轮 best-so-far
- 记录 coordinator 触发次数与成功次数

## 日志模板

后续每次开发建议按下面格式补充：

```md
## YYYY-MM-DD

### 完成内容
- ...

### 验证情况
- ...

### 遇到的问题
- ...

### 决策
- ...

### 下一步
- ...
```

## 初始记录

## 2026-03-16

### 完成内容

- 完成 `development_plan.md` 的主计划收敛
- 明确 V1 为回合制多 worker orchestrator，包含 coordinator
- 明确真实 LLM 是 V1 必选依赖
- 决定 benchmark 不阻塞主流程，延后到主线稳定后再做
- 新增 `orchestrator/` 代码骨架
- 定义 `RunConfig`、`CoordinatorConfig`、`ExperimentProposal`、`ExperimentResult`、`RoundState`、`RunState`
- 固定 `runs/<tag>/` 目录布局，并补充 `orchestrator/README.md`

### 当前判断

- 先打通本地 orchestration，再接真实 LLM，风险最低
- 真实 LLM 接入优先考虑轻量开源 agent engine
- 当前最值得优先评估的是 `PydanticAI`

### 验证情况

- 本地完成了一次 schema/import smoke check
- `RunConfig`、`RunLayout`、`RunState`、`ExperimentProposal`、`ExperimentResult` 的实例化与序列化正常

### 下一步

- 先完成 worktree、patcher、executor
- 之后再接真实 LLM backend

## 2026-03-16（阶段 1）

### 完成内容

- 新增 `orchestrator/git_ops.py`，封装目标仓库的 git 命令、分支创建、worktree 创建与重置
- 新增 `orchestrator/worktree.py`，实现 run 初始化与 baseline 同步逻辑
- 新增 `orchestrator/persistence.py`，实现 `run.json`、`results.tsv`、`experiments.jsonl` 和 `round.json` 的落盘
- 扩展 `RunConfig`，补充分支命名约定
- 扩展 `RunState`，记录 worker worktree 与 merge worktree 路径
- 更新 `orchestrator/README.md`，补齐当前模块职责

### 验证情况

- 使用临时 Git 仓库编写并通过了阶段 1 单元测试
- 已验证 run 初始化会创建 baseline / worker / merge 分支与 worktree
- 已验证 `sync_all_to_baseline()` 会把 worker 与 merge worktree 重置回 baseline commit
- 已验证 `results.tsv`、`experiments.jsonl`、`round.json` 的初始化与追加写入
- 测试命令：`python3 -m unittest tests.test_stage1`

### 遇到的问题

- `git worktree add` 对目标目录状态比较敏感，不适合在布局初始化阶段提前创建每个 worker 目录

### 决策

- `RunLayout.create()` 只创建 `runs/<tag>/`、`rounds/` 和 `workspaces/` 父目录
- 具体 `worker-*` 和 `merge` worktree 路径交给 git 自己创建，避免和 worktree 初始化冲突
- 当前 orchestrator 只依赖本地 `autoresearch/` clone 的 Git 能力，不依赖 GitHub 远程仓库权限

### 下一步

- 实现 `Search/Replace` patcher
- 加入只允许修改 `train.py` 的约束
- 加入预检与 diff 导出

## 2026-03-16（阶段 2）

### 完成内容

- 新增 `orchestrator/patcher.py`，实现 `Search/Replace` 补丁应用
- 支持精确匹配与 whitespace-tolerant 匹配两种模式
- 限制补丁只落到 `train.py`
- 新增 `orchestrator/preflight.py`，实现 preflight 检查
- preflight 现在会检查：
  - 工作区中只有 `train.py` 被修改
  - `train.py` 的 Python 语法有效
  - `train.py` 中声明的 import 模块可导入
- 新增 `tests/test_stage2.py`

### 验证情况

- 已通过阶段 1 + 阶段 2 的完整单元测试
- 测试命令：`python3 -m unittest tests.test_stage1 tests.test_stage2 -v`
- 当前共通过 9 个测试，覆盖：
  - worktree 初始化
  - baseline 同步
  - 状态落盘
  - 精确补丁应用
  - whitespace-tolerant 补丁应用
  - 多重匹配拒绝
  - 非法额外文件修改拒绝
  - 缺失 import 拒绝
  - 合法补丁 preflight 通过

### 遇到的问题

- `git status --porcelain` 的输出格式在不同状态组合下不完全一致，最初的路径解析漏掉了首字符

### 决策

- preflight 的修改路径解析改为更稳妥的分支逻辑，而不是固定切片
- 当前 preflight 不直接 import `train.py` 本身，避免触发 CUDA / kernel 副作用；只检查其声明的模块能否导入

### 下一步

- 实现 training executor
- 解析训练日志并提取核心指标
- 为后续 mock/replay 单轮实验打基础

## 2026-03-16（阶段 3）

### 完成内容

- 新增 `orchestrator/executor.py`
- 实现 `ExecutionSlotPool`，作为单槽/多槽训练执行资源池抽象
- 实现 `TrainingExecutor`，负责：
  - 启动训练命令
  - 写入 `run.log`
  - 处理 timeout
  - 处理非零退出
  - 在成功路径上解析训练 summary
- 实现 `TrainingLogParser`，解析：
  - `val_bpb`
  - `training_seconds`
  - `total_seconds`
  - `peak_vram_mb`
- 新增 `tests/test_stage3.py`

### 验证情况

- 已通过阶段 1 到阶段 3 的完整单元测试
- 测试命令：`python3 -m unittest tests.test_stage1 tests.test_stage2 tests.test_stage3 -v`
- 当前共通过 15 个测试，新增覆盖：
  - summary 日志解析
  - 缺失 summary 字段判 crash
  - timeout 判定
  - 非零退出判 crash
  - timeout / crash 后执行槽释放

### 决策

- 训练执行状态单独使用 `ExecutionStatus`，不直接复用 `ExperimentStatus`
- 当前解析器要求四个核心 summary 字段都出现，否则视为 crash
- timeout 与 crash 都在 executor 层吸收，并返回结构化结果，后续 worker loop 再决定如何映射到实验状态

### 下一步

- 进入阶段 4，开始实现 mock/replay 驱动的单轮 worker loop
- 把已有模块串起来：baseline -> proposal -> patch -> preflight -> execute -> result

## 2026-03-16（阶段 4）

### 完成内容

- 新增 `orchestrator/agents.py`
- 定义 `AgentRunner` 抽象、`ProposalRequest` 和 `ReplayAgentRunner`
- 新增 `orchestrator/round_runner.py`
- 实现单轮 worker-only round runner，串联：
  - baseline 同步
  - replay proposal 获取
  - proposal 落盘
  - patch 应用
  - preflight 检查
  - candidate commit
  - 训练执行
  - worker 结果裁决
  - baseline 晋升与全 worktree 同步
- 扩展 `RunState` / `RoundState` / `ExperimentResult`
  - 增加 `baseline_val_bpb`
  - 增加 `metrics_path`
  - 增加 `failure_reason`
- 扩展 `GitRepo`
  - 增加 `force_branch`
  - 增加 `commit_paths`
- 新增 `tests/test_stage4.py`

### 验证情况

- 已通过阶段 1 到阶段 4 的完整单元测试
- 测试命令：`python3 -m unittest tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 -v`
- 当前共通过 18 个测试，新增覆盖：
  - worker round 成功选出最佳改动
  - baseline 被晋升并同步到所有 worktree
  - proposal failure 处理
  - preflight failure 处理
  - 无提升时保持 baseline 不变

### 遇到的问题

- 测试里最初用“无变化补丁”模拟不提升路径，但 no-op patch 会被 preflight 视为未真正修改 `train.py`

### 决策

- worker round 的“不提升”测试改为“做了合法修改但跑分更差”，更符合真实实验语义
- baseline 分数现在显式存入 `RunState.baseline_val_bpb`，后续 coordinator 和多轮运行都能复用

### 下一步

- 进入阶段 5，实现 coordinator merge 路径
- 在 round runner 基础上扩展 merged candidate 的生成、验证与回退

## 2026-03-16（阶段 5）

### 完成内容

- 扩展 `ProposalRequest`，支持结构化 `context`
- 将 `round_runner.py` 重构为统一候选执行路径，worker 和 coordinator 共用 proposal -> patch -> preflight -> commit -> execute 流程
- 在 `WorkerRoundRunner` 中接入可选 coordinator：
  - 当正向 worker 候选数量达到阈值时触发
  - 默认只消费 top-k 正向候选
  - 生成并保存 `coordinator_input.json`
  - 在 merge worktree 中执行 merged candidate
  - merged 更优时晋升 merged
  - merged 更差或失败时回退到最佳 worker
- 新增 `tests/test_stage5.py`

### 验证情况

- 已通过阶段 1 到阶段 5 的完整单元测试
- 测试命令：`python3 -m unittest tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 -v`
- 当前共通过 21 个测试，新增覆盖：
  - merged candidate 更优时晋升
  - merged candidate 更差时丢弃并保留最佳 worker
  - merged proposal 失败时回退到最佳 worker

### 决策

- coordinator 仍然复用统一 `AgentRunner` 抽象，不单独引入第二套 runtime
- `positive_results` 继续只表示 worker 的正向候选；merged candidate 单独记录在 `merge_result`
- 当 merged candidate 分数与最佳 worker 相同或更差时，优先保留单一 worker 候选

### 下一步

- 开始接入真实 LLM backend
- 优先完成 worker 侧真实 LLM 接入，再接 coordinator
- 保留 replay backend 作为回归测试基线

## 2026-03-17：切换智谱默认模型到 `glm-4.5-air`，并补 provider backoff

### 完成内容

- 将默认智谱模型从 `glm-4.7` 切换为 `glm-4.5-air`
  - `.env.local`
  - `.env.example`
  - `orchestrator/agents.py`
  - 相关测试和 README 示例
- 在 `ZhipuChatAgentRunner` 中新增 provider 级重试回退逻辑
  - `HTTP 429` / `1302` 限流：按 `5s, 10s, ...` 递增 backoff
  - request timeout：按 `3s, 6s, ...` 递增 backoff
  - 每次尝试开始、失败、重试、成功都会即时打印日志
- 在 `scripts/live_glm_smoke.py` 中新增阶段性实时输出
  - `starting worker_direct`
  - `finished worker_direct`
  - `starting coordinator_direct`
  - `starting round_smoke`
- 新增 `tests.test_stage6.Stage6Tests.test_zhipu_runner_backs_off_after_rate_limit`

### 选型决策

- 这次模型选择的约束是：
  - 你在智谱项目限流页中确认需要并发数至少 `5`
  - 我们的任务形态不是长篇自由写作，而是：
    - 读取 `train.py`
    - 生成结构化 Search/Replace JSON
    - 支持 worker / coordinator 两类 agent
- 在这个约束下，先把默认模型切到 `glm-4.5-air`
- 选择理由：
  - 相比 `glm-4.7`，它更适合高频调用和实验期反复试跑
  - 相比更轻的 flash 类模型，它仍然更接近“编码 / agent”用途，而不是纯低成本占位
  - 对我们现在这种“结构化 proposal + 多次短请求”的 workload，更重视吞吐和稳定性，而不是单次最强推理上限

### 验证情况

- 定向回归：
  - `python3 -m unittest tests.test_env tests.test_stage6 -v`
- 完整回归：
  - `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_live_baseline tests.test_live_multi -v`
  - 当前共通过 `41` 个测试
- 真实 smoke：
  - 命令：`env PYTHONPATH=. python3 scripts/live_glm_smoke.py`
  - 结果：
    - `worker_direct`：成功
    - `coordinator_direct`：成功返回 proposal，但 validation 失败，原因是 `search_block did not match the editable file`
    - `round_smoke`：成功，worker 被 `keep`，`val_bpb` 从 `1.0` 降到 `0.95`

### 遇到的问题

- `glm-4.7` 阶段的主要问题是 provider 限流：
  - `HTTP 429`
  - 错误码 `1302`
- 切到 `glm-4.5-air` 后，限流问题显著缓解，但暴露出新的两个真实问题：
  - 部分请求仍会出现 `read operation timed out`
  - coordinator proposal 的 `search_block` 偶发不精确，导致 patch validation 失败

### 决策

- 开发 / 联调阶段先使用 `glm-4.5-air`
- provider 层失败先做可见化和 backoff，而不是继续“静默重试”
- 当前最主要的稳定性问题已经从“模型并发不够”切换成“proposal 质量不稳定”，尤其是 coordinator 的精确匹配能力

### 下一步

- 在 `autoresearch-3090` 上跑第一次真实多 worker / coordinator 实验
- 如果 timeout 仍偏多，再给智谱调用加更长的 read timeout 或更保守的 smoke 配置
- 单独收紧 coordinator prompt，优先提高 `search_block` 命中率

## 2026-03-18：为多轮实验设计 `program_exp.md` 共享经验文档

### 背景

- 当前系统已经能跑多轮 round，但跨轮“经验”仍主要停留在：
  - prompt 固定规则
  - 人工复盘 `summary.json` / `run.log`
- 如果要让 worker 和 coordinator 在同一个 run 内逐轮积累方向性经验，最自然的做法是增加一个精简共享文档：
  - `program_exp.md`

### 这次先做的设计决策

- `program_exp.md` 不放进目标 repo，不参与候选 commit，不受 `train.py`-only 约束影响
- 它的默认位置定为：
  - `runs/<tag>/program_exp.md`
- 它是“每个 run / 每个实验臂独立”的共享上下文：
  - 不跨不同 run 共享
  - 不跨 `autoresearch-3090` 与 `autoresearch-3090-baseline` 共享
  - 这样后续对比实验不会发生 knowledge leakage
- `program_exp.md` 由 orchestrator 单写
  - worker 和 coordinator 只读，不直接编辑文件
  - agent 只通过结构化字段贡献简短的 `idea_summary`
- 负面经验采用“单次即记”规则
  - 单次 `worse`
  - 单次 `crash`
  - 都直接写入 `program_exp.md`
- 文档只记录 idea，不记录代码
  - 不贴 diff
  - 不贴 search/replace block
  - 不写大段日志
  - 只保留“什么方向可能更好 / 更差 / 会导致不稳定”的极简句子

### 当前推荐的实现轮廓

- 扩展 `ExperimentProposal` / `CoordinatorProposal`
  - 新增 `idea_summary`
  - 约束为一句短句，只描述方向，不描述代码实现
- round 开始前：
  - orchestrator 读取当前 `program_exp.md`
  - 将其内容注入 worker 与 coordinator prompt
- round 结束后：
  - orchestrator 根据 `idea_summary + 实验结果` 更新 `program_exp.md`
  - 不让 agent 自由重写整份经验文档
- coordinator 不是经验文档的唯一维护者
  - 如果只有 coordinator 才能写，那么“无 coordinator 触发”的多数轮次就没有 memory update
  - 因此更合理的边界是：coordinator 贡献内容，orchestrator 统一写入
- 文档保持极简三段：
  - `Positive Directions`
  - `Negative Directions`
  - `Open Notes`

### 目前已经识别到的主要风险

- 风险 1：如果把单次噪声结果也写进经验文档，会把偶然波动误当成规律
- 风险 2：如果让 agent 直接编辑文档，文档会很快变长、变重复，甚至出现代码泄漏
- 风险 3：如果文档跨 run 共享，会污染正式对比实验的公平性
- 风险 4：如果负面经验记录得过于激进，会过早压缩搜索空间，让后续 worker 不敢探索

### 当前默认假设

- 经验文档只是一层“轻量方向记忆”，不是 Reflector 或完整长期记忆系统
- 第一版先做成“per-run memory”
- 第一版先让 orchestrator 成为唯一 writer
- 第一版先保持文档短小，优先保证可控和可读，而不是追求自动总结得多聪明
- 当前已经确认：负面经验不等待重复验证，单次 `worse/crash` 就直接记录

### 下一步

- 进入 `program_exp.md` 的代码实现
- 实现后先用本地回归验证 memory 更新规则，再决定是否跑真实联网试验

## 2026-03-18：实现 per-run `program_exp.md` 共享经验文档

### 完成内容

- 新增 `orchestrator/memory.py`
  - `ProgramExperienceStore`
  - `program_exp.md` 初始化、读取、规则写回
- 扩展 `RunLayout`
  - 新增 `program_experience_path`
- 扩展 `StateStore.initialize_run_files()`
  - run 初始化时自动创建 `program_exp.md`
- 扩展 `ExperimentProposal`
  - 新增 `idea_summary`
- 扩展 `CoordinatorProposal`
  - 新增 `curator_note`
- 扩展 `ZhipuChatAgentRunner`
  - worker / coordinator prompt 现在都要求返回 `idea_summary`
  - coordinator 额外支持 `curator_note`
  - 对 `idea_summary` / `curator_note` 做单行、短文本、无 code-fence 校验
- 扩展 `WorkerRoundRunner`
  - round 开始前读取当前 `program_exp.md`
  - 将其注入 worker 与 coordinator 的 `request_context`
  - round 结束后由 orchestrator 规则更新 `program_exp.md`
- 扩展 `run_single_agent_baseline()` / `run_multi_agent_experiment()`
  - summary 现在额外返回 `program_exp_path`
- 更新 `orchestrator/README.md`

### 当前实现策略

- memory 仍然是 per-run、per-arm 独立
- orchestrator 是唯一 writer
- worker / coordinator 只通过结构化字段贡献内容
- 规则写入按“相对 baseline 的真实结果”分类，而不是按“是否被最终选中”分类
  - `val_bpb < baseline` -> `Positive Directions`
  - `val_bpb > baseline` -> `Negative Directions`
  - `val_bpb == baseline` -> `Open Notes`
  - `crash` / `preflight failed` -> `Negative Directions`
  - `proposal failed` -> `Open Notes`
  - coordinator 的 `curator_note` -> `Open Notes`

### 验证情况

- 新增 `tests/test_memory.py`
- 更新了 `tests/test_stage2.py`、`tests/test_stage4.py`、`tests/test_stage5.py`、`tests/test_stage6.py`
- 更新了 `tests/test_live_baseline.py`、`tests/test_live_multi.py`
- 全量回归：
  - `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_memory tests.test_live_baseline tests.test_live_multi -v`
  - 当前共通过 `45` 个测试

### 当前判断

- `program_exp.md` 已经接入主流程，不再只是文档方案
- V1 的 memory 现在具备：
  - 可控
  - 可审阅
  - 不依赖 coordinator 才能更新
  - 不会把代码片段直接塞进经验文档
- 下一步如果要继续推进，最合适的是跑一次真实短程实验，观察真实 `idea_summary` 的质量和文档是否过快膨胀

## 2026-03-18：真实 `program_exp.md` smoke run

### 运行命令

- `python3 scripts/run_glm_multi_agent.py --target-repo autoresearch-3090 --worker-count 2 --rounds 1 --model glm-4.5-air --coordinator-model glm-4.7 --run-tag glm-multi-agent-memory-smoke-20260318`

### 结果摘要

- baseline 成功测得 `val_bpb=1.177383`
- `worker-1` proposal 成功、训练成功，但结果变差到 `1.181646`
- `worker-2` proposal 成功、训练成功，但结果轻微变差到 `1.177649`
- 本轮没有正向候选，因此 `coordinator` 未触发，baseline 保持不变
- `program_exp.md` 在 round 结束后被 orchestrator 正确更新为 2 条 `Negative Directions`

### 额外确认

- 真实 worker prompt 已包含 `program_exp.md` 内容
  - 该内容被写入 `agent_request.json` 的 `messages[1].content` 中，而不是单独的顶层字段
- 文档更新仍然是 round 末统一写入，不会在单个 worker 完成后提前污染共享经验

### 产物位置

- run summary: `runs/glm-multi-agent/glm-multi-agent-memory-smoke-20260318/summary.json`
- shared memory: `runs/glm-multi-agent/glm-multi-agent-memory-smoke-20260318/program_exp.md`

### 当前判断

- `program_exp.md` 的读写链路已经在真实 LLM + 真实训练环境下跑通
- 当前文档质量基本符合预期，但 idea 去重和压缩规则后续还可以继续加强
- 这轮结果再次说明当前主要问题还是 worker proposal 质量，而不是 memory 接入本身

## 2026-03-18：单 worker 模型 A/B（第一轮）

### 目标

- 粗测 `worker` 模型质量是否是当前 proposal 质量偏低的主要原因
- 固定实验形态为：
  - 单 worker
  - `rounds=1`
  - 无 coordinator
  - 同一目标仓库：`autoresearch-3090-baseline`

### 运行命令

- `python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1 --model glm-4.5-air --run-tag glm-model-ab-45air-r1`
- `python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1 --model glm-4.6 --run-tag glm-model-ab-46-r1`
- `python3 scripts/run_glm_single_baseline.py --target-repo autoresearch-3090-baseline --rounds 1 --model glm-4.7 --run-tag glm-model-ab-47-r1`

### 结果

- `glm-4.5-air`
  - baseline: `1.177050`
  - candidate: `1.177017`
  - 结果：`keep`
  - proposal 方向：调缓 warmdown，使尾段学习率不要降得太快
- `glm-4.6`
  - baseline: `1.177317`
  - candidate: `1.183399`
  - 结果：`discard`
  - proposal 方向：加入 `5%` warmup
  - 额外现象：第一次 API 调用 read timeout，第二次重试成功
- `glm-4.7`
  - baseline: `1.177567`
  - candidate: `1.183420`
  - 结果：`discard`
  - proposal 方向：加入 `5%` warmup

### 当前判断

- 仅从这组第一轮小样本看，不能支持“模型越强 worker 就越好”
- 在当前 prompt / memory / repo 状态下：
  - `glm-4.5-air` 至少拿到了一次真实正提升
  - `glm-4.6` 和 `glm-4.7` 都收敛到同类 warmup 修改，并且都明显变差
- 这说明当前瓶颈不只是模型强弱，还包括：
  - prompt 诱导方向
  - memory 内容是否足够区分有效/无效思路
  - 单轮样本数过少，随机性仍然明显

## 2026-03-18：固化默认模型分层

### 完成内容

- 默认模型分层固定为：
  - worker: `glm-4.5-air`
  - coordinator: `glm-4.7`
- 新增环境变量：
  - `ZHIPUAI_WORKER_MODEL`
  - `ZHIPUAI_COORDINATOR_MODEL`
- 保留 `ZHIPUAI_MODEL` 作为旧配置回退

### 修改位置

- `orchestrator/agents.py`
- `.env.example`
- `.env.local`
- `orchestrator/README.md`
- `tests/test_stage6.py`

### 当前使用方式

- 以后优先在 `.env.local` 里直接修改：
  - `ZHIPUAI_WORKER_MODEL=...`
  - `ZHIPUAI_COORDINATOR_MODEL=...`
- 如果脚本命令行显式传了 `--model` 或 `--coordinator-model`，命令行仍然优先

### 验证

- `python3 -m unittest tests.test_env tests.test_stage6 -v`
- 结果：通过

## 2026-03-18：长程 MAAR launcher 与续跑恢复

### 完成内容

- 新增 `scripts/run_long_maar.py`
  - 默认长程配置就是：
    - `3 workers`
    - `1 coordinator`
    - `20 rounds`
    - worker 默认 `glm-4.5-air`
    - coordinator 默认 `glm-4.7`
- 新增多智能体 resume 入口：
  - `orchestrator.resume_multi_agent_experiment()`
- 扩展持久化层：
  - `StateStore.load_run_state()`
  - `StateStore.load_round_state()`
- resume 支持两类场景：
  - 已完成若干轮后继续追加到目标总轮数
  - 中断在某一轮中途时，自动回退到“上一轮已完成状态”并重跑该轮

### 使用约定

- `scripts/run_long_maar.py --rounds 20` 中的 `20` 表示目标总轮数，不是追加轮数
- 如果同一 `run_tag` 已存在，脚本默认自动 resume
- 如果想强制新开一次同名 run，必须显式更换 `run_tag` 或使用 `--new-run-tag`

### 验证

- `python3 -m unittest tests.test_live_multi tests.test_stage6 tests.test_env -v`
- `python3 -m unittest tests.test_env tests.test_runtime tests.test_stage1 tests.test_stage2 tests.test_stage3 tests.test_stage4 tests.test_stage5 tests.test_stage6 tests.test_memory tests.test_live_baseline tests.test_live_multi -v`
- `python3 scripts/run_long_maar.py --help`
- 当前总计 `48` 个测试通过

## 2026-03-18：对齐原始 `autoresearch` 心智模型，重写 worker prompt framing

### 背景判断

- 复盘原始 `autoresearch/program.md` 后确认：
  - 原始单智能体 prompt 的强项不只是“允许大搜索空间”
  - 更关键的是它把 agent 明确塑造成“推进主线的自治研究员”
  - 它强调：
    - 单轮就是一个实验假设
    - 变好就推进主线，变差就回退
    - 简洁本身也是价值
- 反观我们之前的 `orchestrator/agents.py`：
  - 更像“安全 Search/Replace patch 生成器”
  - 约束密度很高，但研究 framing 偏弱
  - 可能导致 worker 更关注“不出错”，而不是“做一轮像样的实验”

### 本轮修改

- 在 `orchestrator/agents.py` 中重写 worker / coordinator 的 system prompt 前半部分：
  - 明确要求把自己视为推进当前 mainline 的自治研究员
  - 明确目标是提出“一个 coherent experiment”
  - 明确要求利用 `program_exp.md` 避开最近失败方向
  - 明确加入“简单优先”的价值判断
- 保留执行层和安全边界不变：
  - 仍然只允许单个 Search/Replace
  - 仍然只允许改 `train.py`
  - 仍然保留 side effects / main loop / runtime bootstrap 等关键禁止项
- 同时放宽了一条过于保守的表达：
  - 不再把 worker 强行压成“只能改常量/小表达式”
  - 现在明确允许在实验假设确实需要时替换完整的既有函数体
- 在 user prompt 中额外强调：
  - 这是一轮 mainline research iteration
  - 先读 context 中的 shared experience notes
  - 除非有非常具体的反例理由，否则不要重复最近负向方向

### 当前决定

- 共享经验输入当前仍然只喂 `program_exp.md`
- 暂时不额外注入最近 3-5 条结果摘要
- 下一轮真实试跑先观察：
  - worker proposal 是否更像“单一实验假设”
  - 是否减少重复 cosine / warmup 类失败方向

### 验证

- 更新 `tests/test_stage6.py`，改为断言新的研究型 prompt 文案

## 2026-03-18：固定新 run 的 baseline 起点到原始 3090 commit

### 背景判断

- 当前项目的主要目标不是把单个 run 推到最优模型
- 更重要的是公平验证：
  - `MAAR` 多智能体架构
  - 相比单 worker `autoresearch-style baseline`
  - 在相同轮数下是否能更快取得更好的 `val_bpb`
- 因此，每个新 run 都应从同一个“原始 3090 版本”起点开始，而不是从目标仓库当前 `HEAD` 自动继承

### 本轮修改

- 为 `RunConfig` 新增 `baseline_source_ref`
- 为 `RunState` 新增：
  - `baseline_source_ref`
  - `initial_baseline_commit`
- `WorktreeManager.initialize_run()` 现在会：
  - fresh run 时优先从 `baseline_source_ref` 解析 commit
  - 再基于这个固定 commit 创建 baseline / worker / merge 分支与 worktree
- run 内部的 keep/discard 推进逻辑不变：
  - 只改变“新 run 从哪里开始”
  - 不改变“同一 run 内主线是否逐轮推进”
- 多个入口脚本默认统一把 fresh run 起点固定为：
  - `90243dd`
  - 即 `Add 3090 baseline training profile`
  - 这个 commit 在 `autoresearch-3090` 与 `autoresearch-3090-baseline` 中都可解析

### 入口变化

- `scripts/run_long_maar.py`
- `scripts/run_glm_multi_agent.py`
- `scripts/run_glm_single_baseline.py`

它们现在都支持：

- `--baseline-source-ref`

默认值都是：

- `90243dd`

### 验证

- `python3 -m unittest tests.test_stage1 tests.test_live_baseline tests.test_live_multi -v`
- 结果：通过

## 2026-03-18：加入小算力 prompt 建议，并创建 300s / 600s / 900s 共享 benchmark 家族

### Prompt 调整

- 在 `orchestrator/agents.py` 中追加了更接近原始 `autoresearch` README 的“小算力 / 短预算”提示：
  - 对固定短预算和较小 GPU，优先寻找“更早见效的训练效率改动”
  - 明确提示 3090 场景下优先考虑：
    - 更简单的 attention window pattern
    - 更合适的 batch / grad accumulation
    - 更适合短跑的 learning-rate schedule
    - 必要时更小、更干净的模型形状
- 对应回归：
  - `python3 -m unittest tests.test_stage6 -v`
  - 结果：通过

### 新 benchmark family

- 新建三套共享 benchmark repo：
  - `autoresearch-3090-bench300`
  - `autoresearch-3090-bench600`
  - `autoresearch-3090-bench900`
- 三者都从 `autoresearch-3090` 克隆而来，并共享同一套“中度弱化”初始 `train.py`
- 三者唯一系统差异是 `prepare.py` 中的 `TIME_BUDGET`：
  - `300`
  - `600`
  - `900`

### 共享的中度弱化点

- 在三个 bench 的 `train.py` 中统一引入同一套共享弱点：
  - `WINDOW_PATTERN = "SSLL"`
  - `TOTAL_BATCH_SIZE = 2**18`
  - `WARMUP_RATIO = 0.10`
  - `WARMDOWN_RATIO = 0.75`
  - `FINAL_LR_FRAC = 0.10`
- 设计意图：
  - 留下少量“有点蠢但不明显”的问题
  - 同时保留训练效率型改进空间
  - 让多智能体 / 单 worker 在相同轮数下更容易显现方法差异

### Repo 内固定 baseline ref

- 每个新 bench repo 都新增：
  - `.maar_baseline_ref`
- 文件内容统一为：
  - `maar-bench-root`
- 每个 repo 都给当前 benchmark 根提交打了同名 tag：
  - `maar-bench-root`
- 启动脚本现在会自动解析 baseline 起点：
  - 显式 `--baseline-source-ref`
  - 否则读取目标 repo 中的 `.maar_baseline_ref`
  - 再否则回退到旧的 `90243dd`

### 脚本层变化

- 已更新：
  - `scripts/run_long_maar.py`
  - `scripts/run_glm_multi_agent.py`
  - `scripts/run_glm_single_baseline.py`
- 这样未来切换到新 benchmark repo 时，不需要再手工记住对应的 baseline commit/tag

### 验证

- 静态检查：
  - `python3 -m py_compile autoresearch-3090-bench300/prepare.py autoresearch-3090-bench300/train.py`
  - `python3 -m py_compile autoresearch-3090-bench600/prepare.py autoresearch-3090-bench600/train.py`
  - `python3 -m py_compile autoresearch-3090-bench900/prepare.py autoresearch-3090-bench900/train.py`
  - 结果：通过
- 脚本层验证：
  - 三个 launcher 都已确认会自动解析新 bench repo 中的 `.maar_baseline_ref -> maar-bench-root`
- `bench300` GPU smoke：
  - 能顺利进入真实 GPU 训练阶段
  - 本次未完成 baseline 测量，因为当时 GPU 上已有其他 Python 进程占用约 `15.5GiB`
  - 日志显示当前 benchmark 进程自身已使用约 `9.4GiB`，因此这次失败更像并发占用导致的 OOM，而不是 benchmark 代码本身无法运行

## 2026-03-18：在原始 `autoresearch-3090` 上做一次单 worker 两轮对照

### 实验目的

- 在不改变当前 worker 模型的前提下，直接对原始 `autoresearch-3090` 做一次更接近原始 `autoresearch` 工作方式的单 worker 短跑
- 用来判断：
  - 如果单 worker 在原始 3090 版本上也连续两轮没有提升，那么问题不一定只来自 MAAR 架构
  - 但结果仍然只能说明“当前模型 + 当前 prompt + 当前两轮搜索方向”的表现，不能直接证明模型能力一定不够

### 运行配置

- 目标仓库：
  - `autoresearch-3090`
- 运行入口：
  - `scripts/run_glm_single_baseline.py`
- 轮数：
  - `2`
- coordinator：
  - 不启用
- baseline source ref：
  - `90243dd`

### 结果

- 运行目录：
  - `runs/glm-single-baseline/glm-single-baseline-autoresearch3090-r2-20260318`
- baseline：
  - `1.196359`
- 第 1 轮：
  - candidate `val_bpb = 1.199781`
  - `discard`
- 第 2 轮：
  - candidate `val_bpb = 1.199073`
  - `discard`
- 2 轮结束后：
  - `baseline_after = 1.196359`
  - 无提升

### 初步判断

- 这次对照说明：
  - 在原始 `autoresearch-3090` 上，当前单 worker 配置也没有在 2 轮内拿到提升
- 但这还不足以直接推出“模型太差”：
  - 两轮 proposal 都集中在 `WINDOW_PATTERN` 方向
  - 属于非常窄的一段搜索空间
  - 更像是这次短跑没有找到好方向，而不是已经证明模型没有优化能力

### 证据文件

- summary：
  - `runs/glm-single-baseline/glm-single-baseline-autoresearch3090-r2-20260318/summary.json`
- 经验文档：
  - `runs/glm-single-baseline/glm-single-baseline-autoresearch3090-r2-20260318/program_exp.md`
- round 1 proposal：
  - `runs/glm-single-baseline/glm-single-baseline-autoresearch3090-r2-20260318/rounds/round-0001/workers/worker-1/proposal.json`
- round 2 proposal：
  - `runs/glm-single-baseline/glm-single-baseline-autoresearch3090-r2-20260318/rounds/round-0002/workers/worker-1/proposal.json`

## 2026-03-19：在弱化后的 `bench300` 上做单 worker 5 轮实验

### 实验目的

- 验证“中度弱化后的共享 benchmark”是否真的给 agent 留出了更明显的改进空间
- 继续使用当前 worker 模型 `glm-4.5-air`
- 对照前一天在原始 `autoresearch-3090` 上的单 worker 2 轮无提升结果

### 运行配置

- 目标仓库：
  - `autoresearch-3090-bench300`
- 入口：
  - `scripts/run_glm_single_baseline.py`
- 轮数：
  - `5`
- 模型：
  - `glm-4.5-air`
- baseline source ref：
  - `maar-bench-root`

### baseline

- 初始 baseline：
  - `1.189626`
- 说明：
  - 这个分数确实比原始 `autoresearch-3090` 更差，说明共享弱化已生效

### 结果

- round 1：
  - `1.189626 -> 1.189593`
  - `keep`
- round 2：
  - `1.189593 -> 1.187981`
  - `keep`
- round 3：
  - `1.187981 -> 1.186557`
  - `keep`
- round 4：
  - `1.186557 -> 1.187840`
  - `discard`
- round 5：
  - `1.186557 -> 1.188892`
  - `discard`

### 结论

- 5 轮结束后：
  - `baseline_after = 1.186557`
- 相比初始 baseline：
  - 总改进约 `0.003069`
- 直接结论：
  - 当前 `glm-4.5-air` 不是完全做不出改进
  - 在原始 `autoresearch-3090` 上没提升，不足以单独证明模型能力太差
  - 弱化后的 benchmark 的确更容易显现 agent 搜索带来的收益

### 观察

- 前 3 轮连续 `keep`，说明弱化后的共享 baseline 留出的空间足够让单 worker 逐步推进
- 后 2 轮开始变差，说明：
  - 当前搜索并不是“永远单调上升”
  - 但至少已经证明了现有模型在这个 benchmark 上存在真实可用的优化能力
- 因此后续 MAAR vs 单 worker 的对比，更适合放在这套共享弱化 benchmark 上做

### 证据文件

- summary：
  - `runs/glm-single-baseline/glm-single-baseline-bench300-r5-20260319/summary.json`
- 经验文档：
  - `runs/glm-single-baseline/glm-single-baseline-bench300-r5-20260319/program_exp.md`
- 全部结果：
  - `runs/glm-single-baseline/glm-single-baseline-bench300-r5-20260319/results.tsv`

## 2026-03-19：拆分 MAAR / baseline prompt profile，并改进 `program_exp.md`

### 本次目的

- 解决 worker 被 `program_exp.md` 中重复的 `WINDOW_PATTERN` 成功经验过度锚定的问题
- 让 MAAR worker 在 `train.py` 内拥有更宽的探索空间
- 同时让单 worker baseline 更接近原始 `autoresearch/program.md` 的研究式 prompt
- 把 `autoresearch-3090-baseline` 的初始 `train.py` / `prepare.py` 也同步到和 `bench300` 一致的中度弱化起点

### Prompt profile 分层

- 新增两种 worker prompt profile：
  - `maar_wide`
  - `autoresearch_original`
- `maar_wide` 用于 MAAR：
  - 明确允许更宽泛地修改模型结构、优化器、调度、batch、head/layout、activation、residual/value-embedding 等逻辑
  - 明确把 positive directions 视为“证据”而不是“继续沿同一机制优化的命令”
  - 仅保留少量硬边界：
    - 只改 `train.py`
    - 单次 `Search/Replace`
    - 不改 `prepare.py` / tokenizer / eval harness / runtime bootstrap
    - 不引入新的文件或进程 side effects
    - 避免明显超出 3090 预算的 width / depth / batch 爆炸
- `autoresearch_original` 用于单 worker baseline：
  - 尽量贴近原始 `autoresearch/program.md`
  - 强调：
    - `train.py` 里 everything is fair game
    - 固定 5 分钟预算
    - simplicity criterion
    - keep / discard / advance 的研究心智模型
  - 仍保留我们自己的 JSON 输出契约与 `Search/Replace` 执行接口

### `program_exp.md` 写入规则调整

- 之前的 memory 问题：
  - 会把连续 3 次 `WINDOW_PATTERN` 成功直接写成 3 条具体 idea
  - 导致后续 worker 在 prompt 中被高度锚定到同一机制
- 现在改为：
  - 对重复出现的 idea family 做按机制归并
  - 对 `attention-window` / `schedule` / `batch-geometry` / `optimizer` / `model-shape` / `activation-mlp` / `residual-value` 等族做规则级摘要
  - 当同一家族多次成功时，额外写入一条 `diversify` note：
    - 提醒后续 worker 优先探索不同机制
- 结果是：
  - memory 仍然保留“哪些方向有效/无效”的证据
  - 但不再把上下文压缩成一串几乎重复的具体 patch idea

### baseline 仓库对齐

- 已把 `autoresearch-3090-baseline` 的：
  - `train.py`
  - `prepare.py`
- 同步到与 `autoresearch-3090-bench300` 相同的中度弱化起点：
  - `WINDOW_PATTERN = "SSLL"`
  - `TOTAL_BATCH_SIZE = 2**18`
  - `WARMUP_RATIO = 0.10`
  - `WARMDOWN_RATIO = 0.75`
  - `FINAL_LR_FRAC = 0.10`
  - `TIME_BUDGET = 300`
  - `EVAL_TOKENS = 10 * 524288`

### launcher 变化

- `scripts/run_glm_single_baseline.py`
  - 新增 `--worker-prompt-profile`
  - 默认行为：
    - 若 target repo 名字以 `-baseline` 结尾，则自动用 `autoresearch_original`
    - 否则默认 `maar_wide`
- `scripts/run_glm_multi_agent.py`
  - 默认 worker prompt 固定为 `maar_wide`
  - coordinator prompt 固定为 `coordinator`
- `scripts/run_long_maar.py`
  - 同样固定使用 `maar_wide` / `coordinator`

### 验证

- `python3 -m py_compile orchestrator/agents.py orchestrator/memory.py orchestrator/config.py scripts/run_glm_single_baseline.py scripts/run_glm_multi_agent.py scripts/run_long_maar.py autoresearch-3090-baseline/run_single_agent_baseline.py autoresearch-3090-baseline/train.py autoresearch-3090-baseline/prepare.py`
  - 结果：通过
- `python3 -m unittest tests.test_stage6 tests.test_memory tests.test_live_baseline tests.test_live_multi -v`
  - 结果：通过

## 2026-03-19：MAAR-only coordinator / preflight 增强

### 本次目的

- 提升 MAAR coordinator 的实际有效性，而不是让它在没有真实合并价值时重复最佳 worker
- 提升 MAAR worker 的 preflight 安全性，提前拦截“结构改了但其余引用没同步”的运行时崩溃
- 严格保持 baseline 臂不吃这些新增强，避免把 `autoresearch_original` 对照臂一起强化

### Coordinator 升级

- 强化了 coordinator prompt：
  - 明确要求优先做“真实可组合”的 merge
  - 若多个候选触及同一机制或同一代码区域，不要随意做折中平均
  - 若没有明确组合价值，则应更接近“保留最强候选”而不是发明新 midpoint
- 给 coordinator 输入补充了更明确的候选上下文：
  - `candidate_val_bpb`
  - `improvement_delta`
  - `idea_family`
- 同时在 orchestrator 侧加入了一个轻量 gating：
  - 若 top-k 正向候选本质上是完全相同的 proposal，则不再浪费一次 coordinator merge 运行

### MAAR-only preflight 增强

- 新增显式 `preflight_profile`：
  - `maar_strict`
  - `baseline_legacy`
  - `standard`
- `run_glm_multi_agent.py` 与 `run_long_maar.py` 现在固定走 `maar_strict`
- baseline 入口继续走 `baseline_legacy`
- `maar_strict` 在原有检查之外，新增了结构一致性静态检查：
  - 检查类内部 `self.xxx` 访问是否引用了未定义成员
  - 检查外部组件访问（如 `.mlp.xxx` / `.attn.xxx`）是否仍然对应类里真实存在的成员
- 这样能在训练前拦截这类错误：
  - worker 把 `MLP.c_fc` 改成 `c_fc1/c_fc2`
  - 但 `init_weights()` 仍然访问旧的 `block.mlp.c_fc`

### 对 baseline 的边界

- 这次增强不改变 `autoresearch_original` prompt
- 不改变 baseline 臂的默认 preflight 行为
- 不改变 `autoresearch-3090-baseline` 的研究 loop 语义
- baseline 仍然尽量贴近原始 `autoresearch` 风格；新增的更强 guard 只属于 MAAR

### 验证

- `python3 -m unittest tests.test_stage2 tests.test_runtime tests.test_live_multi tests.test_stage6 -v`
  - 结果：通过（32 tests）
- 新增/更新的关键覆盖：
  - `MAAR strict preflight` 能拦截组件成员失配
  - `baseline legacy preflight` 不会吃到这条 MAAR-only guard
  - coordinator 在候选完全重复时会跳过 merge
  - coordinator 在确实存在更优 merged candidate 时仍可正常晋升

## 2026-03-21：收窄 MAAR strict preflight，修复 GLM-4.6v 长跑误报

### 本次问题

- `glm-multi-agent-bench300-r50-glm46v` 在前两轮全部出现 `null`
- 根因不是网络，也不是训练崩溃，而是 `maar_strict` preflight 在训练前误报：
  - `register_buffer("cos"/"sin")` 被误判成 `GPT` 未定义成员
  - `torch.optim.Optimizer` 继承而来的 `param_groups/state` 被误判成 `MuonAdamW` 未定义成员
- 结果是所有 worker 都停在 `preflight_failed`，根本没有进入训练

### 调整

- 收窄了 `maar_strict`：
  - 不再扫描类内部所有 `self.xxx` 读取
  - 只保留更确定的外部组件成员失配检查，例如 `.mlp.c_fc` 在类中已被删除但外部仍在访问
- 这样做的目的不是取消 MAAR 的安全性，而是把 preflight 限制回“尽量只拦几乎一定会错的问题”，避免再次误杀合法 proposal

### 运行清理

- 中止并清理了失败的 `glm-multi-agent-bench300-r50-glm46v`
- 删除了对应：
  - run 目录
  - `nohup` 日志
  - worker/merge worktree
  - 相关临时分支

### 验证

- `python3 -m unittest tests.test_stage2 -v`
  - 结果：通过
- `python3 -m unittest tests.test_live_multi -v`
  - 结果：通过
- 新增回归：
  - `maar_strict` 允许 `register_buffer` 和 `Optimizer` 继承成员
  - `maar_strict` 仍然会拦截明确的 `.mlp.c_fc` 这类外部成员失配

## 2026-03-23：修复 bench600/900 默认训练超时过短的问题

### 现象

- `glm-single-baseline-bench900-r50-20260323` 在 baseline 测量阶段直接失败
- 错误不是来自模型、代理或训练环境，而是：
  - `baseline measurement failed: command exceeded timeout of 900.0s`

### 原因

- `bench900` 的 `TIME_BUDGET` 本身就是 `900s`
- launcher 默认的 `train_timeout_seconds` 也是 `900s`
- 这意味着 baseline 训练只要再加上启动、编译、final eval 和日志收尾时间，就会被外层执行器提前杀掉

### 决策

- 将以下 launcher 的默认训练超时从 `900s` 提高到 `1500s`：
  - `run_glm_single_baseline.py`
  - `run_glm_multi_agent.py`
  - `run_long_maar.py`
- 新帮助文案明确说明：
  - `train_timeout_seconds` 应显著高于 `TIME_BUDGET`
  - 需要为启动和最终评估预留缓冲

### 清理

- 删除了失败的 `glm-single-baseline-bench900-r50-20260323`
- 清理了对应：
  - run 目录
  - `nohup` 日志
  - worker/merge worktree
  - baseline/worker/merge 分支

### 验证

- `python3 -m py_compile scripts/run_glm_single_baseline.py scripts/run_glm_multi_agent.py scripts/run_long_maar.py`
  - 结果：通过
