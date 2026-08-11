import math
from types import ModuleType

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
        frame_position=(2.0, 3.0, 4.0),
        frame_linear_velocity=(5.0, 6.0, 7.0),
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
    command = go2.Go2LowCmdLike(
        q=(1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6),
        dq=(2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7),
        kp=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
        kd=(0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6),
        tau=(-1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6),
    )
    assert command.dds_motor_slots == 20
    assert go2.evaluate_source_bound_bridge_equation(
        command,
        (0.25, -0.5, 0.75, -1, 1.25, -1.5, 1.75, -2, 2.25, -2.5, 2.75, -3),
        (0.5, -1, 1.5, -2, 2.5, -3, 3.5, -4, 4.5, -5, 5.5, -6),
    ) == (0.5, -1.0, 4.0, -4, 9.5, -9.0, 17.0, -16, 26.5, -25.0, 38.0, -36)


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
            frame_position=[0.0] * 3,
            frame_linear_velocity=[0.0] * 3,
        )


def test_mujoco_mapping_rejects_missing_state_field_and_swapped_destination():
    values = {
        "q": [0.0] * 12,
        "dq": [0.0] * 12,
        "actuator_force": [0.0] * 12,
        "imu_quaternion": [1.0, 0.0, 0.0, 0.0],
        "imu_gyroscope": [0.0] * 3,
        "imu_accelerometer": [0.0] * 3,
        "frame_position": [0.0] * 3,
        "frame_linear_velocity": [0.0] * 3,
    }
    del values["frame_linear_velocity"]
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
        go2.verify_unitree_mujoco_key_files(tmp_path)


def test_pinned_mujoco_source_path_mismatch_is_rejected(tmp_path):
    with pytest.raises(go2.Go2SourceEvidenceError):
        go2.verify_unitree_mujoco_key_files(tmp_path / "missing")


def test_pinned_sdk_key_file_hash_mismatch_is_rejected(tmp_path):
    for relative in go2.UNITREE_SDK2PY_KEY_FILE_HASHES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not the recorded SDK source")
    with pytest.raises(go2.Go2SourceEvidenceError, match="source hash mismatch"):
        go2.verify_unitree_sdk2py_key_files(tmp_path)


def test_real_unitree_distribution_versions_exact_match(monkeypatch):
    versions = {
        "unitree_sdk2py": "1.0.1",
        "cyclonedds": "0.10.2",
    }
    monkeypatch.setattr(go2.metadata, "version", versions.__getitem__)
    assert go2.verify_unitree_distribution_versions() == versions


def test_real_unitree_missing_distribution_is_unavailable(monkeypatch):
    def missing(distribution):
        raise go2.metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(go2.metadata, "version", missing)
    with pytest.raises(go2.Go2ProbeUnavailable):
        go2.verify_unitree_distribution_versions()


def test_real_unitree_wrong_distribution_version_is_version_error(monkeypatch):
    versions = {
        "unitree_sdk2py": "0.0.1",
        "cyclonedds": "0.10.2",
    }
    monkeypatch.setattr(go2.metadata, "version", versions.__getitem__)
    with pytest.raises(go2.Go2ProbeVersionError, match="unitree_sdk2py==1.0.1"):
        go2.verify_unitree_distribution_versions()


def test_real_unitree_probe_does_not_synthetic_pass_on_non_linux(monkeypatch):
    monkeypatch.setattr(go2.sys, "platform", "darwin")
    with pytest.raises(go2.Go2ProbeUnavailable, match="Linux"):
        go2.probe_real_unitree_sdk_symbols()


