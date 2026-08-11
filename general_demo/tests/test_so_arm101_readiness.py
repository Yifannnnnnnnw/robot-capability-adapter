from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoadapter2.integrations.so_arm101.feetech_protocol import (
    INST_PING,
    MOTOR_IDS,
    MOTOR_NAMES,
    PRESENT_POSITION,
    decode_packet,
    encode_packet,
    encode_sync_read,
    encode_sync_write,
    pop_frames,
)
import autoadapter2.integrations.so_arm101.readiness as so_readiness
from autoadapter2.integration import (
    ExperimentIntegrationGate,
    stable_json_sha256,
    write_stable_json,
)
from autoadapter2.integrations.so_arm101.readiness import (
    CHECK_IDS,
    ReadinessError,
    public_positions_to_ticks,
    run_readiness,
)
from autoadapter2.integration.artifacts import validate_readiness_report


ROOT = Path(__file__).resolve().parents[1]


class FakeBackend:
    timestep = 0.005

    def __init__(self) -> None:
        self.qpos = [0.0] * 6
        self.qvel = [0] * 6
        self.ctrl = [0.0] * 6

    @staticmethod
    def _tick_to_value(motor_id, tick):
        fraction = tick / 4095.0
        if motor_id == 6:
            return -0.17453 + fraction * (1.74533 + 0.17453)
        return (fraction * 2.0 - 1.0) * 3.141592653589793

    @staticmethod
    def _value_to_tick(motor_id, value):
        if motor_id == 6:
            fraction = (value + 0.17453) / (1.74533 + 0.17453)
        else:
            fraction = (value / 3.141592653589793 + 1.0) / 2.0
        return min(4095, max(0, round(fraction * 4095)))

    def set_goal_ticks(self, values):
        for motor_id, tick in values.items():
            self.ctrl[motor_id - 1] = self._tick_to_value(motor_id, tick)

    def present_ticks(self, motor_ids):
        return {
            motor_id: self._value_to_tick(motor_id, self.qpos[motor_id - 1])
            for motor_id in motor_ids
        }

    def step(self, seconds):
        del seconds
        self.qpos = list(self.ctrl)
        self.qvel = [0] * 6

    def reset(self):
        self.qpos = [0.0] * 6
        self.qvel = [0] * 6
        self.ctrl = [0.0] * 6

    def state(self):
        return {
            "qpos": list(self.qpos),
            "qvel": list(self.qvel),
            "ctrl": list(self.ctrl),
            "named_qpos": dict(zip(MOTOR_NAMES, self.qpos, strict=True)),
            "named_ctrl": dict(zip(MOTOR_NAMES, self.ctrl, strict=True)),
            "gripper_control_range": [-0.17453, 1.74533],
        }

    def close(self):
        pass


