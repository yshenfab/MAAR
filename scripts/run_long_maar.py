from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import (
    CoordinatorConfig,
    RunConfig,
    clear_proxy_env,
    load_project_env,
    resume_multi_agent_experiment,
    run_multi_agent_experiment,
)


# Edit these defaults directly for your long-run experiment profile.
DEFAULT_TARGET_REPO = PROJECT_ROOT / "autoresearch-3090"
DEFAULT_ARTIFACT_ROOT = PROJECT_ROOT / "runs" / "maar-long"
DEFAULT_RUN_TAG = "maar-long-3w20r"
DEFAULT_BASELINE_SOURCE_REF = "90243dd"
DEFAULT_TOTAL_ROUNDS = 20
DEFAULT_WORKER_COUNT = 3
DEFAULT_COORDINATOR_ENABLED = True
DEFAULT_TRIGGER_MIN_IMPROVEMENTS = 2
DEFAULT_TOP_K = 2
DEFAULT_WORKER_MODEL = ""
DEFAULT_COORDINATOR_MODEL = ""
DEFAULT_AGENT_TIMEOUT_SECONDS = 120
DEFAULT_AGENT_MAX_RETRIES = 2
DEFAULT_TRAIN_TIMEOUT_SECONDS = 1500.0
DEFAULT_EXECUTION_SLOTS = 1
ESTIMATED_NORMAL_TRAIN_SECONDS = 330.0


def make_run_tag() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"maar-long-{stamp}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or resume a longer MAAR experiment profile.")
    parser.add_argument("--target-repo", type=Path, default=DEFAULT_TARGET_REPO)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--run-tag", default=DEFAULT_RUN_TAG)
    parser.add_argument(
        "--baseline-source-ref",
        default="",
        help="Git ref/commit used as the fixed starting point for every fresh run. If omitted, use .maar_baseline_ref in the target repo when present, else fall back to the original 3090 baseline commit.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_TOTAL_ROUNDS,
        help="Target total rounds for this run. On resume this is the total desired round count, not additional rounds.",
    )
    parser.add_argument("--worker-count", type=int, default=DEFAULT_WORKER_COUNT)
    parser.add_argument("--worker-model", default=DEFAULT_WORKER_MODEL)
    parser.add_argument("--coordinator-model", default=DEFAULT_COORDINATOR_MODEL)
    parser.add_argument("--disable-coordinator", action="store_true")
    parser.add_argument("--trigger-min-improvements", type=int, default=DEFAULT_TRIGGER_MIN_IMPROVEMENTS)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--agent-timeout-seconds", type=int, default=DEFAULT_AGENT_TIMEOUT_SECONDS)
    parser.add_argument("--agent-max-retries", type=int, default=DEFAULT_AGENT_MAX_RETRIES)
    parser.add_argument(
        "--train-timeout-seconds",
        type=float,
        default=DEFAULT_TRAIN_TIMEOUT_SECONDS,
        help="Per-train subprocess timeout, not per-round time. Keep this comfortably above TIME_BUDGET to cover startup and final evaluation overhead.",
    )
    parser.add_argument("--execution-slots", type=int, default=DEFAULT_EXECUTION_SLOTS)
    parser.add_argument("--runtime-pythonpath", type=Path, default=None)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Force a fresh run. Fails if the same run_tag already exists.",
    )
    parser.add_argument(
        "--new-run-tag",
        action="store_true",
        help="Ignore the default run_tag and generate a timestamped one for a fresh run.",
    )
    return parser.parse_args()


def resolve_runtime_pythonpath(target_repo: Path, requested: Path | None) -> Path | None:
    if requested is not None:
        return requested
    local_site = target_repo / ".orchestrator-site"
    if local_site.exists():
        return local_site
    return None


def resolve_baseline_source_ref(target_repo: Path, requested: str) -> str:
    value = requested.strip()
    if value:
        return value
    marker = target_repo / ".maar_baseline_ref"
    if marker.exists():
        marker_value = marker.read_text(encoding="utf-8").strip()
        if marker_value:
            return marker_value
    return DEFAULT_BASELINE_SOURCE_REF


def inspect_existing_progress(run_root: Path) -> dict[str, object]:
    run_json_path = run_root / "run.json"
    if not run_json_path.exists():
        return {
            "exists": False,
            "completed_rounds": 0,
            "status": "missing",
        }

    payload = json.loads(run_json_path.read_text(encoding="utf-8"))
    current_round = int(payload.get("current_round", 0))
    status = str(payload.get("status", "missing"))
    completed_rounds = current_round
    if status == "running" and current_round > 0:
        completed_rounds = current_round - 1
    return {
        "exists": True,
        "completed_rounds": completed_rounds,
        "status": status,
    }


