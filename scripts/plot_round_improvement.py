from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EPSILON = 1e-9
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "results" / "round_improvement_v1"
_DEFAULT_EXPORT_JSON = _DEFAULT_OUTPUT_DIR / "round_plot_data_v1.json"
_DEFAULT_FAILURE_JSON = _DEFAULT_OUTPUT_DIR / "failure_count_every10rounds_v1.json"
_BENCH_TITLES = {
    "bench300": "bench300",
    "bench600": "bench600",
}
_SERIES_COLORS = {
    "Single-Agent Baseline": "#4C78A8",
    "MAAR": "#F58518",
    "Agent Groupchat": "#54A24B",
}
_FAILURE_STATUS_COLORS = {
    "success": "#54A24B",
    "crash": "#E45756",
    "proposal_failed": "#4C78A8",
    "preflight_failed": "#F2CF5B",
}


@dataclass(frozen=True)
class SeriesSpec:
    bench: str
    label: str
    run_dir: Path


@dataclass(frozen=True)
class RunTrajectory:
    bench: str
    label: str
    run_dir: Path
    initial_baseline: float
    final_baseline: float
    rounds: list[int]
    best_val_bpb: list[float]
    improvement: list[float]
    keep_rounds: list[int]
    keep_best_val_bpb: list[float]
    keep_improvement: list[float]


@dataclass(frozen=True)
class SampledNormalizedSeries:
    bench: str
    label: str
    rounds: list[int]
    normalized_improvement: list[float]


@dataclass(frozen=True)
class ComputeNormalizedSeries:
    bench: str
    label: str
    training_hours: list[float]
    normalized_improvement: list[float]
    keep_training_hours: list[float]
    keep_normalized_improvement: list[float]


@dataclass(frozen=True)
class BucketedFailureSeries:
    bench: str
    label: str
    round_end_ticks: list[int]
    bucket_labels: list[str]
    failure_counts: list[int]


@dataclass(frozen=True)
class BucketedFailureBreakdownSeries:
    bench: str
    label: str
    bucket_labels: list[str]
    counts_by_status: dict[str, list[int]]


