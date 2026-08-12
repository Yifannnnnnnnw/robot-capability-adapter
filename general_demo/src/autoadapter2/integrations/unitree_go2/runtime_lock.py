"""Capture the small frozen runtime identity used by the Go2 experiment.

This is deliberately not a general environment attestation system.  It binds
only the image, Python runtime, direct Python dependency lock, pinned upstream
checkouts, MuJoCo asset closure, native libraries used by those dependencies,
and the few AutoAdapter sources that execute the readiness route.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from ...foundation.canonical import canonical_bytes


RUNTIME_ID = "unitree-go2-linux-amd64"
RUNTIME_VERSION = "1.0.0"
EXPERIMENTAL_ARM64_RUNTIME_ID = "unitree-go2-linux-arm64-experimental"
EXPERIMENTAL_ARM64_RUNTIME_VERSION = "1.0.0"
EXPERIMENTAL_ARM64_RUNTIME_STATUS = "EXPERIMENTAL_FROZEN_FROM_VERIFIED_LINUX_ARM64_BUILD"
SDK_COMMIT = "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5"
MUJOCO_COMMIT = "ae6a8403e272733e9996ef59990880330496177f"
DIRECT_DISTRIBUTIONS = {
    "unitree_sdk2py": ("unitree-sdk2py", "1.0.1"),
    "cyclonedds": ("cyclonedds", "0.10.2"),
    "mujoco": ("mujoco", "3.3.6"),
    "numpy": ("numpy", "2.2.6"),
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeLockError(RuntimeError):
    """The live environment does not match the Go2 runtime contract."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _records_digest(records: list[dict[str, Any]]) -> str:
    if not records:
        raise RuntimeLockError("cannot hash an empty runtime artifact")
    return _sha256_bytes(canonical_bytes(records))


