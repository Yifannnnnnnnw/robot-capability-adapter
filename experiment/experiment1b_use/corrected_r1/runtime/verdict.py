"""Minimal trusted verdict composition for corrected-R1 audit records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def compose_corrected_harness_verdict(
    *,
    task_metric: Mapping[str, Any],
    contact_integrity: Mapping[str, Any],
    physical_execution_passed: bool,
    guard_outcomes: Mapping[str, bool],
    video_complete: bool,
    guard_error: str | None = None,
) -> dict[str, Any]:
    """Compose, without rerunning, the same four trusted verdict dimensions."""

    task_metric_passed = task_metric.get("passed") is True
    contact_passed = contact_integrity.get("passed") is True
    guards_passed = (
        guard_error is None
        and bool(guard_outcomes)
        and all(value is True for value in guard_outcomes.values())
    )
    physical_integrity_passed = (
        physical_execution_passed and contact_passed and guards_passed
    )
    passed = task_metric_passed and physical_integrity_passed and video_complete
    return {
        "audit_kind": "exp1b_corrected_r1",
        "formal_episode": False,
        "task_metric": dict(task_metric),
        "task_metric_passed": task_metric_passed,
        "physical_execution_passed": physical_execution_passed,
        "contact_integrity": dict(contact_integrity),
        "guard_outcomes": dict(guard_outcomes),
        "guard_error": guard_error,
        "physical_integrity_passed": physical_integrity_passed,
        "video_complete": video_complete,
        "physical_harness_verdict": "PASS" if passed else "FAIL",
    }
