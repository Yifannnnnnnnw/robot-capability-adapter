from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.integrations.direct_mujoco import DirectMuJoCoLibraryConfig
from autoadapter2.orchestration.direct_mujoco_run import (
    DIRECT_MUJOCO_EXPERIMENTAL,
    DirectMuJoCoRunResolutionError,
    create_direct_mujoco_experiment,
    resolve_morphology_record,
)


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
    record = _record("synthetic-alpha")
    record["robot_configuration_id"] = "different-configuration"
    _write_record(tmp_path, record, path_configuration_id="synthetic-alpha")

    with pytest.raises(DirectMuJoCoRunResolutionError, match="does not match the requested path"):
        resolve_morphology_record("synthetic-alpha", "1.0.0", tmp_path, cache_root)
