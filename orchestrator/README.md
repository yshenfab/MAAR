# Orchestrator Skeleton

这个目录承载多智能体 `autoresearch` orchestrator 的外层代码。  
当前阶段已经完成：schema、layout、worktree、patcher、preflight、executor、worker loop、coordinator merge、真实 GLM agent backend。后续阶段重点转向真实小规模试跑和长跑稳定化。

## 目录职责

- `config.py`
  - 运行级配置
  - `RunConfig`
  - `CoordinatorConfig`
- `state.py`
  - 运行状态和结果 schema
  - `ExperimentProposal`
  - `ExperimentResult`
  - `RoundState`
  - `RunState`
- `layout.py`
  - `runs/<tag>/` 的目录布局约定
  - worker / coordinator 的 workspace 与产物路径
- `git_ops.py`
  - 对目标仓库执行 git 命令
  - 分支与 worktree 管理
- `worktree.py`
  - run 初始化
  - baseline / worker / merge worktree 同步
- `patcher.py`
  - `Search/Replace` 补丁应用
  - 自动生成 `train.py` 的 git diff
- `preflight.py`
  - 只允许 `train.py` 被修改
  - Python 语法检查
  - import 可用性检查
- `executor.py`
  - 训练命令执行
  - `run.log` 采集
  - summary 指标解析
  - timeout / crash 处理
- `persistence.py`
  - `run.json`
  - `results.tsv`
  - `experiments.jsonl`
- `memory.py`
  - `program_exp.md` 的初始化、读取和规则写回
  - per-run 共享经验文档维护
- `agents.py`
  - `AgentRunner` 抽象
  - `ReplayAgentRunner` 测试与本地回放实现
  - `ZhipuChatAgentRunner` 真实 GLM backend
  - `build_agent_runner` 后端装配入口
- `round_runner.py`
  - 单轮 worker proposal 执行
  - coordinator merge 触发、验证与回退
  - baseline 晋升与 worktree 同步
- `live_baseline.py`
  - 真实单智能体 baseline 臂入口
  - baseline 首次测量
  - `1 worker / no coordinator` 的真实 round 执行
- `runtime.py`
  - 目标仓库运行时解析
  - `RunConfig` 到 preflight / executor 的命令装配
- `serialization.py`
  - dataclass 到 JSON 友好结构的转换

## 固定运行目录布局

每次运行都落到：

```text
runs/<run_tag>/
  run.json
  results.tsv
  experiments.jsonl
  program_exp.md
  workspaces/
    worker-1/
    worker-2/
    ...
    merge/
  rounds/
    round-0001/
      round.json
      workers/
        worker-1/
        worker-2/
        ...
      coordinator/
    round-0002/
      ...
```

这里的约定是：

- `workspaces/` 放真实 Git worktree
- `rounds/` 放每一轮的实验产物
- `run.json` 放全局运行状态快照
- `results.tsv` 放便于人工查看和后续分析的扁平结果表
- `experiments.jsonl` 放逐条追加的实验记录
- `program_exp.md` 放当前 run 的共享经验文档，只记录 idea，不记录代码

## 当前阶段完成的事

- 明确了 orchestrator 代码落点
- 明确了运行产物的固定目录结构
- 定义了 V1 需要的核心配置和状态 schema
- 实现了 worktree 初始化与 baseline 同步
- 实现了 `Search/Replace` patcher 与 preflight
- 实现了训练执行器与日志解析
- 实现了 mock/replay 驱动的单轮 worker loop
- 实现了 coordinator merge 路径
- 实现了基于智谱 GLM `chat/completions` 的真实 agent backend
- 实现了 agent request/response/output/error 的落盘
- 实现了运行时解析层，用于把 preflight / executor 绑定到目标仓库环境
- 实现了 per-run `program_exp.md` 经验文档，由 orchestrator 规则写入，worker/coordinator 只读

## 真实 GLM backend

当前支持的真实 agent backend 标识：

- `zhipu`
- `glm`
- `zhipuai`

环境变量约定：

```text
ZHIPUAI_API_KEY=...
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPUAI_WORKER_MODEL=glm-4.6v
ZHIPUAI_COORDINATOR_MODEL=glm-4.7
ZHIPUAI_MODEL=glm-4.6v
```

默认解析顺序是：

- worker: `ZHIPUAI_WORKER_MODEL` -> `ZHIPUAI_MODEL` -> `glm-4.6v`
- coordinator: `ZHIPUAI_COORDINATOR_MODEL` -> `ZHIPUAI_MODEL` -> `glm-4.7`

真实 backend 的职责边界：

- 读取当前 worktree 下的 `train.py`
- 读取当前 run 的 `program_exp.md` 快照
- 构造 worker / coordinator prompt
- 调用标准 `chat/completions` 接口
- 校验结构化 JSON 输出
- 返回 `ExperimentProposal` / `CoordinatorProposal`

真实 backend 不负责：

- 直接修改文件
- 执行训练
- 决定最终晋升哪个候选
- 直接编辑 `program_exp.md`

每次真实调用会在对应实验目录额外落盘：

- `agent_request.json`
- `agent_response_01.json`
- `agent_output_01.txt`
- `agent_error_01.txt`

这样可以在 proposal failure 或 patch failure 时直接回看 prompt 和模型原始输出。

## Long-Run Launcher

