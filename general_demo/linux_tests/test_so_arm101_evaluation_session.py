"""Explicit real SO101Follower/PTY/MuJoCo EvaluationRobotSession test.

This test is intentionally non-skipping.  It belongs to the frozen Linux
runtime and must fail if the exact LeRobot, Feetech, MuJoCo, model, or shared
external-frame capture dependency is unavailable.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from autoadapter2.evaluation import RGBFrame
from autoadapter2.integrations.so_arm101.session import (
    create_so_arm101_evaluation_session,
)
from autoadapter2.validation import HarnessInvocation


ROOT = Path(__file__).resolve().parents[1]


class _RealSDKCandidate:
    def _invoke(self, _capability_id: str, _arguments: dict[str, object], sdk: object) -> dict[str, object]:
        action = {
            "shoulder_pan.pos": -30.0,
            "shoulder_lift.pos": -10.0,
            "elbow_flex.pos": 10.0,
            "wrist_flex.pos": 30.0,
            "wrist_roll.pos": 50.0,
            "gripper.pos": 25.0,
        }
        accepted = sdk.send_action(action)  # type: ignore[attr-defined]
        observation = sdk.get_observation()  # type: ignore[attr-defined]
        return {"accepted": accepted, "observation": observation}


def _invocation() -> HarnessInvocation:
    digest = lambda char: "sha256:" + char * 64
    return HarnessInvocation(
        capability_id="real-sdk-command",
        case_id="so101-session-command",
        inputs={},
        initial_state={"task_id": "T01"},
        repetition=1,
        measurement={
            "measurement_id": "tip-position-error",
            "entity": "gripper_reference",
            "unit": "m",
            "frame": "robot_base",
        },
        metric="tip_position_error_m",
        threshold={"comparator": "<=", "value": 0.020},
        dwell_s=0.25,
        timeout_s=1.0,
        aggregation="ALL",
        guard_ids=("finite-physical-state",),
        run_snapshot_hash=digest("0"),
        candidate_source_hash=digest("1"),
        suite_hash=digest("2"),
        execution_attempt=1,
    )


def test_linux_real_evaluation_session_command_readback_and_external_frames(tmp_path: Path) -> None:
    assert sys.platform == "linux", "formal SO-ARM101 session must run on Linux"
    assert platform.machine().lower() in {"x86_64", "amd64"}, "formal SO-ARM101 session must run on amd64"
    model_path = Path(os.environ["AUTOADAPTER_SO101_MODEL"])
    assert model_path.is_file(), model_path
    manifest_path = ROOT / "integrations/so-arm101/integration_manifest.json"
    direction = os.environ["AUTOADAPTER_GRIPPER_DIRECTION"]
    assert direction in {"tick-increases-qpos", "tick-decreases-qpos"}

    session = create_so_arm101_evaluation_session("so-arm101", manifest_path, tmp_path)
    try:
        assert session.evidence_scope == "SDK_GROUNDED_SIMULATION"
        with session:
            session.reset(
                phase="VALIDATION_B",
                execution_id="linux-so101-evaluation-session",
                initial_state={"task_id": "T01"},
            )
            session.start_external_recording(
                phase="VALIDATION_B",
                execution_id="linux-so101-evaluation-session",
            )
            result = session.invoke(_RealSDKCandidate(), "real-sdk-command", {})
            assert set(result["observation"]) == {
                "shoulder_pan.pos",
                "shoulder_lift.pos",
                "elbow_flex.pos",
                "wrist_flex.pos",
                "wrist_roll.pos",
                "gripper.pos",
            }
            frames = session.stop_external_recording()
            assert frames
            assert all(isinstance(frame, RGBFrame) for frame in frames)
            evidence = session.validation_evidence(_invocation())
            assert evidence.sdk_route_verified is True, evidence
            assert evidence.guard_results["sdk-route-verified"] is True
            assert evidence.guard_results["physics-progress-observed"] is True
    finally:
        session.close()
