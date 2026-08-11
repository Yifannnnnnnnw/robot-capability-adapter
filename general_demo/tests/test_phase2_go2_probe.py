import math

import pytest

from probes.phase2 import unitree_go2_probe as go2


def _command_kwargs(**overrides):
    values = {
        "q": [0.1] * go2.ACTIVE_MOTOR_COUNT,
        "dq": [0.2] * go2.ACTIVE_MOTOR_COUNT,
        "kp": [2.0] * go2.ACTIVE_MOTOR_COUNT,
        "kd": [0.5] * go2.ACTIVE_MOTOR_COUNT,
        "tau": [0.3] * go2.ACTIVE_MOTOR_COUNT,
    }
    values.update(overrides)
    return values


def _state():
    return go2.MuJoCoLikeState(
        q=range(12),
        dq=range(12, 24),
        actuator_force=range(24, 36),
        imu_quaternion=(1.0, 0.0, 0.0, 0.0),
        imu_gyroscope=(0.1, 0.2, 0.3),
        imu_accelerometer=(1.1, 1.2, 1.3),
        base_position=(2.0, 3.0, 4.0),
        base_velocity=(5.0, 6.0, 7.0),
    )


def test_go2_active_order_is_explicit_and_not_worldbody_order():
    assert go2.GO2_MOTOR_NAMES == (
        "FR_hip",
        "FR_thigh",
        "FR_calf",
        "FL_hip",
        "FL_thigh",
        "FL_calf",
        "RR_hip",
        "RR_thigh",
        "RR_calf",
        "RL_hip",
        "RL_thigh",
        "RL_calf",
    )
    assert tuple(go2.GO2_MOTOR_INDEX.values()) == tuple(range(12))
    assert go2.GO2_MOTOR_JOINT_NAMES[0] == "FR_hip_joint"
    assert go2.GO2_MOTOR_JOINT_NAMES[3] == "FL_hip_joint"
    assert go2.GO2_LEG_IDS == (
        "FR_0",
        "FR_1",
        "FR_2",
        "FL_0",
        "FL_1",
        "FL_2",
        "RR_0",
        "RR_1",
        "RR_2",
        "RL_0",
        "RL_1",
        "RL_2",
    )


def test_low_command_accepts_exact_active_projection_and_equation():
    command = go2.Go2LowCmdLike(**_command_kwargs())
    assert command.dds_motor_slots == 20
    assert go2.translate_bridge_control_equation(command, [0.0] * 12, [0.0] * 12) == (
        0.6,
    ) * 12


@pytest.mark.parametrize(
    "field",
    ["q", "dq", "kp", "kd", "tau"],
)
def test_low_command_rejects_wrong_active_vector_length(field):
    values = _command_kwargs(**{field: [0.0] * 11})
    with pytest.raises(go2.Go2ProbeError):
        go2.Go2LowCmdLike(**values)


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_low_command_rejects_nan_and_inf(bad_value):
    with pytest.raises(go2.Go2ProbeError):
        go2.Go2LowCmdLike(**_command_kwargs(q=[bad_value] + [0.0] * 11))


def test_low_command_rejects_swapped_leg_order_and_wrong_index():
    swapped = list(go2.GO2_MOTOR_NAMES)
    swapped[0], swapped[3] = swapped[3], swapped[0]
    with pytest.raises(go2.Go2ProbeError):
        go2.Go2LowCmdLike(**_command_kwargs(motor_names=swapped))

    wrong_indices = list(range(12))
    wrong_indices[0] = 3
    with pytest.raises(go2.Go2ProbeError):
        go2.Go2LowCmdLike(**_command_kwargs(motor_indices=wrong_indices))


def test_duplicate_destination_and_20_vs_12_container_fail_closed():
    duplicate = list(go2.GO2_MOTOR_NAMES)
    duplicate[-1] = duplicate[0]
    with pytest.raises(go2.Go2ProbeError):
        go2.validate_active_motor_destinations(duplicate)
    with pytest.raises(go2.Go2ProbeError):
        go2.Go2LowCmdLike(**_command_kwargs(dds_motor_slots=12))
    with pytest.raises(go2.Go2ProbeError):
        go2.validate_dds_motor_slots(12)