长程多智能体实验现在有一个专门入口：

```bash
python3 scripts/run_long_maar.py
```

这个脚本的默认目标就是长跑 MAAR 配置：

- `worker_count=3`
- `rounds=20`
- worker 默认走 `glm-4.6v`
- coordinator 默认走 `glm-4.7`

使用特点：

- 脚本顶部有一组可直接编辑的默认参数
- 也支持命令行覆盖，例如：

```bash
python3 scripts/run_long_maar.py --rounds 10 --worker-count 4
```

- 如果同一个 `run_tag` 已经存在，脚本默认进入 resume 模式
- resume 语义是“把 `--rounds` 视为总目标轮数”，不是“再加多少轮”
- 如果上一次中断在某一轮中途，resume 会自动回退到“上一轮已完成状态”，然后重跑这一个未完成轮

## Baseline Source Resolution

fresh run 的 baseline 起点现在支持两层解析：

- 如果显式传了 `--baseline-source-ref`，优先使用它
- 否则如果目标 repo 根目录存在 `.maar_baseline_ref`，读取其中的 git ref
- 否则回退到旧的默认 `90243dd`

这允许不同 benchmark repo 自带各自固定的“原始起点”，而不需要每次手工记 commit。

## Benchmark Families

当前除了原始 `autoresearch-3090` 之外，还预置了三套共享 benchmark repo：

- `autoresearch-3090-bench300`
- `autoresearch-3090-bench600`
- `autoresearch-3090-bench900`

它们的共同特点：

- 都面向 `MAAR` 和单 worker baseline 的公平对比
- 都带有同一套“中度弱化”的初始 `train.py`
- 都在 repo 根目录提供 `.maar_baseline_ref=maar-bench-root`
- 三者唯一的系统性差异是 `prepare.py` 里的 `TIME_BUDGET`

共享的中度弱化点包括：

- 更保守的 `WINDOW_PATTERN`
- 更大的 `TOTAL_BATCH_SIZE`
- 过长的 warmup / warmdown
- 非零的 `FINAL_LR_FRAC`

这类弱点是刻意留下的 benchmark 空间，目标不是训练出最佳模型，而是更清楚地观察多智能体架构是否能在固定轮数里更快改进。

## Program Experience Memory

每个 run 都会维护一个共享经验文档：

```text
runs/<run_tag>/program_exp.md
```

当前策略：

- 由 orchestrator 单写，agent 不直接改这个文件
- worker 和 coordinator 的 prompt 都会读到它的当前快照
- 文档只记录 idea，不记录代码、diff 或长日志
- 单次 `improved` / `worse` / `crash` 都可以留下简短经验
- coordinator 触发时可以额外贡献一条 `curator_note`

V1 的目标不是做复杂长期记忆，而是给多轮实验一个短小、可控、可审阅的 per-run 方向记忆。

## 运行时解析

`runtime.py` 负责把目标仓库解析成一个可执行的运行时：

- 显式 `runtime_python_command`
- `<repo>/.venv/bin/python`
- `<repo>/venv/bin/python`
- `uv run python`
- 系统 `python3`

对应能力：

- `resolve_runtime(config)`
- `build_preflight_checker(config)`
- `build_training_executor(config)`

如果你想检查当前 `autoresearch/` 是否已经具备真实训练环境，可以直接运行：

```bash
python3 scripts/check_autoresearch_runtime.py
```

这个脚本会打印：

- 当前解析到的 `python_command`
- `train_command`
- `import_check_command`
- 一个最小 import probe 的结果

## 下一阶段

真实单智能体对比臂可以直接运行：

```bash
python3 scripts/run_glm_single_baseline.py --rounds 1
```

这个入口会：

- 在目标 repo 上创建独立 run worktree
- 先测一次 baseline `val_bpb`
- 再用同一套 GLM backend 跑 `worker_count=1`、`coordinator=false` 的单智能体轮次
- 在每轮后规则更新 `program_exp.md`
- 把汇总结果写到 `runs/glm-single-baseline/<run_tag>/summary.json`

真实多智能体实验臂可以直接运行：

```bash
python3 scripts/run_glm_multi_agent.py --worker-count 2 --rounds 1
```

这个入口会：

- 在目标 repo 上创建独立 run worktree
- 先测一次 baseline `val_bpb`
- 再用同一套 GLM backend 跑多个 worker
- 在满足阈值时触发 coordinator merge
- 在每轮后规则更新 `program_exp.md`，coordinator 触发时可附带 `curator_note`
- 把汇总结果写到 `runs/glm-multi-agent/<run_tag>/summary.json`

如果你想试混合架构，比如 worker 用 `glm-4.6v`、coordinator 用 `glm-4.7`，当前入口已经支持：

```bash
python3 scripts/run_glm_multi_agent.py \
  --worker-count 3 \
  --rounds 1 \
  --model glm-4.6v \
  --coordinator-model glm-4.7
```

这里的语义是：

- `--model` 控制 worker 默认模型
- `--coordinator-model` 单独覆盖 coordinator 模型
- 如果不传 `--coordinator-model`，coordinator 会跟 worker 使用同一个模型

下一步是实现：

- 用 `autoresearch-3090-baseline` 和 `autoresearch-3090` 跑第一次对称的真实对比实验
- 继续提高真实 proposal 的稳定性和有效提升率