DEFAULT_SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        bench="bench300",
        label="Single-Agent Baseline",
        run_dir=_REPO_ROOT / "runs" / "glm-single-baseline" / "glm-single-baseline-bench300-r50-glm46v-20260323",
    ),
    SeriesSpec(
        bench="bench300",
        label="MAAR",
        run_dir=_REPO_ROOT / "runs" / "glm-multi-agent" / "glm-multi-agent-bench300-r50-glm46v-v2",
    ),
    SeriesSpec(
        bench="bench300",
        label="Agent Groupchat",
        run_dir=_REPO_ROOT / "runs" / "agent-groupchat" / "agent-groupchat-bench300-r50-20260326",
    ),
    SeriesSpec(
        bench="bench600",
        label="Single-Agent Baseline",
        run_dir=_REPO_ROOT / "runs" / "glm-single-baseline" / "glm-single-baseline-bench600-r50-glm46v-20260323",
    ),
    SeriesSpec(
        bench="bench600",
        label="MAAR",
        run_dir=_REPO_ROOT / "runs" / "glm-multi-agent" / "glm-multi-agent-bench600-r50-glm46v-20260324",
    ),
    SeriesSpec(
        bench="bench600",
        label="Agent Groupchat",
        run_dir=_REPO_ROOT / "runs" / "agent-groupchat" / "agent-groupchat-bench600-r50-20260327",
    ),
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_float(value: Any, *, context: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{context} must be a number, got {value!r}")


def _metric_from_payload(payload: dict[str, Any], metric_name: str, *, context: str) -> float:
    if metric_name in payload:
        return _require_float(payload[metric_name], context=f"{context}.{metric_name}")
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and metric_name in metrics:
        return _require_float(metrics[metric_name], context=f"{context}.metrics.{metric_name}")
    raise ValueError(f"missing {metric_name} in {context}")


def _selected_keep_val_bpb(round_payload: dict[str, Any]) -> float | None:
    selected = round_payload.get("selected_result")
    if not isinstance(selected, dict):
        return None
    if selected.get("status") != "keep":
        return None
    return _metric_from_payload(selected, "val_bpb", context="selected_result")


def _sorted_round_paths(run_dir: Path) -> list[Path]:
    return sorted(run_dir.joinpath("rounds").glob("round-*/round.json"))


def load_run_trajectory(spec: SeriesSpec) -> RunTrajectory:
    baseline_path = spec.run_dir / "baseline" / "baseline_metrics.json"
    run_state_path = spec.run_dir / "run.json"
    round_paths = _sorted_round_paths(spec.run_dir)

    if not baseline_path.exists():
        raise FileNotFoundError(f"missing baseline metrics: {baseline_path}")
    if not run_state_path.exists():
        raise FileNotFoundError(f"missing run state: {run_state_path}")
    if not round_paths:
        raise FileNotFoundError(f"no round state files found under {spec.run_dir / 'rounds'}")

    baseline_payload = _read_json(baseline_path)
    run_state = _read_json(run_state_path)
    initial_baseline = _metric_from_payload(baseline_payload, "val_bpb", context=str(baseline_path))
    final_baseline = _metric_from_payload(run_state, "baseline_val_bpb", context=str(run_state_path))

    rounds = [0]
    best_val_bpb = [initial_baseline]
    improvement = [0.0]
    keep_rounds: list[int] = []
    keep_best_val_bpb: list[float] = []
    keep_improvement: list[float] = []
    expected_keep_count = 0

    current_best = initial_baseline
    last_round_id = 0
    for round_path in round_paths:
        round_payload = _read_json(round_path)
        round_id = int(round_payload["round_id"])
        if round_id <= last_round_id:
            raise ValueError(f"round ids must be strictly increasing in {spec.run_dir}")
        last_round_id = round_id

        kept_val_bpb = _selected_keep_val_bpb(round_payload)
        if kept_val_bpb is not None:
            expected_keep_count += 1
            current_best = kept_val_bpb
            keep_rounds.append(round_id)
            keep_best_val_bpb.append(current_best)
            keep_improvement.append(initial_baseline - current_best)

        rounds.append(round_id)
        best_val_bpb.append(current_best)
        improvement.append(initial_baseline - current_best)

    trajectory = RunTrajectory(
        bench=spec.bench,
        label=spec.label,
        run_dir=spec.run_dir,
        initial_baseline=initial_baseline,
        final_baseline=final_baseline,
        rounds=rounds,
        best_val_bpb=best_val_bpb,
        improvement=improvement,
        keep_rounds=keep_rounds,
        keep_best_val_bpb=keep_best_val_bpb,
        keep_improvement=keep_improvement,
    )
    validate_run_trajectory(trajectory, expected_keep_count=expected_keep_count)
    return trajectory


def validate_run_trajectory(trajectory: RunTrajectory, *, expected_keep_count: int | None = None) -> None:
    if not trajectory.rounds:
        raise ValueError(f"{trajectory.label}: rounds must not be empty")
    if trajectory.rounds[0] != 0:
        raise ValueError(f"{trajectory.label}: round 0 is required")
    if abs(trajectory.improvement[0]) > _EPSILON:
        raise ValueError(f"{trajectory.label}: round 0 improvement must be exactly 0")
    if len(trajectory.rounds) != len(trajectory.best_val_bpb) or len(trajectory.rounds) != len(trajectory.improvement):
        raise ValueError(f"{trajectory.label}: round/value arrays must have equal length")
    if len(trajectory.keep_rounds) != len(trajectory.keep_best_val_bpb) or len(trajectory.keep_rounds) != len(trajectory.keep_improvement):
        raise ValueError(f"{trajectory.label}: keep marker arrays must have equal length")
    if expected_keep_count is not None and len(trajectory.keep_rounds) != expected_keep_count:
        raise ValueError(
            f"{trajectory.label}: keep marker count {len(trajectory.keep_rounds)} does not match expected {expected_keep_count}"
        )

    keep_round_set = set(trajectory.keep_rounds)
    if len(keep_round_set) != len(trajectory.keep_rounds):
        raise ValueError(f"{trajectory.label}: keep rounds must be unique")

    for idx, best_val_bpb in enumerate(trajectory.best_val_bpb):
        expected_improvement = trajectory.initial_baseline - best_val_bpb
        if abs(expected_improvement - trajectory.improvement[idx]) > _EPSILON:
            raise ValueError(f"{trajectory.label}: improvement must equal initial_baseline - best_val_bpb at every round")

    for idx in range(1, len(trajectory.rounds)):
        prev_round = trajectory.rounds[idx - 1]
        current_round = trajectory.rounds[idx]
        if current_round <= prev_round:
            raise ValueError(f"{trajectory.label}: rounds must be strictly increasing")

        prev_best = trajectory.best_val_bpb[idx - 1]
        current_best = trajectory.best_val_bpb[idx]
        prev_improvement = trajectory.improvement[idx - 1]
        current_improvement = trajectory.improvement[idx]

        if current_improvement + _EPSILON < prev_improvement:
            raise ValueError(f"{trajectory.label}: improvement must be monotonic non-decreasing")

        if current_round in keep_round_set:
            if current_best >= prev_best - _EPSILON:
                raise ValueError(f"{trajectory.label}: keep round {current_round} must strictly improve best val_bpb")
        elif abs(current_best - prev_best) > _EPSILON:
            raise ValueError(f"{trajectory.label}: non-keep round {current_round} must preserve the previous best val_bpb")

    if abs(trajectory.best_val_bpb[-1] - trajectory.final_baseline) > _EPSILON:
        raise ValueError(
            f"{trajectory.label}: final plotted best {trajectory.best_val_bpb[-1]:.6f} does not match run.json "
            f"baseline {trajectory.final_baseline:.6f}"
        )


def _group_by_bench(trajectories: list[RunTrajectory]) -> dict[str, list[RunTrajectory]]:
    grouped: dict[str, list[RunTrajectory]] = {}
    for trajectory in trajectories:
        grouped.setdefault(trajectory.bench, []).append(trajectory)
    for bench in grouped:
        grouped[bench].sort(key=lambda item: item.label)
    return grouped


def _ordered_method_labels(labels: list[str]) -> list[str]:
    present = set(labels)
    ordered = [label for label in _SERIES_COLORS if label in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _render_named_color_legend(
    labels: list[str],
    color_lookup: dict[str, str],
    output_stem: Path,
    *,
    ncol: int | None = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    ordered_labels = labels
    if not ordered_labels:
        raise ValueError("legend labels must not be empty")

    fig_width = max(4.2, 2.5 * len(ordered_labels))
    fig, ax = plt.subplots(figsize=(fig_width, 1.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")

    handles = [
        Line2D(
            [0],
            [0],
            color=color_lookup.get(label, "#444444"),
            linewidth=2.4,
            marker="o",
            markersize=5.6,
            label=label,
        )
        for label in ordered_labels
    ]
    ax.legend(
        handles=handles,
        labels=ordered_labels,
        loc="center",
        ncol=ncol or min(len(ordered_labels), 3),
        frameon=False,
        handlelength=2.4,
        columnspacing=1.6,
        handletextpad=0.6,
        fontsize=10,
    )
    fig.tight_layout(pad=0.1)

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight", facecolor="white", transparent=False)
    plt.close(fig)


def _render_method_legend(labels: list[str], output_stem: Path) -> None:
    _render_named_color_legend(_ordered_method_labels(labels), _SERIES_COLORS, output_stem)


def _ordered_failure_statuses(statuses: list[str]) -> list[str]:
    present = set(statuses)
    ordered = [status for status in _FAILURE_STATUS_COLORS if status in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _render_failure_status_legend(statuses: list[str], output_stem: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    ordered_statuses = _ordered_failure_statuses(statuses)
    if not ordered_statuses:
        raise ValueError("failure status labels must not be empty")

    fig_width = max(4.2, 2.1 * len(ordered_statuses))
    fig, ax = plt.subplots(figsize=(fig_width, 1.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")

    handles = [
        Patch(
            facecolor=_FAILURE_STATUS_COLORS.get(status, "#444444"),
            edgecolor="none",
            label=status,
        )
        for status in ordered_statuses
    ]
    ax.legend(
        handles=handles,
        labels=ordered_statuses,
        loc="center",
        ncol=min(len(ordered_statuses), 4),
        frameon=False,
        columnspacing=1.4,
        handletextpad=0.5,
        fontsize=10,
    )
    fig.tight_layout(pad=0.1)

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight", facecolor="white", transparent=False)
    plt.close(fig)


def _render_figure(
    trajectories: list[RunTrajectory],
    *,
    metric: str,
    ylabel: str,
    title: str,
    output_stem: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    grouped = _group_by_bench(trajectories)
    bench_order = [bench for bench in ("bench300", "bench600") if bench in grouped]
    if not bench_order:
        raise ValueError("no trajectories available to plot")

    max_round = max(max(item.rounds) for item in trajectories)
    fig, axes = plt.subplots(1, len(bench_order), figsize=(13, 4.8))
    if len(bench_order) == 1:
        axes = [axes]

    for ax, bench in zip(axes, bench_order):
        bench_series = grouped[bench]
        for series in bench_series:
            color = _SERIES_COLORS.get(series.label, None)
            y_values = series.improvement if metric == "improvement" else series.best_val_bpb
            keep_y_values = series.keep_improvement if metric == "improvement" else series.keep_best_val_bpb
            ax.step(series.rounds, y_values, where="post", color=color, linewidth=2.4)
            if series.keep_rounds:
                ax.scatter(series.keep_rounds, keep_y_values, color=color, s=34, zorder=3)

        ax.set_title(_BENCH_TITLES.get(bench, bench))
        ax.set_xlim(0, max_round + 2)
        ax.set_xticks([tick for tick in range(0, max_round + 1, 10)])
        ax.set_xlabel("Round")
        ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.45)

    axes[0].set_ylabel(ylabel)
    fig.suptitle(title, fontsize=13)
    fig.text(0.5, 0.01, "Filled circles mark rounds that advanced the baseline.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight")
    plt.close(fig)


def _checkpoint_rounds(max_round: int, *, round_stride: int) -> list[int]:
    if round_stride <= 0:
        raise ValueError(f"round_stride must be positive, got {round_stride}")
    rounds = list(range(0, max_round + 1, round_stride))
    if not rounds or rounds[-1] != max_round:
        rounds.append(max_round)
    return rounds


def load_sampled_normalized_series(export_json_path: Path, *, round_stride: int = 10) -> list[SampledNormalizedSeries]:
    export_payload = _read_json(export_json_path)
    series_payloads = export_payload.get("series")
    if not isinstance(series_payloads, list) or not series_payloads:
        raise ValueError(f"{export_json_path} must contain a non-empty 'series' list")

    sampled_series: list[SampledNormalizedSeries] = []
    for index, payload in enumerate(series_payloads):
        if not isinstance(payload, dict):
            raise ValueError(f"{export_json_path}: series[{index}] must be an object")

        bench = payload.get("bench")
        label = payload.get("label")
        if not isinstance(bench, str) or not isinstance(label, str):
            raise ValueError(f"{export_json_path}: series[{index}] is missing bench/label")

        initial_baseline = _require_float(
            payload.get("initial_baseline_val_bpb"),
            context=f"{export_json_path}.series[{index}].initial_baseline_val_bpb",
        )
        if initial_baseline <= 0:
            raise ValueError(f"{label}: initial_baseline_val_bpb must be positive")

        points_payload = payload.get("points")
        if not isinstance(points_payload, list) or not points_payload:
            raise ValueError(f"{label}: points must be a non-empty list")

        points_by_round: dict[int, dict[str, Any]] = {}
        for point_index, point in enumerate(points_payload):
            if not isinstance(point, dict):
                raise ValueError(f"{label}: point {point_index} must be an object")
            round_id = int(point["round"])
            if round_id in points_by_round:
                raise ValueError(f"{label}: duplicate round {round_id} in export json")
            points_by_round[round_id] = point

        sampled_rounds = _checkpoint_rounds(max(points_by_round), round_stride=round_stride)
        normalized_improvement: list[float] = []
        for round_id in sampled_rounds:
            point = points_by_round.get(round_id)
            if point is None:
                raise ValueError(f"{label}: missing round {round_id} required by round_stride={round_stride}")
            improvement = _require_float(
                point.get("improvement_vs_initial"),
                context=f"{export_json_path}.series[{index}].points[{round_id}].improvement_vs_initial",
            )
            normalized_improvement.append(improvement / initial_baseline)

        sampled_series.append(
            SampledNormalizedSeries(
                bench=bench,
                label=label,
                rounds=sampled_rounds,
                normalized_improvement=normalized_improvement,
            )
        )
    return sampled_series


def _group_sampled_by_bench(series_items: list[SampledNormalizedSeries]) -> dict[str, list[SampledNormalizedSeries]]:
    grouped: dict[str, list[SampledNormalizedSeries]] = {}
    for item in series_items:
        grouped.setdefault(item.bench, []).append(item)
    for bench in grouped:
        grouped[bench].sort(key=lambda item: item.label)
    return grouped


def _render_sampled_normalized_figure(
    series_items: list[SampledNormalizedSeries],
    *,
    ylabel: str,
    title: str,
    output_stem: Path,
    round_stride: int,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    grouped = _group_sampled_by_bench(series_items)
    bench_order = [bench for bench in ("bench300", "bench600") if bench in grouped]
    if not bench_order:
        raise ValueError("no sampled series available to plot")

    max_round = max(max(item.rounds) for item in series_items)
    fig, axes = plt.subplots(1, len(bench_order), figsize=(13, 4.8))
    if len(bench_order) == 1:
        axes = [axes]

    for ax, bench in zip(axes, bench_order):
        bench_series = grouped[bench]
        x_ticks = sorted({round_id for series in bench_series for round_id in series.rounds})
        max_y = max(max(series.normalized_improvement) for series in bench_series)

        for series in bench_series:
            color = _SERIES_COLORS.get(series.label, None)
            ax.plot(
                series.rounds,
                series.normalized_improvement,
                color=color,
                linewidth=2.4,
                marker="o",
                markersize=5.6,
            )

        ax.set_title(_BENCH_TITLES.get(bench, bench))
        ax.set_xlim(0, max_round + 2)
        ax.set_xticks(x_ticks)
        ax.set_xlabel(f"Round (every {round_stride} rounds)")
        ax.set_ylim(0, max_y * 1.18 if max_y > 0 else 0.01)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
        ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.45)

    axes[0].set_ylabel(ylabel)
    fig.suptitle(title, fontsize=13)
    fig.text(
        0.5,
        0.01,
        f"Each point shows best-so-far improvement / initial baseline at {round_stride}-round checkpoints.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight")
    plt.close(fig)


def load_compute_normalized_series(
    export_json_path: Path,
    *,
    x_key: str = "cumulative_training_seconds_measured_jobs",
    seconds_per_hour: float = 3600.0,
) -> list[ComputeNormalizedSeries]:
    if seconds_per_hour <= 0:
        raise ValueError(f"seconds_per_hour must be positive, got {seconds_per_hour}")

    export_payload = _read_json(export_json_path)
    series_payloads = export_payload.get("series")
    if not isinstance(series_payloads, list) or not series_payloads:
        raise ValueError(f"{export_json_path} must contain a non-empty 'series' list")

    compute_series: list[ComputeNormalizedSeries] = []
    for index, payload in enumerate(series_payloads):
        if not isinstance(payload, dict):
            raise ValueError(f"{export_json_path}: series[{index}] must be an object")

        bench = payload.get("bench")
        label = payload.get("label")
        if not isinstance(bench, str) or not isinstance(label, str):
            raise ValueError(f"{export_json_path}: series[{index}] is missing bench/label")

        initial_baseline = _require_float(
            payload.get("initial_baseline_val_bpb"),
            context=f"{export_json_path}.series[{index}].initial_baseline_val_bpb",
        )
        if initial_baseline <= 0:
            raise ValueError(f"{label}: initial_baseline_val_bpb must be positive")

        points_payload = payload.get("points")
        if not isinstance(points_payload, list) or not points_payload:
            raise ValueError(f"{label}: points must be a non-empty list")

        training_hours: list[float] = []
        normalized_improvement: list[float] = []
        keep_training_hours: list[float] = []
        keep_normalized_improvement: list[float] = []

        last_training_hours = -1.0
        for point_index, point in enumerate(points_payload):
            if not isinstance(point, dict):
                raise ValueError(f"{label}: point {point_index} must be an object")

            x_payload = point.get("x")
            if not isinstance(x_payload, dict):
                raise ValueError(f"{label}: point {point_index} is missing x payload")

            cumulative_training_seconds = _require_float(
                x_payload.get(x_key),
                context=f"{export_json_path}.series[{index}].points[{point_index}].x.{x_key}",
            )
            training_hours_value = cumulative_training_seconds / seconds_per_hour
            if training_hours_value + _EPSILON < last_training_hours:
                raise ValueError(f"{label}: compute-axis x values must be monotonic non-decreasing")
            last_training_hours = training_hours_value

            improvement = _require_float(
                point.get("improvement_vs_initial"),
                context=f"{export_json_path}.series[{index}].points[{point_index}].improvement_vs_initial",
            )
            normalized_value = improvement / initial_baseline

            training_hours.append(training_hours_value)
            normalized_improvement.append(normalized_value)

            if point.get("kept_this_round"):
                keep_training_hours.append(training_hours_value)
                keep_normalized_improvement.append(normalized_value)

        compute_series.append(
            ComputeNormalizedSeries(
                bench=bench,
                label=label,
                training_hours=training_hours,
                normalized_improvement=normalized_improvement,
                keep_training_hours=keep_training_hours,
                keep_normalized_improvement=keep_normalized_improvement,
            )
        )

    return compute_series


def _group_compute_by_bench(series_items: list[ComputeNormalizedSeries]) -> dict[str, list[ComputeNormalizedSeries]]:
    grouped: dict[str, list[ComputeNormalizedSeries]] = {}
    for item in series_items:
        grouped.setdefault(item.bench, []).append(item)
    for bench in grouped:
        grouped[bench].sort(key=lambda item: item.label)
    return grouped


def _render_compute_normalized_figure(
    series_items: list[ComputeNormalizedSeries],
    *,
    ylabel: str,
    title: str,
    output_stem: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    grouped = _group_compute_by_bench(series_items)
    bench_order = [bench for bench in ("bench300", "bench600") if bench in grouped]
    if not bench_order:
        raise ValueError("no compute-axis series available to plot")

    fig, axes = plt.subplots(1, len(bench_order), figsize=(13, 4.8))
    if len(bench_order) == 1:
        axes = [axes]

    for ax, bench in zip(axes, bench_order):
        bench_series = grouped[bench]
        max_x = max(max(series.training_hours) for series in bench_series)
        max_y = max(max(series.normalized_improvement) for series in bench_series)

        for series in bench_series:
            color = _SERIES_COLORS.get(series.label, None)
            ax.plot(
                series.training_hours,
                series.normalized_improvement,
                color=color,
                linewidth=2.2,
            )
            if series.keep_training_hours:
                ax.scatter(
                    series.keep_training_hours,
                    series.keep_normalized_improvement,
                    color=color,
                    s=34,
                    zorder=3,
                )

        ax.set_title(_BENCH_TITLES.get(bench, bench))
        ax.set_xlim(0, max_x * 1.08 if max_x > 0 else 1.0)
        ax.set_xlabel("Cumulative Training Time (GPU hours)")
        ax.set_ylim(0, max_y * 1.18 if max_y > 0 else 0.01)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
        ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.45)

    axes[0].set_ylabel(ylabel)
    fig.suptitle(title, fontsize=13)
    fig.text(
        0.5,
        0.01,
        "X-axis sums measured training_seconds across evaluated jobs; baseline measurement excluded.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight")
    plt.close(fig)


def load_bucketed_failure_series(failure_json_path: Path) -> list[BucketedFailureSeries]:
    failure_payload = _read_json(failure_json_path)
    series_payloads = failure_payload.get("series")
    if not isinstance(series_payloads, list) or not series_payloads:
        raise ValueError(f"{failure_json_path} must contain a non-empty 'series' list")

    bucketed_series: list[BucketedFailureSeries] = []
    for index, payload in enumerate(series_payloads):
        if not isinstance(payload, dict):
            raise ValueError(f"{failure_json_path}: series[{index}] must be an object")

        bench = payload.get("bench")
        label = payload.get("label")
        if not isinstance(bench, str) or not isinstance(label, str):
            raise ValueError(f"{failure_json_path}: series[{index}] is missing bench/label")

        buckets = payload.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            raise ValueError(f"{label}: buckets must be a non-empty list")

        round_end_ticks: list[int] = []
        bucket_labels: list[str] = []
        failure_counts: list[int] = []
        for bucket_index, bucket in enumerate(buckets):
            if not isinstance(bucket, dict):
                raise ValueError(f"{label}: bucket {bucket_index} must be an object")
            round_end_ticks.append(int(bucket["x_round_end"]))
            bucket_labels.append(str(bucket["label"]))
            failure_counts.append(int(bucket["total_failures"]))

        bucketed_series.append(
            BucketedFailureSeries(
                bench=bench,
                label=label,
                round_end_ticks=round_end_ticks,
                bucket_labels=bucket_labels,
                failure_counts=failure_counts,
            )
        )

    return bucketed_series


def load_bucketed_failure_breakdown_series(
    failure_json_path: Path,
) -> tuple[list[BucketedFailureBreakdownSeries], list[str]]:
    failure_payload = _read_json(failure_json_path)
    series_payloads = failure_payload.get("series")
    failure_statuses = failure_payload.get("outcome_statuses") or failure_payload.get("failure_statuses")
    if not isinstance(series_payloads, list) or not series_payloads:
        raise ValueError(f"{failure_json_path} must contain a non-empty 'series' list")
    if not isinstance(failure_statuses, list) or not failure_statuses:
        raise ValueError(f"{failure_json_path} must contain a non-empty 'failure_statuses' list")

    ordered_statuses = _ordered_failure_statuses([str(item) for item in failure_statuses])
    breakdown_series: list[BucketedFailureBreakdownSeries] = []

    for index, payload in enumerate(series_payloads):
        if not isinstance(payload, dict):
            raise ValueError(f"{failure_json_path}: series[{index}] must be an object")

        bench = payload.get("bench")
        label = payload.get("label")
        if not isinstance(bench, str) or not isinstance(label, str):
            raise ValueError(f"{failure_json_path}: series[{index}] is missing bench/label")

        buckets = payload.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            raise ValueError(f"{label}: buckets must be a non-empty list")

        bucket_labels: list[str] = []
        counts_by_status = {status: [] for status in ordered_statuses}
        for bucket_index, bucket in enumerate(buckets):
            if not isinstance(bucket, dict):
                raise ValueError(f"{label}: bucket {bucket_index} must be an object")
            bucket_labels.append(str(bucket["label"]))
            status_counts = bucket.get("counts_by_status")
            if not isinstance(status_counts, dict):
                raise ValueError(f"{label}: bucket {bucket_index} is missing counts_by_status")
            for status in ordered_statuses:
                counts_by_status[status].append(int(status_counts.get(status, 0)))

        breakdown_series.append(
            BucketedFailureBreakdownSeries(
                bench=bench,
                label=label,
                bucket_labels=bucket_labels,
                counts_by_status=counts_by_status,
            )
        )

    return breakdown_series, ordered_statuses


def _group_failure_by_bench(series_items: list[BucketedFailureSeries]) -> dict[str, list[BucketedFailureSeries]]:
    grouped: dict[str, list[BucketedFailureSeries]] = {}
    for item in series_items:
        grouped.setdefault(item.bench, []).append(item)
    for bench in grouped:
        grouped[bench].sort(key=lambda item: item.label)
    return grouped


def _render_failure_figure(
    series_items: list[BucketedFailureSeries],
    *,
    ylabel: str,
    title: str,
    output_stem: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    grouped = _group_failure_by_bench(series_items)
    bench_order = [bench for bench in ("bench300", "bench600") if bench in grouped]
    if not bench_order:
        raise ValueError("no failure series available to plot")

    fig, axes = plt.subplots(1, len(bench_order), figsize=(13, 4.8))
    if len(bench_order) == 1:
        axes = [axes]

    for ax, bench in zip(axes, bench_order):
        bench_series = grouped[bench]
        tick_positions = bench_series[0].round_end_ticks
        tick_labels = bench_series[0].bucket_labels
        max_y = max(max(series.failure_counts) for series in bench_series)

        for series in bench_series:
            color = _SERIES_COLORS.get(series.label, None)
            ax.plot(
                series.round_end_ticks,
                series.failure_counts,
                color=color,
                linewidth=2.2,
                marker="o",
                markersize=5.6,
            )

        ax.set_title(_BENCH_TITLES.get(bench, bench))
        ax.set_xticks(tick_positions, tick_labels)
        ax.set_xlabel("Round Bucket")
        ax.set_ylim(0, max_y + 0.6 if max_y > 0 else 1.0)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.45)

    axes[0].set_ylabel(ylabel)
    fig.suptitle(title, fontsize=13)
    fig.text(
        0.5,
        0.01,
        "Failure count includes crash, proposal_failed, and preflight_failed events in each 10-round bucket.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight")
    plt.close(fig)


def _render_failure_breakdown_figure(
    series_items: list[BucketedFailureBreakdownSeries],
    *,
    failure_statuses: list[str],
    title: str,
    output_stem: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import PercentFormatter

    grouped: dict[str, dict[str, BucketedFailureBreakdownSeries]] = {}
    for item in series_items:
        grouped.setdefault(item.bench, {})[item.label] = item

    bench_order = [bench for bench in ("bench300", "bench600") if bench in grouped]
    method_order = _ordered_method_labels([item.label for item in series_items])
    if not bench_order or not method_order:
        raise ValueError("no failure breakdown series available to plot")

    fig, axes = plt.subplots(
        len(bench_order),
        len(method_order),
        figsize=(15, 6.8),
        sharex=True,
        sharey=True,
    )
    axes_array = np.atleast_2d(axes)

    global_max = 0
    for item in series_items:
        totals = [sum(item.counts_by_status[status][idx] for status in failure_statuses) for idx in range(len(item.bucket_labels))]
        global_max = max(global_max, max(totals, default=0))

    for row_index, bench in enumerate(bench_order):
        for col_index, label in enumerate(method_order):
            ax = axes_array[row_index, col_index]
            series = grouped[bench].get(label)
            if series is None:
                ax.axis("off")
                continue

            x_positions = np.arange(len(series.bucket_labels))
            bottoms = np.zeros(len(series.bucket_labels))
            totals = np.sum(
                np.array([series.counts_by_status[status] for status in failure_statuses], dtype=float),
                axis=0,
            )
            for status in failure_statuses:
                raw_values = np.array(series.counts_by_status[status], dtype=float)
                values = np.divide(raw_values, totals, out=np.zeros_like(raw_values), where=totals > 0)
                ax.bar(
                    x_positions,
                    values,
                    bottom=bottoms,
                    color=_FAILURE_STATUS_COLORS.get(status, "#777777"),
                    width=0.72,
                )
                bottoms += values

            ax.set_title(f"{_BENCH_TITLES.get(bench, bench)} / {label}", fontsize=10)
            ax.set_xticks(x_positions, series.bucket_labels, rotation=0)
            ax.set_ylim(0, 1.0)
            ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
            ax.grid(True, axis="y", linestyle=":", linewidth=0.8, alpha=0.45)

            if row_index == len(bench_order) - 1:
                ax.set_xlabel("Round Bucket")
            if col_index == 0:
                ax.set_ylabel("Outcome Share")

    fig.suptitle(title, fontsize=13)
    fig.text(
        0.5,
        0.01,
        "Success is any counted event whose status is not crash, proposal_failed, or preflight_failed.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf"):
        fig.savefig(output_stem.with_suffix(suffix), dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_summary(trajectories: list[RunTrajectory], output_path: Path) -> None:
    payload = []
    for trajectory in trajectories:
        payload.append(
            {
                "bench": trajectory.bench,
                "label": trajectory.label,
                "run_dir": str(trajectory.run_dir),
                "initial_baseline_val_bpb": trajectory.initial_baseline,
                "final_baseline_val_bpb": trajectory.final_baseline,
                "final_improvement": trajectory.improvement[-1],
                "keep_rounds": trajectory.keep_rounds,
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_figures(output_dir: Path, series_specs: list[SeriesSpec] | None = None) -> list[RunTrajectory]:
    specs = list(series_specs or DEFAULT_SERIES)
    trajectories = [load_run_trajectory(spec) for spec in specs]

    _render_figure(
        trajectories,
        metric="improvement",
        ylabel="Improvement in val_bpb (higher is better)",
        title="Fig 1. Best-so-far Improvement vs Round",
        output_stem=output_dir / "fig1_round_improvement_v1",
    )
    _render_figure(
        trajectories,
        metric="best_val_bpb",
        ylabel="Best-so-far val_bpb (lower is better)",
        title="Appendix Fig A1. Best-so-far Raw val_bpb vs Round",
        output_stem=output_dir / "appendix_fig_a1_best_val_bpb_v1",
    )
    write_summary(trajectories, output_dir / "round_improvement_summary.json")
    _render_method_legend([trajectory.label for trajectory in trajectories], output_dir / "legend_methods_v1")
    return trajectories


def build_normalized_checkpoint_figure(
    output_dir: Path,
    export_json_path: Path,
    *,
    round_stride: int = 10,
) -> list[SampledNormalizedSeries]:
    sampled_series = load_sampled_normalized_series(export_json_path, round_stride=round_stride)
    _render_sampled_normalized_figure(
        sampled_series,
        ylabel="Improvement / Initial Baseline",
        title=f"Fig 1B. Normalized Improvement vs Round ({round_stride}-Round Nodes)",
        output_stem=output_dir / f"fig1b_normalized_improvement_every{round_stride}rounds_v1",
        round_stride=round_stride,
    )
    return sampled_series


def build_compute_axis_figure(
    output_dir: Path,
    export_json_path: Path,
) -> list[ComputeNormalizedSeries]:
    compute_series = load_compute_normalized_series(export_json_path)
    _render_compute_normalized_figure(
        compute_series,
        ylabel="Improvement / Initial Baseline",
        title="Fig 1C. Normalized Improvement vs Cumulative Training Time",
        output_stem=output_dir / "fig1c_normalized_improvement_vs_training_time_v1",
    )
    return compute_series


def build_failure_figure(
    output_dir: Path,
    failure_json_path: Path,
) -> list[BucketedFailureSeries]:
    failure_series = load_bucketed_failure_series(failure_json_path)
    _render_failure_figure(
        failure_series,
        ylabel="Failure Count",
        title="Fig 2. Failure Count vs Round (10-Round Buckets)",
        output_stem=output_dir / "fig2_failure_count_every10rounds_v1",
    )
    return failure_series


def build_failure_breakdown_figure(
    output_dir: Path,
    failure_json_path: Path,
) -> list[BucketedFailureBreakdownSeries]:
    breakdown_series, failure_statuses = load_bucketed_failure_breakdown_series(failure_json_path)
    _render_failure_breakdown_figure(
        breakdown_series,
        failure_statuses=failure_statuses,
        title="Fig 2B. Outcome Breakdown by Type (10-Round Buckets)",
        output_stem=output_dir / "fig2b_failure_breakdown_every10rounds_v1",
    )
    _render_failure_status_legend(failure_statuses, output_dir / "legend_failure_statuses_v1")
    return breakdown_series


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot best-so-far round improvement curves from completed orchestrator runs.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated figures and summary JSON (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=_DEFAULT_EXPORT_JSON,
        help=f"Export JSON used for normalized checkpoint plots (default: {_DEFAULT_EXPORT_JSON})",
    )
    parser.add_argument(
        "--failure-json",
        type=Path,
        default=_DEFAULT_FAILURE_JSON,
        help=f"Failure JSON used for bucketed failure plots (default: {_DEFAULT_FAILURE_JSON})",
    )
    parser.add_argument(
        "--round-stride",
        type=int,
        default=10,
        help="Round spacing for normalized checkpoint plot nodes (default: 10)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trajectories = build_figures(args.output_dir)
    for trajectory in trajectories:
        print(
            f"{trajectory.bench}\t{trajectory.label}\t"
            f"initial={trajectory.initial_baseline:.6f}\t"
            f"final={trajectory.final_baseline:.6f}\t"
            f"improvement=+{trajectory.improvement[-1]:.6f}\t"
            f"keeps={len(trajectory.keep_rounds)}"
        )
    if args.export_json.exists():
        sampled_series = build_normalized_checkpoint_figure(
            args.output_dir,
            args.export_json,
            round_stride=args.round_stride,
        )
        compute_series = build_compute_axis_figure(args.output_dir, args.export_json)
        for series in sampled_series:
            print(
                f"{series.bench}\t{series.label}\t"
                f"normalized_improvement=+{series.normalized_improvement[-1]:.2%}\t"
                f"nodes={series.rounds}"
            )
        for series in compute_series:
            print(
                f"{series.bench}\t{series.label}\t"
                f"training_hours={series.training_hours[-1]:.2f}\t"
                f"normalized_improvement=+{series.normalized_improvement[-1]:.2%}"
            )
    else:
        print(f"skipped normalized checkpoint figure because export json was not found: {args.export_json}")
    if args.failure_json.exists():
        failure_series = build_failure_figure(args.output_dir, args.failure_json)
        breakdown_series = build_failure_breakdown_figure(args.output_dir, args.failure_json)
        for series in failure_series:
            print(
                f"{series.bench}\t{series.label}\t"
                f"failure_counts={series.failure_counts}\t"
                f"buckets={series.bucket_labels}"
            )
        for series in breakdown_series:
            total_by_status = {
                status: sum(series.counts_by_status[status]) for status in series.counts_by_status
            }
            print(
                f"{series.bench}\t{series.label}\t"
                f"failure_breakdown={total_by_status}"
            )
    else:
        print(f"skipped failure figure because failure json was not found: {args.failure_json}")
    print(f"wrote figures to {args.output_dir}")


if __name__ == "__main__":
    main()
