from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import Iterator, Sequence

from .env import build_subprocess_env
from .serialization import SerializableDataclass
from .state import ExperimentMetrics


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    CRASH = "crash"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class ExecutionResult(SerializableDataclass):
    workspace_path: Path
    command: tuple[str, ...]
    log_path: Path
    status: ExecutionStatus
    metrics: ExperimentMetrics
    exit_code: int | None
    timed_out: bool
    failure_reason: str = ""


class LogParseError(RuntimeError):
    """Raised when a run log does not contain a complete training summary."""


class ExecutionSlotPool:
    """Small token pool abstraction for serialized or limited training execution."""

    def __init__(self, size: int):
        if size < 1:
            raise ValueError("pool size must be >= 1")
        self._tokens: Queue[int] = Queue(maxsize=size)
        for token in range(size):
            self._tokens.put(token)

    @property
    def size(self) -> int:
        return self._tokens.maxsize

    @property
    def available_slots(self) -> int:
        return self._tokens.qsize()

    @contextmanager
    def acquire(self) -> Iterator[int]:
        token = self._tokens.get()
        try:
            yield token
        finally:
            self._tokens.put(token)


class TrainingLogParser:
    """Parse the fixed summary block produced by autoresearch-style training runs."""

    REQUIRED_FIELDS = ("val_bpb", "training_seconds", "total_seconds", "peak_vram_mb")

    def parse_file(self, log_path: Path) -> ExperimentMetrics:
        log_text = Path(log_path).read_text(encoding="utf-8")
        return self.parse_text(log_text)

    def parse_text(self, log_text: str) -> ExperimentMetrics:
        metrics: dict[str, float] = {}
        for raw_line in log_text.splitlines():
            line = raw_line.strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key not in self.REQUIRED_FIELDS:
                continue
            try:
                metrics[key] = float(value)
            except ValueError:
                raise LogParseError(f"failed to parse numeric value for {key!r}: {value!r}") from None

        missing = [field for field in self.REQUIRED_FIELDS if field not in metrics]
        if missing:
            joined = ", ".join(missing)
            raise LogParseError(f"missing summary fields: {joined}")

        return ExperimentMetrics(
            val_bpb=metrics["val_bpb"],
            peak_vram_mb=metrics["peak_vram_mb"],
            training_seconds=metrics["training_seconds"],
            total_seconds=metrics["total_seconds"],
        )


class TrainingExecutor:
    """Run a training command, capture logs, and parse fixed summary metrics."""

    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float = 600.0,
        slot_pool: ExecutionSlotPool | None = None,
        log_parser: TrainingLogParser | None = None,
    ):
        if not command:
            raise ValueError("command must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.slot_pool = slot_pool or ExecutionSlotPool(1)
        self.log_parser = log_parser or TrainingLogParser()

    def run(self, workspace_path: Path, log_path: Path, env: dict[str, str] | None = None) -> ExecutionResult:
        workspace_path = Path(workspace_path).expanduser().resolve()
        log_path = Path(log_path).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with self.slot_pool.acquire():
            return self._run_once(workspace_path, log_path, env)

    def _run_once(self, workspace_path: Path, log_path: Path, env: dict[str, str] | None) -> ExecutionResult:
        proc_env = build_subprocess_env(base_env=env)
        with log_path.open("w", encoding="utf-8") as handle:
            proc = subprocess.Popen(
                self.command,
                cwd=str(workspace_path),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=proc_env,
            )
            timed_out = False
            failure_reason = ""

            try:
                proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                failure_reason = f"command exceeded timeout of {self.timeout_seconds}s"
                proc.kill()
                proc.wait()
                handle.write(f"\n[orchestrator] timeout: {failure_reason}\n")

        if timed_out:
            return ExecutionResult(
                workspace_path=workspace_path,
                command=self.command,
                log_path=log_path,
                status=ExecutionStatus.TIMEOUT,
                metrics=ExperimentMetrics(),
                exit_code=proc.returncode,
                timed_out=True,
                failure_reason=failure_reason,
            )

        if proc.returncode != 0:
            return ExecutionResult(
                workspace_path=workspace_path,
                command=self.command,
                log_path=log_path,
                status=ExecutionStatus.CRASH,
                metrics=ExperimentMetrics(),
                exit_code=proc.returncode,
                timed_out=False,
                failure_reason=f"command exited with code {proc.returncode}",
            )

        try:
            metrics = self.log_parser.parse_file(log_path)
        except LogParseError as exc:
            return ExecutionResult(
                workspace_path=workspace_path,
                command=self.command,
                log_path=log_path,
                status=ExecutionStatus.CRASH,
                metrics=ExperimentMetrics(),
                exit_code=proc.returncode,
                timed_out=False,
                failure_reason=str(exc),
            )

        return ExecutionResult(
            workspace_path=workspace_path,
            command=self.command,
            log_path=log_path,
            status=ExecutionStatus.SUCCESS,
            metrics=metrics,
            exit_code=proc.returncode,
            timed_out=False,
        )
