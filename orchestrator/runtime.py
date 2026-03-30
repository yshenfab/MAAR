from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_TRAIN_COMMAND, RunConfig
from .executor import ExecutionSlotPool, TrainingExecutor, TrainingLogParser
from .preflight import (
    PREFLIGHT_PROFILE_BASELINE_LEGACY,
    PREFLIGHT_PROFILE_MAAR_STRICT,
    PREFLIGHT_PROFILE_STANDARD,
    PreflightChecker,
)
from .serialization import SerializableDataclass

DEFAULT_UV_PYTHON_COMMAND = ("uv", "run", "python")
DEFAULT_SYSTEM_PYTHON_COMMAND = ("python3",)
RUNTIME_PYTHON_ENV = "AUTORESEARCH_RUNTIME_PYTHON"
RUNTIME_PYTHONPATH_ENV = "AUTORESEARCH_RUNTIME_PYTHONPATH"


@dataclass(slots=True)
class RuntimeResolution(SerializableDataclass):
    repo_path: Path
    python_command: tuple[str, ...]
    train_command: tuple[str, ...]
    import_check_command: tuple[str, ...]
    source: str


def resolve_runtime(config: RunConfig) -> RuntimeResolution:
    repo_path = config.target_repo_path
    python_command, source = _resolve_python_command(repo_path, config.runtime_python_command)
    train_command = _resolve_train_command(config, python_command)
    import_check_command = tuple(config.preflight_import_check_command) or python_command
    return RuntimeResolution(
        repo_path=repo_path,
        python_command=python_command,
        train_command=train_command,
        import_check_command=import_check_command,
        source=source,
    )


def build_preflight_checker(config: RunConfig) -> PreflightChecker:
    runtime = resolve_runtime(config)
    return PreflightChecker(
        import_check_command=runtime.import_check_command,
        check_imports=config.preflight_check_imports,
        profile=_resolve_preflight_profile(config),
    )


def build_training_executor(
    config: RunConfig,
    *,
    slot_pool: ExecutionSlotPool | None = None,
    log_parser: TrainingLogParser | None = None,
) -> TrainingExecutor:
    runtime = resolve_runtime(config)
    return TrainingExecutor(
        runtime.train_command,
        timeout_seconds=config.train_timeout_seconds,
        slot_pool=slot_pool,
        log_parser=log_parser,
    )


def _resolve_python_command(repo_path: Path, explicit_command: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    if explicit_command:
        return tuple(explicit_command), "explicit"

    env_runtime = os.environ.get(RUNTIME_PYTHON_ENV, "").strip()
    if env_runtime:
        env_pythonpath = os.environ.get(RUNTIME_PYTHONPATH_ENV, "").strip()
        if env_pythonpath:
            existing_pythonpath = os.environ.get("PYTHONPATH", "").strip()
            pythonpath = env_pythonpath if not existing_pythonpath else f"{env_pythonpath}:{existing_pythonpath}"
            return ("env", f"PYTHONPATH={pythonpath}", env_runtime), "env"
        return (env_runtime,), "env"

    candidates = (
        repo_path / ".conda-env" / "bin" / "python",
        repo_path / ".venv" / "bin" / "python",
        repo_path / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return (str(candidate),), "venv"

    if shutil.which("uv"):
        return DEFAULT_UV_PYTHON_COMMAND, "uv"

    return DEFAULT_SYSTEM_PYTHON_COMMAND, "system"


def _resolve_train_command(config: RunConfig, python_command: tuple[str, ...]) -> tuple[str, ...]:
    train_command = tuple(config.train_command)
    if train_command != DEFAULT_TRAIN_COMMAND:
        return train_command
    if python_command == DEFAULT_UV_PYTHON_COMMAND:
        return DEFAULT_TRAIN_COMMAND
    return (*python_command, "train.py")


def _resolve_preflight_profile(config: RunConfig) -> str:
    if config.preflight_profile:
        return config.preflight_profile
    if config.worker_prompt_profile == "autoresearch_original" and config.coordinator_agent_backend == "mock":
        return PREFLIGHT_PROFILE_BASELINE_LEGACY
    if config.worker_prompt_profile.startswith("maar") or config.coordinator_agent_backend != "mock":
        return PREFLIGHT_PROFILE_MAAR_STRICT
    return PREFLIGHT_PROFILE_STANDARD
