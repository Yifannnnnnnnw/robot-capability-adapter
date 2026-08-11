from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoadapter2.integration.artifacts import validate_readiness_report
from autoadapter2.integrations.unitree_go2.bridge import (
    ACTIVE_MOTOR_NAMES,
    INACTIVE_SAFE_FIELDS,
    LowStateFrame,
    MuJoCoSensorFrame,
    SportModeStateFrame,
)
from autoadapter2.integrations.unitree_go2.readiness import (
    CHECK_IDS,
    Go2ReadinessError,
    _real_identity,
    run_readiness,
)


@dataclass
class Slot:
    mode: int = 1
    q: float = 0.0
    dq: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    tau: float = 0.0


@dataclass
class Command:
    motor_cmd: list[Slot]
    crc: int = 123


class Backend:
    actuator_names = ACTIVE_MOTOR_NAMES
    timestep = 0.01

    def __init__(self) -> None:
        self.time = 0.0
        self.q = [0.0] * 12
        self.dq = [0.0] * 12
        self.ctrl = [0.0] * 12

    @property
    def simulation_time(self):
        return self.time

    def reset(self):
        self.time = 0.0
        self.q[:] = [0.0] * 12
        self.dq[:] = [0.0] * 12

    def sensors(self):
        return MuJoCoSensorFrame(
            self.q,
            self.dq,
            self.ctrl,
            (1.0, 0.0, 0.0, 0.0),
            (0.1, 0.2, 0.3),
            (1.0, 2.0, 3.0),
            (4.0, 5.0, 6.0),
            (7.0, 8.0, 9.0),
        )

    def set_controls(self, controls):
        self.ctrl[:] = list(controls)

    def step(self):
        self.time += self.timestep
        self.q[0] += self.ctrl[0] * self.timestep
        self.dq[0] = self.ctrl[0]

    def close(self):
        return None


class Transport:
    def __init__(self):
        self.command = None
        self.lowstate = None
        self.sportstate = None

    def start(self):
        return None

    def take_lowcmd(self):
        command, self.command = self.command, None
        return command

    def is_lowcmd_type(self, message):
        return isinstance(message, Command)

    def compute_crc(self, message):
        return 123

    def publish_lowstate(self, state):
        self.lowstate = state

    def publish_sportmodestate(self, state):
        self.sportstate = state

    def close(self):
        return None


class Client:
    def __init__(self, transport: Transport):
        self.transport = transport

    def start(self):
        return None

    def send_probe(self, state):
        slots = []
        for index in range(20):
            if index < 12:
                slots.append(
                    Slot(
                        q=state.q[index] + (0.1 if index == 0 else 0.0),
                        kp=20.0 if index == 0 else 0.0,
                        kd=0.5 if index == 0 else 0.0,
                    )
                )
            else:
                slots.append(Slot(**INACTIVE_SAFE_FIELDS))
        self.transport.command = Command(slots)

    def wait_lowstate(self, timeout_s):
        assert isinstance(self.transport.lowstate, LowStateFrame)
        return self.transport.lowstate

    def wait_sportmodestate(self, timeout_s):
        assert isinstance(self.transport.sportstate, SportModeStateFrame)
        return self.transport.sportstate

    def close(self):
        return None


def _inputs(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    profile = tmp_path / "profile.json"
    runtime = tmp_path / "runtime-lock.json"
    model = tmp_path / "go2.xml"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "profile_id": "general-demo-integration-readiness",
                "version": "1.0.0",
                "check_ids": list(CHECK_IDS),
                "time_limits": {
                    "per_check_wall_s": 60,
                    "attempt_wall_s": 180,
                    "transport_operation_wall_s": 2,
                    "max_probe_simulation_s": 1.0,
                    "cleanup_wall_s": 5,
                    "hidden_retry_count": 0,
                },
                "numerical_tolerances": {
                    "unitree_go2": {
                        "absolute": 1e-5,
                        "relative": 1e-6,
                        "minimum_commanded_joint_change_rad": 1e-5,
                    },
                    "reset": {"qpos_absolute": 1e-9, "qvel_absolute": 1e-9},
                },
            }
        )
    )
    runtime.write_text(json.dumps({"runtime_id": "unitree-go2-linux-amd64", "status": "DRAFT"}))
    model.write_text("not loaded by injected backend")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "manifest_id": "unitree-go2-stock-12dof-mujoco",
                "version": "1.0.0",
                "status": "DRAFT",
                "robot_model_id": "unitree-go2",
                "robot_configuration_id": "unitree-go2-stock-12dof",
                "morphology_ref": {"path": "morphology.json", "sha256": "a" * 64},
                "sdk_ref": {"path": "sdk.json", "sha256": "b" * 64},
                "translation_ref": {"path": "translation.json", "sha256": "c" * 64},
                "readiness_profile_ref": {
                    "path": profile.name,
                    "sha256": digest(profile),
                },
                "runtime": {
                    "id": "unitree-go2-linux-amd64",
                    "version": "1.0.0",
                    "os": "Ubuntu 22.04",
                    "architecture": "amd64",
                    "python": "3.10",
                    "mujoco": "3.3.6",
                    "cyclonedds": "0.10.2",
                    "lock_sha256": digest(runtime),
                },
                "compatibility_checks": [{"check_id": "fixture", "verdict": "NOT_RUN"}],
                "unresolved_gaps": ["unit fixture"],
            }
        )
    )
    return manifest, profile, runtime, model


def test_injected_six_check_route_exercises_mechanics_but_never_formal_pass(tmp_path: Path) -> None:
    manifest, profile, runtime, model = _inputs(tmp_path)
    backend = Backend()
    transport = Transport()
    report = run_readiness(
        run_id="unit-go2",
        manifest_path=manifest,
        profile_path=profile,
        runtime_lock_path=runtime,
        model_path=model,
        report_path=tmp_path / "report.json",
        reference_root=tmp_path,
        identity_checker=lambda _: {"fixture": True},
        backend_factory=lambda: backend,
        transport_factory=lambda: transport,
        sdk_factory=lambda: Client(transport),
    )
    assert [item["check_id"] for item in report["checks"]] == list(CHECK_IDS)
    assert report["checks"][0]["verdict"] == "FAIL"
    assert report["checks"][0]["infrastructure_category"] == "test_dependency_injection"
    assert all(item["verdict"] == "PASS" for item in report["checks"][1:]), report
    assert report["cleanup"]["verdict"] == "PASS"
    assert report["verdict"] == "FAIL"
    validate_readiness_report(report)
    for item in report["checks"]:
        for reference in item["evidence_refs"]:
            evidence = tmp_path / reference["path"]
            assert hashlib.sha256(evidence.read_bytes()).hexdigest() == reference["sha256"]


def test_changed_readiness_limits_fail_before_execution(tmp_path: Path) -> None:
    manifest, profile, runtime, model = _inputs(tmp_path)
    value = json.loads(profile.read_text())
    value["time_limits"]["hidden_retry_count"] = 1
    profile.write_text(json.dumps(value))
    with pytest.raises(Go2ReadinessError, match="time limits"):
        run_readiness(
            run_id="bad-profile",
            manifest_path=manifest,
            profile_path=profile,
            runtime_lock_path=runtime,
            model_path=model,
            report_path=tmp_path / "bad-report.json",
            reference_root=tmp_path,
        )


def test_real_identity_rejects_non_linux_even_with_claimed_lock(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    with pytest.raises(Go2ReadinessError, match="Linux amd64"):
        _real_identity({"status": "FROZEN_FROM_VERIFIED_LINUX_BUILD"})
