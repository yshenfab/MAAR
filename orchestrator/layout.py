from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import RunConfig

RESULTS_TSV_HEADER = (
    "round_id\tactor_role\tactor_id\tbaseline_commit\tcandidate_commit\tval_bpb\t"
    "peak_vram_mb\ttraining_seconds\ttotal_seconds\tstatus\tdiff_path\tlog_path\n"
)


@dataclass(slots=True)
class RunLayout:
    """Canonical filesystem layout for a single orchestrator run."""

    root: Path
    run_json_path: Path
    results_tsv_path: Path
    experiments_jsonl_path: Path
    program_experience_path: Path
    groupchat_memory_path: Path
    groupchat_log_path: Path
    rounds_dir: Path
    workspaces_dir: Path

    @classmethod
    def from_config(cls, config: RunConfig) -> "RunLayout":
        root = config.run_root
        return cls(
            root=root,
            run_json_path=root / "run.json",
            results_tsv_path=root / "results.tsv",
            experiments_jsonl_path=root / "experiments.jsonl",
            program_experience_path=root / "program_exp.md",
            groupchat_memory_path=root / config.agent_groupchat.groupchat_memory_filename,
            groupchat_log_path=root / config.agent_groupchat.groupchat_log_filename,
            rounds_dir=root / "rounds",
            workspaces_dir=root / "workspaces",
        )

    def create(self, worker_count: int) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be >= 1")
        self.root.mkdir(parents=True, exist_ok=True)
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def worker_workspace(self, worker_id: int) -> Path:
        if worker_id < 1:
            raise ValueError("worker_id must be >= 1")
        return self.workspaces_dir / f"worker-{worker_id}"

    def merge_workspace(self) -> Path:
        return self.workspaces_dir / "merge"

    def shared_candidate_workspace(self) -> Path:
        return self.workspaces_dir / "shared-candidate"

    def round_dir(self, round_id: int) -> Path:
        if round_id < 1:
            raise ValueError("round_id must be >= 1")
        return self.rounds_dir / f"round-{round_id:04d}"

    def round_state_path(self, round_id: int) -> Path:
        return self.round_dir(round_id) / "round.json"

    def worker_artifact_dir(self, round_id: int, worker_id: int) -> Path:
        return self.round_dir(round_id) / "workers" / f"worker-{worker_id}"

    def coordinator_artifact_dir(self, round_id: int) -> Path:
        return self.round_dir(round_id) / "coordinator"

    def groupchat_artifact_dir(self, round_id: int) -> Path:
        return self.round_dir(round_id) / "groupchat"

    def groupchat_engineer_artifact_dir(self, round_id: int) -> Path:
        return self.groupchat_artifact_dir(round_id) / "engineer"

    def groupchat_turn_artifact_dir(self, round_id: int, turn_index: int, specialist_role: str) -> Path:
        if turn_index < 1:
            raise ValueError("turn_index must be >= 1")
        role = specialist_role.strip()
        if not role:
            raise ValueError("specialist_role must not be empty")
        return self.groupchat_artifact_dir(round_id) / f"turn-{turn_index:02d}-{role}"

    def create_round_dirs(self, round_id: int, worker_count: int) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be >= 1")
        round_dir = self.round_dir(round_id)
        (round_dir / "workers").mkdir(parents=True, exist_ok=True)
        for worker_id in range(1, worker_count + 1):
            self.worker_artifact_dir(round_id, worker_id).mkdir(parents=True, exist_ok=True)
        self.coordinator_artifact_dir(round_id).mkdir(parents=True, exist_ok=True)
        self.groupchat_artifact_dir(round_id).mkdir(parents=True, exist_ok=True)
        self.groupchat_engineer_artifact_dir(round_id).mkdir(parents=True, exist_ok=True)
