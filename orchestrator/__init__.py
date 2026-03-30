"""Core package for the multi-agent autoresearch orchestrator."""

from .agents import (
    AUTORESEARCH_ORIGINAL_PROMPT_PROFILE,
    DEFAULT_ENGINEER_PROMPT_PROFILE,
    DEFAULT_ZHIPU_BASE_URL,
    DEFAULT_ZHIPU_COORDINATOR_MODEL,
    DEFAULT_ZHIPU_MODEL,
    DEFAULT_SPECIALIST_PROMPT_PROFILE,
    DEFAULT_ZHIPU_WORKER_MODEL,
    AgentError,
    AgentRunner,
    OpenAICompatibleChatClient,
    ProposalRequest,
    ReplayAgentRunner,
    ZhipuChatAgentRunner,
    build_agent_runner,
)
from .config import CoordinatorConfig, RunConfig
from .env import build_subprocess_env, clear_proxy_env, load_env_file, load_project_env
from .executor import (
    ExecutionResult,
    ExecutionSlotPool,
    ExecutionStatus,
    LogParseError,
    TrainingExecutor,
    TrainingLogParser,
)
from .git_ops import GitError, GitRepo
from .layout import RESULTS_TSV_HEADER, RunLayout
from .live_baseline import BaselineMeasurement, measure_baseline, run_single_agent_baseline
from .live_multi import resume_multi_agent_experiment, run_multi_agent_experiment
from .memory import ProgramExperienceStore, SEED_PROFILE_MAAR_FIXED_PRIORS
from .patcher import MatchMode, PatchApplyError, PatchResult, SearchReplacePatcher
from .persistence import StateStore
from .preflight import PreflightChecker, PreflightError, PreflightReport
from .preflight import (
    PREFLIGHT_PROFILE_BASELINE_LEGACY,
    PREFLIGHT_PROFILE_MAAR_STRICT,
    PREFLIGHT_PROFILE_STANDARD,
)
from .round_runner import RoundRunResult, WorkerRoundRunner
from .runtime import (
    DEFAULT_SYSTEM_PYTHON_COMMAND,
    DEFAULT_UV_PYTHON_COMMAND,
    RuntimeResolution,
    build_preflight_checker,
    build_training_executor,
    resolve_runtime,
)
from .state import (
    ActorRole,
    ArchitectureMode,
    CoordinatorProposal,
    ExperimentMetrics,
    ExperimentProposal,
    ExperimentResult,
    ExperimentStatus,
    GroupChatTurnResult,
    GroupChatTurnStatus,
    RoundState,
    RunState,
    RunStatus,
)
from .worktree import InitializedRun, WorktreeManager

__all__ = [
    "ActorRole",
    "AgentError",
    "AgentRunner",
    "ArchitectureMode",
    "AUTORESEARCH_ORIGINAL_PROMPT_PROFILE",
    "build_agent_runner",
    "CoordinatorConfig",
    "CoordinatorProposal",
    "BaselineMeasurement",
    "build_subprocess_env",
    "build_preflight_checker",
    "build_training_executor",
    "clear_proxy_env",
    "DEFAULT_ENGINEER_PROMPT_PROFILE",
    "DEFAULT_SYSTEM_PYTHON_COMMAND",
    "DEFAULT_SPECIALIST_PROMPT_PROFILE",
    "DEFAULT_UV_PYTHON_COMMAND",
    "DEFAULT_ZHIPU_BASE_URL",
    "DEFAULT_ZHIPU_COORDINATOR_MODEL",
    "DEFAULT_ZHIPU_MODEL",
    "DEFAULT_ZHIPU_WORKER_MODEL",
    "ExecutionResult",
    "ExecutionSlotPool",
    "ExecutionStatus",
    "ExperimentMetrics",
    "ExperimentProposal",
    "ExperimentResult",
    "ExperimentStatus",
    "GroupChatTurnResult",
    "GroupChatTurnStatus",
    "GitError",
    "GitRepo",
    "InitializedRun",
    "LogParseError",
    "load_env_file",
    "load_project_env",
    "measure_baseline",
    "MatchMode",
    "OpenAICompatibleChatClient",
    "PatchApplyError",
    "PatchResult",
    "PREFLIGHT_PROFILE_BASELINE_LEGACY",
    "PREFLIGHT_PROFILE_MAAR_STRICT",
    "PREFLIGHT_PROFILE_STANDARD",
    "ProgramExperienceStore",
    "SEED_PROFILE_MAAR_FIXED_PRIORS",
    "PreflightChecker",
    "PreflightError",
    "PreflightReport",
    "ProposalRequest",
    "RESULTS_TSV_HEADER",
    "ReplayAgentRunner",
    "RoundRunResult",
    "RoundState",
    "RuntimeResolution",
    "RunConfig",
    "RunLayout",
    "RunState",
    "RunStatus",
    "run_single_agent_baseline",
    "run_multi_agent_experiment",
    "resolve_runtime",
    "resume_multi_agent_experiment",
    "SearchReplacePatcher",
    "StateStore",
    "TrainingExecutor",
    "TrainingLogParser",
    "WorkerRoundRunner",
    "WorktreeManager",
    "ZhipuChatAgentRunner",
]
