"""Explicit Linux real SDK2 DDS → Go2 MuJoCo session smoke test.

This file is intentionally not skippable.  The frozen Linux runner supplies
the exact SDK2Py/CycloneDDS/MuJoCo runtime and model; any missing prerequisite
is a failed smoke route rather than a test pass.
"""

from __future__ import annotations

import os
import sys
import time

from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.integrations.unitree_go2.bridge import INACTIVE_SAFE_FIELDS
from autoadapter2.integrations.unitree_go2.session import (
    UnitreeGo2EvaluationRobotSession,
)


class _RealCommand:
    def _invoke(self, _capability_id, _arguments, sdk):
        publisher = sdk.ChannelPublisher("rt/lowcmd", sdk.LowCmd_)
        publisher.Init()
        subscriber = sdk.ChannelSubscriber("rt/lowstate", sdk.LowState_)
        subscriber.Init()
        crc = sdk.CRC()
        for _ in range(2):
            state = subscriber.Read()
            if state is None:
                raise RuntimeError("real SDK2 LowState was not available")
            command = sdk.unitree_go_msg_dds__LowCmd_()
            command.head[0] = 0xFE
            command.head[1] = 0xEF
            command.level_flag = 0xFF
            command.gpio = 0
            assert len(command.motor_cmd) == 20
            for index, slot in enumerate(command.motor_cmd):
                slot.mode = 0x01
                if index < 12:
                    fresh_q = state.motor_state[index].q
                    slot.q = fresh_q + (0.0 - float(fresh_q))
                    slot.dq = 0.0
                    slot.kp = 20.0
                    slot.kd = 0.5
                    slot.tau = 0.0
                else:
                    for name, value in INACTIVE_SAFE_FIELDS.items():
                        setattr(slot, name, value)
            command.crc = crc.Crc(command)
            publisher.Write(command)
            time.sleep(0.01)
        return {"status": "issued"}


def test_real_unitree_sdk2_dds_mujoco_session_command_state_frame_route() -> None:
    assert sys.platform == "linux", "the Go2 session smoke test requires Linux amd64"
    model_path = os.environ["AUTOADAPTER_GO2_MODEL"]
    profile = FrozenVideoProfile(
        profile_id="linux-smoke",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-task-scene",
        fps=30,
        width=64,
        height=48,
        container="raw",
        codec="raw",
    )
    session = UnitreeGo2EvaluationRobotSession(
        model_path=model_path,
        video_profile=profile,
        rollout_steps=1,
        state_wait_s=2.0,
    )
    try:
        with session:
            session.reset(
                phase="VALIDATION_B",
                execution_id="linux-session-smoke",
                initial_state={"task_id": "G01"},
            )
            session.start_external_recording(
                phase="VALIDATION_B",
                execution_id="linux-session-smoke",
            )
            result = session.invoke(_RealCommand(), "low-level-command", {})
            frames = session.stop_external_recording()
            route = session.route_evidence
            assert result == {"status": "issued"}
            assert frames, "the real MuJoCo external RGB capture returned no frames"
            assert all(frame.width == 64 and frame.height == 48 for frame in frames)
            assert route["accepted_command_count"] >= 1, route
            assert route["accepted_command_type_verified"] is True, route
            assert route["accepted_command_crc_verified"] is True, route
            assert route["state_publication_observed"] is True, route
            assert route["simulation_time_progressed"] is True, route
    finally:
        session.close()
