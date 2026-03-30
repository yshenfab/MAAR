from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentRunner, build_agent_runner
from orchestrator.config import RunConfig
from orchestrator.executor import TrainingExecutor
from orchestrator.live_baseline import BaselineMeasurement, measure_baseline
from orchestrator.preflight import PreflightChecker
from orchestrator.runtime import RuntimeResolution, build_preflight_checker, build_training_executor, resolve_runtime
from orchestrator.state import ActorRole, ArchitectureMode, RunState
from orchestrator.worktree import WorktreeManager

from .runner import AgentGroupChatRoundRunner


def run_agent_groupchat_experiment(
    config: RunConfig,
    *,
    rounds: int,
    project_root: Path | None = None,
    agent_runner: AgentRunner | None = None,
    executor: TrainingExecutor | None = None,
    preflight: PreflightChecker | None = None,
    require_clean: bool = True,
) -> dict[str, Any]:
    if config.architecture_mode is not ArchitectureMode.AGENT_GROUPCHAT:
        raise ValueError("agent_groupchat experiment requires architecture_mode=agent_groupchat")
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    worktree_manager = WorktreeManager.from_config(config)
    initialized = worktree_manager.initialize_run(config, require_clean=require_clean)
    run_state = initialized.state
    runtime = resolve_runtime(config)
    resolved_executor = executor or build_training_executor(config)
    resolved_preflight = preflight or build_preflight_checker(config)
    resolved_agent_runner = agent_runner or build_agent_runner(config, ActorRole.SPECIALIST, project_root=project_root)
    resolved_engineer_runner = agent_runner or build_agent_runner(config, ActorRole.ENGINEER, project_root=project_root)

    baseline_workspace = run_state.worker_worktrees[0]
    baseline_dir = initialized.layout.root / "baseline"
    baseline_measurement = measure_baseline(baseline_workspace, resolved_executor, baseline_dir)
    run_state.baseline_val_bpb = baseline_measurement.metrics.val_bpb
    run_state.selected_commit = run_state.baseline_commit
    worktree_manager.store.save_run_state(run_state)
    worktree_manager.sync_all_to_baseline(run_state)

    round_runner = AgentGroupChatRoundRunner(
        worktree_manager=worktree_manager,
        groupchat_config=config.agent_groupchat,
        agent_runner=resolved_agent_runner,
        engineer_agent_runner=resolved_engineer_runner,
        executor=resolved_executor,
        preflight=resolved_preflight,
    )

    current_state = run_state
    for _ in range(rounds):
        round_result = round_runner.run_round(current_state)
        current_state = round_result.run_state

    return _write_summary(
        config,
        run_state=current_state,
        worktree_manager=worktree_manager,
        runtime=runtime,
        baseline_measurement=baseline_measurement,
        rounds_requested=rounds,
    )


def _write_summary(
    config: RunConfig,
    *,
    run_state: RunState,
    worktree_manager: WorktreeManager,
    runtime: RuntimeResolution,
    baseline_measurement: BaselineMeasurement,
    rounds_requested: int,
) -> dict[str, Any]:
    round_summaries = _collect_round_summaries(worktree_manager, run_state.current_round)
    summary = {
        "status": "ok",
        "architecture_mode": config.architecture_mode.value,
        "run_root": str(config.run_root),
        "program_exp_path": str(worktree_manager.layout.program_experience_path),
        "groupchat_memory_path": str(worktree_manager.layout.groupchat_memory_path),
        "groupchat_log_path": str(worktree_manager.layout.groupchat_log_path),
        "target_repo_path": str(config.target_repo_path),
        "baseline_source_ref": run_state.baseline_source_ref,
        "initial_baseline_commit": run_state.initial_baseline_commit,
        "runtime_source": runtime.source,
        "runtime_train_command": list(runtime.train_command),
        "specialist_count": config.specialist_count,
        "specialist_roles": list(config.agent_groupchat.specialist_roles),
        "engineer_model_name": config.agent_groupchat.engineer_model_name,
        "turn_order": list(config.agent_groupchat.turn_order),
        "turns_per_round": config.agent_groupchat.turns_per_round,
        "train_jobs_executed": sum(item["train_jobs_executed"] for item in round_summaries),
        "baseline_train_jobs": 1,
        "baseline_commit": run_state.baseline_commit,
        "baseline_measurement": baseline_measurement.to_dict(),
        "baseline_before": baseline_measurement.metrics.val_bpb,
        "baseline_after": run_state.baseline_val_bpb,
        "rounds_requested": rounds_requested,
        "rounds_completed": len(round_summaries),
        "rounds": round_summaries,
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
        accepted_turn_count = sum(1 for turn in round_state.groupchat_turns if turn.status.value == "accepted")
        rejected_turn_count = len(round_state.groupchat_turns) - accepted_turn_count
        round_summaries.append(
            {
                "round_id": round_state.round_id,
                "baseline_before": round_state.baseline_val_bpb,
                "baseline_after": baseline_after,
                "selected_actor_id": selected_actor_id,
                "selected_status": selected_status,
                "accepted_turn_count": accepted_turn_count,
                "rejected_turn_count": rejected_turn_count,
                "train_jobs_executed": 1 + int(round_state.groupchat_engineer_result is not None),
                "groupchat_result": round_state.groupchat_result.to_dict() if round_state.groupchat_result else None,
                "groupchat_engineer_result": (
                    round_state.groupchat_engineer_result.to_dict() if round_state.groupchat_engineer_result else None
                ),
                "groupchat_turns": [turn.to_dict() for turn in round_state.groupchat_turns],
            }
        )
    return round_summaries