def _mock_sdk_modules(
    *,
    low_cmd_factory=None,
    low_cmd_type=None,
    low_state_factory=None,
    low_state_type=None,
    sport_factory=None,
    sport_type=None,
):
    class LowCmd:
        def __init__(self):
            self.motor_cmd = [object() for _ in range(20)]

    class LowState:
        def __init__(self):
            self.motor_state = [object() for _ in range(20)]

    class SportModeState:
        pass

    low_cmd_type = LowCmd if low_cmd_type is None else low_cmd_type
    low_state_type = LowState if low_state_type is None else low_state_type
    sport_type = SportModeState if sport_type is None else sport_type
    low_cmd_factory = (lambda: LowCmd()) if low_cmd_factory is None else low_cmd_factory
    low_state_factory = (lambda: LowState()) if low_state_factory is None else low_state_factory
    sport_factory = (lambda: SportModeState()) if sport_factory is None else sport_factory

    channel = ModuleType("unitree_sdk2py.core.channel")
    channel.ChannelFactoryInitialize = lambda *args: None
    channel.ChannelPublisher = type("ChannelPublisher", (), {})
    channel.ChannelSubscriber = type("ChannelSubscriber", (), {})
    default = ModuleType("unitree_sdk2py.idl.default")
    default.unitree_go_msg_dds__LowCmd_ = low_cmd_factory
    default.unitree_go_msg_dds__LowState_ = low_state_factory
    default.unitree_go_msg_dds__SportModeState_ = sport_factory
    dds = ModuleType("unitree_sdk2py.idl.unitree_go.msg.dds_")
    dds.LowCmd_ = low_cmd_type
    dds.LowState_ = low_state_type
    dds.SportModeState_ = sport_type
    return {
        "unitree_sdk2py.core.channel": channel,
        "unitree_sdk2py.idl.default": default,
        "unitree_sdk2py.idl.unitree_go.msg.dds_": dds,
    }


def _patch_mock_sdk(monkeypatch, modules):
    versions = {"unitree_sdk2py": "1.0.1", "cyclonedds": "0.10.2"}
    monkeypatch.setattr(go2.sys, "platform", "linux")
    monkeypatch.setattr(go2.metadata, "version", versions.__getitem__)

    def import_module(name):
        try:
            return modules[name]
        except KeyError as exc:
            raise ModuleNotFoundError(name) from exc

    monkeypatch.setattr(go2.importlib, "import_module", import_module)


def test_real_unitree_symbol_probe_checks_factory_type_shape_and_20_slots(monkeypatch):
    modules = _mock_sdk_modules()
    _patch_mock_sdk(monkeypatch, modules)
    result = go2.probe_real_unitree_sdk_symbols()
    assert result["dds_initialized"] is False
    assert result["route"] == "LOW_LEVEL_ONLY"
    assert result["container_widths"] == {"LowCmd_.motor_cmd": 20, "LowState_.motor_state": 20}
    assert "unitree_sdk2py.go2.sport.sport_client" not in result["modules"]


@pytest.mark.parametrize(
    "bad_shape",
    ["missing_channel", "non_callable_factory", "not_a_type", "wrong_instance", "twelve_slots", "twelve_state_slots"],
)
def test_real_unitree_symbol_probe_rejects_bad_factory_or_type_shape(monkeypatch, bad_shape):
    modules = _mock_sdk_modules()
    if bad_shape == "missing_channel":
        del modules["unitree_sdk2py.core.channel"].ChannelPublisher
    elif bad_shape == "non_callable_factory":
        modules["unitree_sdk2py.idl.default"].unitree_go_msg_dds__LowCmd_ = object()
    elif bad_shape == "not_a_type":
        modules["unitree_sdk2py.idl.unitree_go.msg.dds_"].LowCmd_ = 42
    elif bad_shape == "wrong_instance":
        modules["unitree_sdk2py.idl.default"].unitree_go_msg_dds__LowCmd_ = lambda: object()
    elif bad_shape == "twelve_slots":
        class TwelveSlotLowCmd:
            def __init__(self):
                self.motor_cmd = [object() for _ in range(12)]

        modules["unitree_sdk2py.idl.default"].unitree_go_msg_dds__LowCmd_ = TwelveSlotLowCmd
        modules["unitree_sdk2py.idl.unitree_go.msg.dds_"].LowCmd_ = TwelveSlotLowCmd
    elif bad_shape == "twelve_state_slots":
        class TwelveSlotLowState:
            def __init__(self):
                self.motor_state = [object() for _ in range(12)]

        modules["unitree_sdk2py.idl.default"].unitree_go_msg_dds__LowState_ = TwelveSlotLowState
        modules["unitree_sdk2py.idl.unitree_go.msg.dds_"].LowState_ = TwelveSlotLowState
    _patch_mock_sdk(monkeypatch, modules)
    with pytest.raises(go2.Go2ProbeSymbolError):
        go2.probe_real_unitree_sdk_symbols()


def test_sport_client_high_level_method_cannot_be_marked_as_low_level():
    assert go2.require_low_level_probe_route("LOW_LEVEL_ONLY") == "LOW_LEVEL_ONLY"
    with pytest.raises(go2.Go2ProbeScopeError):
        go2.require_low_level_probe_route("SportClient.StandUp")
    with pytest.raises(go2.Go2ProbeScopeError):
        go2.reject_high_level_sport_method("SportClient.Move")
