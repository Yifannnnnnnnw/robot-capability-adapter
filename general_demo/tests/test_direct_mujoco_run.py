from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.integrations.direct_mujoco import DirectMuJoCoLibraryConfig
from autoadapter2.libraries.no_sdk_direct_mujoco import (
    NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH,
    NoSDKDirectMuJoCoRecordError,
    load_no_sdk_direct_mujoco_record,
)
from autoadapter2.orchestration.direct_mujoco_run import (
    DIRECT_MUJOCO_EXPERIMENTAL,
    DirectMuJoCoRunResolutionError,
    create_direct_mujoco_experiment,
    resolve_morphology_record,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_SENTINEL_SOURCE = REPO_ROOT / NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH


def _record(configuration_id: str, version: str = "1.0.0") -> dict[str, Any]:
    return {
        "record_type": "morphology",
        "id": f"record-{configuration_id}",
        "version": version,
        "robot_model_id": f"model-{configuration_id}",
        "robot_configuration_id": configuration_id,
        "joint_names": ["joint"],
        "actuator_names": ["actuator"],
        "sensor_names": ["sensor"],
        "mujoco": {
            "entrypoint": "assets/robot.xml",
            "reset": {"qpos": [0.0], "qvel": [0.0]},
            "frames": {"body_names": ["base"], "site_names": ["tip"]},
            "render": {"camera": "camera", "width": 16, "height": 12, "fps": 10},
        },
    }


def _write_record(
    repo_root: Path,
    record: dict[str, Any],
    *,
    path_configuration_id: str | None = None,
) -> Path:
    target = (
        repo_root
        / "general_demo"
        / "libraries"
        / "morphology"
        / str(path_configuration_id or record["robot_configuration_id"])
        / str(record["version"])
        / "record.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record), encoding="utf-8")
    return target


def _copy_sdk_sentinel(repo_root: Path) -> Path:
    target = repo_root / NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SDK_SENTINEL_SOURCE, target)
    return target


class _FakeSession:
    def __init__(self, config: DirectMuJoCoLibraryConfig) -> None:
        self.config = config
        self.actuator_names = config.actuator_names
        self.sdk = self
        self.closed = False

    def send_action(self, action: dict[str, float]) -> dict[str, Any]:
        return {"accepted": dict(action)}

    def step(self, seconds: float) -> dict[str, Any]:
        return {
            "mode": DIRECT_MUJOCO_EXPERIMENTAL,
            "status": "EXPERIMENTAL",
            "simulation_time_s": seconds,
        }

    def close(self) -> None:
        self.closed = True


class _FakeSandbox:
    def __init__(self, config: DirectMuJoCoLibraryConfig, *, session_factory: object) -> None:
        self.config = config
        self.session_factory = session_factory

    def run(self, source: str, probe: dict[str, Any]) -> dict[str, Any]:
        return {"status": "OK", "source": source, "probe": probe}


def test_two_configuration_ids_use_one_library_route_and_shared_construction(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "external-auto-adapter-1-cache"
    cache_root.mkdir()
    sentinel_path = _copy_sdk_sentinel(tmp_path)
    ids = ("synthetic-alpha", "synthetic-beta")
    for configuration_id in ids:
        _write_record(tmp_path, _record(configuration_id))

    seen: list[tuple[str, Path]] = []

    def session_factory(config: DirectMuJoCoLibraryConfig) -> _FakeSession:
        seen.append((config.robot_configuration_id, config.asset_root))
        return _FakeSession(config)

    def sandbox_factory(
        config: DirectMuJoCoLibraryConfig,
        *,
        session_factory: object,
    ) -> _FakeSandbox:
        assert session_factory is not None
        return _FakeSandbox(config, session_factory=session_factory)

    for configuration_id in ids:
        experiment = create_direct_mujoco_experiment(
            configuration_id,
            "1.0.0",
            tmp_path,
            cache_root,
            session_factory=session_factory,
            sandbox_factory=sandbox_factory,
        )
        try:
            assert experiment.package.record_path == (
                tmp_path
                / "general_demo"
                / "libraries"
                / "morphology"
                / configuration_id
                / "1.0.0"
                / "record.json"
            )
            assert experiment.package.config.robot_configuration_id == configuration_id
            assert experiment.package.config.asset_root == cache_root.resolve()
            assert experiment.package.sdk_record.record_path == sentinel_path.resolve()
            assert experiment.package.sdk_record.record["id"] == "no-sdk-direct-mujoco"
            result = experiment.run_action()
            assert result["mode"] == DIRECT_MUJOCO_EXPERIMENTAL
            assert result["status"] == "EXPERIMENTAL"
            assert result["robot_configuration_id"] == configuration_id
            assert result["record_path"].endswith(
                f"general_demo/libraries/morphology/{configuration_id}/1.0.0/record.json"
            )
            assert result["asset_cache_root"] == str(cache_root.resolve())
        finally:
            experiment.close()

    assert seen == [(configuration_id, cache_root.resolve()) for configuration_id in ids]


def test_shared_sdk_sentinel_is_configuration_neutral_and_empty() -> None:
    loaded = load_no_sdk_direct_mujoco_record(REPO_ROOT)

    assert loaded.record["record_type"] == "sdk"
    assert loaded.record["id"] == "no-sdk-direct-mujoco"
    assert loaded.record["version"] == "1.0.0"
    assert loaded.record["execution_mode"] == DIRECT_MUJOCO_EXPERIMENTAL
    assert loaded.record["sdk_status"] == "NOT_APPLICABLE"
    assert loaded.record["transport"] == "NONE"
    assert "robot_configuration_id" not in loaded.record
    for field in (
        "public_symbols",
        "operations",
        "action_fields",
        "observation_fields",
        "packages",
        "dependencies",
    ):
        assert loaded.record[field] == []


def test_path_traversal_missing_record_and_wrong_route_fail_clearly(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (tmp_path / "general_demo" / "libraries" / "morphology").mkdir(parents=True)

    with pytest.raises(DirectMuJoCoRunResolutionError, match="safe path component"):
        resolve_morphology_record("../escape", "1.0.0", tmp_path, cache_root)

    with pytest.raises(DirectMuJoCoRunResolutionError, match="exact route"):
        resolve_morphology_record("missing", "1.0.0", tmp_path, cache_root)

    wrong_root = tmp_path / "general_demo" / "libraries"
    with pytest.raises(DirectMuJoCoRunResolutionError, match="general_demo/libraries/morphology"):
        resolve_morphology_record("missing", "1.0.0", wrong_root, cache_root)


def test_record_identity_must_match_the_requested_route(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    _copy_sdk_sentinel(tmp_path)
    record = _record("synthetic-alpha")
    record["robot_configuration_id"] = "different-configuration"
    _write_record(tmp_path, record, path_configuration_id="synthetic-alpha")

    with pytest.raises(DirectMuJoCoRunResolutionError, match="does not match the requested path"):
        resolve_morphology_record("synthetic-alpha", "1.0.0", tmp_path, cache_root)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("id", "other-sdk"),
        ("version", "2.0.0"),
        ("operations", ["send_action"]),
        ("public_symbols", ["DirectMuJoCoFacade"]),
        ("execution_mode", "OTHER"),
        ("sdk_status", "AVAILABLE"),
    ),
)
def test_shared_sdk_sentinel_rejects_non_experimental_or_nonempty_surfaces(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    sentinel_path = _copy_sdk_sentinel(tmp_path)
    mutated = json.loads(sentinel_path.read_text(encoding="utf-8"))
    mutated[field] = value
    sentinel_path.write_text(json.dumps(mutated), encoding="utf-8")
    _write_record(tmp_path, _record("synthetic-alpha"))

    with pytest.raises(NoSDKDirectMuJoCoRecordError):
        resolve_morphology_record("synthetic-alpha", "1.0.0", tmp_path, cache_root)
