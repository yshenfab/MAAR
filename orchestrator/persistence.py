from __future__ import annotations

import json
from pathlib import Path

from agent_teams.config import AgentGroupChatConfig
from .layout import RESULTS_TSV_HEADER, RunLayout
from .memory import ProgramExperienceStore
from .state import (
    ActorRole,
    ArchitectureMode,
    ExperimentMetrics,
    ExperimentResult,
    ExperimentStatus,
    GroupChatTurnResult,
    GroupChatTurnStatus,
    RoundState,
    RunState,
    RunStatus,
)


def _stringify_path(path: Path | None) -> str:
    return "" if path is None else str(path)


def _format_metric(value: float | None) -> str:
    return "" if value is None else str(value)


class StateStore:
    """Persistence helpers for run-level and experiment-level artifacts."""

    def __init__(self, layout: RunLayout):
        self.layout = layout

    def initialize_run_files(self, run_state: RunState, *, program_experience_seed_profile: str = "") -> None:
        self.layout.run_json_path.write_text(
            json.dumps(run_state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.layout.results_tsv_path.write_text(RESULTS_TSV_HEADER, encoding="utf-8")
        self.layout.experiments_jsonl_path.write_text("", encoding="utf-8")
        ProgramExperienceStore(
            self.layout.program_experience_path,
            seed_profile=program_experience_seed_profile,
        ).initialize()
        if run_state.architecture_mode == ArchitectureMode.AGENT_GROUPCHAT:
            from agent_teams.memory import GroupChatMemoryStore

            GroupChatMemoryStore(self.layout.groupchat_memory_path).initialize()
            self.layout.groupchat_log_path.write_text("", encoding="utf-8")

    def load_run_state(self) -> RunState:
        payload = json.loads(self.layout.run_json_path.read_text(encoding="utf-8"))
        return RunState(
            run_tag=payload["run_tag"],
            target_repo_path=Path(payload["target_repo_path"]),
            architecture_mode=ArchitectureMode(payload.get("architecture_mode", ArchitectureMode.MAAR.value)),
            agent_groupchat=AgentGroupChatConfig(
                specialist_roles=tuple(
                    payload.get("agent_groupchat", {}).get("specialist_roles", ())
                ),
                turn_order=tuple(payload.get("agent_groupchat", {}).get("turn_order", ())),
                turns_per_round=int(payload.get("agent_groupchat", {}).get("turns_per_round", 0) or 0),
                specialist_model_name=str(
                    payload.get("agent_groupchat", {}).get("specialist_model_name", "")
                ),
                specialist_prompt_profile=str(
                    payload.get("agent_groupchat", {}).get("specialist_prompt_profile", "agent_groupchat_specialist")
                ),
                groupchat_memory_filename=str(
                    payload.get("agent_groupchat", {}).get("groupchat_memory_filename", "groupchat_memory.md")
                ),
                groupchat_log_filename=str(
                    payload.get("agent_groupchat", {}).get("groupchat_log_filename", "groupchat_log.jsonl")
                ),
            )
            if payload.get("agent_groupchat")
            else AgentGroupChatConfig(),
            baseline_source_ref=payload.get("baseline_source_ref", "HEAD"),
            initial_baseline_commit=payload.get("initial_baseline_commit", payload["baseline_commit"]),
            baseline_branch=payload["baseline_branch"],
            baseline_commit=payload["baseline_commit"],
            worker_branches=list(payload["worker_branches"]),
            worker_worktrees=[Path(item) for item in payload["worker_worktrees"]],
            merge_branch=payload["merge_branch"],
            merge_worktree=Path(payload["merge_worktree"]),
            shared_candidate_branch=str(payload.get("shared_candidate_branch", "")),
            shared_candidate_worktree=Path(payload["shared_candidate_worktree"])
            if payload.get("shared_candidate_worktree")
            else None,
            baseline_val_bpb=payload.get("baseline_val_bpb"),
            current_round=payload.get("current_round", 0),
            status=RunStatus(payload.get("status", RunStatus.INITIALIZING.value)),
            selected_commit=payload.get("selected_commit", ""),
        )

    def load_round_state(self, round_id: int) -> RoundState:
        payload = json.loads(self.layout.round_state_path(round_id).read_text(encoding="utf-8"))
        worker_results = [self._deserialize_result(item) for item in payload.get("worker_results", [])]
        positive_results = [self._deserialize_result(item) for item in payload.get("positive_results", [])]
        merge_result = self._deserialize_result(payload["merge_result"]) if payload.get("merge_result") else None
        groupchat_turns = [self._deserialize_groupchat_turn(item) for item in payload.get("groupchat_turns", [])]
        groupchat_result = (
            self._deserialize_result(payload["groupchat_result"]) if payload.get("groupchat_result") else None
        )
        groupchat_engineer_result = (
            self._deserialize_result(payload["groupchat_engineer_result"])
            if payload.get("groupchat_engineer_result")
            else None
        )
        selected_result = self._deserialize_result(payload["selected_result"]) if payload.get("selected_result") else None
        return RoundState(
            round_id=payload["round_id"],
            baseline_commit=payload["baseline_commit"],
            baseline_val_bpb=payload.get("baseline_val_bpb"),
            worker_results=worker_results,
            positive_results=positive_results,
            merge_result=merge_result,
            groupchat_turns=groupchat_turns,
            groupchat_result=groupchat_result,
            groupchat_engineer_result=groupchat_engineer_result,
            selected_result=selected_result,
        )

    def save_run_state(self, run_state: RunState) -> None:
        self.layout.run_json_path.write_text(
            json.dumps(run_state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def initialize_round(self, round_state: RoundState, worker_count: int) -> None:
        self.layout.create_round_dirs(round_state.round_id, worker_count)
        self.save_round_state(round_state)

    def save_round_state(self, round_state: RoundState) -> None:
        round_path = self.layout.round_state_path(round_state.round_id)
        round_path.write_text(
            json.dumps(round_state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def append_experiment_result(self, result: ExperimentResult) -> None:
        tsv_line = "\t".join(
            [
                str(result.round_id),
                result.actor_role.value,
                result.actor_id,
                result.baseline_commit,
                result.candidate_commit,
                _format_metric(result.metrics.val_bpb),
                _format_metric(result.metrics.peak_vram_mb),
                _format_metric(result.metrics.training_seconds),
                _format_metric(result.metrics.total_seconds),
                result.status.value,
                _stringify_path(result.diff_path),
                _stringify_path(result.log_path),
            ]
        )
        with self.layout.results_tsv_path.open("a", encoding="utf-8") as handle:
            handle.write(tsv_line + "\n")

        with self.layout.experiments_jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")

    def _deserialize_result(self, payload: dict[str, object]) -> ExperimentResult:
        metrics_payload = payload.get("metrics") or {}
        if not isinstance(metrics_payload, dict):
            metrics_payload = {}
        return ExperimentResult(
            round_id=int(payload["round_id"]),
            actor_role=ActorRole(str(payload["actor_role"])),
            actor_id=str(payload["actor_id"]),
            baseline_commit=str(payload["baseline_commit"]),
            candidate_commit=str(payload.get("candidate_commit", "")),
            status=ExperimentStatus(str(payload.get("status", ExperimentStatus.PENDING.value))),
            metrics=ExperimentMetrics(
                val_bpb=metrics_payload.get("val_bpb"),  # type: ignore[arg-type]
                peak_vram_mb=metrics_payload.get("peak_vram_mb"),  # type: ignore[arg-type]
                training_seconds=metrics_payload.get("training_seconds"),  # type: ignore[arg-type]
                total_seconds=metrics_payload.get("total_seconds"),  # type: ignore[arg-type]
            ),
            diff_path=Path(payload["diff_path"]) if payload.get("diff_path") else None,
            log_path=Path(payload["log_path"]) if payload.get("log_path") else None,
            proposal_path=Path(payload["proposal_path"]) if payload.get("proposal_path") else None,
            metrics_path=Path(payload["metrics_path"]) if payload.get("metrics_path") else None,
            improved=bool(payload.get("improved", False)),
            failure_reason=str(payload.get("failure_reason", "")),
        )

    def _deserialize_groupchat_turn(self, payload: dict[str, object]) -> GroupChatTurnResult:
        return GroupChatTurnResult(
            turn_index=int(payload["turn_index"]),
            specialist_role=str(payload["specialist_role"]),
            actor_id=str(payload["actor_id"]),
            baseline_commit=str(payload["baseline_commit"]),
            shared_commit_before=str(payload["shared_commit_before"]),
            shared_commit_after=str(payload.get("shared_commit_after", "")),
            status=GroupChatTurnStatus(str(payload.get("status", GroupChatTurnStatus.PROPOSAL_FAILED.value))),
            proposal_path=Path(payload["proposal_path"]) if payload.get("proposal_path") else None,
            diff_path=Path(payload["diff_path"]) if payload.get("diff_path") else None,
            failure_reason=str(payload.get("failure_reason", "")),
        )
