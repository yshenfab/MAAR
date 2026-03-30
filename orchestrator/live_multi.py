from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents import AgentRunner, build_agent_runner
from .config import CoordinatorConfig, RunConfig
from .executor import TrainingExecutor
from .live_baseline import BaselineMeasurement, measure_baseline
from .preflight import PreflightChecker
from .round_runner import WorkerRoundRunner
from .runtime import RuntimeResolution, build_preflight_checker, build_training_executor, resolve_runtime
from .state import ActorRole, ExperimentMetrics, RunState, RunStatus
from .worktree import WorktreeManager


def run_multi_agent_experiment(
    config: RunConfig,
    *,
    rounds: int,
    coordinator_config: CoordinatorConfig | None = None,
    project_root: Path | None = None,
    agent_runner: AgentRunner | None = None,
    coordinator_agent_runner: AgentRunner | None = None,
    executor: TrainingExecutor | None = None,
    preflight: PreflightChecker | None = None,
    require_clean: bool = True,
) -> dict[str, Any]:
    if config.worker_count < 2:
        raise ValueError("multi-agent experiment requires worker_count >= 2")
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    resolved_coordinator_config = coordinator_config or CoordinatorConfig()
    worktree_manager = WorktreeManager.from_config(config)
    initialized = worktree_manager.initialize_run(config, require_clean=require_clean)
    run_state = initialized.state
    runtime = resolve_runtime(config)
    resolved_executor = executor or build_training_executor(config)
    resolved_preflight = preflight or build_preflight_checker(config)
    resolved_agent_runner = agent_runner or build_agent_runner(config, ActorRole.WORKER, project_root=project_root)
    resolved_coordinator_runner: AgentRunner | None = None
    if resolved_coordinator_config.enabled:
        resolved_coordinator_runner = coordinator_agent_runner or build_agent_runner(
            config,
            ActorRole.COORDINATOR,
            project_root=project_root,
        )

    baseline_dir = initialized.layout.root / "baseline"
    baseline_measurement = measure_baseline(run_state.worker_worktrees[0], resolved_executor, baseline_dir)
    run_state.baseline_val_bpb = baseline_measurement.metrics.val_bpb
    run_state.selected_commit = run_state.baseline_commit
    worktree_manager.store.save_run_state(run_state)
    worktree_manager.sync_all_to_baseline(run_state)

    round_runner = WorkerRoundRunner(
        worktree_manager=worktree_manager,
        agent_runner=resolved_agent_runner,
        coordinator_agent_runner=resolved_coordinator_runner,
        coordinator_config=resolved_coordinator_config,
        executor=resolved_executor,
        preflight=resolved_preflight,
    )

    run_state = _run_rounds(run_state, rounds_to_run=rounds, round_runner=round_runner)
    return _write_summary(
        config,
        run_state=run_state,
        worktree_manager=worktree_manager,
        runtime=runtime,
        coordinator_config=resolved_coordinator_config,
        baseline_measurement=baseline_measurement,
        rounds_requested=rounds,
        resumed=False,
    )


def resume_multi_agent_experiment(
    config: RunConfig,
    *,
    total_rounds: int,
    coordinator_config: CoordinatorConfig | None = None,
    project_root: Path | None = None,
    agent_runner: AgentRunner | None = None,
    coordinator_agent_runner: AgentRunner | None = None,
    executor: TrainingExecutor | None = None,
    preflight: PreflightChecker | None = None,
) -> dict[str, Any]:
    if config.worker_count < 2:
        raise ValueError("multi-agent experiment requires worker_count >= 2")
    if total_rounds < 1:
        raise ValueError("total_rounds must be >= 1")

    resolved_coordinator_config = coordinator_config or CoordinatorConfig()
    worktree_manager = WorktreeManager.from_config(config)
    if not worktree_manager.layout.run_json_path.exists():
        raise FileNotFoundError(f"run state not found for resume: {worktree_manager.layout.run_json_path}")

    runtime = resolve_runtime(config)
    resolved_executor = executor or build_training_executor(config)
    resolved_preflight = preflight or build_preflight_checker(config)
    resolved_agent_runner = agent_runner or build_agent_runner(config, ActorRole.WORKER, project_root=project_root)
    resolved_coordinator_runner: AgentRunner | None = None
    if resolved_coordinator_config.enabled:
        resolved_coordinator_runner = coordinator_agent_runner or build_agent_runner(
            config,
            ActorRole.COORDINATOR,
            project_root=project_root,
        )

    run_state = worktree_manager.store.load_run_state()
    _validate_resume_target(config, run_state)
    run_state = _prepare_run_state_for_resume(run_state, worktree_manager)
    baseline_measurement = _load_baseline_measurement(config, worktree_manager)
    if total_rounds < run_state.current_round:
        raise ValueError(
            f"total_rounds={total_rounds} is smaller than the resumable progress {run_state.current_round}"
        )

    round_runner = WorkerRoundRunner(
        worktree_manager=worktree_manager,
        agent_runner=resolved_agent_runner,
        coordinator_agent_runner=resolved_coordinator_runner,
        coordinator_config=resolved_coordinator_config,
        executor=resolved_executor,
        preflight=resolved_preflight,
    )

    remaining_rounds = max(0, total_rounds - run_state.current_round)
    run_state = _run_rounds(run_state, rounds_to_run=remaining_rounds, round_runner=round_runner)
    return _write_summary(
        config,
        run_state=run_state,
        worktree_manager=worktree_manager,
        runtime=runtime,
        coordinator_config=resolved_coordinator_config,
        baseline_measurement=baseline_measurement,
        rounds_requested=total_rounds,
        resumed=True,
    )


