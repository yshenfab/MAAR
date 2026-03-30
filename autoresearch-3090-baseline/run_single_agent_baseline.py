from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_REPO = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import RunConfig, clear_proxy_env, load_project_env, run_single_agent_baseline


def make_run_tag() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"autoresearch-3090-baseline-{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the single-agent GLM baseline arm from this repo.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of rounds to run.")
    parser.add_argument("--run-tag", default="", help="Override the generated run tag.")
    parser.add_argument("--model", default="", help="Override ZHIPUAI_MODEL for this run.")
    parser.add_argument("--agent-timeout-seconds", type=int, default=120, help="LLM request timeout.")
    parser.add_argument("--agent-max-retries", type=int, default=2, help="LLM retry count.")
    parser.add_argument("--train-timeout-seconds", type=float, default=900.0, help="Per-train timeout.")
    args = parser.parse_args()

    load_project_env(PROJECT_ROOT)
    clear_proxy_env()
    local_site = TARGET_REPO / ".orchestrator-site"
    if local_site.exists():
        os.environ["AUTORESEARCH_RUNTIME_PYTHONPATH"] = str(local_site.resolve())

    config = RunConfig(
        run_tag=args.run_tag.strip() or make_run_tag(),
        worker_count=1,
        target_repo_path=TARGET_REPO,
        artifact_root=PROJECT_ROOT / "runs" / "autoresearch-3090-baseline",
        execution_slots=1,
        worker_agent_backend="zhipu",
        coordinator_agent_backend="mock",
        worker_model_name=args.model,
        worker_prompt_profile="autoresearch_original",
        preflight_profile="baseline_legacy",
        agent_timeout_seconds=args.agent_timeout_seconds,
        agent_max_retries=args.agent_max_retries,
        train_timeout_seconds=args.train_timeout_seconds,
    )
    summary = run_single_agent_baseline(config, rounds=args.rounds, project_root=PROJECT_ROOT)
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    print(f"summary_path={config.run_root / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
