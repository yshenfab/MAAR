from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_teams.config import AgentGroupChatConfig

from .serialization import SerializableDataclass
from .state import ArchitectureMode

DEFAULT_TRAIN_COMMAND = ("uv", "run", "train.py")


@dataclass(slots=True)
class RunConfig(SerializableDataclass):
    """Top-level run configuration for a single orchestrator session."""

    run_tag: str
    worker_count: int
    target_repo_path: Path
    artifact_root: Path
    architecture_mode: ArchitectureMode = ArchitectureMode.MAAR
    agent_groupchat: AgentGroupChatConfig = field(default_factory=AgentGroupChatConfig)
    baseline_source_ref: str = ""
    execution_slots: int = 1
    agent_command_template: tuple[str, ...] = field(default_factory=tuple)
    worker_agent_backend: str = "mock"
    coordinator_agent_backend: str = "mock"
    worker_model_name: str = ""
    coordinator_model_name: str = ""
    worker_prompt_profile: str = "maar_wide"
    coordinator_prompt_profile: str = "coordinator"
    program_experience_seed_profile: str = ""
    preflight_profile: str = ""
    agent_timeout_seconds: int = 120
    agent_max_retries: int = 2
    runtime_python_command: tuple[str, ...] = field(default_factory=tuple)
    preflight_import_check_command: tuple[str, ...] = field(default_factory=tuple)
    preflight_check_imports: bool = True
    train_command: tuple[str, ...] = DEFAULT_TRAIN_COMMAND
    train_timeout_seconds: float = 600.0
    max_rounds: int | None = None
    continuous: bool = False

    def __post_init__(self) -> None:
        self.target_repo_path = Path(self.target_repo_path).expanduser().resolve()
        self.artifact_root = Path(self.artifact_root).expanduser().resolve()
        self.run_tag = self.run_tag.strip()
        self.architecture_mode = ArchitectureMode(self.architecture_mode)
        self.baseline_source_ref = self.baseline_source_ref.strip()
        self.worker_agent_backend = self.worker_agent_backend.strip()
        self.coordinator_agent_backend = self.coordinator_agent_backend.strip()
        self.worker_model_name = self.worker_model_name.strip()
        self.coordinator_model_name = self.coordinator_model_name.strip()
        self.worker_prompt_profile = self.worker_prompt_profile.strip()
        self.coordinator_prompt_profile = self.coordinator_prompt_profile.strip()
        self.program_experience_seed_profile = self.program_experience_seed_profile.strip()
        self.preflight_profile = self.preflight_profile.strip()

        if not self.run_tag:
            raise ValueError("run_tag must not be empty")
        if self.worker_count < 1:
            raise ValueError("worker_count must be >= 1")
        if self.execution_slots < 1:
            raise ValueError("execution_slots must be >= 1")
        if self.agent_timeout_seconds < 1:
            raise ValueError("agent_timeout_seconds must be >= 1")
        if self.agent_max_retries < 0:
            raise ValueError("agent_max_retries must be >= 0")
        if self.train_timeout_seconds <= 0:
            raise ValueError("train_timeout_seconds must be > 0")
        if self.max_rounds is not None and self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1 when provided")
        if self.continuous and self.max_rounds is not None:
            raise ValueError("continuous runs cannot also set max_rounds")

    @property
    def run_root(self) -> Path:
        return self.artifact_root / self.run_tag

    @property
    def branch_prefix(self) -> str:
        return f"autoresearch/{self.run_tag}"

    @property
    def specialist_count(self) -> int:
        return self.agent_groupchat.specialist_count

    @property
    def baseline_branch_name(self) -> str:
        return f"{self.branch_prefix}/baseline"

    def worker_branch_name(self, worker_id: int) -> str:
        if worker_id < 1:
            raise ValueError("worker_id must be >= 1")
        return f"{self.branch_prefix}/worker-{worker_id}"

    @property
    def merge_branch_name(self) -> str:
        return f"{self.branch_prefix}/merge"

    @property
    def shared_candidate_branch_name(self) -> str:
        return f"{self.branch_prefix}/shared-candidate"


@dataclass(slots=True)
class CoordinatorConfig(SerializableDataclass):
    """Coordinator-specific tuning knobs."""

    enabled: bool = True
    trigger_min_improvements: int = 2
    top_k: int = 2
    validate_with_priority: bool = True

    def __post_init__(self) -> None:
        if self.trigger_min_improvements < 1:
            raise ValueError("trigger_min_improvements must be >= 1")
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