def test_mujoco_sensor_data_layout_and_state_mapping_are_explicit():
    mapped = go2.map_mujoco_state_to_probe(go2.MuJoCoLikeState.from_sensor_data(range(52)))
    assert mapped.low_state.motor_state[0] == go2.MotorStateProbe(0, "FR_hip", 0.0, 12.0, 24.0)
    assert mapped.low_state.motor_state[-1] == go2.MotorStateProbe(11, "RL_calf", 11.0, 23.0, 35.0)
    assert mapped.low_state.imu_state == go2.IMUStateProbe(
        (36.0, 37.0, 38.0, 39.0),
        (40.0, 41.0, 42.0),
        (43.0, 44.0, 45.0),
    )
    assert mapped.sport_mode_state.position == (46.0, 47.0, 48.0)
    assert mapped.sport_mode_state.velocity == (49.0, 50.0, 51.0)
    assert mapped.sport_mode_state.topic == "rt/sportmodestate"
    assert mapped.low_state.dds_motor_slots == 20

    without_frame_sensor = go2.map_mujoco_state_to_probe(go2.MuJoCoLikeState.from_sensor_data(range(52)), imu_available=False)
    assert without_frame_sensor.low_state.imu_state is None


def test_mujoco_sensor_data_rejects_wrong_length_and_bad_quaternion():
    with pytest.raises(go2.Go2ProbeError):
        go2.MuJoCoLikeState.from_sensor_data(range(12))
    with pytest.raises(go2.Go2ProbeError):
        go2.MuJoCoLikeState(
            q=[0.0] * 12,
            dq=[0.0] * 12,
            actuator_force=[0.0] * 12,
            imu_quaternion=[0.0] * 3,
            imu_gyroscope=[0.0] * 3,
            imu_accelerometer=[0.0] * 3,
            base_position=[0.0] * 3,
            base_velocity=[0.0] * 3,
        )


def test_mujoco_mapping_rejects_missing_state_field_and_swapped_destination():
    values = {
        "q": [0.0] * 12,
        "dq": [0.0] * 12,
        "actuator_force": [0.0] * 12,
        "imu_quaternion": [1.0, 0.0, 0.0, 0.0],
        "imu_gyroscope": [0.0] * 3,
        "imu_accelerometer": [0.0] * 3,
        "base_position": [0.0] * 3,
        "base_velocity": [0.0] * 3,
    }
    del values["base_velocity"]
    with pytest.raises(go2.Go2ProbeError):
        go2.MuJoCoLikeState.from_mapping(values)

    swapped = list(go2.GO2_MOTOR_NAMES)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(go2.Go2ProbeError):
        go2.map_mujoco_state_to_probe(_state(), motor_destinations=swapped)


def test_pinned_mujoco_source_hash_mismatch_is_rejected(tmp_path):
    for relative in go2.UNITREE_MUJOCO_KEY_FILE_HASHES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not the pinned source")
    with pytest.raises(go2.Go2SourceEvidenceError, match="source hash mismatch"):
        go2.verify_pinned_unitree_mujoco_checkout(tmp_path)


def test_pinned_mujoco_source_path_mismatch_is_rejected(tmp_path):
    with pytest.raises(go2.Go2SourceEvidenceError):
        go2.verify_pinned_unitree_mujoco_checkout(tmp_path / "missing")


def test_real_unitree_distribution_identity_exact_match(monkeypatch):
    versions = {
        "unitree_sdk2py": "1.0.1",
        "cyclonedds": "0.10.2",
    }
    monkeypatch.setattr(go2.metadata, "version", versions.__getitem__)
    assert go2.verify_pinned_unitree_distribution_identity() == versions


def test_real_unitree_missing_distribution_is_unavailable(monkeypatch):
    def missing(distribution):
        raise go2.metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(go2.metadata, "version", missing)
    with pytest.raises(go2.Go2ProbeUnavailable):
        go2.verify_pinned_unitree_distribution_identity()


def test_real_unitree_wrong_distribution_version_is_identity_error(monkeypatch):
    versions = {
        "unitree_sdk2py": "0.0.1",
        "cyclonedds": "0.10.2",
    }
    monkeypatch.setattr(go2.metadata, "version", versions.__getitem__)
    with pytest.raises(go2.Go2ProbeIdentityError, match="unitree_sdk2py==1.0.1"):
        go2.verify_pinned_unitree_distribution_identity()


def test_real_unitree_probe_does_not_synthetic_pass_on_non_linux(monkeypatch):
    monkeypatch.setattr(go2.sys, "platform", "darwin")
    with pytest.raises(go2.Go2ProbeUnavailable, match="Linux"):
        go2.probe_real_unitree_sdk_symbols()


def test_sport_client_high_level_method_cannot_be_marked_as_low_level():
    assert go2.require_low_level_probe_route("LOW_LEVEL_ONLY") == "LOW_LEVEL_ONLY"
    with pytest.raises(go2.Go2ProbeScopeError):
        go2.require_low_level_probe_route("SportClient.StandUp")
    with pytest.raises(go2.Go2ProbeScopeError):
        go2.reject_high_level_sport_method("SportClient.Move")
