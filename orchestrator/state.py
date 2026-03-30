from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agent_teams.config import AgentGroupChatConfig

from .serialization import SerializableDataclass


class ActorRole(str, Enum):
    WORKER = "worker"
    COORDINATOR = "coordinator"
    SPECIALIST = "specialist"
    ENGINEER = "engineer"
    GROUPCHAT = "groupchat"


class ArchitectureMode(str, Enum):
    BASELINE = "baseline"
    MAAR = "maar"
    AGENT_GROUPCHAT = "agent_groupchat"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    PROPOSAL_FAILED = "proposal_failed"
    PREFLIGHT_FAILED = "preflight_failed"
    CRASH = "crash"
    DISCARD = "discard"
    KEEP = "keep"


class GroupChatTurnStatus(str, Enum):
    ACCEPTED = "accepted"
    PROPOSAL_FAILED = "proposal_failed"
    PATCH_FAILED = "patch_failed"
    PREFLIGHT_FAILED = "preflight_failed"


class RunStatus(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass(slots=True)
class ExperimentProposal(SerializableDataclass):
    motivation: str
    idea_summary: str
    search_block: str
    replace_block: str


@dataclass(slots=True)
class CoordinatorProposal(ExperimentProposal):
    merge_rationale: str = ""
    source_candidates: list[str] = field(default_factory=list)
    curator_note: str = ""


@dataclass(slots=True)
class ExperimentMetrics(SerializableDataclass):
    val_bpb: float | None = None
    peak_vram_mb: float | None = None
    training_seconds: float | None = None
    total_seconds: float | None = None


@dataclass(slots=True)
class ExperimentResult(SerializableDataclass):
    round_id: int
    actor_role: ActorRole
    actor_id: str
    baseline_commit: str
    candidate_commit: str = ""
    status: ExperimentStatus = ExperimentStatus.PENDING
    metrics: ExperimentMetrics = field(default_factory=ExperimentMetrics)
    diff_path: Path | None = None
    log_path: Path | None = None
    proposal_path: Path | None = None
    metrics_path: Path | None = None
    improved: bool = False
    failure_reason: str = ""

    def __post_init__(self) -> None:
        self.actor_id = self.actor_id.strip()
        self.baseline_commit = self.baseline_commit.strip()
        self.candidate_commit = self.candidate_commit.strip()
        if self.round_id < 1:
            raise ValueError("round_id must be >= 1")
        if not self.actor_id:
            raise ValueError("actor_id must not be empty")
        if not self.baseline_commit:
            raise ValueError("baseline_commit must not be empty")


@dataclass(slots=True)
class GroupChatTurnResult(SerializableDataclass):
    turn_index: int
    specialist_role: str
    actor_id: str
    baseline_commit: str
    shared_commit_before: str
    shared_commit_after: str = ""
    status: GroupChatTurnStatus = GroupChatTurnStatus.PROPOSAL_FAILED
    proposal_path: Path | None = None
    diff_path: Path | None = None
    failure_reason: str = ""

    def __post_init__(self) -> None:
        self.specialist_role = self.specialist_role.strip()
        self.actor_id = self.actor_id.strip()
        self.baseline_commit = self.baseline_commit.strip()
        self.shared_commit_before = self.shared_commit_before.strip()
        self.shared_commit_after = self.shared_commit_after.strip()
        if self.turn_index < 1:
            raise ValueError("turn_index must be >= 1")
        if not self.specialist_role:
            raise ValueError("specialist_role must not be empty")
        if not self.actor_id:
            raise ValueError("actor_id must not be empty")
        if not self.baseline_commit:
            raise ValueError("baseline_commit must not be empty")
        if not self.shared_commit_before:
            raise ValueError("shared_commit_before must not be empty")


@dataclass(slots=True)
class RoundState(SerializableDataclass):
    round_id: int
    baseline_commit: str
    baseline_val_bpb: float | None = None
    worker_results: list[ExperimentResult] = field(default_factory=list)
    positive_results: list[ExperimentResult] = field(default_factory=list)
    merge_result: ExperimentResult | None = None
    groupchat_turns: list[GroupChatTurnResult] = field(default_factory=list)
    groupchat_result: ExperimentResult | None = None
    groupchat_engineer_result: ExperimentResult | None = None
    selected_result: ExperimentResult | None = None

    def __post_init__(self) -> None:
        self.baseline_commit = self.baseline_commit.strip()
        if self.round_id < 1:
            raise ValueError("round_id must be >= 1")
        if not self.baseline_commit:
            raise ValueError("baseline_commit must not be empty")


@dataclass(slots=True)
class RunState(SerializableDataclass):
    run_tag: str
    target_repo_path: Path
    baseline_source_ref: str
    initial_baseline_commit: str
    baseline_branch: str
    baseline_commit: str
    worker_branches: list[str]
    worker_worktrees: list[Path]
    merge_branch: str
    merge_worktree: Path
    shared_candidate_branch: str = ""
    shared_candidate_worktree: Path | None = None
    architecture_mode: ArchitectureMode = ArchitectureMode.MAAR
    agent_groupchat: AgentGroupChatConfig = field(default_factory=AgentGroupChatConfig)
    baseline_val_bpb: float | None = None
    current_round: int = 0
    status: RunStatus = RunStatus.INITIALIZING
    selected_commit: str = ""

    def __post_init__(self) -> None:
        self.run_tag = self.run_tag.strip()
        self.target_repo_path = Path(self.target_repo_path).expanduser().resolve()
        self.architecture_mode = ArchitectureMode(self.architecture_mode)
        self.baseline_source_ref = self.baseline_source_ref.strip()
        self.initial_baseline_commit = self.initial_baseline_commit.strip()
        self.baseline_branch = self.baseline_branch.strip()
        self.baseline_commit = self.baseline_commit.strip()
        self.merge_branch = self.merge_branch.strip()
        self.shared_candidate_branch = self.shared_candidate_branch.strip()
        self.worker_worktrees = [Path(path).expanduser().resolve() for path in self.worker_worktrees]
        self.merge_worktree = Path(self.merge_worktree).expanduser().resolve()
        self.shared_candidate_worktree = (
            Path(self.shared_candidate_worktree).expanduser().resolve()
            if self.shared_candidate_worktree is not None
            else None
        )
        self.selected_commit = self.selected_commit.strip()
        if not self.run_tag:
            raise ValueError("run_tag must not be empty")
        if not self.baseline_source_ref:
            raise ValueError("baseline_source_ref must not be empty")
        if not self.initial_baseline_commit:
            raise ValueError("initial_baseline_commit must not be empty")
        if not self.baseline_branch:
            raise ValueError("baseline_branch must not be empty")
        if not self.baseline_commit:
            raise ValueError("baseline_commit must not be empty")
        if self.current_round < 0:
            raise ValueError("current_round must be >= 0")
        if len(self.worker_branches) != len(self.worker_worktrees):
            raise ValueError("worker_branches and worker_worktrees must have the same length")