class FakeFollower:
    def __init__(self, port: str) -> None:
        self.port = port
        self.fd = None
        self.bus = SimpleNamespace(is_connected=False)
        self.is_connected = False

    def _responses(self, expected: int) -> list[bytes]:
        assert self.fd is not None
        buffer = bytearray()
        frames: list[bytes] = []
        while len(frames) < expected:
            readable, _, _ = select.select([self.fd], [], [], 1.0)
            if not readable:
                raise TimeoutError("fake SDK timed out waiting for PTY status")
            buffer.extend(os.read(self.fd, 4096))
            frames.extend(pop_frames(buffer))
        return frames

    def connect(self, calibrate=False):
        assert calibrate is False
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY)
        os.write(self.fd, encode_packet(1, INST_PING))
        assert decode_packet(self._responses(1)[0]).motor_id == 1
        self.is_connected = True
        self.bus.is_connected = True

    def send_action(self, action):
        ticks = public_positions_to_ticks(dict(action))
        assert self.fd is not None
        os.write(self.fd, encode_sync_write(42, 2, ticks))
        return dict(action)

    def get_observation(self):
        assert self.fd is not None
        os.write(self.fd, encode_sync_read(PRESENT_POSITION[0], 2, MOTOR_IDS.values()))
        ticks = {
            packet.motor_id: int.from_bytes(packet.params, "little")
            for packet in (decode_packet(frame) for frame in self._responses(6))
        }
        result = {}
        for name in MOTOR_NAMES:
            tick = ticks[MOTOR_IDS[name]]
            result[f"{name}.pos"] = tick * 100.0 / 4095 if name == "gripper" else tick * 360.0 / 4095 - 180.0
        return result

    def disconnect(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.is_connected = False
        self.bus.is_connected = False


def test_injected_mechanics_exercise_all_six_checks_but_cannot_claim_formal_pass(tmp_path: Path) -> None:
    manifest = tmp_path / "integration_manifest.json"
    profile = tmp_path / "profile.json"
    runtime_lock = tmp_path / "runtime-lock.json"
    manifest.write_bytes((ROOT / "integrations/so-arm101/integration_manifest.json").read_bytes())
    profile.write_bytes((ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json").read_bytes())
    runtime_lock.write_bytes((ROOT / "environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json").read_bytes())
    model = tmp_path / "model.xml"
    model.write_text("<mujoco/>", encoding="utf-8")
    backend = FakeBackend()
    report = run_readiness(
        run_id="unit-fake-route",
        manifest_path=manifest,
        profile_path=profile,
        runtime_lock_path=runtime_lock,
        model_path=model,
        gripper_tick_increases_qpos=True,
        report_path=tmp_path / "report.json",
        reference_root=tmp_path,
        identity_checker=lambda _lock: {"identity": "TEST_ONLY"},
        backend_factory=lambda: backend,
        sdk_factory=lambda port, _calibration: FakeFollower(port),
    )
    assert [item["check_id"] for item in report["checks"]] == list(CHECK_IDS)
    assert report["checks"][0]["verdict"] == "FAIL"
    assert report["checks"][0]["infrastructure_category"] == "test_dependency_injection"
    assert all(item["verdict"] == "PASS" for item in report["checks"][1:]), report
    assert report["cleanup"]["verdict"] == "PASS"
    assert report["verdict"] == "FAIL"
    assert report["readiness_profile_ref"] == json.loads(manifest.read_text(encoding="utf-8"))["readiness_profile_ref"]
    validate_readiness_report(report)


def test_public_position_conversion_requires_exact_six_finite_fields() -> None:
    values = {f"{name}.pos": 0.0 for name in MOTOR_NAMES}
    values["gripper.pos"] = 50.0
    ticks = public_positions_to_ticks(values)
    assert set(ticks) == set(MOTOR_IDS.values())
    assert ticks[6] == 2047
    values.pop("wrist_roll.pos")
    try:
        public_positions_to_ticks(values)
    except Exception as exc:
        assert "exactly match" in str(exc)
    else:
        raise AssertionError("missing SDK field was accepted")


def test_readiness_refuses_to_reuse_an_attempt_artifact(tmp_path: Path) -> None:
    report_path = tmp_path / "attempt" / "readiness_report.json"
    report_path.parent.mkdir()
    report_path.write_text("immutable", encoding="utf-8")
    with pytest.raises(ReadinessError, match="already contains artifacts"):
        run_readiness(
            run_id="reused-attempt",
            manifest_path=ROOT / "integrations/so-arm101/integration_manifest.json",
            profile_path=ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json",
            runtime_lock_path=ROOT / "environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json",
            model_path=tmp_path / "model.xml",
            gripper_tick_increases_qpos=True,
            report_path=report_path,
            reference_root=tmp_path,
        )


def test_linux_launcher_uses_local_docker_image_id() -> None:
    launcher = (ROOT / "scripts/run_so_arm101_readiness_linux.sh").read_text(encoding="utf-8")
    readme = (ROOT / "environments/so-arm101-linux-amd64/1.0.0/README.md").read_text(encoding="utf-8")
    assert "--format '{{.Id}}'" in launcher
    assert "--format '{{.Id}}'" in readme
    assert ".Descriptor.Digest" not in launcher
    assert ".Descriptor.Digest" not in readme


def test_runtime_inventory_skips_only_unpinned_local_egg_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Distribution:
        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    local = Distribution("autoadapter2-general-demo")
    required = Distribution("mujoco")
    monkeypatch.setattr(
        so_readiness.importlib.metadata,
        "distributions",
        lambda: [local, required],
    )

    def fingerprint(distribution: Distribution) -> dict[str, object]:
        if distribution is local:
            raise ReadinessError("no regular RECORD file")
        return {
            "version": "3.3.6",
            "installed_files_sha256": "1" * 64,
            "record_sha256": "2" * 64,
            "file_count": 1,
        }

    monkeypatch.setattr(so_readiness, "_installed_distribution_fingerprint", fingerprint)
    assert set(so_readiness._installed_distributions()) == {"mujoco"}

    monkeypatch.setattr(
        so_readiness.importlib.metadata,
        "distributions",
        lambda: [Distribution("mujoco")],
    )
    monkeypatch.setattr(
        so_readiness,
        "_installed_distribution_fingerprint",
        lambda _distribution: (_ for _ in ()).throw(ReadinessError("no regular RECORD file")),
    )
    with pytest.raises(ReadinessError, match="no regular RECORD"):
        so_readiness._installed_distributions()


def _run_formal_preflight_with_manifest(
    tmp_path: Path, manifest: dict[str, object], *, reference_root: Path
) -> None:
    manifest_path = tmp_path / "integration_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReadinessError):
        run_readiness(
            run_id="sdk-record-negative",
            manifest_path=manifest_path,
            profile_path=ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json",
            runtime_lock_path=ROOT / "environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json",
            model_path=tmp_path / "model.xml",
            gripper_tick_increases_qpos=True,
            report_path=tmp_path / "readiness_report.json",
            reference_root=reference_root,
        )


def test_formal_readiness_rejects_missing_sdk_ref(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "integrations/so-arm101/integration_manifest.json").read_text(encoding="utf-8")
    )
    del manifest["sdk_ref"]
    _run_formal_preflight_with_manifest(tmp_path, manifest, reference_root=ROOT)


def test_formal_readiness_rejects_sdk_ref_hash_mismatch(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "integrations/so-arm101/integration_manifest.json").read_text(encoding="utf-8")
    )
    manifest["sdk_ref"]["sha256"] = "0" * 64
    _run_formal_preflight_with_manifest(tmp_path, manifest, reference_root=ROOT)


def test_formal_readiness_rejects_wrong_sdk_record(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "integrations/so-arm101/integration_manifest.json").read_text(encoding="utf-8")
    )
    sdk = json.loads(
        (ROOT / "libraries/sdks/lerobot-so101-follower/1.0.0/record.json").read_text(encoding="utf-8")
    )
    sdk["id"] = "wrong-sdk"
    sdk_path = tmp_path / "sdk.json"
    sdk_path.write_text(json.dumps(sdk), encoding="utf-8")
    manifest["sdk_ref"] = {
        "path": sdk_path.name,
        "sha256": hashlib.sha256(sdk_path.read_bytes()).hexdigest(),
    }
    _run_formal_preflight_with_manifest(tmp_path, manifest, reference_root=tmp_path)