def _distribution_record(
    public_name: str, distribution_name: str, expected_version: str
) -> tuple[dict[str, Any], list[Path]]:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeLockError(f"missing distribution: {distribution_name}") from exc
    if distribution.version != expected_version:
        raise RuntimeLockError(
            f"distribution mismatch: {distribution_name}=={distribution.version}, "
            f"expected {expected_version}"
        )
    records: list[dict[str, Any]] = []
    native_files: list[Path] = []
    for member in sorted(distribution.files or (), key=lambda item: str(item)):
        path = Path(distribution.locate_file(member)).resolve()
        if path.is_symlink() or not path.is_file():
            raise RuntimeLockError(f"missing distribution member: {distribution_name}:{member}")
        records.append(
            {
                "path": str(member),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
        if ".so" in path.name:
            native_files.append(path)
    return (
        {
            "name": public_name,
            "version": expected_version,
            "artifact_sha256": _records_digest(records),
            "file_count": len(records),
        },
        native_files,
    )


def _tree_digest(root: Path, relative_paths: Iterable[Path]) -> tuple[str, int]:
    root = root.resolve()
    records: list[dict[str, Any]] = []
    for relative in sorted(set(relative_paths), key=lambda item: item.as_posix()):
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise RuntimeLockError(f"tree member escapes root: {relative}") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeLockError(f"tree member missing or symlinked: {relative}")
        records.append(
            {
                "path": relative.as_posix(),
                "size": candidate.stat().st_size,
                "sha256": _sha256_file(candidate),
            }
        )
    return _records_digest(records), len(records)


def _git_record(repository: str, checkout: Path, expected_commit: str) -> dict[str, Any]:
    actual_commit = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual_commit != expected_commit:
        raise RuntimeLockError(f"checkout commit mismatch for {checkout}: {actual_commit}")
    output = subprocess.check_output(["git", "-C", str(checkout), "ls-files", "-z"])
    members = [Path(value.decode("utf-8")) for value in output.split(b"\0") if value]
    digest, count = _tree_digest(checkout, members)
    return {
        "repository": repository,
        "commit": actual_commit,
        "tracked_tree_sha256": digest,
        "tracked_file_count": count,
    }


def _compiler_directory(root: ET.Element, name: str) -> str:
    compiler = root.find("compiler")
    if compiler is None:
        return ""
    return compiler.attrib.get(name, compiler.attrib.get("assetdir", ""))


def _mjcf_closure(entrypoint: Path, closure_root: Path) -> list[Path]:
    closure_root = closure_root.resolve()
    pending = [entrypoint.resolve()]
    discovered: set[Path] = set()
    while pending:
        xml_path = pending.pop()
        try:
            xml_path.relative_to(closure_root)
        except ValueError as exc:
            raise RuntimeLockError(f"MJCF include escapes closure root: {xml_path}") from exc
        if xml_path in discovered:
            continue
        if xml_path.is_symlink() or not xml_path.is_file():
            raise RuntimeLockError(f"missing MJCF member: {xml_path}")
        discovered.add(xml_path)
        root = ET.parse(xml_path).getroot()
        for include in root.findall(".//include"):
            filename = include.attrib.get("file")
            if not filename:
                raise RuntimeLockError(f"MJCF include lacks file: {xml_path}")
            pending.append((xml_path.parent / filename).resolve())
        for tag, directory_name in (
            ("mesh", "meshdir"),
            ("texture", "texturedir"),
            ("hfield", "assetdir"),
        ):
            directory = _compiler_directory(root, directory_name)
            for asset in root.findall(f".//asset/{tag}"):
                filename = asset.attrib.get("file")
                if filename:
                    discovered.add((xml_path.parent / directory / filename).resolve())
    for path in discovered:
        try:
            path.relative_to(closure_root)
        except ValueError as exc:
            raise RuntimeLockError(f"MJCF asset escapes closure root: {path}") from exc
        if path.is_symlink() or not path.is_file():
            raise RuntimeLockError(f"missing MJCF asset: {path}")
    return sorted(discovered, key=lambda item: item.as_posix())


def _model_record(entrypoint: Path, closure_root: Path) -> dict[str, Any]:
    try:
        import mujoco
        import numpy as np
    except ImportError as exc:
        raise RuntimeLockError("mujoco and numpy are required") from exc
    closure_root = closure_root.resolve()
    closure = _mjcf_closure(entrypoint, closure_root)
    members = [path.relative_to(closure_root) for path in closure]
    closure_digest, _ = _tree_digest(closure_root, members)
    model = mujoco.MjModel.from_xml_path(str(entrypoint))
    key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if key < 0:
        raise RuntimeLockError("Go2 model lacks home keyframe")
    first = mujoco.MjData(model)
    second = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, first, key)
    mujoco.mj_resetDataKeyframe(model, second, key)
    qpos_error = float(np.max(np.abs(first.qpos - second.qpos), initial=0.0))
    qvel_error = float(np.max(np.abs(first.qvel - second.qvel), initial=0.0))
    if qpos_error > 1e-9 or qvel_error > 1e-9:
        raise RuntimeLockError("two complete Go2 resets differ beyond 1e-9")
    return {
        "path": entrypoint.relative_to(closure_root).as_posix(),
        "sha256": _sha256_file(entrypoint),
        "asset_closure_sha256": closure_digest,
        "asset_files": [
            {"path": member.as_posix(), "sha256": _sha256_file(closure_root / member)}
            for member in members
        ],
        "complete_asset_closure_verified": True,
        "model_signature": {
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "nsensor": int(model.nsensor),
            "nkey": int(model.nkey),
            "timestep": float(model.opt.timestep),
        },
        "reset_verification": {
            "keyframe": "home",
            "qpos_length": len(first.qpos),
            "qvel_length": len(first.qvel),
            "max_qpos_error": qpos_error,
            "max_qvel_error": qvel_error,
        },
    }


def _capture_runtime_lock(
    *,
    image_digest: str,
    sdk_checkout: str | Path = "/opt/unitree_sdk2_python",
    mujoco_checkout: str | Path = "/opt/unitree_mujoco",
    model_path: str | Path = "/opt/unitree_mujoco/unitree_robots/go2/scene.xml",
    dependency_lock_path: str | Path = "/opt/autoadapter/python-requirements.lock",
    runtime_id: str,
    runtime_version: str,
    runtime_status: str,
    architecture: str,
    required_machine: str | tuple[str, ...],
    crc_library: str,
    required_machine_label: str | None = None,
    include_experimental_session_source: bool = False,
) -> dict[str, Any]:
    """Capture one explicitly selected Go2 runtime closure."""

    required_machines = (
        (required_machine,) if isinstance(required_machine, str) else required_machine
    )
    if sys.platform != "linux" or platform.machine().lower() not in required_machines:
        raise RuntimeLockError(
            "Go2 Runtime Lock capture requires Linux "
            f"{required_machine_label or required_machines[0]}"
        )
    if sys.version_info[:2] != (3, 10):
        raise RuntimeLockError("Go2 Runtime Lock capture requires CPython 3.10")
    if not _DIGEST.fullmatch(image_digest):
        raise RuntimeLockError("image digest must be sha256:<64 lowercase hex>")

    sdk_checkout = Path(sdk_checkout).resolve()
    mujoco_checkout = Path(mujoco_checkout).resolve()
    model_path = Path(model_path).resolve()
    dependency_lock_path = Path(dependency_lock_path).resolve()
    if not dependency_lock_path.is_file():
        raise RuntimeLockError("Python dependency lock is missing")

    packages: list[dict[str, Any]] = []
    native_paths: set[Path] = set()
    for public_name, (distribution_name, version) in DIRECT_DISTRIBUTIONS.items():
        record, native = _distribution_record(public_name, distribution_name, version)
        packages.append(record)
        native_paths.update(native)
    packages.sort(key=lambda item: item["name"])
    packages_by_name = {item["name"]: item for item in packages}
    packages_by_name["unitree_sdk2py"]["source_commit"] = SDK_COMMIT

    crc_path = sdk_checkout / f"unitree_sdk2py/utils/lib/{crc_library}"
    native_paths.add(crc_path)
    native_records = []
    for path in sorted(native_paths, key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            raise RuntimeLockError(f"native dependency is missing: {path}")
        native_records.append({"path": path.as_posix(), "sha256": _sha256_file(path)})

    implementation_paths = [
        Path(__file__).resolve(),
        Path(__file__).with_name("bridge.py").resolve(),
        Path(__file__).with_name("readiness.py").resolve(),
        Path("/opt/autoadapter/general_demo/scripts/run_unitree_go2_readiness.py").resolve(),
    ]
    if include_experimental_session_source:
        implementation_paths.append(Path(__file__).with_name("session.py").resolve())
    return {
        "schema_version": "1.0.0",
        "runtime_id": runtime_id,
        "version": runtime_version,
        "status": runtime_status,
        "platform": {
            "os": "Ubuntu 22.04",
            "architecture": architecture,
            "python": "3.10",
            "python_version": platform.python_version(),
            "python_build": list(platform.python_build()),
            "glibc": list(platform.libc_ver()),
            "python_executable_sha256": _sha256_file(Path(sys.executable).resolve()),
        },
        "oci_image_digest": image_digest,
        "dependency_lock": {
            "path": dependency_lock_path.as_posix(),
            "sha256": _sha256_file(dependency_lock_path),
        },
        "packages": packages,
        "complete_dependency_artifact_hashes": {
            "dependency_lock_sha256": _sha256_file(dependency_lock_path),
            "package_artifact_sha256": {
                item["name"]: item["artifact_sha256"] for item in packages
            },
        },
        "native_library_fingerprints": native_records,
        "source_checkouts": [
            _git_record(
                "https://github.com/unitreerobotics/unitree_sdk2_python",
                sdk_checkout,
                SDK_COMMIT,
            ),
            _git_record(
                "https://github.com/unitreerobotics/unitree_mujoco",
                mujoco_checkout,
                MUJOCO_COMMIT,
            ),
        ],
        "mujoco_entrypoint": _model_record(model_path, mujoco_checkout),
        "implementation_source_hashes": [
            {"path": path.as_posix(), "sha256": _sha256_file(path)}
            for path in implementation_paths
        ],
        "unresolved": [],
    }


def capture_runtime_lock(
    *,
    image_digest: str,
    sdk_checkout: str | Path = "/opt/unitree_sdk2_python",
    mujoco_checkout: str | Path = "/opt/unitree_mujoco",
    model_path: str | Path = "/opt/unitree_mujoco/unitree_robots/go2/scene.xml",
    dependency_lock_path: str | Path = "/opt/autoadapter/python-requirements.lock",
) -> dict[str, Any]:
    """Capture the formal Linux amd64 Go2 runtime closure."""

    return _capture_runtime_lock(
        image_digest=image_digest,
        sdk_checkout=sdk_checkout,
        mujoco_checkout=mujoco_checkout,
        model_path=model_path,
        dependency_lock_path=dependency_lock_path,
        runtime_id=RUNTIME_ID,
        runtime_version=RUNTIME_VERSION,
        runtime_status="FROZEN_FROM_VERIFIED_LINUX_BUILD",
        architecture="amd64",
        required_machine=("x86_64", "amd64"),
        required_machine_label="amd64",
        crc_library="crc_amd64.so",
    )


def capture_experimental_arm64_runtime_lock(
    *,
    image_digest: str,
    sdk_checkout: str | Path = "/opt/unitree_sdk2_python",
    mujoco_checkout: str | Path = "/opt/unitree_mujoco",
    model_path: str | Path = "/opt/unitree_mujoco/unitree_robots/go2/scene.xml",
    dependency_lock_path: str | Path = "/opt/autoadapter/python-requirements.lock",
) -> dict[str, Any]:
    """Capture the opt-in native DGX arm64 Go2 runtime closure.

    This deliberately has its own runtime identity and non-formal status.  It
    is not a platform fallback for the formal amd64 contract.
    """

    return _capture_runtime_lock(
        image_digest=image_digest,
        sdk_checkout=sdk_checkout,
        mujoco_checkout=mujoco_checkout,
        model_path=model_path,
        dependency_lock_path=dependency_lock_path,
        runtime_id=EXPERIMENTAL_ARM64_RUNTIME_ID,
        runtime_version=EXPERIMENTAL_ARM64_RUNTIME_VERSION,
        runtime_status=EXPERIMENTAL_ARM64_RUNTIME_STATUS,
        architecture="arm64",
        required_machine="aarch64",
        crc_library="crc_aarch64.so",
        include_experimental_session_source=True,
    )


def verify_runtime_lock(runtime_lock: dict[str, Any]) -> dict[str, Any]:
    """Recompute the small closure and require byte-equivalent content."""

    image_digest = runtime_lock.get("oci_image_digest")
    if not isinstance(image_digest, str):
        raise RuntimeLockError("Runtime Lock lacks oci_image_digest")
    actual = capture_runtime_lock(image_digest=image_digest)
    if runtime_lock != actual:
        raise RuntimeLockError("Runtime Lock does not match the live Linux runtime")
    return actual


def verify_experimental_arm64_runtime_lock(runtime_lock: dict[str, Any]) -> dict[str, Any]:
    """Recompute and verify the explicit experimental native arm64 lock."""

    image_digest = runtime_lock.get("oci_image_digest")
    if not isinstance(image_digest, str):
        raise RuntimeLockError("experimental arm64 Runtime Lock lacks oci_image_digest")
    actual = capture_experimental_arm64_runtime_lock(image_digest=image_digest)
    if runtime_lock != actual:
        raise RuntimeLockError(
            "experimental arm64 Runtime Lock does not match the live Linux runtime"
        )
    return actual


def write_runtime_lock(path: str | Path, runtime_lock: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_bytes(runtime_lock))


def runtime_lock_json(runtime_lock: dict[str, Any]) -> str:
    return json.dumps(runtime_lock, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
