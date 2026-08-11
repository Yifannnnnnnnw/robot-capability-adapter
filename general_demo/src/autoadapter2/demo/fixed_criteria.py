"""Private evaluator for the first robot-scoped fixed Demo collections."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


_COMPARATORS = {"<", "<=", ">", ">=", "=="}
_SO_TASKS = {"T01", "T02", "T03", "T08", "T20"}
_GO2_TASKS = {"G01", "G02", "G03", "G04", "G05"}
FIXED_DEMO_TASK_IDS = frozenset(_SO_TASKS | _GO2_TASKS)
_MISSING = object()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _duration(evidence: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name in evidence:
            value = evidence[name]
            if not _finite_number(value) or float(value) < 0:
                return None
            return float(value)
    return None


def _check_value(actual: Any, comparator: Any, expected: Any) -> bool:
    if comparator not in _COMPARATORS:
        return False
    if isinstance(expected, bool):
        if comparator != "==" or not isinstance(actual, bool):
            return False
        return actual is expected
    if not _finite_number(expected) or not _finite_number(actual):
        return False
    left = float(actual)
    right = float(expected)
    return {
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
        "==": left == right,
    }[comparator]


def _sample_time(item: Mapping[str, Any], fallback: float | None) -> float | None:
    for name in ("time_s", "simulation_time_s", "timestamp_s"):
        if name in item:
            value = item[name]
            return float(value) if _finite_number(value) and float(value) >= 0 else None
    return fallback


def _metric_map(item: Mapping[str, Any]) -> dict[str, Any] | None:
    for name in ("metrics", "sampled_metrics", "values"):
        raw = item.get(name, _MISSING)
        if raw is not _MISSING:
            return dict(raw) if isinstance(raw, Mapping) else None
    metric_name = item.get("metric", _MISSING)
    if metric_name is not _MISSING:
        if not _text(metric_name) or "value" not in item:
            return None
        return {str(metric_name): item["value"]}
    reserved = {
        "time_s",
        "simulation_time_s",
        "timestamp_s",
        "phase",
        "metrics",
        "sampled_metrics",
        "values",
        "metric",
        "value",
    }
    direct = {str(key): value for key, value in item.items() if key not in reserved}
    return direct or None


def _record_sample(item: Any, fallback_time: float | None) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    time_s = _sample_time(item, fallback_time)
    if time_s is None:
        return None
    metrics = _metric_map(item)
    if not metrics or any(not _text(key) for key in metrics):
        return None
    for value in metrics.values():
        if isinstance(value, bool):
            continue
        if not _finite_number(value):
            return None
    return {
        "time_s": time_s,
        "phase": item.get("phase"),
        "metrics": metrics,
    }


def _series_samples(
    metric: str,
    raw: Any,
    sample_times: Sequence[Any] | None,
) -> list[dict[str, Any]] | None:
    if not _text(metric) or not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return None
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, Mapping):
            value = item.get("value", _MISSING)
            if value is _MISSING:
                return None
            time_s = _sample_time(item, float(index))
        elif (
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes, bytearray))
            and len(item) == 2
        ):
            time_s, value = item
        else:
            value = item
            if sample_times is not None and index < len(sample_times):
                time_s = sample_times[index]
            else:
                time_s = float(index)
            if not _finite_number(time_s) or float(time_s) < 0:
                return None
            time_s = float(time_s)
        if time_s is None or (not isinstance(value, bool) and not _finite_number(value)):
            return None
        result.append({"time_s": time_s, "phase": None, "metrics": {metric: value}})
    return result


def _mapping_series(
    raw: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    sample_times = evidence.get("sample_times", evidence.get("timestamps_s"))
    if sample_times is not None:
        if (
            not isinstance(sample_times, Sequence)
            or isinstance(sample_times, (str, bytes, bytearray))
            or any(not _finite_number(value) or float(value) < 0 for value in sample_times)
        ):
            return None
    all_samples: list[dict[str, Any]] = []
    for metric, values in raw.items():
        series = _series_samples(metric, values, sample_times)
        if series is None:
            return None
        all_samples.extend(series)
    grouped: dict[float, dict[str, Any]] = {}
    for item in all_samples:
        time_s = float(item["time_s"])
        bucket = grouped.setdefault(time_s, {"time_s": time_s, "phase": None, "metrics": {}})
        bucket["metrics"].update(item["metrics"])
    return [grouped[key] for key in sorted(grouped)]


def _sample_list(raw: Any, evidence: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if isinstance(raw, Mapping):
        if any(name in raw for name in ("time_s", "simulation_time_s", "timestamp_s")):
            parsed = _record_sample(raw, None)
            return [parsed] if parsed is not None else None
        return _mapping_series(raw, evidence)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return None
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        parsed = _record_sample(item, float(index))
        if parsed is None:
            return None
        result.append(parsed)
    return result


def _samples_from_evidence(evidence: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    for name in ("samples", "sampled_metrics"):
        if name in evidence:
            return _sample_list(evidence[name], evidence)
    for name in ("metrics", "metric_samples"):
        raw = evidence.get(name)
        if isinstance(raw, Mapping):
            return _mapping_series(raw, evidence)
    phase_samples: list[dict[str, Any]] = []
    for name in ("motion_samples", "terminal_samples", "stop_samples"):
        if name in evidence:
            parsed = _sample_list(evidence[name], evidence)
            if parsed is None:
                return None
            phase_samples.extend(parsed)
    if phase_samples:
        return phase_samples
    return None


def _phase_samples(
    evidence: Mapping[str, Any],
    phase: str,
    all_samples: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    direct_names = {
        "motion": ("motion_samples", "motion_metrics"),
        "terminal": ("terminal_samples", "terminal_metrics", "stop_samples"),
    }[phase]
    for name in direct_names:
        if name in evidence:
            parsed = _sample_list(evidence[name], evidence)
            return parsed
    selected = [dict(item) for item in all_samples if item.get("phase") == phase]
    return selected or None


def _ordered(samples: Sequence[Mapping[str, Any]]) -> bool:
    previous: float | None = None
    for sample in samples:
        time_s = sample.get("time_s")
        if not _finite_number(time_s) or float(time_s) < 0:
            return False
        if previous is not None and float(time_s) < previous:
            return False
        previous = float(time_s)
        metrics = sample.get("metrics")
        if not isinstance(metrics, Mapping) or not metrics:
            return False
        for value in metrics.values():
            if not isinstance(value, bool) and not _finite_number(value):
                return False
    return bool(samples)


def _valid_checks(checks: Any) -> list[dict[str, Any]] | None:
    if not isinstance(checks, list) or not checks:
        return None
    result: list[dict[str, Any]] = []
    for raw in checks:
        if not isinstance(raw, Mapping):
            return None
        metric = raw.get("metric")
        comparator = raw.get("comparator")
        value = raw.get("value", _MISSING)
        if not _text(metric) or comparator not in _COMPARATORS or value is _MISSING:
            return None
        if isinstance(value, bool):
            if comparator != "==":
                return None
        elif not _finite_number(value):
            return None
        result.append({"metric": metric, "comparator": comparator, "value": value})
    return result


def _sample_span(samples: Sequence[Mapping[str, Any]]) -> float | None:
    if not samples or not _ordered(samples):
        return None
    return float(samples[-1]["time_s"]) - float(samples[0]["time_s"])


def _evaluate_checks(
    checks: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
    *,
    final_metrics: frozenset[str] = frozenset(),
) -> bool:
    if not _ordered(samples):
        return False
    for check in checks:
        metric = check["metric"]
        checked_samples = samples[-1:] if metric in final_metrics else samples
        for sample in checked_samples:
            metrics = sample["metrics"]
            if metric not in metrics or not _check_value(metrics[metric], check["comparator"], check["value"]):
                return False
    return True


def _guards_pass(criterion: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    raw_ids = criterion.get("guard_ids")
    if not isinstance(raw_ids, list) or not raw_ids or any(not _text(item) for item in raw_ids):
        return False
    if len(set(raw_ids)) != len(raw_ids):
        return False
    raw_results = evidence.get("guard_results", evidence.get("guards", _MISSING))
    if not isinstance(raw_results, Mapping):
        return False
    return all(raw_results.get(guard_id) is True for guard_id in raw_ids)


def _criterion_duration(criterion: Mapping[str, Any]) -> float | None:
    values: list[float] = []
    for name in ("continuous_dwell_s", "observation_window_s", "terminal_continuous_dwell_s"):
        if name in criterion:
            value = criterion[name]
            if not _finite_number(value) or float(value) <= 0:
                return None
            values.append(float(value))
    for group_name in ("checks", "motion_checks", "terminal_checks"):
        checks = criterion.get(group_name, [])
        if not isinstance(checks, list):
            return None
        for check in checks:
            metric = check.get("metric") if isinstance(check, Mapping) else None
            threshold = check.get("value") if isinstance(check, Mapping) else None
            if isinstance(metric, str) and metric.endswith("_dwell_s"):
                if not _finite_number(threshold) or float(threshold) <= 0:
                    return None
                values.append(float(threshold))
    return max(values, default=0.0)


def _duration_passes(
    criterion: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> bool:
    required = _criterion_duration(criterion)
    if required is None:
        return False
    if phase == "motion" and "continuous_dwell_s" not in criterion and "observation_window_s" not in criterion:
        required = 0.0
    if phase == "terminal":
        actual = _duration(
            evidence,
            "terminal_duration_s",
            "terminal_dwell_s",
            "stop_duration_s",
            "duration_s",
            "elapsed_s",
        )
    elif phase == "motion":
        actual = _duration(evidence, "motion_duration_s", "duration_s", "elapsed_s")
    else:
        actual = _duration(
            evidence,
            "duration_s",
            "elapsed_s",
            "observation_duration_s",
            "window_s",
        )
    return actual is not None and actual + 1e-12 >= required


def _duration_span_passes(
    criterion: Mapping[str, Any], samples: Sequence[Mapping[str, Any]], *, phase: str | None = None
) -> bool:
    required = _criterion_duration(criterion)
    if required is None:
        return False
    if phase == "motion" and "continuous_dwell_s" not in criterion and "observation_window_s" not in criterion:
        required = 0.0
    span = _sample_span(samples)
    return span is not None and span + 1e-12 >= required and (required > 0 or span > 0)


def evaluate_fixed_demo_criterion(
    criterion: Mapping[str, Any], evidence: Mapping[str, Any]
) -> bool:
    """Return the private Harness verdict for one frozen fixed-Demo task.

    Only trusted sampled metric values and explicit Harness guard results are
    consulted.  Candidate return values, acknowledgements, and self-reports
    are deliberately ignored.
    """

    if not isinstance(criterion, Mapping) or not isinstance(evidence, Mapping):
        return False
    task_id = criterion.get("task_id")
    if task_id not in FIXED_DEMO_TASK_IDS or evidence.get("task_id") != task_id:
        return False
    if not _guards_pass(criterion, evidence):
        return False
    all_samples = _samples_from_evidence(evidence)
    if all_samples is None or not _ordered(all_samples):
        return False

    checks = _valid_checks(criterion.get("checks"))
    motion_checks = _valid_checks(criterion.get("motion_checks")) if "motion_checks" in criterion else []
    terminal_checks = _valid_checks(criterion.get("terminal_checks")) if "terminal_checks" in criterion else []
    if checks is None and task_id != "G04":
        return False
    if task_id == "G04" and (motion_checks is None or terminal_checks is None):
        return False
    if not _duration_passes(criterion, evidence):
        return False

    if task_id != "G04":
        assert checks is not None
        if not _duration_span_passes(criterion, all_samples):
            return False
        final_metrics = frozenset(
            check["metric"] for check in checks if str(check["metric"]).endswith("_dwell_s")
        )
        if task_id == "T08":
            final_metrics = final_metrics | {"cube_height_increase_m"}
        return _evaluate_checks(checks, all_samples, final_metrics=final_metrics)

    motion_samples = _phase_samples(evidence, "motion", all_samples)
    terminal_samples = _phase_samples(evidence, "terminal", all_samples)
    if motion_samples is None or terminal_samples is None:
        return False
    if not _duration_passes(criterion, evidence, phase="motion"):
        return False
    if not _duration_passes(criterion, evidence, phase="terminal"):
        return False
    if not _duration_span_passes(criterion, motion_samples, phase="motion"):
        return False
    if not _duration_span_passes(criterion, terminal_samples, phase="terminal"):
        return False
    assert motion_checks is not None and terminal_checks is not None
    return _evaluate_checks(
        motion_checks,
        motion_samples,
        final_metrics=frozenset({"forward_displacement_m"}),
    ) and _evaluate_checks(
        terminal_checks, terminal_samples
    )


__all__ = ["FIXED_DEMO_TASK_IDS", "evaluate_fixed_demo_criterion"]
