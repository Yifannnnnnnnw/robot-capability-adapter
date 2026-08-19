"""Minimal real-SDK route check for the Unitree Go2 extension."""

from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import unquote, urlparse

from .bridge import (
    ACTIVE_MOTOR_COUNT,
    ACTIVE_MODE,
    DDS_MOTOR_SLOT_COUNT,
    INACTIVE_SAFE_FIELDS,
    LOWCMD_TOPIC,
    LOWSTATE_TOPIC,
    SPORTMODESTATE_TOPIC,
    Go2Backend,
    Go2DDSMuJoCoBridge,
    Go2Transport,
    IMUStateFrame,
    LowStateFrame,
    MotorStateFrame,
    MuJoCoGo2Backend,
    MuJoCoSensorFrame,
    SportModeStateFrame,
    UnitreeSDK2Transport,
)

EXPECTED_PACKAGES = {
    "unitree_sdk2py": "1.0.1",
    "cyclonedds": "0.10.2",
    "mujoco": "3.3.6",
}
EXPECTED_SDK_COMMIT = "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5"


class Go2RouteCheckError(RuntimeError):
    """The declared Go2 SDK route did not complete."""


class Go2SDKProbeClient(Protocol):
    def start(self) -> None: ...
    def send_probe(self, state: MuJoCoSensorFrame) -> None: ...
    def clear_observations(self) -> None: ...
    def wait_lowstate(self, timeout_s: float) -> LowStateFrame: ...
    def wait_sportmodestate(self, timeout_s: float) -> SportModeStateFrame: ...
    def close(self) -> None: ...


def _installed_sdk_commit(distribution: importlib.metadata.Distribution) -> str:
    direct_url_text = distribution.read_text("direct_url.json")
    if not direct_url_text:
        raise Go2RouteCheckError("unitree_sdk2py installation has no direct_url.json source record")
    direct_url = json.loads(direct_url_text)
    vcs_commit = direct_url.get("vcs_info", {}).get("commit_id")
    if isinstance(vcs_commit, str):
        return vcs_commit

    parsed = urlparse(str(direct_url.get("url", "")))
    if parsed.scheme != "file":
        raise Go2RouteCheckError("unitree_sdk2py source record is not a VCS or local checkout")
    checkout = Path(unquote(parsed.path))
    process = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if process.returncode != 0:
        raise Go2RouteCheckError("could not read the unitree_sdk2py checkout commit")
    return process.stdout.strip()


def _real_sdk_identity() -> dict[str, Any]:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise Go2RouteCheckError("the real Go2 route requires Linux amd64")
    if sys.version_info[:2] != (3, 10):
        raise Go2RouteCheckError(
            f"the pinned route requires CPython 3.10, found {platform.python_version()}"
        )
    installed = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    if installed != EXPECTED_PACKAGES:
        raise Go2RouteCheckError(
            f"package versions differ from the pinned route: expected {EXPECTED_PACKAGES}, got {installed}"
        )
    sdk_commit = _installed_sdk_commit(importlib.metadata.distribution("unitree_sdk2py"))
    if sdk_commit != EXPECTED_SDK_COMMIT:
        raise Go2RouteCheckError(
            f"unitree_sdk2py checkout is {sdk_commit}, expected {EXPECTED_SDK_COMMIT}"
        )
    return {
        "platform": "linux-amd64",
        "python": platform.python_version(),
        "packages": installed,
        "unitree_sdk2py_commit": sdk_commit,
    }


