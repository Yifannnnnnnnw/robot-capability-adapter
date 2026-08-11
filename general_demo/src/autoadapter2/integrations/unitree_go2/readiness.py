"""Six-check readiness attempt for the real SDK2 DDS -> Go2 MuJoCo route.

Injected adapters are useful for deterministic mechanical tests, but their
reports are always non-formal and can never claim ``PASS``.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import math
import platform
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from ...foundation.canonical import canonical_bytes
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
    LowStateFrame,
    MuJoCoGo2Backend,
    MuJoCoSensorFrame,
    SportModeStateFrame,
    UnitreeSDK2Transport,
)


CHECK_IDS = (
    "sdk_identity_load",
    "hook_install",
    "real_sdk_application_execution",
    "sdk_to_mujoco_command",
    "mujoco_to_sdk_observation",
    "reset_close",
)
SDK_COMMIT = "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5"


class Go2ReadinessError(RuntimeError):
    pass


class WallTimeout(Go2ReadinessError):
    pass


class Go2SDKProbeClient(Protocol):
    def start(self) -> None: ...
    def send_probe(self, state: MuJoCoSensorFrame) -> None: ...
    def wait_lowstate(self, timeout_s: float) -> LowStateFrame: ...
    def wait_sportmodestate(self, timeout_s: float) -> SportModeStateFrame: ...
    def close(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_evidence(path: Path, value: dict[str, Any]) -> str:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _reference(path: Path, root: Path) -> dict[str, str]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise Go2ReadinessError(f"artifact is outside the readiness reference root: {path}") from exc
    return {"path": relative, "sha256": _sha256(path)}


def _category(exc: BaseException) -> str:
    name = type(exc).__name__
    converted: list[str] = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            converted.append("_")
        converted.append(character.lower())
    return "".join(converted)


@contextlib.contextmanager
def _wall_timeout(seconds: float):
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timeout(_signum: int, _frame: object) -> None:
        raise WallTimeout(f"operation exceeded {seconds:g} wall seconds")

    signal.signal(signal.SIGALRM, timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _real_identity(runtime_lock: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise Go2ReadinessError("formal Go2 readiness requires Linux amd64")
    if sys.version_info[:2] != (3, 10):
        raise Go2ReadinessError(f"formal runtime requires CPython 3.10, found {platform.python_version()}")
    expected = {"unitree_sdk2py": "1.0.1", "cyclonedds": "0.10.2", "mujoco": "3.3.6"}
    installed = {name: importlib.metadata.version(name) for name in expected}
    if installed != expected:
        raise Go2ReadinessError(f"distribution mismatch: expected {expected}, got {installed}")
    if runtime_lock.get("status") != "FROZEN_FROM_VERIFIED_LINUX_BUILD":
        raise Go2ReadinessError("runtime lock is DRAFT; it cannot support formal sdk_identity_load PASS")
    packages = {item.get("name"): item for item in runtime_lock.get("packages", [])}
    sdk = packages.get("unitree_sdk2py", {})
    if sdk.get("version") != "1.0.1" or sdk.get("source_commit") != SDK_COMMIT:
        raise Go2ReadinessError("runtime lock does not bind the pinned SDK2 version and commit")
    for name, version in expected.items():
        package = packages.get(name, {})
        if package.get("version") != version or not package.get("artifact_sha256"):
            raise Go2ReadinessError(f"runtime lock lacks a verified exact artifact for {name}=={version}")
    digest = runtime_lock.get("oci_image_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise Go2ReadinessError("runtime lock lacks the verified OCI image digest")
    if not runtime_lock.get("complete_dependency_artifact_hashes"):
        raise Go2ReadinessError("runtime lock lacks the complete dependency artifact hashes")
    return {
        "platform": "linux-amd64",
        "python": platform.python_version(),
        "packages": installed,
        "sdk_commit": SDK_COMMIT,
    }


class RealSDK2ProbeClient:
    """Real public SDK2 publisher/subscribers used only by explicit Linux readiness."""

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
            raise Go2ReadinessError("real pinned SDK2 public symbols are unavailable") from exc
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
            raise Go2ReadinessError("real SDK probe client is not started")
        command = self._command_factory()
        if len(command.motor_cmd) != DDS_MOTOR_SLOT_COUNT:
            raise Go2ReadinessError("real LowCmd_ does not have 20 motor slots")
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
                    raise WallTimeout(f"timed out waiting for {field}")
                self._condition.wait(remaining)
            return getattr(self, field)

    def wait_lowstate(self, timeout_s: float) -> LowStateFrame:  # pragma: no cover - DDS route
        message = self._wait("_lowstate", timeout_s)
        if len(message.motor_state) != DDS_MOTOR_SLOT_COUNT:
            raise Go2ReadinessError("real LowState_ does not have 20 motor slots")
        return LowStateFrame(
            motor_state=tuple(
                # Keep this import-local conversion deliberately mechanical.
                _motor_state(float(slot.q), float(slot.dq), float(slot.tau_est))
                for slot in message.motor_state
            ),
            imu_state=_imu_state(
                message.imu_state.quaternion,
                message.imu_state.gyroscope,
                message.imu_state.accelerometer,
            ),
        )

    def wait_sportmodestate(self, timeout_s: float) -> SportModeStateFrame:  # pragma: no cover - DDS route
        message = self._wait("_sportstate", timeout_s)
        return SportModeStateFrame(tuple(message.position), tuple(message.velocity))

    def close(self) -> None:
        for endpoint_name in ("_publisher", "_lowstate_subscriber", "_sport_subscriber"):
            endpoint = getattr(self, endpoint_name, None)
            close = getattr(endpoint, "Close", None)
            if callable(close):
                close()
        self._started = False


def _motor_state(q: float, dq: float, tau_est: float):
    from .bridge import MotorStateFrame

    return MotorStateFrame(q, dq, tau_est)


def _imu_state(quaternion: Any, gyroscope: Any, accelerometer: Any):
    from .bridge import IMUStateFrame

    return IMUStateFrame(tuple(quaternion), tuple(gyroscope), tuple(accelerometer))


def _close_enough(actual: float, expected: float, absolute: float, relative: float) -> bool:
    return math.isclose(actual, expected, abs_tol=absolute, rel_tol=relative)


def _compare_state(
    lowstate: LowStateFrame,
    sportstate: SportModeStateFrame,
    expected: MuJoCoSensorFrame,
    *,
    absolute: float,
    relative: float,
) -> dict[str, float]:
    pairs: list[tuple[float, float]] = []
    for index in range(ACTIVE_MOTOR_COUNT):
        pairs.extend(
            (
                (lowstate.motor_state[index].q, expected.q[index]),
                (lowstate.motor_state[index].dq, expected.dq[index]),
                (lowstate.motor_state[index].tau_est, expected.actuator_force[index]),
            )
        )
    pairs.extend(zip(lowstate.imu_state.quaternion, expected.imu_quaternion))
    pairs.extend(zip(lowstate.imu_state.gyroscope, expected.imu_gyroscope))
    pairs.extend(zip(lowstate.imu_state.accelerometer, expected.imu_accelerometer))
    pairs.extend(zip(sportstate.position, expected.frame_position))
    pairs.extend(zip(sportstate.velocity, expected.frame_linear_velocity))
    errors = [abs(float(actual) - float(reference)) for actual, reference in pairs]
    if not all(_close_enough(actual, reference, absolute, relative) for actual, reference in pairs):
        raise Go2ReadinessError(f"SDK-compatible observation differs from MuJoCo sensors; max error={max(errors)}")
    return {"max_absolute_error": max(errors, default=0.0)}


def run_readiness(
    *,
    run_id: str,
    manifest_path: str | Path,
    profile_path: str | Path,
    runtime_lock_path: str | Path,
    model_path: str | Path,
    report_path: str | Path,
    reference_root: str | Path | None = None,
    identity_checker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    backend_factory: Callable[[], Go2Backend] | None = None,
    transport_factory: Callable[[], Go2Transport] | None = None,
    sdk_factory: Callable[[], Go2SDKProbeClient] | None = None,
) -> dict[str, Any]:
    """Run one ordered, no-hidden-retry readiness attempt."""

    manifest_path = Path(manifest_path).resolve()
    profile_path = Path(profile_path).resolve()
    runtime_lock_path = Path(runtime_lock_path).resolve()
    model_path = Path(model_path).resolve()
    report_path = Path(report_path).resolve()
    reference_root = (
        Path(reference_root).resolve()
        if reference_root is not None
        else manifest_path.parents[3]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    runtime_lock = json.loads(runtime_lock_path.read_text(encoding="utf-8"))
    if tuple(profile.get("check_ids", ())) != CHECK_IDS:
        raise Go2ReadinessError("readiness profile check order does not match Authority 0.15.0")
    limits = profile.get("time_limits", {})
    expected_limits = {
        "per_check_wall_s": 60,
        "attempt_wall_s": 180,
        "transport_operation_wall_s": 2,
        "max_probe_simulation_s": 1.0,
        "cleanup_wall_s": 5,
        "hidden_retry_count": 0,
    }
    if limits != expected_limits:
        raise Go2ReadinessError("readiness time limits differ from Authority 0.15.0")

    injected = any(value is not None for value in (identity_checker, backend_factory, transport_factory, sdk_factory))
    identity_checker = identity_checker or _real_identity
    backend_factory = backend_factory or (lambda: MuJoCoGo2Backend(model_path))
    transport_factory = transport_factory or UnitreeSDK2Transport
    sdk_factory = sdk_factory or RealSDK2ProbeClient

    started_at = _utc_now()
    checks: list[dict[str, Any]] = []
    backend: Go2Backend | None = None
    transport: Go2Transport | None = None
    bridge: Go2DDSMuJoCoBridge | None = None
    client: Go2SDKProbeClient | None = None
    cleanup_errors: list[str] = []

    def check(check_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        check_start = time.monotonic()
        try:
            with _wall_timeout(float(limits["per_check_wall_s"])):
                evidence = operation()
        except BaseException as exc:
            checks.append(
                {
                    "check_id": check_id,
                    "verdict": "FAIL",
                    "duration_s": round(time.monotonic() - check_start, 6),
                    "infrastructure_category": _category(exc),
                    "detail": str(exc),
                }
            )
        else:
            checks.append(
                {
                    "check_id": check_id,
                    "verdict": "PASS",
                    "duration_s": round(time.monotonic() - check_start, 6),
                    "evidence": evidence,
                }
            )

    try:
        with _wall_timeout(float(limits["attempt_wall_s"])):
            def identity() -> dict[str, Any]:
                detail = identity_checker(runtime_lock)
                runtime_lock_sha256 = _sha256(runtime_lock_path)
                expected_runtime_sha256 = manifest.get("runtime", {}).get("lock_sha256")
                if expected_runtime_sha256 not in {None, runtime_lock_sha256}:
                    raise Go2ReadinessError("runtime lock bytes do not match the integration manifest")
                profile_sha256 = _sha256(profile_path)
                expected_profile_sha256 = manifest.get("readiness_profile_ref", {}).get("sha256")
                if expected_profile_sha256 != profile_sha256:
                    raise Go2ReadinessError("readiness profile bytes do not match the integration manifest")
                return {
                    **detail,
                    "runtime_lock_sha256": runtime_lock_sha256,
                    "readiness_profile_sha256": profile_sha256,
                }

            check("sdk_identity_load", identity)

            def install() -> dict[str, Any]:
                nonlocal backend, transport, bridge
                backend = backend_factory()
                transport = transport_factory()
                bridge = Go2DDSMuJoCoBridge(backend, transport)
                bridge.start()
                reset = bridge.reset()
                return {"route": "CycloneDDS domain 1 loopback -> MuJoCo", "reset": reset}

            check("hook_install", install)

            def application() -> dict[str, Any]:
                nonlocal client
                if bridge is None:
                    raise Go2ReadinessError("hook_install did not establish the bridge")
                client = sdk_factory()
                client.start()
                return {"client_class": type(client).__name__, "real_sdk_required": not injected}

            check("real_sdk_application_execution", application)

            def command_path() -> dict[str, Any]:
                if bridge is None or backend is None or client is None:
                    raise Go2ReadinessError("real SDK command route is unavailable")
                before = backend.sensors()
                client.send_probe(before)
                deadline = time.monotonic() + float(limits["transport_operation_wall_s"])
                accepted = False
                start_simulation_time = backend.simulation_time
                last_result: dict[str, object] = {}
                while time.monotonic() < deadline:
                    last_result = bridge.step()
                    accepted = accepted or bool(last_result["accepted"])
                    if accepted and abs(backend.sensors().q[0] - before.q[0]) >= float(
                        profile["numerical_tolerances"]["unitree_go2"]["minimum_commanded_joint_change_rad"]
                    ):
                        break
                    if backend.simulation_time - start_simulation_time >= float(limits["max_probe_simulation_s"]):
                        break
                    if not injected:
                        time.sleep(0.001)
                change = abs(backend.sensors().q[0] - before.q[0])
                minimum = float(profile["numerical_tolerances"]["unitree_go2"]["minimum_commanded_joint_change_rad"])
                if not accepted:
                    raise Go2ReadinessError(f"real rt/lowcmd was not accepted: {last_result}")
                if change < minimum:
                    raise Go2ReadinessError(f"commanded joint changed {change}, below {minimum}")
                return {"accepted": True, "joint_0_change_rad": change}

            check("sdk_to_mujoco_command", command_path)

            def observation_path() -> dict[str, Any]:
                if backend is None or client is None:
                    raise Go2ReadinessError("real SDK observation route is unavailable")
                timeout = float(limits["transport_operation_wall_s"])
                lowstate = client.wait_lowstate(timeout)
                sportstate = client.wait_sportmodestate(timeout)
                tolerance = profile["numerical_tolerances"]["unitree_go2"]
                evidence = _compare_state(
                    lowstate,
                    sportstate,
                    backend.sensors(),
                    absolute=float(tolerance["absolute"]),
                    relative=float(tolerance["relative"]),
                )
                return {**evidence, "lowstate_slots": len(lowstate.motor_state), "sportmodestate_read_only": True}

            check("mujoco_to_sdk_observation", observation_path)

            def reset_close() -> dict[str, Any]:
                nonlocal client, bridge
                if bridge is None or backend is None:
                    raise Go2ReadinessError("simulation route is unavailable")
                bridge.reset()
                first = backend.sensors()
                bridge.step()
                bridge.reset()
                second = backend.sensors()
                qpos_tolerance = float(profile["numerical_tolerances"]["reset"]["qpos_absolute"])
                qvel_tolerance = float(profile["numerical_tolerances"]["reset"]["qvel_absolute"])
                if any(abs(a - b) > qpos_tolerance for a, b in zip(first.q, second.q)):
                    raise Go2ReadinessError("two reset joint-position projections differ")
                if any(abs(a - b) > qvel_tolerance for a, b in zip(first.dq, second.dq)):
                    raise Go2ReadinessError("two reset joint-velocity projections differ")
                if client is not None:
                    client.close()
                    client = None
                bridge.close()
                bridge = None
                return {"two_resets_match": True, "closed": True}

            check("reset_close", reset_close)
    except BaseException as exc:
        checks.append(
            {
                "check_id": "attempt_envelope",
                "verdict": "FAIL",
                "duration_s": 0.0,
                "infrastructure_category": _category(exc),
                "detail": str(exc),
            }
        )
    finally:
        cleanup_start = time.monotonic()
        try:
            with _wall_timeout(float(limits["cleanup_wall_s"])):
                if client is not None:
                    try:
                        client.close()
                    except BaseException as exc:
                        cleanup_errors.append(f"client: {exc}")
                if bridge is not None:
                    try:
                        bridge.close()
                    except BaseException as exc:
                        cleanup_errors.append(f"bridge: {exc}")
        except BaseException as exc:
            cleanup_errors.append(f"cleanup envelope: {exc}")
        cleanup_duration = round(time.monotonic() - cleanup_start, 6)

    results_by_id = {item["check_id"]: item for item in checks}
    ordered_checks = [
        results_by_id.get(
            check_id,
            {
                "check_id": check_id,
                "verdict": "FAIL",
                "duration_s": 0.0,
                "infrastructure_category": "dependency_failure",
                "detail": "check could not run after an earlier infrastructure failure",
            },
        )
        for check_id in CHECK_IDS
    ]
    # A synthetic adapter may exercise every mechanical branch, but its SDK
    # identity check is deliberately failed before producing the contract.
    if injected:
        ordered_checks[0]["verdict"] = "FAIL"
        ordered_checks[0]["infrastructure_category"] = "test_dependency_injection"
        ordered_checks[0]["detail"] = "mechanical fixture ran; the real pinned SDK identity was not used"

    cleanup_detail = {
        "verdict": "PASS" if not cleanup_errors else "FAIL",
        "duration_s": cleanup_duration,
        "errors": cleanup_errors,
    }

    evidence_dir = report_path.parent / "evidence"
    contract_checks: list[dict[str, Any]] = []
    for index, item in enumerate(ordered_checks, 1):
        evidence_path = evidence_dir / f"{index:02d}_{item['check_id']}.json"
        _write_evidence(
            evidence_path,
            {
                "schema_version": "1.0.0",
                "check_id": item["check_id"],
                "duration_s": item.get("duration_s", 0.0),
                "detail": item.get("evidence", item.get("detail")),
                "model_sha256": _sha256(model_path),
            },
        )
        contract_item = {
            "check_id": item["check_id"],
            "verdict": item["verdict"],
            "evidence_refs": [_reference(evidence_path, reference_root)],
        }
        if item["verdict"] == "FAIL":
            contract_item["infrastructure_category"] = item.get(
                "infrastructure_category", "readiness_failure"
            )
        contract_checks.append(contract_item)

    cleanup_evidence_path = evidence_dir / "07_cleanup.json"
    _write_evidence(
        cleanup_evidence_path,
        {
            "schema_version": "1.0.0",
            "duration_s": cleanup_detail["duration_s"],
            "errors": cleanup_detail["errors"],
        },
    )
    contract_cleanup = {
        "verdict": cleanup_detail["verdict"],
        "evidence_refs": [_reference(cleanup_evidence_path, reference_root)],
    }
    formal_pass = (
        all(item["verdict"] == "PASS" for item in contract_checks)
        and contract_cleanup["verdict"] == "PASS"
        and manifest.get("status") == "READY"
    )
    runtime_sha256 = hashlib.sha256(canonical_bytes(manifest["runtime"])).hexdigest()
    environment_fingerprint = {
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "injected_test_dependencies": injected,
    }
    runtime_lock_sha256 = manifest["runtime"].get("lock_sha256") or _sha256(runtime_lock_path)
    report = {
        "schema_version": "1.0.0",
        "attempt_id": f"{run_id}-unitree-go2-readiness",
        "run_id": run_id,
        "integration_manifest_ref": _reference(manifest_path, reference_root),
        "runtime_sha256": runtime_sha256,
        "readiness_profile_ref": _reference(profile_path, reference_root),
        "environment_fingerprint_sha256": hashlib.sha256(
            canonical_bytes(environment_fingerprint)
        ).hexdigest(),
        "dependency_sha256": {
            "morphology": manifest["morphology_ref"]["sha256"],
            "sdk": manifest["sdk_ref"]["sha256"],
            "translation": manifest["translation_ref"]["sha256"],
            "runtime_lock": runtime_lock_sha256,
            "readiness_profile": manifest["readiness_profile_ref"]["sha256"],
        },
        "time_limits": limits,
        "numerical_tolerances": profile["numerical_tolerances"],
        "checks": contract_checks,
        "cleanup": contract_cleanup,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "verdict": "PASS" if formal_pass else "FAIL",
    }
    _write_evidence(report_path, report)
    return report
