"""Six-check readiness runner for the real SO101Follower PTY/MuJoCo route."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from ...foundation.canonical import canonical_bytes
from .feetech_protocol import MOTOR_IDS, MOTOR_NAMES
from .translation import FeetechPTYTranslation, MuJoCoSO101Backend, PositionBackend

CHECK_IDS = (
    "sdk_identity_load",
    "hook_install",
    "real_sdk_application_execution",
    "sdk_to_mujoco_command",
    "mujoco_to_sdk_observation",
    "reset_close",
)
PROBE_ACTION = {
    "shoulder_pan.pos": -30.0,
    "shoulder_lift.pos": -10.0,
    "elbow_flex.pos": 10.0,
    "wrist_flex.pos": 30.0,
    "wrist_roll.pos": 50.0,
    "gripper.pos": 25.0,
}
EXPECTED_SDK_COMMIT = "30da8e687a6dfc617fcd94afc367ac7071c376ce"
EXPECTED_PACKAGE_ARTIFACTS = {
    "lerobot": {
        "version": "0.6.0",
        "artifact_sha256": "b38a564fbc441d98380576863bf68635dde5fc2c42ddc2a39d0486640dc9e9a8",
    },
    "feetech-servo-sdk": {
        "version": "1.0.0",
        "artifact_sha256": "d4d3832e4b1b22a8222133a414db9f868224c2fb639426a1b11d96ddfe84e69c",
    },
    "mujoco": {"version": "3.3.6"},
}
EXPECTED_SDK_RUNTIME = {
    "id": "so-arm101-linux-amd64",
    "version": "1.0.0",
    "os": "Ubuntu 24.04",
    "architecture": "amd64",
    "python": "3.12",
    "mujoco": "3.3.6",
}
EXPECTED_SDK_PACKAGES = {
    "lerobot": {
        "version": "0.6.0",
        "commit": EXPECTED_SDK_COMMIT,
        "artifact_kind": "wheel",
        "sha256": EXPECTED_PACKAGE_ARTIFACTS["lerobot"]["artifact_sha256"],
    },
    "feetech-servo-sdk": {
        "version": "1.0.0",
        "artifact_kind": "sdist",
        "sha256": EXPECTED_PACKAGE_ARTIFACTS["feetech-servo-sdk"]["artifact_sha256"],
    },
}
EXPECTED_SO_SDK_FIELDS = [f"{name}.pos" for name in MOTOR_NAMES]
RUNTIME_SOURCE_PATHS = (
    "general_demo/src/autoadapter2/integrations/so_arm101/feetech_protocol.py",
    "general_demo/src/autoadapter2/integrations/so_arm101/translation.py",
    "general_demo/src/autoadapter2/integrations/so_arm101/readiness.py",
    "general_demo/scripts/run_so_arm101_readiness.py",
)


class ReadinessError(RuntimeError):
    pass


class WallTimeout(ReadinessError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_evidence(path: Path, value: dict[str, Any]) -> str:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ReadinessError(f"readiness artifact already exists and is immutable: {path}") from exc
    return hashlib.sha256(payload).hexdigest()


def _reference(path: Path, root: Path) -> dict[str, str]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReadinessError(f"artifact is outside the readiness reference root: {path}") from exc
    return {"path": relative, "sha256": _sha256(path)}


def _resolve_reference(root: Path, reference: dict[str, Any], *, label: str) -> Path:
    """Resolve one manifest file reference without allowing root escape or symlinks."""
    try:
        relative = Path(reference["path"])
        expected_sha256 = reference["sha256"]
    except (KeyError, TypeError) as exc:
        raise ReadinessError(f"{label} is not a path/hash reference") from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise ReadinessError(f"{label} escapes the readiness reference root")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ReadinessError(f"{label} escapes the readiness reference root") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ReadinessError(f"{label} is not a regular file")
    if _sha256(resolved) != expected_sha256:
        raise ReadinessError(f"{label} hash does not match the integration manifest")
    return resolved


def _normalise_distribution_name(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


def _installed_distribution_fingerprint(
    distribution: importlib.metadata.Distribution,
) -> dict[str, Any]:
    """Hash the files actually installed in this runtime, not a copied lock claim."""
    files: list[dict[str, str]] = []
    record_sha256: str | None = None
    for entry in distribution.files or ():
        path = distribution.locate_file(entry)
        if not path.is_file() or path.is_symlink():
            continue
        digest = _sha256(path)
        relative = entry.as_posix()
        files.append({"path": relative, "sha256": digest})
        if relative.endswith("/RECORD") or relative == "RECORD":
            record_sha256 = digest
    files.sort(key=lambda item: item["path"])
    if record_sha256 is None:
        raise ReadinessError(
            f"installed distribution {distribution.metadata['Name']!r} has no regular RECORD file"
        )
    return {
        "version": distribution.version,
        "installed_files_sha256": _sha256_bytes(canonical_bytes(files)),
        "record_sha256": record_sha256,
        "file_count": len(files),
    }


def _installed_distributions() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalised = _normalise_distribution_name(name)
        try:
            fingerprint = _installed_distribution_fingerprint(distribution)
        except ReadinessError:
            # The project source is copied into the image and is therefore
            # exposed by importlib.metadata as a local egg-info distribution.
            # It has no wheel RECORD; its exact runtime files are bound below
            # through source_hashes and the immutable image ID.  Pinned SDK and
            # simulator distributions must still have a real RECORD.
            if normalised in EXPECTED_PACKAGE_ARTIFACTS:
                raise
            continue
        result[normalised] = {"name": name, **fingerprint}
    return dict(sorted(result.items()))


def _report_artifact_hashes() -> dict[str, dict[str, str]]:
    """Read the hashes emitted by pip's build-time install reports."""
    build_root = Path(os.environ.get("AUTOADAPTER_RUNTIME_BUILD", "/opt/autoadapter/runtime-build"))
    result: dict[str, dict[str, str]] = {}
    for report_path in sorted(build_root.glob("*-install-report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for item in report.get("install", []):
            metadata = item.get("metadata", {})
            name = metadata.get("name")
            version = metadata.get("version")
            hashes = item.get("download_info", {}).get("archive_info", {}).get("hashes", {})
            digest = hashes.get("sha256")
            if not name or not version or not digest:
                continue
            result[_normalise_distribution_name(name)] = {
                "version": version,
                "artifact_sha256": digest,
            }
    pins_path = build_root / "artifact-sha256.txt"
    if pins_path.is_file():
        for line in pins_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            digest, filename = parts
            stem = filename.rsplit("/", 1)[-1].lower()
            for name, expected in EXPECTED_PACKAGE_ARTIFACTS.items():
                package_prefix = name.replace("-", "_")
                if stem.replace("-", "_").startswith(package_prefix + "_"):
                    result.setdefault(name, {"version": expected["version"]})[
                        "artifact_sha256"
                    ] = digest
    return result


def _validate_sdk_record(record: Any) -> dict[str, dict[str, Any]]:
    """Validate the exact SO SDK record selected by the Integration Manifest."""
    if not isinstance(record, dict):
        raise ReadinessError("manifest.sdk_ref does not contain an SDK record object")
    expected_identity = {
        "record_type": "sdk",
        "id": "lerobot-so101-follower",
        "version": "1.0.0",
        "robot_configuration_id": "so-arm101-follower-stock-gripper",
    }
    for field, expected in expected_identity.items():
        if record.get(field) != expected:
            raise ReadinessError(f"manifest.sdk_ref has the wrong SO SDK {field}")
    runtime = record.get("runtime")
    if not isinstance(runtime, dict) or any(
        runtime.get(key) != value for key, value in EXPECTED_SDK_RUNTIME.items()
    ):
        raise ReadinessError("manifest.sdk_ref has the wrong SO SDK runtime identity")
    if (
        record.get("action_fields") != EXPECTED_SO_SDK_FIELDS
        or record.get("observation_fields") != EXPECTED_SO_SDK_FIELDS
    ):
        raise ReadinessError("manifest.sdk_ref has the wrong SO SDK fields")
    packages = record.get("packages")
    if not isinstance(packages, list) or {
        item.get("name") for item in packages if isinstance(item, dict)
    } != set(EXPECTED_SDK_PACKAGES):
        raise ReadinessError("manifest.sdk_ref does not declare the exact SO SDK package set")
    declared: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise ReadinessError("manifest.sdk_ref contains a malformed SDK package")
        name = package.get("name")
        expected = EXPECTED_SDK_PACKAGES.get(name)
        if expected is None or name in declared:
            raise ReadinessError("manifest.sdk_ref contains an unexpected or duplicate SDK package")
        for field, value in expected.items():
            if package.get(field) != value:
                raise ReadinessError(f"manifest.sdk_ref has the wrong {name} {field}")
        declared[name] = package
    return declared


def _compare_sdk_record_to_runtime_lock(
    sdk_record: dict[str, Any], runtime_lock: dict[str, Any]
) -> None:
    declared = _validate_sdk_record(sdk_record)
    locked = {item.get("name"): item for item in runtime_lock.get("packages", [])}
    for name, package in declared.items():
        locked_item = locked.get(name)
        if not isinstance(locked_item, dict):
            raise ReadinessError(f"runtime lock lacks the SDK-declared {name} package")
        for field in ("version", "artifact_kind"):
            if locked_item.get(field) != package[field]:
                raise ReadinessError(f"runtime lock disagrees with the SDK-declared {name} {field}")
        if locked_item.get("artifact_sha256") != package["sha256"]:
            raise ReadinessError(f"runtime lock disagrees with the SDK-declared {name} artifact")
        if name == "lerobot" and locked_item.get("source_commit") != package["commit"]:
            raise ReadinessError("runtime lock disagrees with the SDK-declared LeRobot source commit")
    if runtime_lock.get("sdk_source_commit") != declared["lerobot"]["commit"]:
        raise ReadinessError("runtime lock does not bind the SDK-declared LeRobot source commit")


def capture_verified_runtime_lock(
    *,
    output_path: str | Path,
    model_path: str | Path,
    reference_root: str | Path,
) -> dict[str, Any]:
    """Capture a deterministic lock from the running Linux image.

    The local Docker image ID is supplied by the container launcher.  All other values are measured from
    the running interpreter/filesystem and are never copied from the draft lock.
    """
    output_path = Path(output_path).resolve()
    model_path = Path(model_path).resolve()
    reference_root = Path(reference_root).resolve()
    image_digest = os.environ.get("AUTOADAPTER_IMAGE_DIGEST", "")
    if not image_digest.startswith("sha256:") or len(image_digest) != 71:
        raise ReadinessError("AUTOADAPTER_IMAGE_DIGEST must contain the verified local Docker image ID")
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ReadinessError("runtime capture requires Linux amd64")
    if sys.version_info[:2] != (3, 12):
        raise ReadinessError("runtime capture requires CPython 3.12")

    installed = _installed_distributions()
    artifact_hashes = _report_artifact_hashes()
    packages: list[dict[str, Any]] = []
    for name, expected in EXPECTED_PACKAGE_ARTIFACTS.items():
        actual = installed.get(name)
        if actual is None or actual["version"] != expected["version"]:
            raise ReadinessError(f"required distribution {name}=={expected['version']} is not installed")
        artifact = artifact_hashes.get(name, {})
        digest = artifact.get("artifact_sha256")
        if name in {"lerobot", "feetech-servo-sdk"} and digest != expected["artifact_sha256"]:
            raise ReadinessError(f"build report does not verify the pinned {name} artifact")
        if not digest:
            raise ReadinessError(f"build report does not contain an artifact hash for {name}")
        packages.append(
            {
                "name": name,
                "version": actual["version"],
                "artifact_kind": "sdist" if name == "feetech-servo-sdk" else "wheel",
                "artifact_sha256": digest,
                "installed_files_sha256": actual["installed_files_sha256"],
                "record_sha256": actual["record_sha256"],
                "file_count": actual["file_count"],
                **({"source_commit": EXPECTED_SDK_COMMIT} if name == "lerobot" else {}),
            }
        )

    complete: dict[str, dict[str, Any]] = {}
    for name, actual in installed.items():
        artifact = artifact_hashes.get(name)
        if artifact is None:
            continue
        complete[name] = {
            "version": actual["version"],
            "artifact_sha256": artifact["artifact_sha256"],
            "installed_files_sha256": actual["installed_files_sha256"],
            "record_sha256": actual["record_sha256"],
            "file_count": actual["file_count"],
        }
    if set(EXPECTED_PACKAGE_ARTIFACTS) - set(complete):
        raise ReadinessError("complete dependency artifact capture is missing a required distribution")

    source_hashes: dict[str, str] = {}
    for relative in RUNTIME_SOURCE_PATHS:
        source = reference_root / relative
        if not source.is_file() or source.is_symlink():
            raise ReadinessError(f"runtime source is unavailable for capture: {relative}")
        source_hashes[relative] = _sha256(source)
    model_sha256 = _sha256(model_path)
    mujoco_module = importlib.import_module("mujoco")
    mujoco_module_path = Path(mujoco_module.__file__).resolve()
    python_path = Path(sys.executable).resolve()
    lock = {
        "schema_version": "1.0.0",
        "runtime_id": "so-arm101-linux-amd64",
        "version": "1.0.0",
        "status": "FROZEN_FROM_VERIFIED_LINUX_BUILD",
        "platform": {
            "os": "Ubuntu 24.04",
            "architecture": "amd64",
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "oci_image_digest": image_digest,
        "python": {"executable": str(python_path), "sha256": _sha256(python_path)},
        "packages": packages,
        "complete_dependency_artifact_hashes": complete,
        "sdk_source_commit": EXPECTED_SDK_COMMIT,
        "model": {"path": str(model_path), "sha256": model_sha256},
        "source_hashes": source_hashes,
        "mujoco_module": {
            "version": mujoco_module.__version__,
            "file": str(mujoco_module_path),
            "sha256": _sha256(mujoco_module_path),
        },
    }
    _write_evidence(output_path, lock)
    return lock


def _category(exc: BaseException) -> str:
    name = type(exc).__name__
    converted = []
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

    def handle_timeout(_signum: int, _frame: object) -> None:
        raise WallTimeout(f"operation exceeded {seconds:g} wall seconds")

    signal.signal(signal.SIGALRM, handle_timeout)
    previous = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        restored_delay = max(1e-9, previous[0] - elapsed) if previous[0] > 0 else 0.0
        signal.setitimer(signal.ITIMER_REAL, restored_delay, previous[1])
        signal.signal(signal.SIGALRM, previous_handler)


def public_positions_to_ticks(values: dict[str, Any]) -> dict[int, int]:
    expected = {f"{name}.pos" for name in MOTOR_NAMES}
    if set(values) != expected:
        raise ReadinessError("SDK position fields do not exactly match SO-ARM101")
    result: dict[int, int] = {}
    for name in MOTOR_NAMES:
        value = float(values[f"{name}.pos"])
        if not math.isfinite(value):
            raise ReadinessError("SDK position is non-finite")
        if name == "gripper":
            fraction = min(100.0, max(0.0, value)) / 100.0
        else:
            fraction = (value + 180.0) / 360.0
        result[MOTOR_IDS[name]] = min(4095, max(0, int(fraction * 4095)))
    return result


def _real_identity(
    runtime_lock: dict[str, Any], *, sdk_record: dict[str, Any] | None = None
) -> dict[str, Any]:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ReadinessError("formal SO-ARM101 readiness requires Linux amd64")
    if sys.version_info[:2] != (3, 12):
        raise ReadinessError(f"formal runtime requires CPython 3.12, found {platform.python_version()}")
    if runtime_lock.get("status") != "FROZEN_FROM_VERIFIED_LINUX_BUILD":
        raise ReadinessError("runtime lock is DRAFT; no verified Linux build may claim sdk_identity_load PASS")
    if sdk_record is None:
        raise ReadinessError("formal SDK identity requires the manifest.sdk_ref record")
    _validate_sdk_record(sdk_record)
    image_record = runtime_lock.get("image")
    expected_image_digest = runtime_lock.get("oci_image_digest")
    if expected_image_digest is None and isinstance(image_record, dict):
        expected_image_digest = image_record.get("oci_digest")
    actual_image_digest = os.environ.get("AUTOADAPTER_IMAGE_DIGEST")
    if expected_image_digest != actual_image_digest:
        raise ReadinessError("runtime lock image digest does not match the launched image")
    installed = _installed_distributions()
    reported_artifacts = _report_artifact_hashes()
    locked = {item.get("name"): item for item in runtime_lock.get("packages", [])}
    for name, expected in EXPECTED_PACKAGE_ARTIFACTS.items():
        actual = installed.get(name)
        locked_item = locked.get(name)
        if actual is None or actual.get("version") != expected["version"]:
            raise ReadinessError(f"installed distribution mismatch for {name}=={expected['version']}")
        if not isinstance(locked_item, dict):
            raise ReadinessError(f"runtime lock lacks the pinned {name} distribution")
        if locked_item.get("version") != actual["version"]:
            raise ReadinessError(f"runtime lock version mismatch for {name}")
        if not isinstance(locked_item.get("artifact_sha256"), str):
            raise ReadinessError(f"runtime lock lacks the measured {name} artifact hash")
        reported = reported_artifacts.get(name, {}).get("artifact_sha256")
        if reported != locked_item["artifact_sha256"]:
            raise ReadinessError(f"runtime lock artifact hash does not match the image build report for {name}")
        if name in {"lerobot", "feetech-servo-sdk"} and locked_item.get("artifact_sha256") != expected[
            "artifact_sha256"
        ]:
            raise ReadinessError(f"runtime lock does not bind the pinned {name} artifact")
        for field in ("installed_files_sha256", "record_sha256"):
            if locked_item.get(field) != actual.get(field):
                raise ReadinessError(f"installed {name} {field} does not match the runtime lock")
    complete = runtime_lock.get("complete_dependency_artifact_hashes")
    if not isinstance(complete, dict) or any(
        name not in complete for name in EXPECTED_PACKAGE_ARTIFACTS
    ):
        raise ReadinessError("runtime lock lacks complete dependency artifact hashes")
    for name, locked_item in complete.items():
        actual = installed.get(name)
        if actual is None or locked_item.get("version") != actual.get("version"):
            raise ReadinessError(f"installed dependency identity mismatch for {name}")
        if locked_item.get("artifact_sha256") != reported_artifacts.get(name, {}).get("artifact_sha256"):
            raise ReadinessError(f"installed dependency artifact does not match the image build report for {name}")
        if locked_item.get("installed_files_sha256") != actual.get("installed_files_sha256"):
            raise ReadinessError(f"installed dependency files do not match the runtime lock for {name}")
    _compare_sdk_record_to_runtime_lock(sdk_record, runtime_lock)
    python_record = runtime_lock.get("python")
    if not isinstance(python_record, dict) or python_record.get("sha256") != _sha256(Path(sys.executable).resolve()):
        raise ReadinessError("runtime lock does not bind the running CPython executable")
    mujoco_module = importlib.import_module("mujoco")
    mujoco_record = runtime_lock.get("mujoco_module")
    if (
        not isinstance(mujoco_record, dict)
        or mujoco_record.get("version") != mujoco_module.__version__
        or mujoco_record.get("sha256") != _sha256(Path(mujoco_module.__file__).resolve())
    ):
        raise ReadinessError("runtime lock does not bind the running MuJoCo module")
    return {
        "platform": "linux-amd64",
        "python": platform.python_version(),
        "image_digest": actual_image_digest,
        "packages": {name: installed[name]["version"] for name in EXPECTED_PACKAGE_ARTIFACTS},
        "installed_distribution_count": len(installed),
    }


def _write_calibration(directory: Path) -> None:
    calibration = {
        name: {
            "id": MOTOR_IDS[name],
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 0,
            "range_max": 4095,
        }
        for name in MOTOR_NAMES
    }
    (directory / "autoadapter-readiness.json").write_text(
        json.dumps(calibration, sort_keys=True), encoding="utf-8"
    )


def _real_sdk_factory(port: str, calibration_dir: Path) -> Any:
    if sys.platform == "linux" and not port.startswith("/dev/pts/"):
        raise ReadinessError(f"physical serial paths are forbidden; expected a project PTY, got {port}")
    try:
        from lerobot.motors.feetech import FeetechMotorsBus
        try:
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        except ImportError:
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        import scservo_sdk  # noqa: F401
    except ImportError as exc:
        raise ReadinessError(f"real pinned LeRobot/Feetech imports failed: {exc}") from exc
    _write_calibration(calibration_dir)
    config = SO101FollowerConfig(
        port=port,
        id="autoadapter-readiness",
        calibration_dir=calibration_dir,
    )
    follower = SO101Follower(config)
    if not isinstance(follower.bus, FeetechMotorsBus):
        raise ReadinessError("SO101Follower did not construct the real FeetechMotorsBus")
    return follower


def _expected_ticks_from_named_qpos(
    named_qpos: dict[str, float],
    *,
    gripper_range: tuple[float, float],
    gripper_tick_increases_qpos: bool,
) -> dict[int, int]:
    """Independently project trusted named MuJoCo state to raw SDK ticks."""
    result: dict[int, int] = {}
    for name in MOTOR_NAMES:
        value = float(named_qpos[name])
        if name == "gripper":
            low, high = gripper_range
            fraction = (value - low) / (high - low)
            if not gripper_tick_increases_qpos:
                fraction = 1.0 - fraction
        else:
            fraction = (math.degrees(value) + 180.0) / 360.0
        result[MOTOR_IDS[name]] = min(4095, max(0, int(round(fraction * 4095))))
    return result


def _expected_controls_from_action(
    action: dict[str, float],
    *,
    gripper_range: tuple[float, float],
    gripper_tick_increases_qpos: bool,
) -> dict[str, float]:
    """Independently compute the named actuator target implied by public SDK values."""
    ticks = public_positions_to_ticks(action)
    expected: dict[str, float] = {}
    for name in MOTOR_NAMES:
        fraction = ticks[MOTOR_IDS[name]] / 4095.0
        if name == "gripper":
            low, high = gripper_range
            if not gripper_tick_increases_qpos:
                fraction = 1.0 - fraction
            expected[name] = low + fraction * (high - low)
        else:
            expected[name] = math.radians(-180.0 + 360.0 * fraction)
    return expected


def _verify_model_closure(model_path: Path, morphology: dict[str, Any]) -> None:
    for relative, expected in morphology["mujoco"].get("asset_sha256", {}).items():
        asset = (model_path.parent / relative).resolve()
        try:
            asset.relative_to(model_path.parent.resolve())
        except ValueError as exc:
            raise ReadinessError("MuJoCo asset path escapes the model directory") from exc
        if not asset.is_file() or asset.is_symlink() or _sha256(asset) != expected:
            raise ReadinessError(f"MuJoCo asset closure mismatch: {relative}")


def _verify_runtime_measurements(
    runtime_lock: dict[str, Any], *, model_path: Path, reference_root: Path
) -> None:
    """Verify the measured model/source bytes bound by a captured runtime lock."""
    model = runtime_lock.get("model")
    if not isinstance(model, dict) or model.get("sha256") != _sha256(model_path):
        raise ReadinessError("runtime lock model hash does not match the loaded model bytes")
    source_hashes = runtime_lock.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise ReadinessError("runtime lock does not contain measured source hashes")
    for relative, expected in source_hashes.items():
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ReadinessError("runtime lock contains an unsafe source path")
        source = (reference_root / relative).resolve()
        try:
            source.relative_to(reference_root.resolve())
        except ValueError as exc:
            raise ReadinessError("runtime lock source path escapes the reference root") from exc
        if not source.is_file() or source.is_symlink() or _sha256(source) != expected:
            raise ReadinessError(f"runtime lock source hash mismatch: {relative}")


def _call_with_timeout(seconds: float, operation: Callable[[], Any]) -> Any:
    with _wall_timeout(seconds):
        return operation()


def run_readiness(
    *,
    run_id: str,
    manifest_path: str | Path,
    profile_path: str | Path,
    runtime_lock_path: str | Path,
    model_path: str | Path,
    gripper_tick_increases_qpos: bool,
    report_path: str | Path,
    reference_root: str | Path | None = None,
    identity_checker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    backend_factory: Callable[[], PositionBackend] | None = None,
    sdk_factory: Callable[[str, Path], Any] | None = None,
) -> dict[str, Any]:
    """Execute one no-retry readiness attempt and optionally write its report."""
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
    evidence_dir = report_path.parent / f"{report_path.stem}.evidence"
    if report_path.exists() or evidence_dir.exists():
        raise ReadinessError(f"readiness attempt directory already contains artifacts: {report_path.parent}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    runtime_lock = json.loads(runtime_lock_path.read_text(encoding="utf-8"))
    if tuple(profile.get("check_ids", ())) != CHECK_IDS:
        raise ReadinessError("readiness profile check order does not match the frozen Authority")
    limits = profile["time_limits"]
    expected_limits = {
        "per_check_wall_s": 60,
        "attempt_wall_s": 180,
        "transport_operation_wall_s": 2,
        "max_probe_simulation_s": 1.0,
        "cleanup_wall_s": 5,
        "hidden_retry_count": 0,
    }
    if limits != expected_limits:
        raise ReadinessError("readiness profile time limits do not match the frozen Authority")
    if limits.get("hidden_retry_count") != 0:
        raise ReadinessError("hidden retries are forbidden")
    injected = any(value is not None for value in (identity_checker, backend_factory, sdk_factory))
    morphology: dict[str, Any] | None = None
    sdk_record: dict[str, Any] | None = None
    translation_record: dict[str, Any] | None = None
    if not injected:
        # A formal readiness attempt must exercise the exact files selected by the
        # integration manifest.  Passing a different lock, profile, or model on the
        # command line must never be able to produce evidence for the selected route.
        if _sha256(runtime_lock_path) != manifest["runtime"]["lock_sha256"]:
            raise ReadinessError("runtime lock bytes do not match the integration manifest")
        if _sha256(profile_path) != manifest["readiness_profile_ref"]["sha256"]:
            raise ReadinessError("readiness profile bytes do not match the integration manifest")
        if "sdk_ref" not in manifest:
            raise ReadinessError("integration manifest is missing sdk_ref")
        sdk_path = _resolve_reference(reference_root, manifest["sdk_ref"], label="sdk_ref")
        sdk_record = json.loads(sdk_path.read_text(encoding="utf-8"))
        _validate_sdk_record(sdk_record)
        morphology_path = _resolve_reference(
            reference_root, manifest["morphology_ref"], label="morphology_ref"
        )
        morphology = json.loads(morphology_path.read_text(encoding="utf-8"))
        if _sha256(model_path) != morphology["mujoco"]["source_sha256"]:
            raise ReadinessError("MuJoCo model bytes do not match the selected morphology")
        _verify_model_closure(model_path, morphology)
        if runtime_lock.get("status") == "FROZEN_FROM_VERIFIED_LINUX_BUILD":
            _verify_runtime_measurements(
                runtime_lock, model_path=model_path, reference_root=reference_root
            )
        translation_path = _resolve_reference(
            reference_root, manifest["translation_ref"], label="translation_ref"
        )
        translation_record = json.loads(translation_path.read_text(encoding="utf-8"))
        configured_direction = translation_record["conversion"].get(
            "gripper_tick_increases_qpos"
        )
        if configured_direction is not gripper_tick_increases_qpos:
            raise ReadinessError("gripper direction does not match the selected Translation")
        implementation_refs = list(translation_record["implementation"]["source_files"])
        implementation_refs.append(translation_record["implementation"]["readiness_runner"])
        for index, reference in enumerate(implementation_refs):
            _resolve_reference(
                reference_root, reference, label=f"translation implementation source {index}"
            )
    if identity_checker is None:
        identity_checker = (
            (lambda lock: _real_identity(lock, sdk_record=sdk_record))
            if not injected
            else _real_identity
        )
    backend_factory = backend_factory or (
        lambda: MuJoCoSO101Backend(
            model_path, gripper_tick_increases_qpos=gripper_tick_increases_qpos
        )
    )
    sdk_factory = sdk_factory or _real_sdk_factory

    started_at = _utc_now()
    attempt_start = time.monotonic()
    checks: list[dict[str, Any]] = []
    image_record = runtime_lock.get("image")
    evidence_image_digest = runtime_lock.get("oci_image_digest")
    if evidence_image_digest is None and isinstance(image_record, dict):
        evidence_image_digest = image_record.get("oci_digest")
    evidence: dict[str, Any] = {
        "bindings": {
            "runtime_lock_sha256": _sha256(runtime_lock_path),
            "model_sha256": _sha256(model_path),
            "source_hashes": runtime_lock.get("source_hashes", {}),
            "image_digest": evidence_image_digest,
        }
    }
    backend: PositionBackend | None = None
    translation: FeetechPTYTranslation | None = None
    follower: Any | None = None
    cleanup = {"verdict": "FAIL", "detail": "cleanup not reached"}
    identity_evidence: dict[str, Any] = {}

    def check(check_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        check_start = time.monotonic()
        try:
            with _wall_timeout(float(limits["per_check_wall_s"])):
                detail = operation()
            checks.append(
                {
                    "check_id": check_id,
                    "verdict": "PASS",
                    "duration_s": round(time.monotonic() - check_start, 6),
                    "evidence": detail,
                }
            )
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

    try:
        with _wall_timeout(float(limits["attempt_wall_s"])):
            def sdk_identity() -> dict[str, Any]:
                detail = dict(identity_checker(runtime_lock))
                identity_evidence.update(detail)
                return detail

            check("sdk_identity_load", sdk_identity)

            def hook_install() -> dict[str, Any]:
                nonlocal backend, translation
                backend = backend_factory()
                translation = FeetechPTYTranslation(backend)
                port = translation.install()
                return {"hook": "Linux PTY virtual STS3215", "port": port, "health": translation.health()}

            check("hook_install", hook_install)

            calibration_temp = TemporaryDirectory(prefix="autoadapter-so101-readiness-")
            try:
                def application_execution() -> dict[str, Any]:
                    nonlocal follower
                    if translation is None or not translation.is_open:
                        raise ReadinessError("hook_install did not establish the PTY")
                    follower = sdk_factory(translation.port, Path(calibration_temp.name))
                    _call_with_timeout(float(limits["transport_operation_wall_s"]), lambda: follower.connect(calibrate=False))
                    return {
                        "follower_class": type(follower).__name__,
                        "bus_class": type(follower.bus).__name__,
                        "real_sdk_required": not injected,
                    }

                check("real_sdk_application_execution", application_execution)

                def command_path() -> dict[str, Any]:
                    if follower is None or translation is None or backend is None:
                        raise ReadinessError("real SDK application path is unavailable")
                    before = backend.state()
                    before_goal_writes = translation.accepted_goal_writes
                    accepted = dict(
                        _call_with_timeout(
                            float(limits["transport_operation_wall_s"]),
                            lambda: follower.send_action(PROBE_ACTION),
                        )
                    )
                    required_goal_writes = before_goal_writes + len(MOTOR_NAMES)
                    if not translation.wait_for_goal_writes(
                        required_goal_writes, float(limits["transport_operation_wall_s"])
                    ):
                        raise ReadinessError("timed out waiting for PTY command delivery")
                    after = backend.state()
                    if before["ctrl"] == after["ctrl"]:
                        raise ReadinessError("real SDK command did not change MuJoCo actuator controls")
                    if before["qpos"] != after["qpos"] or before["qvel"] != after["qvel"]:
                        raise ReadinessError("command path wrote qpos/qvel instead of actuator control")
                    expected_controls = _expected_controls_from_action(
                        PROBE_ACTION,
                        gripper_range=tuple(after["gripper_control_range"]),
                        gripper_tick_increases_qpos=gripper_tick_increases_qpos,
                    )
                    errors = {
                        name: abs(after["named_ctrl"][name] - expected_controls[name])
                        for name in MOTOR_NAMES
                    }
                    allowed = float(
                        profile["numerical_tolerances"]["so_arm101"][
                            "mujoco_conversion_extra_rad"
                        ]
                    )
                    if max(errors.values()) > allowed:
                        raise ReadinessError(
                            f"SDK fields did not reach their same-named MuJoCo controls: {errors}"
                        )
                    return {
                        "accepted_action": accepted,
                        "goal_writes": translation.accepted_goal_writes,
                        "named_control_errors_rad": errors,
                    }

                check("sdk_to_mujoco_command", command_path)

                def observation_path() -> dict[str, Any]:
                    if follower is None or translation is None or backend is None:
                        raise ReadinessError("command/state route is unavailable")
                    simulation_seconds = min(0.25, float(limits["max_probe_simulation_s"]))
                    translation.step(simulation_seconds)
                    observation = dict(
                        _call_with_timeout(
                            float(limits["transport_operation_wall_s"]), follower.get_observation
                        )
                    )
                    sdk_ticks = public_positions_to_ticks(observation)
                    private_ticks = _expected_ticks_from_named_qpos(
                        backend.state()["named_qpos"],
                        gripper_range=tuple(backend.state()["gripper_control_range"]),
                        gripper_tick_increases_qpos=gripper_tick_increases_qpos,
                    )
                    tolerance = int(profile["numerical_tolerances"]["so_arm101"]["serial_roundtrip_ticks"])
                    errors = {str(motor_id): abs(sdk_ticks[motor_id] - private_ticks[motor_id]) for motor_id in private_ticks}
                    if max(errors.values()) > tolerance:
                        raise ReadinessError(f"SDK observation differs from MuJoCo state by more than one tick: {errors}")
                    return {"simulation_seconds": simulation_seconds, "observation": observation, "tick_errors": errors}

                check("mujoco_to_sdk_observation", observation_path)

                def reset_close() -> dict[str, Any]:
                    nonlocal follower, translation
                    if translation is None or backend is None:
                        raise ReadinessError("simulation session is unavailable")
                    translation.reset()
                    first = backend.state()
                    translation.step(min(0.01, float(limits["max_probe_simulation_s"])))
                    translation.reset()
                    second = backend.state()
                    tolerance = float(profile["numerical_tolerances"]["reset"]["qpos_absolute"])
                    for field in ("qpos", "qvel"):
                        if len(first[field]) != len(second[field]) or any(
                            abs(a - b) > tolerance for a, b in zip(first[field], second[field], strict=True)
                        ):
                            raise ReadinessError(f"consecutive reset {field} mismatch")
                    if morphology is not None:
                        declared = morphology["mujoco"]["reset"]
                        for field in ("qpos", "qvel"):
                            observed = first[field]
                            expected = declared[field]
                            if len(observed) != len(expected) or any(
                                abs(a - b) > tolerance
                                for a, b in zip(observed, expected, strict=True)
                            ):
                                raise ReadinessError(
                                    f"reset {field} does not match the selected morphology"
                                )
                    if follower is not None:
                        _call_with_timeout(float(limits["transport_operation_wall_s"]), follower.disconnect)
                        follower = None
                    translation.close()
                    if translation.is_open:
                        raise ReadinessError("translation did not close")
                    return {"two_resets_match": True, "closed": True}

                check("reset_close", reset_close)
            finally:
                calibration_temp.cleanup()
    except BaseException as exc:
        evidence["attempt_envelope_error"] = {"category": type(exc).__name__, "detail": str(exc)}
    finally:
        cleanup_start = time.monotonic()
        cleanup_errors = []
        try:
            with _wall_timeout(float(limits["cleanup_wall_s"])):
                if follower is not None:
                    try:
                        follower.disconnect()
                    except BaseException as exc:
                        cleanup_errors.append(f"follower: {exc}")
                if translation is not None:
                    try:
                        translation.close()
                    except BaseException as exc:
                        cleanup_errors.append(f"translation: {exc}")
                elif backend is not None:
                    try:
                        backend.close()
                    except BaseException as exc:
                        cleanup_errors.append(f"backend: {exc}")
        except BaseException as exc:
            cleanup_errors.append(f"cleanup envelope: {exc}")
        cleanup = {
            "verdict": "PASS" if not cleanup_errors else "FAIL",
            "duration_s": round(time.monotonic() - cleanup_start, 6),
            "errors": cleanup_errors,
        }

    # Always emit all six ordered results, including after an attempt-envelope failure.
    by_id = {item["check_id"]: item for item in checks}
    checks = []
    for check_id in CHECK_IDS:
        checks.append(
            by_id.get(
                check_id,
                {
                    "check_id": check_id,
                    "verdict": "FAIL",
                    "duration_s": 0.0,
                    "infrastructure_category": "attempt_envelope_failure",
                    "detail": "check did not complete inside the attempt envelope",
                },
            )
        )
    # Injected unit-test dependencies exercise mechanics but can never create a formal PASS artifact.
    if injected:
        checks[0]["verdict"] = "FAIL"
        checks[0]["infrastructure_category"] = "test_dependency_injection"
        checks[0]["detail"] = "mechanical unit fixture ran; real SDK identity was not used"

    evidence_dir = report_path.parent / f"{report_path.stem}.evidence"
    contract_checks: list[dict[str, Any]] = []
    for index, item in enumerate(checks, 1):
        evidence_payload = {
            "schema_version": "1.0.0",
            "check_id": item["check_id"],
            "duration_s": item.get("duration_s", 0.0),
            "detail": item.get("evidence", item.get("detail")),
            "model_sha256": _sha256(model_path),
            "attempt_envelope": evidence,
        }
        evidence_path = evidence_dir / f"{index:02d}_{item['check_id']}.json"
        _write_evidence(evidence_path, evidence_payload)
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
            "duration_s": cleanup.get("duration_s", 0.0),
            "errors": cleanup.get("errors", []),
        },
    )
    contract_cleanup = {
        "verdict": cleanup["verdict"],
        "evidence_refs": [_reference(cleanup_evidence_path, reference_root)],
    }
    formal_pass = (
        all(item["verdict"] == "PASS" for item in contract_checks)
        and cleanup["verdict"] == "PASS"
    )
    runtime_sha256 = hashlib.sha256(canonical_bytes(manifest["runtime"])).hexdigest()
    environment_fingerprint = {
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "injected_test_dependencies": injected,
        "image_digest": identity_evidence.get("image_digest"),
        "installed_distribution_count": identity_evidence.get("installed_distribution_count"),
        "model_sha256": _sha256(model_path),
    }
    report = {
        "schema_version": "1.0.0",
        "attempt_id": f"{run_id}-so-arm101-{hashlib.sha256(started_at.encode()).hexdigest()[:12]}",
        "run_id": run_id,
        "integration_manifest_ref": _reference(manifest_path, reference_root),
        "runtime_sha256": runtime_sha256,
        "readiness_profile_ref": dict(manifest["readiness_profile_ref"]),
        "environment_fingerprint_sha256": hashlib.sha256(
            canonical_bytes(environment_fingerprint)
        ).hexdigest(),
        "dependency_sha256": {
            "morphology": manifest["morphology_ref"]["sha256"],
            "sdk": manifest["sdk_ref"]["sha256"],
            "translation": manifest["translation_ref"]["sha256"],
            "runtime_lock": manifest["runtime"]["lock_sha256"] or _sha256(runtime_lock_path),
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
