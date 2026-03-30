from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentError, AgentRunner, ProposalRequest
from orchestrator.executor import ExecutionStatus, TrainingExecutor
from orchestrator.git_ops import GitRepo
from orchestrator.memory import ProgramExperienceStore
from orchestrator.patcher import PatchApplyError, SearchReplacePatcher
from orchestrator.preflight import PreflightChecker, PreflightError
from orchestrator.state import (
    ActorRole,
    ExperimentResult,
    ExperimentStatus,
    GroupChatTurnResult,
    GroupChatTurnStatus,
    RoundState,
    RunState,
    RunStatus,
)
from orchestrator.worktree import WorktreeManager

from .config import AgentGroupChatConfig
from .memory import GroupChatMemoryStore


@dataclass(slots=True)
class AgentGroupChatRoundRunResult:
    round_state: RoundState
    run_state: RunState


class AgentGroupChatRoundRunner:
    """Run one round of sequential specialist relay over a shared candidate."""

    def __init__(
        self,
        *,
        worktree_manager: WorktreeManager,
        groupchat_config: AgentGroupChatConfig,
        agent_runner: AgentRunner,
        engineer_agent_runner: AgentRunner | None = None,
        executor: TrainingExecutor,
        patcher: SearchReplacePatcher | None = None,
        preflight: PreflightChecker | None = None,
    ):
        self.worktree_manager = worktree_manager
        self.groupchat_config = groupchat_config
        self.agent_runner = agent_runner
        self.engineer_agent_runner = engineer_agent_runner or agent_runner
        self.executor = executor
        self.patcher = patcher or SearchReplacePatcher()
        self.preflight = preflight or PreflightChecker()
        self.repo = GitRepo(worktree_manager.repo.repo_path)
        self.program_experience = ProgramExperienceStore(worktree_manager.layout.program_experience_path)
        self.groupchat_memory = GroupChatMemoryStore(worktree_manager.layout.groupchat_memory_path)

    def run_round(self, run_state: RunState) -> AgentGroupChatRoundRunResult:
        if run_state.baseline_val_bpb is None:
            raise ValueError("run_state.baseline_val_bpb must be set before running an agent_groupchat round")
        if run_state.shared_candidate_worktree is None:
            raise ValueError("run_state.shared_candidate_worktree must be set for agent_groupchat mode")

        round_id = run_state.current_round + 1
        run_state.current_round = round_id
        run_state.status = RunStatus.RUNNING
        self.worktree_manager.store.save_run_state(run_state)
        self.worktree_manager.sync_all_to_baseline(run_state)

        round_state = RoundState(
            round_id=round_id,
            baseline_commit=run_state.baseline_commit,
            baseline_val_bpb=run_state.baseline_val_bpb,
        )
        self.worktree_manager.store.initialize_round(round_state, worker_count=max(1, len(run_state.worker_worktrees)))

        program_exp_text = self.program_experience.read_text()
        groupchat_memory_text = self._read_groupchat_memory()
        shared_workspace = run_state.shared_candidate_worktree
        current_shared_commit = run_state.baseline_commit

        for turn_index, specialist_role in enumerate(self.groupchat_config.turn_order, start=1):
            turn_result = self._run_turn(
                round_id=round_id,
                turn_index=turn_index,
                specialist_role=specialist_role,
                baseline_commit=run_state.baseline_commit,
                current_shared_commit=current_shared_commit,
                workspace_path=shared_workspace,
                artifact_dir=self.worktree_manager.layout.groupchat_turn_artifact_dir(round_id, turn_index, specialist_role),
                program_exp_text=program_exp_text,
                groupchat_memory_text=groupchat_memory_text,
                accepted_turns=round_state.groupchat_turns,
            )
            round_state.groupchat_turns.append(turn_result)
            self._append_groupchat_log(round_id, turn_result)
            if turn_result.status is GroupChatTurnStatus.ACCEPTED:
                current_shared_commit = turn_result.shared_commit_after
            self.worktree_manager.store.save_round_state(round_state)

        final_result = self._run_final_candidate(
            round_id=round_id,
            baseline_commit=run_state.baseline_commit,
            candidate_commit=current_shared_commit,
            workspace_path=shared_workspace,
            artifact_dir=self.worktree_manager.layout.groupchat_artifact_dir(round_id),
        )
        round_state.groupchat_result = final_result
        engineer_result: ExperimentResult | None = None

        if final_result.status is ExperimentStatus.CRASH:
            engineer_result = self._run_engineer_repair(
                round_id=round_id,
                baseline_commit=run_state.baseline_commit,
                candidate_commit=current_shared_commit,
                workspace_path=shared_workspace,
                artifact_dir=self.worktree_manager.layout.groupchat_engineer_artifact_dir(round_id),
                program_exp_text=program_exp_text,
                groupchat_memory_text=groupchat_memory_text,
                accepted_turns=round_state.groupchat_turns,
                crash_log_path=final_result.log_path,
                crash_failure_reason=final_result.failure_reason,
            )
            round_state.groupchat_engineer_result = engineer_result

        resolved_result = engineer_result or final_result

        if resolved_result.status is ExperimentStatus.PENDING and resolved_result.metrics.val_bpb is not None:
            if resolved_result.metrics.val_bpb < run_state.baseline_val_bpb:
                resolved_result.status = ExperimentStatus.KEEP
                round_state.selected_result = resolved_result
                run_state.status = RunStatus.READY
                run_state.selected_commit = resolved_result.candidate_commit
                run_state.baseline_commit = resolved_result.candidate_commit
                run_state.baseline_val_bpb = resolved_result.metrics.val_bpb
                self.worktree_manager.promote_to_baseline(
                    run_state,
                    resolved_result.candidate_commit,
                    baseline_val_bpb=resolved_result.metrics.val_bpb,
                )
            else:
                resolved_result.status = ExperimentStatus.DISCARD
                round_state.selected_result = None
                run_state.status = RunStatus.READY
                self.worktree_manager.sync_all_to_baseline(run_state)
        else:
            if resolved_result.status is ExperimentStatus.PENDING:
                resolved_result.status = ExperimentStatus.CRASH
            round_state.selected_result = None
            run_state.status = RunStatus.READY
            self.worktree_manager.sync_all_to_baseline(run_state)

        self.program_experience.record_groupchat_round(round_state)
        self.groupchat_memory.record_round(round_state)
        self.worktree_manager.store.append_experiment_result(final_result)
        if engineer_result is not None:
            self.worktree_manager.store.append_experiment_result(engineer_result)
        self.worktree_manager.store.save_round_state(round_state)
        self.worktree_manager.store.save_run_state(run_state)
        return AgentGroupChatRoundRunResult(round_state=round_state, run_state=run_state)

    def _run_turn(
        self,
        *,
        round_id: int,
        turn_index: int,
        specialist_role: str,
        baseline_commit: str,
        current_shared_commit: str,
        workspace_path: Path,
        artifact_dir: Path,
        program_exp_text: str,
        groupchat_memory_text: str,
        accepted_turns: list[GroupChatTurnResult],
    ) -> GroupChatTurnResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = artifact_dir / "proposal.json"
        diff_path = artifact_dir / "candidate.diff"
        actor_id = specialist_role
        request = ProposalRequest(
            actor_role=ActorRole.SPECIALIST,
            actor_id=actor_id,
            round_id=round_id,
            baseline_commit=baseline_commit,
            workspace_path=workspace_path,
            artifact_dir=artifact_dir,
            context=self._build_turn_context(
                specialist_role=specialist_role,
                turn_index=turn_index,
                current_shared_commit=current_shared_commit,
                program_exp_text=program_exp_text,
                groupchat_memory_text=groupchat_memory_text,
                accepted_turns=accepted_turns,
            ),
        )

        try:
            proposal = self.agent_runner.propose(request)
        except AgentError as exc:
            self._write_json(proposal_path, {"error": str(exc)})
            diff_path.write_text("", encoding="utf-8")
            return GroupChatTurnResult(
                turn_index=turn_index,
                specialist_role=specialist_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                shared_commit_before=current_shared_commit,
                status=GroupChatTurnStatus.PROPOSAL_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        self._write_json(proposal_path, proposal.to_dict())

        try:
            patch_result = self.patcher.apply(workspace_path, proposal)
        except PatchApplyError as exc:
            diff_path.write_text("", encoding="utf-8")
            return GroupChatTurnResult(
                turn_index=turn_index,
                specialist_role=specialist_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                shared_commit_before=current_shared_commit,
                status=GroupChatTurnStatus.PATCH_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        diff_path.write_text(patch_result.diff_text, encoding="utf-8")

        try:
            self.preflight.run(workspace_path)
        except PreflightError as exc:
            self.repo.reset_worktree(workspace_path, current_shared_commit)
            return GroupChatTurnResult(
                turn_index=turn_index,
                specialist_role=specialist_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                shared_commit_before=current_shared_commit,
                status=GroupChatTurnStatus.PREFLIGHT_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        next_shared_commit = self.repo.commit_paths(
            workspace_path,
            message=f"{specialist_role} round {round_id} turn {turn_index}",
            paths=(self.patcher.editable_file,),
        )
        return GroupChatTurnResult(
            turn_index=turn_index,
            specialist_role=specialist_role,
            actor_id=actor_id,
            baseline_commit=baseline_commit,
            shared_commit_before=current_shared_commit,
            shared_commit_after=next_shared_commit,
            status=GroupChatTurnStatus.ACCEPTED,
            proposal_path=proposal_path,
            diff_path=diff_path,
        )

    def _run_final_candidate(
        self,
        *,
        round_id: int,
        baseline_commit: str,
        candidate_commit: str,
        workspace_path: Path,
        artifact_dir: Path,
    ) -> ExperimentResult:
        log_path = artifact_dir / "groupchat_final.log"
        metrics_path = artifact_dir / "groupchat_metrics.json"
        execution_result = self.executor.run(workspace_path, log_path)
        self._write_json(metrics_path, execution_result.metrics.to_dict())

        if execution_result.status is not ExecutionStatus.SUCCESS:
            return ExperimentResult(
                round_id=round_id,
                actor_role=ActorRole.GROUPCHAT,
                actor_id="groupchat",
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                status=ExperimentStatus.CRASH,
                metrics=execution_result.metrics,
                log_path=log_path,
                metrics_path=metrics_path,
                failure_reason=execution_result.failure_reason,
            )

        return ExperimentResult(
            round_id=round_id,
            actor_role=ActorRole.GROUPCHAT,
            actor_id="groupchat",
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            status=ExperimentStatus.PENDING,
            metrics=execution_result.metrics,
            log_path=log_path,
            metrics_path=metrics_path,
        )

    def _run_engineer_repair(
        self,
        *,
        round_id: int,
        baseline_commit: str,
        candidate_commit: str,
        workspace_path: Path,
        artifact_dir: Path,
        program_exp_text: str,
        groupchat_memory_text: str,
        accepted_turns: list[GroupChatTurnResult],
        crash_log_path: Path | None,
        crash_failure_reason: str,
    ) -> ExperimentResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = artifact_dir / "proposal.json"
        diff_path = artifact_dir / "candidate.diff"
        actor_id = "engineer"
        request = ProposalRequest(
            actor_role=ActorRole.ENGINEER,
            actor_id=actor_id,
            round_id=round_id,
            baseline_commit=baseline_commit,
            workspace_path=workspace_path,
            artifact_dir=artifact_dir,
            context=self._build_engineer_context(
                candidate_commit=candidate_commit,
                program_exp_text=program_exp_text,
                groupchat_memory_text=groupchat_memory_text,
                accepted_turns=accepted_turns,
                crash_log_path=crash_log_path,
                crash_failure_reason=crash_failure_reason,
            ),
        )

        try:
            proposal = self.engineer_agent_runner.propose(request)
        except AgentError as exc:
            self._write_json(proposal_path, {"error": str(exc)})
            diff_path.write_text("", encoding="utf-8")
            return ExperimentResult(
                round_id=round_id,
                actor_role=ActorRole.ENGINEER,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                status=ExperimentStatus.PROPOSAL_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        self._write_json(proposal_path, proposal.to_dict())

        try:
            patch_result = self.patcher.apply(workspace_path, proposal)
        except PatchApplyError as exc:
            self.repo.reset_worktree(workspace_path, candidate_commit)
            diff_path.write_text("", encoding="utf-8")
            return ExperimentResult(
                round_id=round_id,
                actor_role=ActorRole.ENGINEER,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                status=ExperimentStatus.CRASH,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        diff_path.write_text(patch_result.diff_text, encoding="utf-8")

        try:
            self.preflight.run(workspace_path)
        except PreflightError as exc:
            self.repo.reset_worktree(workspace_path, candidate_commit)
            return ExperimentResult(
                round_id=round_id,
                actor_role=ActorRole.ENGINEER,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                status=ExperimentStatus.PREFLIGHT_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                failure_reason=str(exc),
            )

        repaired_commit = self.repo.commit_paths(
            workspace_path,
            message=f"engineer repair round {round_id}",
            paths=(self.patcher.editable_file,),
        )
        log_path = artifact_dir / "engineer_final.log"
        metrics_path = artifact_dir / "engineer_metrics.json"
        execution_result = self.executor.run(workspace_path, log_path)
        self._write_json(metrics_path, execution_result.metrics.to_dict())

        if execution_result.status is not ExecutionStatus.SUCCESS:
            return ExperimentResult(
                round_id=round_id,
                actor_role=ActorRole.ENGINEER,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                candidate_commit=repaired_commit,
                status=ExperimentStatus.CRASH,
                metrics=execution_result.metrics,
                proposal_path=proposal_path,
                diff_path=diff_path,
                log_path=log_path,
                metrics_path=metrics_path,
                failure_reason=execution_result.failure_reason,
            )

        return ExperimentResult(
            round_id=round_id,
            actor_role=ActorRole.ENGINEER,
            actor_id=actor_id,
            baseline_commit=baseline_commit,
            candidate_commit=repaired_commit,
            status=ExperimentStatus.PENDING,
            metrics=execution_result.metrics,
            proposal_path=proposal_path,
            diff_path=diff_path,
            log_path=log_path,
            metrics_path=metrics_path,
        )

    def _build_turn_context(
        self,
        *,
        specialist_role: str,
        turn_index: int,
        current_shared_commit: str,
        program_exp_text: str,
        groupchat_memory_text: str,
        accepted_turns: list[GroupChatTurnResult],
    ) -> dict[str, Any]:
        return {
            "specialist_role": specialist_role,
            "turn_index": turn_index,
            "current_shared_commit": current_shared_commit,
            "program_exp_markdown": program_exp_text,
            "groupchat_memory_markdown": groupchat_memory_text,
            "accepted_turns": self._serialize_accepted_turns(accepted_turns),
        }

    def _build_engineer_context(
        self,
        *,
        candidate_commit: str,
        program_exp_text: str,
        groupchat_memory_text: str,
        accepted_turns: list[GroupChatTurnResult],
        crash_log_path: Path | None,
        crash_failure_reason: str,
    ) -> dict[str, Any]:
        return {
            "candidate_commit": candidate_commit,
            "program_exp_markdown": program_exp_text,
            "groupchat_memory_markdown": groupchat_memory_text,
            "accepted_turns": self._serialize_accepted_turns(accepted_turns),
            "crash_failure_reason": crash_failure_reason,
            "crash_log_tail": self._read_text_tail(crash_log_path),
        }

    def _append_groupchat_log(self, round_id: int, turn_result: GroupChatTurnResult) -> None:
        path = self.worktree_manager.layout.groupchat_log_path
        payload = {
            "round_id": round_id,
            **turn_result.to_dict(),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _read_groupchat_memory(self) -> str:
        return self.groupchat_memory.read_text()

    def _serialize_accepted_turns(self, turns: list[GroupChatTurnResult]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for turn in turns:
            if turn.status is not GroupChatTurnStatus.ACCEPTED:
                continue
            payload = self._load_payload(turn.proposal_path)
            serialized.append(
                {
                    "turn_index": turn.turn_index,
                    "specialist_role": turn.specialist_role,
                    "shared_commit_after": turn.shared_commit_after,
                    "idea_summary": self._normalize_context_text(str(payload.get("idea_summary", ""))) if payload else "",
                    "motivation": self._normalize_context_text(str(payload.get("motivation", ""))) if payload else "",
                }
            )
        return serialized

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _read_text_tail(self, path: Path | None, limit: int = 4000) -> str:
        if path is None or not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:]

    def _load_payload(self, proposal_path: Path | None) -> dict[str, Any] | None:
        if proposal_path is None or not proposal_path.exists():
            return None
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _normalize_context_text(self, text: str, limit: int = 240) -> str:
        return " ".join(text.split())[:limit].strip()
