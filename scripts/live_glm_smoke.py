from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import (
    ActorRole,
    CoordinatorConfig,
    PreflightChecker,
    ProposalRequest,
    RunConfig,
    SearchReplacePatcher,
    TrainingExecutor,
    WorkerRoundRunner,
    WorktreeManager,
    ZhipuChatAgentRunner,
    clear_proxy_env,
    load_project_env,
)
AUTORESEARCH_ROOT = PROJECT_ROOT / "autoresearch"
SMOKE_ROOT = PROJECT_ROOT / "runs" / "live-glm-smoke"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def clone_clean_repo(source_repo: Path, destination: Path) -> None:
    subprocess.run(
        ["git", "clone", "--no-hardlinks", str(source_repo), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )


def validate_proposal_against_clean_clone(proposal: object) -> dict[str, object]:
    patcher = SearchReplacePatcher()
    preflight = PreflightChecker(check_imports=False)
    with tempfile.TemporaryDirectory(prefix="glm-proposal-validate-") as tmp:
        repo_copy = Path(tmp) / "repo"
        clone_clean_repo(AUTORESEARCH_ROOT, repo_copy)
        try:
            patch_result = patcher.apply(repo_copy, proposal)
            report = preflight.run(repo_copy)
        except Exception as exc:
            return {
                "status": "failed",
                "error": str(exc),
            }
        return {
            "status": "ok",
            "match_mode": patch_result.match_mode.value,
            "modified_paths": report.changed_paths,
            "imported_modules_count": len(report.imported_modules),
        }


def run_worker_smoke(runner: ZhipuChatAgentRunner) -> dict[str, object]:
    artifact_dir = SMOKE_ROOT / "worker-direct"
    proposal = runner.propose(
        ProposalRequest(
            actor_role=ActorRole.WORKER,
            actor_id="worker-1",
            round_id=1,
            baseline_commit="live-worker-smoke",
            workspace_path=AUTORESEARCH_ROOT,
            artifact_dir=artifact_dir,
            context={"mode": "worker_direct_smoke"},
        )
    )
    return {
        "status": "ok",
        "artifact_dir": str(artifact_dir),
        "motivation": proposal.motivation,
        "search_preview": proposal.search_block[:160],
        "replace_preview": proposal.replace_block[:160],
        "validation": validate_proposal_against_clean_clone(proposal),
    }


def run_coordinator_smoke(runner: ZhipuChatAgentRunner) -> dict[str, object]:
    artifact_dir = SMOKE_ROOT / "coordinator-direct"
    proposal = runner.propose(
        ProposalRequest(
            actor_role=ActorRole.COORDINATOR,
            actor_id="coordinator",
            round_id=1,
            baseline_commit="live-coordinator-smoke",
            workspace_path=AUTORESEARCH_ROOT,
            artifact_dir=artifact_dir,
            context={
                "baseline_val_bpb": 0.971,
                "source_candidates": [
                    {
                        "actor_id": "worker-1",
                        "metrics": {"val_bpb": 0.966},
                        "motivation": "increase evaluation frequency",
                        "diff_text": "diff --git a/train.py b/train.py\n...",
                    },
                    {
                        "actor_id": "worker-2",
                        "metrics": {"val_bpb": 0.961},
                        "motivation": "tighten warmup schedule",
                        "diff_text": "diff --git a/train.py b/train.py\n...",
                    },
                ],
            },
        )
    )
    return {
        "status": "ok",
        "artifact_dir": str(artifact_dir),
        "motivation": proposal.motivation,
        "merge_rationale": getattr(proposal, "merge_rationale", ""),
        "source_candidates": getattr(proposal, "source_candidates", []),
        "search_preview": proposal.search_block[:160],
        "replace_preview": proposal.replace_block[:160],
        "validation": validate_proposal_against_clean_clone(proposal),
    }


def run_round_smoke(runner: ZhipuChatAgentRunner) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="glm-round-smoke-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test User")
        git(repo, "config", "user.email", "test@example.com")
        (repo / "train.py").write_text(
            "eval_interval = 1000\n"
            "\n"
            "def simulated_val_bpb() -> float:\n"
            "    if eval_interval >= 1000:\n"
            "        return 1.0\n"
            "    return 0.95\n"
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

        run_tag = f"live-glm-round-smoke-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        config = RunConfig(
            run_tag=run_tag,
            worker_count=1,
            target_repo_path=repo,
            artifact_root=SMOKE_ROOT / "round-run",
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
            coordinator_agent_runner=None,
            coordinator_config=CoordinatorConfig(enabled=False),
            executor=TrainingExecutor(("python3", "train.py"), timeout_seconds=10.0),
            preflight=PreflightChecker(check_imports=False),
        )

        result = round_runner.run_round(initialized.state)
        round_state = result.round_state
        selected = round_state.selected_result
        worker_result = round_state.worker_results[0]
        return {
            "status": "ok",
            "artifact_root": str(config.run_root),
            "round_id": round_state.round_id,
            "worker_status": worker_result.status.value,
            "worker_failure_reason": worker_result.failure_reason,
            "worker_val_bpb": worker_result.metrics.val_bpb,
            "selected_actor_id": selected.actor_id if selected is not None else "",
            "selected_status": selected.status.value if selected is not None else "",
            "baseline_val_bpb_after_round": result.run_state.baseline_val_bpb,
        }


def main() -> int:
    load_project_env(PROJECT_ROOT)
    clear_proxy_env()
    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)

    runner = ZhipuChatAgentRunner.from_env(project_root=PROJECT_ROOT)
    summary: dict[str, object] = {}
    for key, func in (
        ("worker_direct", run_worker_smoke),
        ("coordinator_direct", run_coordinator_smoke),
        ("round_smoke", run_round_smoke),
    ):
        print(f"[live_glm_smoke] starting {key}", flush=True)
        try:
            summary[key] = func(runner)
        except Exception as exc:
            summary[key] = {
                "status": "failed",
                "error": str(exc),
            }
        print(f"[live_glm_smoke] finished {key}: {summary[key]}", flush=True)
    output_path = SMOKE_ROOT / "summary.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    print(f"summary_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