def _run_rounds(run_state: RunState, *, rounds_to_run: int, round_runner: WorkerRoundRunner) -> RunState:
    current_state = run_state
    for _ in range(rounds_to_run):
        round_result = round_runner.run_round(current_state)
        current_state = round_result.run_state
    return current_state


def _write_summary(
    config: RunConfig,
    *,
    run_state: RunState,
    worktree_manager: WorktreeManager,
    runtime: RuntimeResolution,
    coordinator_config: CoordinatorConfig,
    baseline_measurement: BaselineMeasurement,
    rounds_requested: int,
    resumed: bool,
) -> dict[str, Any]:
    round_summaries = _collect_round_summaries(worktree_manager, run_state.current_round)
    summary = {
        "status": "ok",
        "run_root": str(config.run_root),
        "program_exp_path": str(worktree_manager.layout.program_experience_path),
        "target_repo_path": str(config.target_repo_path),
        "baseline_source_ref": run_state.baseline_source_ref,
        "initial_baseline_commit": run_state.initial_baseline_commit,
        "runtime_source": runtime.source,
        "runtime_train_command": list(runtime.train_command),
        "worker_count": config.worker_count,
        "coordinator_enabled": coordinator_config.enabled,
        "baseline_commit": run_state.baseline_commit,
        "baseline_measurement": baseline_measurement.to_dict(),
        "baseline_before": baseline_measurement.metrics.val_bpb,
        "baseline_after": run_state.baseline_val_bpb,
        "rounds_requested": rounds_requested,
        "rounds_completed": len(round_summaries),
        "rounds": round_summaries,
        "resumed": resumed,
    }
    summary_path = config.run_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _collect_round_summaries(worktree_manager: WorktreeManager, completed_rounds: int) -> list[dict[str, Any]]:
    round_summaries: list[dict[str, Any]] = []
    for round_id in range(1, completed_rounds + 1):
        round_state = worktree_manager.store.load_round_state(round_id)
        if round_state.selected_result is not None:
            baseline_after = round_state.selected_result.metrics.val_bpb
            selected_actor_id = round_state.selected_result.actor_id
            selected_status = round_state.selected_result.status.value
        else:
            baseline_after = round_state.baseline_val_bpb
            selected_actor_id = ""
            selected_status = ""
        round_summaries.append(
            {
                "round_id": round_state.round_id,
                "baseline_before": round_state.baseline_val_bpb,
                "baseline_after": baseline_after,
                "selected_actor_id": selected_actor_id,
                "selected_status": selected_status,
                "merge_result": round_state.merge_result.to_dict() if round_state.merge_result else None,
                "worker_results": [result.to_dict() for result in round_state.worker_results],
            }
        )
    return round_summaries


def _validate_resume_target(config: RunConfig, run_state: RunState) -> None:
    if Path(run_state.target_repo_path).resolve() != Path(config.target_repo_path).resolve():
        raise ValueError("resume target_repo_path does not match the stored run state")
    if len(run_state.worker_worktrees) != config.worker_count:
        raise ValueError("resume worker_count does not match the stored run state")


def _prepare_run_state_for_resume(run_state: RunState, worktree_manager: WorktreeManager) -> RunState:
    if run_state.status is RunStatus.RUNNING and run_state.current_round > 0:
        run_state.current_round -= 1
    run_state.status = RunStatus.READY
    worktree_manager.sync_all_to_baseline(run_state)
    return run_state


def _load_baseline_measurement(config: RunConfig, worktree_manager: WorktreeManager) -> BaselineMeasurement:
    metrics_path = config.run_root / "baseline" / "baseline_metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics = payload["metrics"]

    return BaselineMeasurement(
        workspace_path=Path(payload["workspace_path"]),
        log_path=config.run_root / "baseline" / "baseline.log",
        metrics_path=metrics_path,
        metrics=ExperimentMetrics(
            val_bpb=metrics.get("val_bpb"),
            peak_vram_mb=metrics.get("peak_vram_mb"),
            training_seconds=metrics.get("training_seconds"),
            total_seconds=metrics.get("total_seconds"),
        ),
        command=tuple(payload.get("command", [])),
    )
