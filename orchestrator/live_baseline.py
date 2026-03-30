from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import AgentRunner, build_agent_runner
from .config import CoordinatorConfig, RunConfig
from .executor import ExecutionStatus, TrainingExecutor
from .preflight import PreflightChecker
from .round_runner import WorkerRoundRunner
from .runtime import build_preflight_checker, build_training_executor, resolve_runtime
from .serialization import SerializableDataclass
from .state import ActorRole, ExperimentMetrics
from .worktree import WorktreeManager


@dataclass(slots=True)
class BaselineMeasurement(SerializableDataclass):
    workspace_path: Path
    log_path: Path
    metrics_path: Path
    metrics: ExperimentMetrics
    command: tuple[str, ...]


def measure_baseline(
    workspace_path: Path,
    executor: TrainingExecutor,
    artifact_dir: Path,
) -> BaselineMeasurement:
    workspace_path = Path(workspace_path).expanduser().resolve()
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    log_path = artifact_dir / "baseline.log"
    metrics_path = artifact_dir / "baseline_metrics.json"
    execution = executor.run(workspace_path, log_path)
    if execution.status is not ExecutionStatus.SUCCESS:
        raise RuntimeError(f"baseline measurement failed: {execution.failure_reason}")

    payload = {
        "command": list(execution.command),
        "metrics": execution.metrics.to_dict(),
        "workspace_path": str(workspace_path),
    }
    metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BaselineMeasurement(
        workspace_path=workspace_path,
        log_path=log_path,
        metrics_path=metrics_path,
        metrics=execution.metrics,
        command=tuple(execution.command),
    )


def run_single_agent_baseline(
    config: RunConfig,
    *,
    rounds: int,
    project_root: Path | None = None,
    agent_runner: AgentRunner | None = None,
    executor: TrainingExecutor | None = None,
    preflight: PreflightChecker | None = None,
    require_clean: bool = True,
) -> dict[str, Any]:
    if config.worker_count != 1:
        raise ValueError("single-agent baseline requires worker_count=1")
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    worktree_manager = WorktreeManager.from_config(config)
    initialized = worktree_manager.initialize_run(config, require_clean=require_clean)
    run_state = initialized.state
    runtime = resolve_runtime(config)
    resolved_executor = executor or build_training_executor(config)
    resolved_preflight = preflight or build_preflight_checker(config)
    resolved_agent_runner = agent_runner or build_agent_runner(config, ActorRole.WORKER, project_root=project_root)

    baseline_workspace = run_state.worker_worktrees[0]
    baseline_dir = initialized.layout.root / "baseline"
    baseline_measurement = measure_baseline(baseline_workspace, resolved_executor, baseline_dir)
    run_state.baseline_val_bpb = baseline_measurement.metrics.val_bpb
    run_state.selected_commit = run_state.baseline_commit
    worktree_manager.store.save_run_state(run_state)
    worktree_manager.sync_all_to_baseline(run_state)

    round_runner = WorkerRoundRunner(
        worktree_manager=worktree_manager,
        agent_runner=resolved_agent_runner,
        coordinator_agent_runner=None,
        coordinator_config=CoordinatorConfig(enabled=False),
        executor=resolved_executor,
        preflight=resolved_preflight,
    )

    round_summaries: list[dict[str, Any]] = []
    for _ in range(rounds):
        round_result = round_runner.run_round(run_state)
        run_state = round_result.run_state
        round_state = round_result.round_state
        round_summaries.append(
            {
                "round_id": round_state.round_id,
                "baseline_before": round_state.baseline_val_bpb,
                "baseline_after": run_state.baseline_val_bpb,
                "selected_actor_id": round_state.selected_result.actor_id if round_state.selected_result else "",
                "selected_status": round_state.selected_result.status.value if round_state.selected_result else "",
                "worker_result": round_state.worker_results[0].to_dict(),
            }
        )

    summary = {
        "status": "ok",
        "run_root": str(config.run_root),
        "program_exp_path": str(worktree_manager.layout.program_experience_path),
        "target_repo_path": str(config.target_repo_path),
        "baseline_source_ref": run_state.baseline_source_ref,
        "initial_baseline_commit": run_state.initial_baseline_commit,
        "runtime_source": runtime.source,
        "runtime_train_command": list(runtime.train_command),
        "baseline_commit": run_state.baseline_commit,
        "baseline_measurement": baseline_measurement.to_dict(),
        "baseline_before": baseline_measurement.metrics.val_bpb,
        "baseline_after": run_state.baseline_val_bpb,
        "rounds_requested": rounds,
        "rounds_completed": len(round_summaries),
        "rounds": round_summaries,
    }
    summary_path = config.run_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
