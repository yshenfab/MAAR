from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .plot_round_improvement import (
        _DEFAULT_OUTPUT_DIR,
        _EPSILON,
        DEFAULT_SERIES,
        SeriesSpec,
        _read_json,
        _sorted_round_paths,
        load_run_trajectory,
    )
except ImportError:
    from plot_round_improvement import (  # type: ignore
        _DEFAULT_OUTPUT_DIR,
        _EPSILON,
        DEFAULT_SERIES,
        SeriesSpec,
        _read_json,
        _sorted_round_paths,
        load_run_trajectory,
    )

_DEFAULT_OUTPUT_PATH = _DEFAULT_OUTPUT_DIR / "round_plot_data_v1.json"
_DEFAULT_FAILURE_OUTPUT_PATH = _DEFAULT_OUTPUT_DIR / "failure_count_every10rounds_v1.json"
_FAILURE_STATUSES: tuple[str, ...] = ("crash", "proposal_failed", "preflight_failed")
_OUTCOME_STATUSES: tuple[str, ...] = ("success",) + _FAILURE_STATUSES


def _optional_float(value: Any, *, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a number or null, got {value!r}")
    return float(value)


def _optional_metric_from_payload(payload: dict[str, Any], metric_name: str, *, context: str) -> float | None:
    if metric_name in payload:
        return _optional_float(payload[metric_name], context=f"{context}.{metric_name}")
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and metric_name in metrics:
        return _optional_float(metrics[metric_name], context=f"{context}.metrics.{metric_name}")
    return None


def _series_id(bench: str, label: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in label)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"{bench}__{slug.strip('_')}"


def _summarize_result(result: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "actor_id": result.get("actor_id"),
        "actor_role": result.get("actor_role"),
        "status": result.get("status"),
        "improved": result.get("improved"),
        "failure_reason": result.get("failure_reason"),
        "baseline_commit": result.get("baseline_commit"),
        "candidate_commit": result.get("candidate_commit"),
        "val_bpb": _optional_metric_from_payload(result, "val_bpb", context=source),
        "training_seconds": _optional_metric_from_payload(result, "training_seconds", context=source),
        "total_seconds": _optional_metric_from_payload(result, "total_seconds", context=source),
        "peak_vram_mb": _optional_metric_from_payload(result, "peak_vram_mb", context=source),
    }


def _collect_round_jobs(round_payload: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []

    worker_results = round_payload.get("worker_results")
    if isinstance(worker_results, list):
        for index, worker_result in enumerate(worker_results, start=1):
            if isinstance(worker_result, dict):
                jobs.append(_summarize_result(worker_result, source=f"worker_result_{index}"))

    merge_result = round_payload.get("merge_result")
    if isinstance(merge_result, dict):
        jobs.append(_summarize_result(merge_result, source="merge_result"))

    groupchat_result = round_payload.get("groupchat_result")
    if isinstance(groupchat_result, dict):
        jobs.append(_summarize_result(groupchat_result, source="groupchat_result"))

    engineer_result = round_payload.get("groupchat_engineer_result")
    if isinstance(engineer_result, dict):
        jobs.append(_summarize_result(engineer_result, source="groupchat_engineer_result"))

    return jobs


def _collect_outcome_events(round_payload: dict[str, Any], *, round_id: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def maybe_append(payload: Any, *, source: str, actor_role_key: str = "actor_role") -> None:
        if not isinstance(payload, dict):
            return
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            return
        actor_role = payload.get(actor_role_key)
        if actor_role is None and actor_role_key != "specialist_role":
            actor_role = payload.get("specialist_role")
        outcome_status = status if status in _FAILURE_STATUSES else "success"
        events.append(
            {
                "round": round_id,
                "source": source,
                "actor_id": payload.get("actor_id"),
                "actor_role": actor_role,
                "status": status,
                "outcome_status": outcome_status,
                "failure_reason": payload.get("failure_reason"),
            }
        )

    worker_results = round_payload.get("worker_results")
    if isinstance(worker_results, list):
        for index, worker_result in enumerate(worker_results, start=1):
            maybe_append(worker_result, source=f"worker_result_{index}")

    maybe_append(round_payload.get("merge_result"), source="merge_result")
    maybe_append(round_payload.get("groupchat_result"), source="groupchat_result")
    maybe_append(round_payload.get("groupchat_engineer_result"), source="groupchat_engineer_result")

    groupchat_turns = round_payload.get("groupchat_turns")
    if isinstance(groupchat_turns, list):
        for turn in groupchat_turns:
            maybe_append(turn, source=f"groupchat_turn_{turn.get('turn_index')}", actor_role_key="specialist_role")

    return events


def _sum_present(values: list[float | None]) -> tuple[float, int]:
    present = [value for value in values if value is not None]
    return sum(present), len(present)


def _maybe_add(base_value: float | None, delta: float) -> float | None:
    if base_value is None:
        return None
    return base_value + delta


def _round_floats(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, float):
        rounded = round(value, 6)
        return 0.0 if abs(rounded) <= _EPSILON else rounded
    return value


def build_series_payload(spec: SeriesSpec) -> dict[str, Any]:
    trajectory = load_run_trajectory(spec)
    run_state_path = spec.run_dir / "run.json"
    baseline_path = spec.run_dir / "baseline" / "baseline_metrics.json"
    round_paths = _sorted_round_paths(spec.run_dir)

    run_state = _read_json(run_state_path)
    baseline_payload = _read_json(baseline_path)
    baseline_measurement = {
        "val_bpb": _optional_metric_from_payload(baseline_payload, "val_bpb", context=str(baseline_path)),
        "training_seconds": _optional_metric_from_payload(baseline_payload, "training_seconds", context=str(baseline_path)),
        "total_seconds": _optional_metric_from_payload(baseline_payload, "total_seconds", context=str(baseline_path)),
        "peak_vram_mb": _optional_metric_from_payload(baseline_payload, "peak_vram_mb", context=str(baseline_path)),
        "command": baseline_payload.get("command"),
        "workspace_path": baseline_payload.get("workspace_path"),
    }

    series_id = _series_id(spec.bench, spec.label)
    round_to_best = dict(zip(trajectory.rounds, trajectory.best_val_bpb, strict=True))
    round_to_improvement = dict(zip(trajectory.rounds, trajectory.improvement, strict=True))

    cumulative_training_seconds = 0.0
    cumulative_total_seconds = 0.0
    total_job_count = 0
    jobs_with_training_time = 0
    jobs_missing_training_time = 0
    jobs_with_total_time = 0
    jobs_missing_total_time = 0
    current_best = trajectory.initial_baseline

    points = [
        {
            "round": 0,
            "baseline_before_round": None,
            "baseline_after_round": trajectory.initial_baseline,
            "best_val_bpb_so_far": trajectory.initial_baseline,
            "improvement_vs_initial": 0.0,
            "kept_this_round": False,
            "selected_result": None,
            "round_job_count": 0,
            "round_jobs_with_training_time": 0,
            "round_jobs_missing_training_time": 0,
            "round_jobs_with_total_time": 0,
            "round_jobs_missing_total_time": 0,
            "round_training_seconds_measured_jobs": 0.0,
            "round_total_seconds_measured_jobs": 0.0,
            "x": {
                "round": 0,
                "cumulative_training_seconds_measured_jobs": 0.0,
                "cumulative_total_seconds_measured_jobs": 0.0,
                "cumulative_training_seconds_including_baseline": baseline_measurement["training_seconds"],
                "cumulative_total_seconds_including_baseline": baseline_measurement["total_seconds"],
            },
            "jobs": [],
        }
    ]

    expected_rounds = trajectory.rounds[1:]
    if len(round_paths) != len(expected_rounds):
        raise ValueError(f"{spec.label}: round file count {len(round_paths)} does not match trajectory length {len(expected_rounds)}")

    for expected_round_id, round_path in zip(expected_rounds, round_paths, strict=True):
        round_payload = _read_json(round_path)
        round_id = int(round_payload["round_id"])
        if round_id != expected_round_id:
            raise ValueError(f"{spec.label}: expected round {expected_round_id}, found {round_id} in {round_path}")

        baseline_before = _optional_metric_from_payload(round_payload, "baseline_val_bpb", context=str(round_path))
        if baseline_before is not None and abs(baseline_before - current_best) > _EPSILON:
            raise ValueError(
                f"{spec.label}: round {round_id} recorded baseline {baseline_before:.6f} does not match previous best {current_best:.6f}"
            )
        baseline_before = current_best if baseline_before is None else baseline_before

        jobs = _collect_round_jobs(round_payload)
        total_job_count += len(jobs)

        training_values = [job["training_seconds"] for job in jobs]
        total_values = [job["total_seconds"] for job in jobs]
        round_training_seconds, round_jobs_with_training_time = _sum_present(training_values)
        round_total_seconds, round_jobs_with_total_time = _sum_present(total_values)
        round_jobs_missing_training_time = len(jobs) - round_jobs_with_training_time
        round_jobs_missing_total_time = len(jobs) - round_jobs_with_total_time

        jobs_with_training_time += round_jobs_with_training_time
        jobs_missing_training_time += round_jobs_missing_training_time
        jobs_with_total_time += round_jobs_with_total_time
        jobs_missing_total_time += round_jobs_missing_total_time

        cumulative_training_seconds += round_training_seconds
        cumulative_total_seconds += round_total_seconds

        selected_result = round_payload.get("selected_result")
        selected_summary = _summarize_result(selected_result, source="selected_result") if isinstance(selected_result, dict) else None
        kept_this_round = bool(isinstance(selected_result, dict) and selected_result.get("status") == "keep")
        if kept_this_round:
            selected_val_bpb = selected_summary["val_bpb"]
            if selected_val_bpb is None:
                raise ValueError(f"{spec.label}: keep round {round_id} is missing selected_result val_bpb")
            current_best = selected_val_bpb

        expected_best = round_to_best[round_id]
        if abs(current_best - expected_best) > _EPSILON:
            raise ValueError(
                f"{spec.label}: round {round_id} best {current_best:.6f} does not match trajectory best {expected_best:.6f}"
            )

        point = {
            "round": round_id,
            "baseline_before_round": baseline_before,
            "baseline_after_round": current_best,
            "best_val_bpb_so_far": expected_best,
            "improvement_vs_initial": round_to_improvement[round_id],
            "kept_this_round": kept_this_round,
            "selected_result": selected_summary,
            "round_job_count": len(jobs),
            "round_jobs_with_training_time": round_jobs_with_training_time,
            "round_jobs_missing_training_time": round_jobs_missing_training_time,
            "round_jobs_with_total_time": round_jobs_with_total_time,
            "round_jobs_missing_total_time": round_jobs_missing_total_time,
            "round_training_seconds_measured_jobs": round_training_seconds,
            "round_total_seconds_measured_jobs": round_total_seconds,
            "x": {
                "round": round_id,
                "cumulative_training_seconds_measured_jobs": cumulative_training_seconds,
                "cumulative_total_seconds_measured_jobs": cumulative_total_seconds,
                "cumulative_training_seconds_including_baseline": _maybe_add(
                    baseline_measurement["training_seconds"], cumulative_training_seconds
                ),
                "cumulative_total_seconds_including_baseline": _maybe_add(
                    baseline_measurement["total_seconds"], cumulative_total_seconds
                ),
            },
            "jobs": jobs,
        }
        points.append(point)

    summary = {
        "total_rounds": len(points) - 1,
        "keep_rounds": trajectory.keep_rounds,
        "keep_count": len(trajectory.keep_rounds),
        "total_job_count": total_job_count,
        "jobs_with_training_time": jobs_with_training_time,
        "jobs_missing_training_time": jobs_missing_training_time,
        "jobs_with_total_time": jobs_with_total_time,
        "jobs_missing_total_time": jobs_missing_total_time,
        "cumulative_training_seconds_measured_jobs": cumulative_training_seconds,
        "cumulative_total_seconds_measured_jobs": cumulative_total_seconds,
        "cumulative_training_seconds_including_baseline": _maybe_add(
            baseline_measurement["training_seconds"], cumulative_training_seconds
        ),
        "cumulative_total_seconds_including_baseline": _maybe_add(
            baseline_measurement["total_seconds"], cumulative_total_seconds
        ),
    }

    return {
        "series_id": series_id,
        "bench": spec.bench,
        "label": spec.label,
        "run_dir": str(spec.run_dir),
        "run_tag": run_state.get("run_tag"),
        "target_repo_path": run_state.get("target_repo_path"),
        "baseline_source_ref": run_state.get("baseline_source_ref"),
        "initial_baseline_commit": run_state.get("initial_baseline_commit"),
        "final_baseline_commit": run_state.get("baseline_commit"),
        "initial_baseline_val_bpb": trajectory.initial_baseline,
        "final_baseline_val_bpb": trajectory.final_baseline,
        "baseline_measurement": baseline_measurement,
        "summary": summary,
        "points": points,
    }


def build_export_payload(series_specs: list[SeriesSpec] | None = None) -> dict[str, Any]:
    specs = list(series_specs or DEFAULT_SERIES)
    series = [build_series_payload(spec) for spec in specs]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": series,
    }


def _build_bucket_template(*, bucket_index: int, start_round: int, end_round: int) -> dict[str, Any]:
    return {
        "bucket_index": bucket_index,
        "start_round": start_round,
        "end_round": end_round,
        "label": f"{start_round}-{end_round}",
        "x_round_end": end_round,
        "total_events": 0,
        "total_failures": 0,
        "counts_by_status": {status: 0 for status in _OUTCOME_STATUSES},
        "rounds_with_failures": [],
    }


def build_failure_export_payload(
    series_specs: list[SeriesSpec] | None = None,
    *,
    round_stride: int = 10,
) -> dict[str, Any]:
    if round_stride <= 0:
        raise ValueError(f"round_stride must be positive, got {round_stride}")

    specs = list(series_specs or DEFAULT_SERIES)
    payload_series = []
    for spec in specs:
        run_state_path = spec.run_dir / "run.json"
        round_paths = _sorted_round_paths(spec.run_dir)
        if not round_paths:
            raise FileNotFoundError(f"no round state files found under {spec.run_dir / 'rounds'}")

        run_state = _read_json(run_state_path)
        max_round = max(int(_read_json(path)["round_id"]) for path in round_paths)
        bucket_count = (max_round + round_stride - 1) // round_stride
        buckets = [
            _build_bucket_template(
                bucket_index=index,
                start_round=(index - 1) * round_stride + 1,
                end_round=min(index * round_stride, max_round),
            )
            for index in range(1, bucket_count + 1)
        ]

        per_round = []
        counts_by_status = {status: 0 for status in _OUTCOME_STATUSES}
        total_failures = 0
        total_events = 0

        for round_path in round_paths:
            round_payload = _read_json(round_path)
            round_id = int(round_payload["round_id"])
            outcome_events = _collect_outcome_events(round_payload, round_id=round_id)
            round_counts = {status: 0 for status in _OUTCOME_STATUSES}
            round_failures = 0
            for event in outcome_events:
                outcome_status = event["outcome_status"]
                round_counts[outcome_status] += 1
                counts_by_status[outcome_status] += 1
                total_events += 1
                if outcome_status != "success":
                    round_failures += 1
                    total_failures += 1

            per_round.append(
                {
                    "round": round_id,
                    "total_events": len(outcome_events),
                    "total_failures": round_failures,
                    "counts_by_status": round_counts,
                }
            )

            bucket = buckets[(round_id - 1) // round_stride]
            bucket["total_events"] = bucket.get("total_events", 0) + len(outcome_events)
            bucket["total_failures"] += round_failures
            if round_failures:
                bucket["rounds_with_failures"].append(round_id)
            for status, count in round_counts.items():
                bucket["counts_by_status"][status] += count

        payload_series.append(
            {
                "series_id": _series_id(spec.bench, spec.label),
                "bench": spec.bench,
                "label": spec.label,
                "run_dir": str(spec.run_dir),
                "run_tag": run_state.get("run_tag"),
                "round_stride": round_stride,
                "failure_statuses": list(_FAILURE_STATUSES),
                "outcome_statuses": list(_OUTCOME_STATUSES),
                "summary": {
                    "total_rounds": max_round,
                    "total_events": total_events,
                    "total_failures": total_failures,
                    "counts_by_status": counts_by_status,
                },
                "per_round": per_round,
                "buckets": buckets,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_stride": round_stride,
        "failure_statuses": list(_FAILURE_STATUSES),
        "outcome_statuses": list(_OUTCOME_STATUSES),
        "series": payload_series,
    }


def write_export_payload(payload: dict[str, Any], output_path: Path) -> list[Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rounded_payload = _round_floats(payload)
    output_path.write_text(json.dumps(rounded_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    written_paths = [output_path]
    series_dir = output_path.parent / f"{output_path.stem}_series"
    series_dir.mkdir(parents=True, exist_ok=True)
    for series_payload in rounded_payload["series"]:
        series_path = series_dir / f"{series_payload['series_id']}.json"
        series_path.write_text(json.dumps(series_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written_paths.append(series_path)
    return written_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export round-by-round plotting data with cumulative time axes.")
    parser.add_argument(
        "--output-path",
        type=Path,
        default=_DEFAULT_OUTPUT_PATH,
        help=f"Combined JSON output path (default: {_DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--failure-output-path",
        type=Path,
        default=_DEFAULT_FAILURE_OUTPUT_PATH,
        help=f"Failure-count JSON output path (default: {_DEFAULT_FAILURE_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--round-stride",
        type=int,
        default=10,
        help="Round bucket size for failure aggregation (default: 10)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_export_payload()
    written_paths = write_export_payload(payload, args.output_path)
    failure_payload = build_failure_export_payload(round_stride=args.round_stride)
    failure_written_paths = write_export_payload(failure_payload, args.failure_output_path)
    for series_payload in payload["series"]:
        summary = series_payload["summary"]
        print(
            f"{series_payload['bench']}\t{series_payload['label']}\t"
            f"points={len(series_payload['points'])}\t"
            f"keeps={summary['keep_count']}\t"
            f"train_s={summary['cumulative_training_seconds_measured_jobs']:.1f}\t"
            f"total_s={summary['cumulative_total_seconds_measured_jobs']:.1f}"
        )
    for series_payload in failure_payload["series"]:
        summary = series_payload["summary"]
        print(
            f"{series_payload['bench']}\t{series_payload['label']}\t"
            f"failures={summary['total_failures']}\t"
            f"breakdown={summary['counts_by_status']}"
        )
    print(
        f"wrote {len(written_paths) + len(failure_written_paths)} json files under "
        f"{args.output_path.parent}"
    )


if __name__ == "__main__":
    main()