class RealSDK2ProbeClient:
    """Real SDK2 publisher/subscribers used by the explicit Linux route."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._lowstate: object | None = None
        self._sportstate: object | None = None
        self._started = False

    def start(self) -> None:  # pragma: no cover - explicit Linux integration route
        try:
            from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber
            from unitree_sdk2py.idl.default import unitree_go_msg_dds__LowCmd_
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowCmd_, LowState_, SportModeState_
            from unitree_sdk2py.utils.crc import CRC
        except ImportError as exc:
            raise Go2RouteCheckError("pinned SDK2 public symbols are unavailable") from exc
        self._command_factory = unitree_go_msg_dds__LowCmd_
        self._crc = CRC()
        self._publisher = ChannelPublisher(LOWCMD_TOPIC, LowCmd_)
        self._lowstate_subscriber = ChannelSubscriber(LOWSTATE_TOPIC, LowState_)
        self._sport_subscriber = ChannelSubscriber(SPORTMODESTATE_TOPIC, SportModeState_)
        self._publisher.Init()
        self._lowstate_subscriber.Init(self._on_lowstate, 10)
        self._sport_subscriber.Init(self._on_sportstate, 10)
        self._started = True

    def _on_lowstate(self, message: object) -> None:  # pragma: no cover - DDS callback
        with self._condition:
            self._lowstate = message
            self._condition.notify_all()

    def _on_sportstate(self, message: object) -> None:  # pragma: no cover - DDS callback
        with self._condition:
            self._sportstate = message
            self._condition.notify_all()

    def send_probe(self, state: MuJoCoSensorFrame) -> None:  # pragma: no cover - DDS route
        if not self._started:
            raise Go2RouteCheckError("SDK2 probe client is not started")
        command = self._command_factory()
        if len(command.motor_cmd) != DDS_MOTOR_SLOT_COUNT:
            raise Go2RouteCheckError("real LowCmd_ does not have 20 motor slots")
        for index, slot in enumerate(command.motor_cmd):
            slot.mode = ACTIVE_MODE
            if index < ACTIVE_MOTOR_COUNT:
                slot.q = state.q[index] + (0.1 if index == 0 else 0.0)
                slot.dq = 0.0
                slot.kp = 20.0 if index == 0 else 0.0
                slot.kd = 0.5 if index == 0 else 0.0
                slot.tau = 0.0
            else:
                for field, value in INACTIVE_SAFE_FIELDS.items():
                    setattr(slot, field, value)
        command.crc = self._crc.Crc(command)
        self._publisher.Write(command)

    def _wait(self, field: str, timeout_s: float) -> object:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while getattr(self, field) is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Go2RouteCheckError(f"timed out waiting for {field}")
                self._condition.wait(remaining)
            value = getattr(self, field)
            setattr(self, field, None)
            return value

    def clear_observations(self) -> None:  # pragma: no cover - DDS route
        with self._condition:
            self._lowstate = None
            self._sportstate = None

    def wait_lowstate(self, timeout_s: float) -> LowStateFrame:  # pragma: no cover - DDS route
        message = self._wait("_lowstate", timeout_s)
        if len(message.motor_state) != DDS_MOTOR_SLOT_COUNT:
            raise Go2RouteCheckError("real LowState_ does not have 20 motor slots")
        return LowStateFrame(
            motor_state=tuple(
                MotorStateFrame(float(slot.q), float(slot.dq), float(slot.tau_est))
                for slot in message.motor_state
            ),
            imu_state=IMUStateFrame(
                tuple(message.imu_state.quaternion),
                tuple(message.imu_state.gyroscope),
                tuple(message.imu_state.accelerometer),
            ),
        )

    def wait_sportmodestate(
        self, timeout_s: float
    ) -> SportModeStateFrame:  # pragma: no cover - DDS route
        message = self._wait("_sportstate", timeout_s)
        return SportModeStateFrame(tuple(message.position), tuple(message.velocity))

    def close(self) -> None:
        for endpoint_name in ("_publisher", "_lowstate_subscriber", "_sport_subscriber"):
            endpoint = getattr(self, endpoint_name, None)
            close = getattr(endpoint, "Close", None)
            if callable(close):
                close()
        self._started = False


def _compare_observation(
    lowstate: LowStateFrame,
    sportstate: SportModeStateFrame,
    expected: MuJoCoSensorFrame,
    *,
    absolute: float,
    relative: float,
) -> float:
    pairs: list[tuple[float, float]] = []
    for index in range(ACTIVE_MOTOR_COUNT):
        pairs.extend(
            (
                (lowstate.motor_state[index].q, expected.q[index]),
                (lowstate.motor_state[index].dq, expected.dq[index]),
                (lowstate.motor_state[index].tau_est, expected.actuator_force[index]),
            )
        )
    pairs.extend(zip(lowstate.imu_state.quaternion, expected.imu_quaternion, strict=True))
    pairs.extend(zip(lowstate.imu_state.gyroscope, expected.imu_gyroscope, strict=True))
    pairs.extend(zip(lowstate.imu_state.accelerometer, expected.imu_accelerometer, strict=True))
    pairs.extend(zip(sportstate.position, expected.frame_position, strict=True))
    pairs.extend(zip(sportstate.velocity, expected.frame_linear_velocity, strict=True))
    errors = [abs(float(actual) - float(reference)) for actual, reference in pairs]
    if not all(
        math.isclose(actual, reference, abs_tol=absolute, rel_tol=relative)
        for actual, reference in pairs
    ):
        raise Go2RouteCheckError(
            f"SDK observation differs from MuJoCo sensors; max error={max(errors)}"
        )
    return max(errors, default=0.0)


def run_route_check(
    *,
    model_path: str | Path,
    command_timeout_s: float = 2.0,
    max_simulation_s: float = 1.0,
    minimum_joint_change_rad: float = 1e-5,
    observation_absolute: float = 1e-5,
    observation_relative: float = 1e-6,
    reset_absolute: float = 1e-9,
    identity_checker: Callable[[], dict[str, Any]] | None = None,
    backend_factory: Callable[[], Go2Backend] | None = None,
    transport_factory: Callable[[], Go2Transport] | None = None,
    sdk_factory: Callable[[], Go2SDKProbeClient] | None = None,
) -> dict[str, Any]:
    """Exercise the bidirectional SDK2 route once without retries."""
    if min(
        command_timeout_s,
        max_simulation_s,
        minimum_joint_change_rad,
        observation_absolute,
        observation_relative,
        reset_absolute,
    ) <= 0:
        raise Go2RouteCheckError("route limits and tolerances must be positive")
    injected = any(
        value is not None
        for value in (identity_checker, backend_factory, transport_factory, sdk_factory)
    )
    identity = (identity_checker or _real_sdk_identity)()
    backend_factory = backend_factory or (lambda: MuJoCoGo2Backend(model_path))
    transport_factory = transport_factory or UnitreeSDK2Transport
    sdk_factory = sdk_factory or RealSDK2ProbeClient

    backend: Go2Backend | None = None
    transport: Go2Transport | None = None
    bridge: Go2DDSMuJoCoBridge | None = None
    client: Go2SDKProbeClient | None = None
    completed: list[str] = ["sdk_identity"]
    result: dict[str, Any] | None = None
    failure: Exception | None = None
    cleanup_errors: list[str] = []

    try:
        backend = backend_factory()
        transport = transport_factory()
        bridge = Go2DDSMuJoCoBridge(backend, transport)
        bridge.start()
        bridge.reset()
        completed.append("translation_install")

        client = sdk_factory()
        client.start()
        completed.append("real_sdk_application")

        before = backend.sensors()
        client.send_probe(before)
        deadline = time.monotonic() + command_timeout_s
        start_simulation_time = backend.simulation_time
        accepted = False
        last_step: dict[str, object] = {}
        while time.monotonic() < deadline:
            last_step = bridge.step()
            accepted = accepted or bool(last_step["accepted"])
            joint_change = abs(backend.sensors().q[0] - before.q[0])
            if accepted and joint_change >= minimum_joint_change_rad:
                break
            if backend.simulation_time - start_simulation_time >= max_simulation_s:
                break
            if not injected:
                time.sleep(0.001)
        joint_change = abs(backend.sensors().q[0] - before.q[0])
        if not accepted:
            raise Go2RouteCheckError(f"real rt/lowcmd was not accepted: {last_step}")
        if joint_change < minimum_joint_change_rad:
            raise Go2RouteCheckError(
                f"commanded joint changed {joint_change}, below {minimum_joint_change_rad}"
            )
        completed.append("sdk_to_mujoco_control")

        client.clear_observations()
        publication = bridge.step()
        expected_observation = backend.sensors()
        lowstate = client.wait_lowstate(command_timeout_s)
        sportstate = client.wait_sportmodestate(command_timeout_s)
        max_observation_error = _compare_observation(
            lowstate,
            sportstate,
            expected_observation,
            absolute=observation_absolute,
            relative=observation_relative,
        )
        completed.append("mujoco_to_sdk_observation")

        bridge.reset()
        full_state = getattr(backend, "full_state", None)
        if callable(full_state):
            first_qpos, first_qvel = full_state()
        elif injected:
            first = backend.sensors()
            first_qpos, first_qvel = tuple(first.q), tuple(first.dq)
        else:
            raise Go2RouteCheckError("real Go2 backend does not expose full qpos/qvel")
        bridge.step()
        bridge.reset()
        if callable(full_state):
            second_qpos, second_qvel = full_state()
        else:
            second = backend.sensors()
            second_qpos, second_qvel = tuple(second.q), tuple(second.dq)
        for field, first_values, second_values in (
            ("qpos", first_qpos, second_qpos),
            ("qvel", first_qvel, second_qvel),
        ):
            if len(first_values) != len(second_values) or any(
                abs(a - b) > reset_absolute
                for a, b in zip(first_values, second_values, strict=True)
            ):
                raise Go2RouteCheckError(f"consecutive reset {field} values differ")
        completed.append("reset")

        result = {
            "passed": True,
            "sdk_grounded": not injected,
            "robot_configuration_id": "unitree-go2-stock-12dof",
            "route": "SDK2 DDS LowCmd -> MuJoCo -> DDS LowState/SportModeState",
            "sdk": identity,
            "model_path": str(Path(model_path).resolve()),
            "checks": completed,
            "joint_0_change_rad": joint_change,
            "max_observation_error": max_observation_error,
            "publication_simulation_time_s": publication["simulation_time"],
            "test_dependencies_injected": injected,
        }
    except Exception as exc:
        failure = exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                cleanup_errors.append(f"client: {exc}")
        if bridge is not None:
            try:
                bridge.close()
            except Exception as exc:
                cleanup_errors.append(f"bridge: {exc}")
        else:
            for name, value in (("transport", transport), ("backend", backend)):
                close = getattr(value, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:
                        cleanup_errors.append(f"{name}: {exc}")

    if failure is not None:
        detail = str(failure)
        if cleanup_errors:
            detail += f"; cleanup errors: {cleanup_errors}"
        raise Go2RouteCheckError(detail) from failure
    if cleanup_errors:
        raise Go2RouteCheckError(f"route cleanup failed: {cleanup_errors}")
    assert result is not None
    return result