def test_launcher_report_profile_ref_passes_experiment_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifact-root"
    root.mkdir()
    repository_root = ROOT.parent
    manifest = json.loads(
        (ROOT / "integrations/so-arm101/integration_manifest.json").read_text(encoding="utf-8")
    )

    def write_ref(relative: str, value: object) -> dict[str, str]:
        path = root / relative
        return {"path": relative, "sha256": write_stable_json(path, value)}

    morphology = json.loads(
        (repository_root / manifest["morphology_ref"]["path"]).read_text(encoding="utf-8")
    )
    manifest["morphology_ref"] = write_ref(manifest["morphology_ref"]["path"], morphology)

    sdk = json.loads(
        (repository_root / manifest["sdk_ref"]["path"]).read_text(encoding="utf-8")
    )
    sdk["runtime"]["container_digest_status"] = "VERIFIED"
    manifest["sdk_ref"] = write_ref(manifest["sdk_ref"]["path"], sdk)

    translation = json.loads(
        (repository_root / manifest["translation_ref"]["path"]).read_text(encoding="utf-8")
    )
    translation.update(status="READY", conformance_status="PASS", unresolved=[])
    implementation_refs = list(translation["implementation"]["source_files"])
    implementation_refs.append(translation["implementation"]["readiness_runner"])
    for reference in implementation_refs:
        target = root / reference["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository_root / reference["path"], target)
    manifest["translation_ref"] = write_ref(manifest["translation_ref"]["path"], translation)

    profile_relative = manifest["readiness_profile_ref"]["path"]
    profile = json.loads((repository_root / profile_relative).read_text(encoding="utf-8"))
    profile_ref = write_ref(profile_relative, profile)
    attempt_profile = root / "attempt/readiness_profile.json"
    attempt_profile.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / profile_relative, attempt_profile)

    runtime_lock = root / "attempt/runtime-lock.json"
    runtime_lock.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        repository_root / "general_demo/environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json",
        runtime_lock,
    )
    manifest["readiness_profile_ref"] = profile_ref
    manifest["runtime"]["lock_sha256"] = hashlib.sha256(runtime_lock.read_bytes()).hexdigest()
    manifest.update(status="READY", unresolved_gaps=[])
    for check in manifest["compatibility_checks"]:
        check["verdict"] = "PASS"
    manifest_relative = "general_demo/integrations/so-arm101/integration_manifest.json"
    manifest_path = root / manifest_relative
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_stable_json(manifest_path, manifest)

    model_path = root / "attempt/model.xml"
    model_path.write_text("<mujoco/>", encoding="utf-8")
    real_sha256 = so_readiness._sha256
    expected_model_sha256 = morphology["mujoco"]["source_sha256"]

    def test_hash(path: Path) -> str:
        if path.resolve() == model_path.resolve():
            return expected_model_sha256
        return real_sha256(path)

    monkeypatch.setattr(so_readiness, "_sha256", test_hash)
    monkeypatch.setattr(so_readiness, "_verify_model_closure", lambda *_args: None)
    monkeypatch.setattr(
        so_readiness,
        "_real_identity",
        lambda _lock, *, sdk_record=None: {
            "platform": "linux-amd64",
            "python": "3.12",
            "image_digest": "sha256:" + "0" * 64,
            "installed_distribution_count": 3,
        },
    )
    monkeypatch.setattr(
        so_readiness,
        "MuJoCoSO101Backend",
        lambda _model, *, gripper_tick_increases_qpos: FakeBackend(),
    )
    monkeypatch.setattr(so_readiness, "_real_sdk_factory", lambda port, _calibration: FakeFollower(port))

    report_path = root / "runs/run-1/readiness_report.json"
    report = run_readiness(
        run_id="run-1",
        manifest_path=manifest_path,
        profile_path=attempt_profile,
        runtime_lock_path=runtime_lock,
        model_path=model_path,
        gripper_tick_increases_qpos=True,
        report_path=report_path,
        reference_root=root,
    )
    assert report["verdict"] == "PASS"
    assert report["readiness_profile_ref"] == profile_ref

    manifest_ref = {
        "path": manifest_relative,
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    input_path = root / "runs/run-1/input.json"
    input_ref = {"path": input_path.relative_to(root).as_posix(), "sha256": write_stable_json(input_path, {"frozen": True})}
    report_ref = {
        "path": report_path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
    snapshot = {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "integration_manifest_ref": manifest_ref,
        "readiness_report_ref": report_ref,
        "runtime_sha256": stable_json_sha256(manifest["runtime"]),
        "readiness_profile_ref": profile_ref,
        "library_view_refs": [input_ref],
        "task_set_ref": input_ref,
        "g2_profile_ref": input_ref,
        "observation_profile_ref": input_ref,
        "model_prompt_config_ref": input_ref,
        "budget_ref": input_ref,
        "blue_line_input_refs": [input_ref],
        "sealed_artifact_refs": [],
    }
    snapshot_relative = "runs/run-1/run_snapshot.json"
    snapshot_path = root / snapshot_relative
    write_stable_json(snapshot_path, snapshot)
    result = ExperimentIntegrationGate(root).verify(
        manifest_relative,
        snapshot_relative,
        report_path.relative_to(root),
    )
    assert result.run_id == "run-1"
