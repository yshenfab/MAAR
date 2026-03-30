from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import AgentError, AgentRunner, ProposalRequest
from .config import CoordinatorConfig
from .executor import ExecutionStatus, TrainingExecutor
from .git_ops import GitRepo
from .memory import ProgramExperienceStore, classify_idea_family
from .patcher import PatchApplyError, SearchReplacePatcher
from .preflight import PreflightChecker, PreflightError
from .state import (
    ActorRole,
    ExperimentResult,
    ExperimentStatus,
    RoundState,
    RunState,
    RunStatus,
)
from .worktree import WorktreeManager


@dataclass(slots=True)
class RoundRunResult:
    round_state: RoundState
    run_state: RunState


class WorkerRoundRunner:
    """Run one round of worker proposals, validation, and optional coordinator merge."""

    def __init__(
        self,
        worktree_manager: WorktreeManager,
        agent_runner: AgentRunner,
        executor: TrainingExecutor,
        coordinator_agent_runner: AgentRunner | None = None,
        coordinator_config: CoordinatorConfig | None = None,
        patcher: SearchReplacePatcher | None = None,
        preflight: PreflightChecker | None = None,
    ):
        self.worktree_manager = worktree_manager
        self.agent_runner = agent_runner
        self.executor = executor
        self.coordinator_agent_runner = coordinator_agent_runner
        self.coordinator_config = coordinator_config or CoordinatorConfig()
        self.patcher = patcher or SearchReplacePatcher()
        self.preflight = preflight or PreflightChecker()
        self.repo = GitRepo(worktree_manager.repo.repo_path)
        self.program_experience = ProgramExperienceStore(worktree_manager.layout.program_experience_path)

    def run_round(self, run_state: RunState) -> RoundRunResult:
        if run_state.baseline_val_bpb is None:
            raise ValueError("run_state.baseline_val_bpb must be set before running a worker round")

        round_id = run_state.current_round + 1
        run_state.current_round = round_id
        run_state.status = RunStatus.RUNNING
        self.worktree_manager.store.save_run_state(run_state)
        self.worktree_manager.sync_all_to_baseline(run_state)
        program_experience_text = self.program_experience.read_text()

        round_state = RoundState(
            round_id=round_id,
            baseline_commit=run_state.baseline_commit,
            baseline_val_bpb=run_state.baseline_val_bpb,
        )
        worker_count = len(run_state.worker_worktrees)
        self.worktree_manager.store.initialize_round(round_state, worker_count=worker_count)

        for worker_id, workspace_path in enumerate(run_state.worker_worktrees, start=1):
            result = self._run_candidate(
                round_id=round_id,
                baseline_commit=run_state.baseline_commit,
                actor_role=ActorRole.WORKER,
                actor_id=f"worker-{worker_id}",
                workspace_path=workspace_path,
                artifact_dir=self.worktree_manager.layout.worker_artifact_dir(round_id, worker_id),
                proposal_filename="proposal.json",
                diff_filename="candidate.diff",
                log_filename="run.log",
                metrics_filename="metrics.json",
                request_context=self._with_program_experience({}, program_experience_text),
            )
            round_state.worker_results.append(result)

        self._adjudicate(round_state, run_state, program_experience_text)
        self.program_experience.record_round(round_state)

        for result in round_state.worker_results:
            self.worktree_manager.store.append_experiment_result(result)
        if round_state.merge_result is not None:
            self.worktree_manager.store.append_experiment_result(round_state.merge_result)
        self.worktree_manager.store.save_round_state(round_state)
        self.worktree_manager.store.save_run_state(run_state)
        return RoundRunResult(round_state=round_state, run_state=run_state)

    def _run_candidate(
        self,
        round_id: int,
        baseline_commit: str,
        actor_role: ActorRole,
        actor_id: str,
        workspace_path: Path,
        artifact_dir: Path,
        proposal_filename: str,
        diff_filename: str,
        log_filename: str,
        metrics_filename: str,
        request_context: dict[str, Any],
    ) -> ExperimentResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = artifact_dir / proposal_filename
        diff_path = artifact_dir / diff_filename
        log_path = artifact_dir / log_filename
        metrics_path = artifact_dir / metrics_filename

        request = ProposalRequest(
            actor_role=actor_role,
            actor_id=actor_id,
            round_id=round_id,
            baseline_commit=baseline_commit,
            workspace_path=workspace_path,
            artifact_dir=artifact_dir,
            context=request_context,
        )

        runner = self.agent_runner if actor_role is ActorRole.WORKER else self.coordinator_agent_runner
        if runner is None:
            raise RuntimeError(f"no agent runner configured for {actor_role.value}")

        try:
            proposal = runner.propose(request)
        except AgentError as exc:
            result = ExperimentResult(
                round_id=round_id,
                actor_role=actor_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                status=ExperimentStatus.PROPOSAL_FAILED,
                proposal_path=proposal_path,
                metrics_path=metrics_path,
                failure_reason=str(exc),
            )
            self._write_json(proposal_path, {"error": str(exc)})
            self._write_json(metrics_path, result.metrics.to_dict())
            return result

        self._write_json(proposal_path, proposal.to_dict())

        try:
            patch_result = self.patcher.apply(workspace_path, proposal)
        except PatchApplyError as exc:
            result = ExperimentResult(
                round_id=round_id,
                actor_role=actor_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                status=ExperimentStatus.PROPOSAL_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                metrics_path=metrics_path,
                failure_reason=str(exc),
            )
            diff_path.write_text("", encoding="utf-8")
            self._write_json(metrics_path, result.metrics.to_dict())
            return result

        diff_path.write_text(patch_result.diff_text, encoding="utf-8")

        try:
            self.preflight.run(workspace_path)
        except PreflightError as exc:
            result = ExperimentResult(
                round_id=round_id,
                actor_role=actor_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                status=ExperimentStatus.PREFLIGHT_FAILED,
                proposal_path=proposal_path,
                diff_path=diff_path,
                metrics_path=metrics_path,
                failure_reason=str(exc),
            )
            self._write_json(metrics_path, result.metrics.to_dict())
            return result

        candidate_commit = self.repo.commit_paths(
            workspace_path,
            message=f"{actor_id} round {round_id}",
            paths=(self.patcher.editable_file,),
        )
        execution_result = self.executor.run(workspace_path, log_path)
        self._write_json(metrics_path, execution_result.metrics.to_dict())

        if execution_result.status is not ExecutionStatus.SUCCESS:
            return ExperimentResult(
                round_id=round_id,
                actor_role=actor_role,
                actor_id=actor_id,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                status=ExperimentStatus.CRASH,
                metrics=execution_result.metrics,
                diff_path=diff_path,
                log_path=log_path,
                proposal_path=proposal_path,
                metrics_path=metrics_path,
                failure_reason=execution_result.failure_reason,
            )

        return ExperimentResult(
            round_id=round_id,
            actor_role=actor_role,
            actor_id=actor_id,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            status=ExperimentStatus.PENDING,
            metrics=execution_result.metrics,
            diff_path=diff_path,
            log_path=log_path,
            proposal_path=proposal_path,
            metrics_path=metrics_path,
        )

    def _adjudicate(self, round_state: RoundState, run_state: RunState, program_experience_text: str) -> None:
        successful = [result for result in round_state.worker_results if result.status is ExperimentStatus.PENDING]
        positives = [
            result
            for result in successful
            if result.metrics.val_bpb is not None and result.metrics.val_bpb < run_state.baseline_val_bpb
        ]
        positives.sort(key=lambda result: (result.metrics.val_bpb, result.actor_id))
        round_state.positive_results = positives

        for result in successful:
            result.improved = result in positives
            result.status = ExperimentStatus.DISCARD

        best_worker = positives[0] if positives else None
        selected: ExperimentResult | None = best_worker

        if best_worker is not None and self._should_run_coordinator(positives):
            round_state.merge_result = self._run_coordinator(round_state, run_state, positives, program_experience_text)
            merge_result = round_state.merge_result
            if merge_result.status is ExperimentStatus.PENDING:
                if merge_result.metrics.val_bpb is not None and merge_result.metrics.val_bpb < best_worker.metrics.val_bpb:
                    merge_result.status = ExperimentStatus.KEEP
                    selected = merge_result
                else:
                    merge_result.status = ExperimentStatus.DISCARD

        if selected is None:
            round_state.selected_result = None
            run_state.status = RunStatus.READY
            self.worktree_manager.sync_all_to_baseline(run_state)
            return

        if selected is best_worker:
            best_worker.status = ExperimentStatus.KEEP

        round_state.selected_result = selected
        run_state.status = RunStatus.READY
        run_state.selected_commit = selected.candidate_commit
        run_state.baseline_commit = selected.candidate_commit
        run_state.baseline_val_bpb = selected.metrics.val_bpb
        self.worktree_manager.promote_to_baseline(
            run_state,
            selected.candidate_commit,
            baseline_val_bpb=selected.metrics.val_bpb,
        )

    def _run_coordinator(
        self,
        round_state: RoundState,
        run_state: RunState,
        positives: list[ExperimentResult],
        program_experience_text: str,
    ) -> ExperimentResult:
        round_id = round_state.round_id
        merge_workspace = run_state.merge_worktree
        self.repo.reset_worktree(merge_workspace, run_state.baseline_commit)

        top_candidates = positives[: self.coordinator_config.top_k]
        artifact_dir = self.worktree_manager.layout.coordinator_artifact_dir(round_id)
        coordinator_input_path = artifact_dir / "coordinator_input.json"

        coordinator_input = {
            "baseline_commit": run_state.baseline_commit,
            "baseline_val_bpb": run_state.baseline_val_bpb,
            "source_candidates": [
                self._candidate_payload(result, baseline_val_bpb=run_state.baseline_val_bpb)
                for result in top_candidates
            ],
        }
        self._write_json(coordinator_input_path, coordinator_input)

        return self._run_candidate(
            round_id=round_id,
            baseline_commit=run_state.baseline_commit,
            actor_role=ActorRole.COORDINATOR,
            actor_id="coordinator",
            workspace_path=merge_workspace,
            artifact_dir=artifact_dir,
            proposal_filename="merge_proposal.json",
            diff_filename="merged.diff",
            log_filename="merge.log",
            metrics_filename="metrics.json",
            request_context=self._with_program_experience(coordinator_input, program_experience_text),
        )

    def _should_run_coordinator(self, positives: list[ExperimentResult]) -> bool:
        if not self.coordinator_config.enabled:
            return False
        if self.coordinator_agent_runner is None:
            return False
        if len(positives) < self.coordinator_config.trigger_min_improvements:
            return False
        return self._has_real_merge_value(positives[: self.coordinator_config.top_k])

    def _has_real_merge_value(self, candidates: list[ExperimentResult]) -> bool:
        if len(candidates) < 2:
            return False
        proposal_keys = {
            (
                proposal.get("search_block", "").strip(),
                proposal.get("replace_block", "").strip(),
            )
            for proposal in (self._load_proposal_payload(candidate) for candidate in candidates)
            if proposal is not None
        }
        if len(proposal_keys) < 2:
            return False
        return True

    def _candidate_payload(self, result: ExperimentResult, baseline_val_bpb: float | None = None) -> dict[str, Any]:
        payload = result.to_dict()
        if result.proposal_path is not None and result.proposal_path.exists():
            proposal = json.loads(result.proposal_path.read_text(encoding="utf-8"))
            payload["proposal"] = proposal
            idea_summary = str(proposal.get("idea_summary", "")).strip()
            payload["idea_family"] = classify_idea_family(idea_summary)
        if result.metrics.val_bpb is not None:
            payload["candidate_val_bpb"] = result.metrics.val_bpb
            if baseline_val_bpb is not None:
                payload["improvement_delta"] = baseline_val_bpb - result.metrics.val_bpb
        if result.diff_path is not None and result.diff_path.exists():
            payload["diff_text"] = result.diff_path.read_text(encoding="utf-8")
        return payload

    def _load_proposal_payload(self, result: ExperimentResult) -> dict[str, Any] | None:
        if result.proposal_path is None or not result.proposal_path.exists():
            return None
        try:
            payload = json.loads(result.proposal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _with_program_experience(self, payload: dict[str, Any], program_experience_text: str) -> dict[str, Any]:
        merged = dict(payload)
        merged["program_exp_markdown"] = program_experience_text
        return merged