def estimate_runtime(
    *,
    fresh: bool,
    remaining_rounds: int,
    worker_count: int,
    coordinator_enabled: bool,
    train_timeout_seconds: float,
) -> dict[str, float]:
    baseline_jobs = 1 if fresh else 0
    optimistic_jobs = baseline_jobs + remaining_rounds * worker_count
    likely_jobs = optimistic_jobs + (remaining_rounds if coordinator_enabled else 0)
    worst_case_jobs = baseline_jobs + remaining_rounds * (worker_count + (1 if coordinator_enabled else 0))
    return {
        "optimistic_hours": optimistic_jobs * ESTIMATED_NORMAL_TRAIN_SECONDS / 3600.0,
        "likely_hours": likely_jobs * ESTIMATED_NORMAL_TRAIN_SECONDS / 3600.0,
        "worst_case_hours": worst_case_jobs * train_timeout_seconds / 3600.0,
    }


def main() -> int:
    args = parse_args()
    load_project_env(PROJECT_ROOT)
    clear_proxy_env()

    run_tag = make_run_tag() if args.new_run_tag else args.run_tag.strip()
    if not run_tag:
        raise SystemExit("run_tag must not be empty")

    target_repo = args.target_repo.expanduser().resolve()
    artifact_root = args.artifact_root.expanduser().resolve()
    baseline_source_ref = resolve_baseline_source_ref(target_repo, args.baseline_source_ref)
    runtime_pythonpath = resolve_runtime_pythonpath(target_repo, args.runtime_pythonpath)
    if runtime_pythonpath is not None:
        os.environ["AUTORESEARCH_RUNTIME_PYTHONPATH"] = str(runtime_pythonpath.expanduser().resolve())

    coordinator_enabled = not args.disable_coordinator and DEFAULT_COORDINATOR_ENABLED
    config = RunConfig(
        run_tag=run_tag,
        worker_count=args.worker_count,
        target_repo_path=target_repo,
        artifact_root=artifact_root,
        baseline_source_ref=baseline_source_ref,
        execution_slots=args.execution_slots,
        worker_agent_backend="zhipu",
        coordinator_agent_backend="zhipu" if coordinator_enabled else "mock",
        worker_model_name=args.worker_model,
        coordinator_model_name=args.coordinator_model,
        worker_prompt_profile="maar_wide",
        coordinator_prompt_profile="coordinator",
        program_experience_seed_profile="maar_fixed_priors",
        preflight_profile="maar_strict",
        agent_timeout_seconds=args.agent_timeout_seconds,
        agent_max_retries=args.agent_max_retries,
        train_timeout_seconds=args.train_timeout_seconds,
    )

    progress = inspect_existing_progress(config.run_root)
    run_exists = bool(progress["exists"])
    if args.fresh and run_exists:
        raise SystemExit(f"run already exists for run_tag={run_tag}: {config.run_root}")

    mode = "resume" if run_exists and not args.fresh else "fresh"
    remaining_rounds = max(0, args.rounds - int(progress["completed_rounds"]))
    estimate = estimate_runtime(
        fresh=(mode == "fresh"),
        remaining_rounds=remaining_rounds,
        worker_count=args.worker_count,
        coordinator_enabled=coordinator_enabled,
        train_timeout_seconds=args.train_timeout_seconds,
    )

    config_preview = {
        "mode": mode,
        "run_tag": run_tag,
        "run_root": str(config.run_root),
        "target_repo": str(target_repo),
        "baseline_source_ref": baseline_source_ref,
        "worker_count": args.worker_count,
        "rounds_target_total": args.rounds,
        "rounds_already_completed": int(progress["completed_rounds"]),
        "rounds_remaining": remaining_rounds,
        "coordinator_enabled": coordinator_enabled,
        "worker_model": args.worker_model or "env/default",
        "coordinator_model": args.coordinator_model or "env/default",
        "agent_timeout_seconds": args.agent_timeout_seconds,
        "agent_max_retries": args.agent_max_retries,
        "train_timeout_seconds": args.train_timeout_seconds,
        "estimate_hours": estimate,
    }
    print(json.dumps(config_preview, indent=2, sort_keys=True, ensure_ascii=False))

    coordinator_config = CoordinatorConfig(
        enabled=coordinator_enabled,
        trigger_min_improvements=args.trigger_min_improvements,
        top_k=args.top_k,
    )

    try:
        if mode == "resume":
            summary = resume_multi_agent_experiment(
                config,
                total_rounds=args.rounds,
                coordinator_config=coordinator_config,
                project_root=PROJECT_ROOT,
            )
        else:
            summary = run_multi_agent_experiment(
                config,
                rounds=args.rounds,
                coordinator_config=coordinator_config,
                project_root=PROJECT_ROOT,
            )
    except KeyboardInterrupt:
        print(
            f"Interrupted. Resume with: python3 scripts/run_long_maar.py --run-tag {run_tag} --rounds {args.rounds}",
            flush=True,
        )
        return 130

    final_view = {
        "status": summary["status"],
        "run_root": summary["run_root"],
        "baseline_before": summary["baseline_before"],
        "baseline_after": summary["baseline_after"],
        "baseline_source_ref": summary["baseline_source_ref"],
        "initial_baseline_commit": summary["initial_baseline_commit"],
        "rounds_completed": summary["rounds_completed"],
        "rounds_requested": summary["rounds_requested"],
        "resumed": summary.get("resumed", False),
        "program_exp_path": summary["program_exp_path"],
    }
    print(json.dumps(final_view, indent=2, sort_keys=True, ensure_ascii=False))
    print(f"summary_path={config.run_root / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
