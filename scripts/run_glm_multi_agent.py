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

from orchestrator import CoordinatorConfig, RunConfig, clear_proxy_env, load_project_env, run_multi_agent_experiment

DEFAULT_BASELINE_SOURCE_REF = "90243dd"


def make_run_tag() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"glm-multi-agent-{stamp}"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the multi-agent GLM arm on autoresearch.")
    parser.add_argument(
        "--target-repo",
        type=Path,
        default=PROJECT_ROOT / "autoresearch-3090",
        help="Path to the clean multi-agent target repo.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=PROJECT_ROOT / "runs" / "glm-multi-agent",
        help="Parent directory for run artifacts.",
    )
    parser.add_argument("--run-tag", default="", help="Override the generated run tag.")
    parser.add_argument(
        "--baseline-source-ref",
        default="",
        help="Git ref/commit used as the fixed starting point for every fresh run. If omitted, use .maar_baseline_ref in the target repo when present.",
    )
    parser.add_argument("--rounds", type=int, default=1, help="Number of rounds to run.")
    parser.add_argument("--worker-count", type=int, default=2, help="Number of workers.")
    parser.add_argument("--model", default="", help="Override ZHIPUAI_MODEL for worker and coordinator.")
    parser.add_argument("--coordinator-model", default="", help="Override ZHIPUAI_MODEL for the coordinator only.")
    parser.add_argument("--disable-coordinator", action="store_true", help="Disable the coordinator merge path.")
    parser.add_argument(
        "--runtime-pythonpath",
        type=Path,
        default=None,
        help="Override AUTORESEARCH_RUNTIME_PYTHONPATH for this run.",
    )
    parser.add_argument("--agent-timeout-seconds", type=int, default=120, help="LLM request timeout.")
    parser.add_argument("--agent-max-retries", type=int, default=2, help="LLM retry count.")
    parser.add_argument(
        "--train-timeout-seconds",
        type=float,
        default=1500.0,
        help="Per-train timeout. Keep this comfortably above TIME_BUDGET to cover startup and final evaluation overhead.",
    )
    args = parser.parse_args()

    load_project_env(PROJECT_ROOT)
    clear_proxy_env()
    runtime_pythonpath = args.runtime_pythonpath
    if runtime_pythonpath is None:
        local_site = args.target_repo / ".orchestrator-site"
        if local_site.exists():
            runtime_pythonpath = local_site
    if runtime_pythonpath is not None:
        os.environ["AUTORESEARCH_RUNTIME_PYTHONPATH"] = str(runtime_pythonpath.resolve())

    coordinator_enabled = not args.disable_coordinator
    worker_model_name = args.model
    coordinator_model_name = args.coordinator_model or args.model
    baseline_source_ref = resolve_baseline_source_ref(args.target_repo.resolve(), args.baseline_source_ref)
    config = RunConfig(
        run_tag=args.run_tag.strip() or make_run_tag(),
        worker_count=args.worker_count,
        target_repo_path=args.target_repo,
        artifact_root=args.artifact_root,
        baseline_source_ref=baseline_source_ref,
        execution_slots=1,
        worker_agent_backend="zhipu",
        coordinator_agent_backend="zhipu" if coordinator_enabled else "mock",
        worker_model_name=worker_model_name,
        coordinator_model_name=coordinator_model_name,
        worker_prompt_profile="maar_wide",
        coordinator_prompt_profile="coordinator",
        program_experience_seed_profile="maar_fixed_priors",
        preflight_profile="maar_strict",
        agent_timeout_seconds=args.agent_timeout_seconds,
        agent_max_retries=args.agent_max_retries,
        train_timeout_seconds=args.train_timeout_seconds,
    )
    summary = run_multi_agent_experiment(
        config,
        rounds=args.rounds,
        coordinator_config=CoordinatorConfig(enabled=coordinator_enabled, trigger_min_improvements=2, top_k=2),
        project_root=PROJECT_ROOT,
    )
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    print(f"summary_path={config.run_root / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
