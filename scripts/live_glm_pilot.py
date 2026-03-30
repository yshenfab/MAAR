from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import (
    CoordinatorConfig,
    PreflightChecker,
    RunConfig,
    TrainingExecutor,
    WorkerRoundRunner,
    WorktreeManager,
    ZhipuChatAgentRunner,
    clear_proxy_env,
    load_project_env,
)
PILOT_ROOT = PROJECT_ROOT / "runs" / "live-glm-pilot"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def build_toy_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "train.py").write_text(
        "# Conservative baseline for orchestrator pilot runs.\n"
        "# The safest high-impact edits are usually top-level constant changes.\n"
        "# Better simulated values are typically:\n"
        "#   eval_interval = 500\n"
        "#   warmup_steps = 200\n"
        "#   grad_accum_steps = 4\n"
        "eval_interval = 1000\n"
        "warmup_steps = 400\n"
        "grad_accum_steps = 8\n"
        "\n"
        "def simulated_val_bpb() -> float:\n"
        "    improvement = 0.0\n"
        "    if eval_interval == 500:\n"
        "        improvement += 0.02\n"
        "    if warmup_steps == 200:\n"
        "        improvement += 0.03\n"
        "    if grad_accum_steps == 4:\n"
        "        improvement += 0.005\n"
        "    if eval_interval == 500 and warmup_steps == 200:\n"
        "        improvement += 0.01\n"
        "    return 1.0 - improvement\n"
        "\n"
        "print('---')\n"
        "print(f'val_bpb:          {simulated_val_bpb():.6f}')\n"
        "print('training_seconds: 300.0')\n"
        "print('total_seconds:    320.0')\n"
        "print('peak_vram_mb:     12345.0')\n",
        encoding="utf-8",
    )
    git(repo, "add", "train.py")
    git(repo, "commit", "-m", "baseline")
    return repo


@dataclass(slots=True)
class TrialConfig:
    label: str
    worker_count: int
    coordinator_enabled: bool
    rounds: int


def make_run_tag(prefix: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{prefix}-{stamp}"


def run_trial(runner: ZhipuChatAgentRunner, trial: TrialConfig) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="glm-pilot-") as tmp:
        root = Path(tmp)
        repo = build_toy_repo(root)
        config = RunConfig(
            run_tag=make_run_tag(trial.label),
            worker_count=trial.worker_count,
            target_repo_path=repo,
            artifact_root=PILOT_ROOT / trial.label,
            execution_slots=1,
        )
        manager = WorktreeManager.from_config(config)
        initialized = manager.initialize_run(config)
        initialized.state.baseline_val_bpb = 1.0
        initialized.state.selected_commit = initialized.state.baseline_commit
        manager.store.save_run_state(initialized.state)

        round_runner = WorkerRoundRunner(
            worktree_manager=manager,
            agent_runner=runner,
            coordinator_agent_runner=runner if trial.coordinator_enabled else None,
            coordinator_config=CoordinatorConfig(enabled=trial.coordinator_enabled, trigger_min_improvements=2, top_k=2),
            executor=TrainingExecutor(("python3", "train.py"), timeout_seconds=10.0),
            preflight=PreflightChecker(check_imports=False),
        )
        run_state = initialized.state
        round_summaries: list[dict[str, Any]] = []
        for _ in range(trial.rounds):
            round_result = round_runner.run_round(run_state)
            run_state = round_result.run_state
            round_state = round_result.round_state
            round_summaries.append(
                {
                    "round_id": round_state.round_id,
                    "baseline_before": round_state.baseline_val_bpb,
                    "baseline_after": run_state.baseline_val_bpb,
                    "selected_actor_id": round_state.selected_result.actor_id if round_state.selected_result else "",
                    "selected_status": round_state.selected_result.status.value if round_state.selected_result else "",
                    "coordinator_triggered": round_state.merge_result is not None,
                    "coordinator_status": round_state.merge_result.status.value if round_state.merge_result else "",
                    "worker_results": [
                        {
                            "actor_id": result.actor_id,
                            "status": result.status.value,
                            "val_bpb": result.metrics.val_bpb,
                            "failure_reason": result.failure_reason,
                            "proposal_path": str(result.proposal_path) if result.proposal_path else "",
                        }
                        for result in round_state.worker_results
                    ],
                }
            )
        return {
            "status": "ok",
            "label": trial.label,
            "run_root": str(config.run_root),
            "baseline_before": 1.0,
            "baseline_after": run_state.baseline_val_bpb,
            "rounds_requested": trial.rounds,
            "rounds_completed": len(round_summaries),
            "final_selected_actor_id": round_summaries[-1]["selected_actor_id"] if round_summaries else "",
            "final_selected_status": round_summaries[-1]["selected_status"] if round_summaries else "",
            "rounds": round_summaries,
        }


def build_trial_sequence(two_worker_trials: int, rounds: int) -> list[TrialConfig]:
    trials = [TrialConfig(label="single-worker", worker_count=1, coordinator_enabled=False, rounds=1)]
    for index in range(1, two_worker_trials + 1):
        trials.append(
            TrialConfig(
                label=f"two-workers-trial-{index}",
                worker_count=2,
                coordinator_enabled=True,
                rounds=rounds,
            )
        )
    return trials


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live GLM pilot trials on a toy train.py repo.")
    parser.add_argument("--two-worker-trials", type=int, default=3, help="Number of 2-worker coordinator trials to run.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of rounds to run for each 2-worker trial.")
    args = parser.parse_args()

    load_project_env(PROJECT_ROOT)
    clear_proxy_env()
    PILOT_ROOT.mkdir(parents=True, exist_ok=True)
    runner = ZhipuChatAgentRunner.from_env(project_root=PROJECT_ROOT)

    summary: dict[str, Any] = {"trials": []}
    for trial in build_trial_sequence(args.two_worker_trials, args.rounds):
        try:
            summary["trials"].append(run_trial(runner, trial))
        except Exception as exc:
            summary["trials"].append(
                {
                    "status": "failed",
                    "label": trial.label,
                    "error": str(exc),
                }
            )

    output_path = PILOT_ROOT / "summary.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    print(f"summary_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
