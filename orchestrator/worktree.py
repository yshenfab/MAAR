from __future__ import annotations

from dataclasses import dataclass

from .config import RunConfig
from .git_ops import GitRepo
from .layout import RunLayout
from .persistence import StateStore
from .state import ArchitectureMode, RunState, RunStatus


@dataclass(slots=True)
class InitializedRun:
    config: RunConfig
    layout: RunLayout
    state: RunState


class WorktreeManager:
    """Create and synchronize the baseline/worker/merge worktree topology."""

    def __init__(self, repo: GitRepo, layout: RunLayout, store: StateStore):
        self.repo = repo
        self.layout = layout
        self.store = store

    @classmethod
    def from_config(cls, config: RunConfig) -> "WorktreeManager":
        layout = RunLayout.from_config(config)
        return cls(GitRepo(config.target_repo_path), layout, StateStore(layout))

    def initialize_run(self, config: RunConfig, require_clean: bool = True) -> InitializedRun:
        self.repo.ensure_repo()
        if require_clean:
            self.repo.require_clean()
        self._ensure_fresh_run_root()
        self._ensure_branch_names_available(config)

        self.layout.create(config.worker_count)
        baseline_source_ref = config.baseline_source_ref or "HEAD"
        baseline_commit = self.repo.resolve_commit(baseline_source_ref)
        self.repo.create_branch(config.baseline_branch_name, baseline_commit)

        worker_branches: list[str] = []
        worker_worktrees = []
        for worker_id in range(1, config.worker_count + 1):
            branch_name = config.worker_branch_name(worker_id)
            worktree_path = self.layout.worker_workspace(worker_id)
            self.repo.create_branch(branch_name, config.baseline_branch_name)
            self.repo.add_worktree(worktree_path, branch_name)
            worker_branches.append(branch_name)
            worker_worktrees.append(worktree_path)

        self.repo.create_branch(config.merge_branch_name, config.baseline_branch_name)
        merge_worktree = self.layout.merge_workspace()
        self.repo.add_worktree(merge_worktree, config.merge_branch_name)

        shared_candidate_branch = ""
        shared_candidate_worktree = None
        if config.architecture_mode is ArchitectureMode.AGENT_GROUPCHAT:
            shared_candidate_branch = config.shared_candidate_branch_name
            shared_candidate_worktree = self.layout.shared_candidate_workspace()
            self.repo.create_branch(shared_candidate_branch, config.baseline_branch_name)
            self.repo.add_worktree(shared_candidate_worktree, shared_candidate_branch)

        state = RunState(
            run_tag=config.run_tag,
            target_repo_path=config.target_repo_path,
            architecture_mode=config.architecture_mode,
            agent_groupchat=config.agent_groupchat,
            baseline_source_ref=baseline_source_ref,
            initial_baseline_commit=baseline_commit,
            baseline_branch=config.baseline_branch_name,
            baseline_commit=baseline_commit,
            baseline_val_bpb=None,
            worker_branches=worker_branches,
            worker_worktrees=worker_worktrees,
            merge_branch=config.merge_branch_name,
            merge_worktree=merge_worktree,
            shared_candidate_branch=shared_candidate_branch,
            shared_candidate_worktree=shared_candidate_worktree,
            status=RunStatus.READY,
            selected_commit=baseline_commit,
        )
        self.store.initialize_run_files(
            state,
            program_experience_seed_profile=config.program_experience_seed_profile,
        )
        return InitializedRun(config=config, layout=self.layout, state=state)

    def sync_all_to_baseline(
        self,
        state: RunState,
        baseline_commit: str | None = None,
        baseline_val_bpb: float | None = None,
    ) -> RunState:
        target_commit = (baseline_commit or state.baseline_commit).strip()
        if not target_commit:
            raise ValueError("baseline commit must not be empty")

        for worktree_path in state.worker_worktrees:
            self.repo.reset_worktree(worktree_path, target_commit)
        self.repo.reset_worktree(state.merge_worktree, target_commit)
        if state.shared_candidate_worktree is not None:
            self.repo.reset_worktree(state.shared_candidate_worktree, target_commit)

        state.baseline_commit = target_commit
        if baseline_val_bpb is not None:
            state.baseline_val_bpb = baseline_val_bpb
        state.selected_commit = target_commit
        self.store.save_run_state(state)
        return state

    def promote_to_baseline(
        self,
        state: RunState,
        commit: str,
        baseline_val_bpb: float | None = None,
    ) -> RunState:
        commit = commit.strip()
        if not commit:
            raise ValueError("commit must not be empty")
        self.repo.force_branch(state.baseline_branch, commit)
        return self.sync_all_to_baseline(state, baseline_commit=commit, baseline_val_bpb=baseline_val_bpb)

    def _ensure_fresh_run_root(self) -> None:
        if self.layout.root.exists() and any(self.layout.root.iterdir()):
            raise FileExistsError(f"run root already exists and is not empty: {self.layout.root}")

    def _ensure_branch_names_available(self, config: RunConfig) -> None:
        branch_names = [config.baseline_branch_name, config.merge_branch_name]
        branch_names.extend(config.worker_branch_name(worker_id) for worker_id in range(1, config.worker_count + 1))
        if config.architecture_mode is ArchitectureMode.AGENT_GROUPCHAT:
            branch_names.append(config.shared_candidate_branch_name)
        collisions = [branch_name for branch_name in branch_names if self.repo.branch_exists(branch_name)]
        if collisions:
            names = ", ".join(collisions)
            raise FileExistsError(f"git branches already exist for run_tag {config.run_tag}: {names}")
