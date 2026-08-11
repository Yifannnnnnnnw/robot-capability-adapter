"""Production SO-ARM101 EvaluationRobotSession.

The session is the private Harness-side owner of one exact
``SO101Follower -> FeetechMotorsBus -> PTY -> MuJoCo`` route.  Candidate code
receives the real ``SO101Follower`` instance only.  The MuJoCo model, PTY
translation, task scene, truth measurements, and video capture remain behind
this module's private boundary.

The task scene is composed around the caller-selected, pinned official robot
MJCF.  Composition happens in a temporary file so the admitted robot asset is
not copied or modified.  Normal execution only writes actuator controls via
the existing translation; qpos/qvel/mocap/object writes are confined to
``reset``.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ...demo import ValidationEvidence
from ...evaluation import FrozenVideoProfile, RGBFrame
from ...validation import HarnessInvocation, MeasurementSample
from .feetech_protocol import MOTOR_IDS, MOTOR_NAMES
from .translation import FeetechPTYTranslation, MuJoCoSO101Backend


ROBOT_MODEL_ID = "so-arm101"
ROBOT_CONFIGURATION_ID = "so-arm101-follower-stock-gripper"
EVIDENCE_SCOPE = "SDK_GROUNDED_SIMULATION"
RESET_TOLERANCE = 1e-9
DEFAULT_TIMESTEP = 0.005
DEFAULT_INVOCATION_WINDOW_S = 0.25
DEFAULT_TRANSPORT_WAIT_S = 2.0
DEFAULT_VIDEO_PROFILE = FrozenVideoProfile(
    profile_id="general-demo-external-evaluation",
    profile_version="1.0.0",
    camera="external-evaluation",
    view="robot-and-task-scene",
    fps=30,
    width=640,
    height=480,
    container="matroska",
    codec="ffv1",
)


class SOArm101SessionError(RuntimeError):
    """The private SO-ARM101 session could not open or collect evidence."""


def _repo_general_demo_root() -> Path:
    # session.py -> so_arm101 -> integrations -> autoadapter2 -> src -> general_demo
    return Path(__file__).resolve().parents[4]


def _default_scene_path() -> Path:
    return (
        _repo_general_demo_root()
        / "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene.xml"
    )


def _default_scene_config_path() -> Path:
    return (
        _repo_general_demo_root()
        / "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene_config.json"
    )


def _default_task_instances_path() -> Path:
    return (
        _repo_general_demo_root()
        / "libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/task_instances_private.json"
    )


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SOArm101SessionError(f"{label} must be non-empty text")
    return value.strip()


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _number(value: Any, label: str) -> float:
    if not _finite(value):
        raise SOArm101SessionError(f"{label} must be finite")
    return float(value)


def _vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != length:
        raise SOArm101SessionError(f"{label} must contain exactly {length} values")
    result = [_number(item, f"{label}[{index}]") for index, item in enumerate(value)]
    return result


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second, strict=True)))


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in values))


def _deepcopy_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SOArm101SessionError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _read_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise SOArm101SessionError(f"{label} must be a regular file: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SOArm101SessionError(f"could not read {label}: {resolved}") from exc
    return _deepcopy_mapping(value, label)


def _default_follower_factory(port: str, calibration_dir: Path) -> Any:
    """Construct the pinned real LeRobot SO101Follower.

    This is intentionally the same public SDK construction used by the
    readiness runner.  No local SDK facade is substituted in the formal path.
    """

    try:
        from lerobot.motors.feetech import FeetechMotorsBus
        try:
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        except ImportError:
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    except ImportError as exc:  # pragma: no cover - exact Linux runtime only
        raise SOArm101SessionError("real LeRobot 0.6.0 SO101Follower imports failed") from exc

    calibration_dir.mkdir(parents=True, exist_ok=True)
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
    (calibration_dir / "autoadapter-so101.json").write_text(
        json.dumps(calibration, sort_keys=True), encoding="utf-8"
    )
    follower = SO101Follower(
        SO101FollowerConfig(
            port=port,
            id="autoadapter-so101-evaluation",
            calibration_dir=calibration_dir,
        )
    )
    if not isinstance(follower.bus, FeetechMotorsBus):
        raise SOArm101SessionError("SO101Follower did not construct the real FeetechMotorsBus")
    return follower


def _load_frame_capture_factory() -> Callable[..., Any]:
    """Resolve the shared capture implementation without defining a duplicate."""

    try:
        from ...evaluation.session_support import MuJoCoFrameCapture
    except ImportError as exc:  # pragma: no cover - shared integration commit supplies this
        raise SOArm101SessionError(
            "the shared evaluation.session_support.MuJoCoFrameCapture is required"
        ) from exc
    return MuJoCoFrameCapture


def _callable_factory(factory: Callable[..., Any] | None, label: str) -> Callable[..., Any]:
    if factory is None:
        raise SOArm101SessionError(f"{label} factory is unavailable")
    if not callable(factory):
        raise SOArm101SessionError(f"{label} factory is not callable")
    return factory


class SOArm101EvaluationRobotSession:
    """One private, real-SDK SO-ARM101 evaluation session.

    ``backend_factory``, ``translation_factory``, ``follower_factory``, and
    ``frame_capture_factory`` are test-only injection seams.  The production
    defaults construct the pinned real LeRobot follower, the checked-in PTY
    translation, MuJoCo 3.3.6, and the shared video capture implementation.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        scene_path: str | Path | None = None,
        scene_config_path: str | Path | None = None,
        task_instances_path: str | Path | None = None,
        calibration_dir: str | Path | None = None,
        run_directory: str | Path | None = None,
        gripper_tick_increases_qpos: bool = True,
        video_profile: FrozenVideoProfile | None = None,
        invocation_window_s: float = DEFAULT_INVOCATION_WINDOW_S,
        transport_wait_s: float = DEFAULT_TRANSPORT_WAIT_S,
        backend_factory: Callable[..., Any] | None = None,
        translation_factory: Callable[[Any], Any] | None = None,
        follower_factory: Callable[[str, Path], Any] | None = None,
        frame_capture_factory: Callable[..., Any] | None = None,
        truth_provider: Callable[["SOArm101EvaluationRobotSession"], Mapping[str, Any]] | None = None,
    ) -> None:
        self._model_path = Path(model_path).expanduser().absolute()
        self._scene_path = Path(scene_path or _default_scene_path()).resolve()
        self._scene_config_path = Path(scene_config_path or _default_scene_config_path()).resolve()
        self._task_instances_path = Path(task_instances_path or _default_task_instances_path()).resolve()
        self._scene_config = _read_json(self._scene_config_path, "SO-ARM101 private scene config")
        task_payload = _read_json(self._task_instances_path, "SO-ARM101 private task instances")
        self._task_records = self._index_task_records(task_payload)
        self._gripper_tick_increases_qpos = bool(gripper_tick_increases_qpos)
        self._video_profile = video_profile or DEFAULT_VIDEO_PROFILE
        self._invocation_window_s = _number(invocation_window_s, "invocation_window_s")
        self._transport_wait_s = _number(transport_wait_s, "transport_wait_s")
        if self._invocation_window_s <= 0 or self._transport_wait_s <= 0:
            raise SOArm101SessionError("session timing values must be positive")
        self._truth_provider = truth_provider
        self._frame_capture_factory = frame_capture_factory
        self._injected_dependencies = any(
            value is not None
            for value in (
                backend_factory,
                translation_factory,
                follower_factory,
                frame_capture_factory,
                truth_provider,
            )
        )
        self._backend: Any | None = None
        self._translation: Any | None = None
        self._follower: Any | None = None
        self._composed_model_path: Path | None = None
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._renderer: dict[tuple[int, int], Any] = {}
        self._capture: Any | None = None
        self._opened = False
        self._closed = False
        self._recording = False
        self._simulation_time_s = 0.0
        self._physics_steps = 0
        self._current_task_id: str | None = None
        self._current_task: dict[str, Any] | None = None
        self._episode_samples: list[dict[str, Any]] = []
        self._event_state: dict[str, Any] = {}
        self._reset_snapshot: dict[str, Any] = {"qpos": [], "qvel": [], "ctrl": []}
        self._reset_goal_writes = 0
        self._last_invocation_start_s = 0.0
        self._last_truth: dict[str, Any] | None = None

        try:
            if backend_factory is None:
                self._composed_model_path = self._compose_model()
                self._backend = MuJoCoSO101Backend(
                    self._composed_model_path,
                    gripper_tick_increases_qpos=self._gripper_tick_increases_qpos,
                )
            else:
                self._backend = _callable_factory(backend_factory, "MuJoCo backend")(
                    self._model_path,
                    gripper_tick_increases_qpos=self._gripper_tick_increases_qpos,
                )
            self._translation = _callable_factory(
                translation_factory or FeetechPTYTranslation,
                "PTY translation",
            )(self._backend)
            port = self._translation.install()
            calibration = Path(calibration_dir or (Path(run_directory or tempfile.gettempdir()) / "so101-calibration"))
            self._follower = _callable_factory(
                follower_factory or _default_follower_factory,
                "SO101Follower",
            )(port, calibration)
            self._validate_follower_surface(self._follower)
            self._follower.connect(calibrate=False)
            self._opened = True
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    @staticmethod
    def _index_task_records(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        if payload.get("artifact_type") != "so_arm101_private_task_instances":
            raise SOArm101SessionError("private SO-ARM101 task instance artifact type is invalid")
        if payload.get("schema_version") != "1.0.0":
            raise SOArm101SessionError("private SO-ARM101 task instance schema is unsupported")
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            raise SOArm101SessionError("private SO-ARM101 task instances must contain tasks")
        result: dict[str, dict[str, Any]] = {}
        for raw in raw_tasks:
            if not isinstance(raw, Mapping):
                raise SOArm101SessionError("private SO-ARM101 task instance is not an object")
            task_id = _require_text(raw.get("task_id"), "private task_id")
            if task_id in result:
                raise SOArm101SessionError(f"duplicate private task instance: {task_id}")
            result[task_id] = copy.deepcopy(dict(raw))
        expected = {"T01", "T02", "T03", "T08", "T20"}
        if set(result) != expected:
            raise SOArm101SessionError("private SO-ARM101 task instances must be exactly T01/T02/T03/T08/T20")
        return result

    @staticmethod
    def _validate_follower_surface(follower: Any) -> None:
        required = ("connect", "disconnect", "send_action", "get_observation")
        if any(not callable(getattr(follower, name, None)) for name in required):
            raise SOArm101SessionError("SO101Follower does not expose the admitted public surface")

    @property
    def sdk(self) -> object:
        if not self._opened or self._follower is None:
            raise SOArm101SessionError("the real SO101Follower is not open")
        return self._follower

    @property
    def simulation_time_s(self) -> float:
        if self._backend is not None:
            self._simulation_time_s = max(self._simulation_time_s, self._backend_time())
        return float(self._simulation_time_s)

    @property
    def evidence_scope(self) -> str:
        # Never claim SDK-grounded evidence before all three real route owners
        # are open, or after the session has been closed.
        if self._opened and self._follower is not None and self._translation is not None and self._backend is not None:
            if self._injected_dependencies:
                return "TEST_FIXTURE_ONLY"
            return EVIDENCE_SCOPE
        return "UNAVAILABLE"

    @property
    def robot_model_id(self) -> str:
        return ROBOT_MODEL_ID

    @property
    def robot_configuration_id(self) -> str:
        return ROBOT_CONFIGURATION_ID

    def __enter__(self) -> "SOArm101EvaluationRobotSession":
        if self._closed:
            raise SOArm101SessionError("a closed SO-ARM101 session cannot be re-entered")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _assert_open(self) -> None:
        if not self._opened or self._closed or self._backend is None or self._translation is None or self._follower is None:
            raise SOArm101SessionError("SO-ARM101 evaluation session is not open")

    def _compose_model(self) -> Path:
        if not self._model_path.is_file() or self._model_path.is_symlink():
            raise SOArm101SessionError(f"selected official SO-ARM101 model is not a regular file: {self._model_path}")
        if not self._scene_path.is_file() or self._scene_path.is_symlink():
            raise SOArm101SessionError(f"private SO-ARM101 scene is not a regular file: {self._scene_path}")
        try:
            robot_root = ET.parse(self._model_path).getroot()
            scene_root = ET.parse(self._scene_path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise SOArm101SessionError("could not parse the selected SO-ARM101 MJCF or private scene") from exc
        if robot_root.tag != "mujoco" or scene_root.tag != "mujoco":
            raise SOArm101SessionError("SO-ARM101 MJCF roots must be <mujoco>")

        compiler = robot_root.find("compiler")
        if compiler is not None:
            for field in ("meshdir", "texturedir"):
                raw = compiler.get(field)
                if raw and not Path(raw).is_absolute():
                    compiler.set(field, str((self._model_path.parent / raw).resolve()))

        robot_asset = robot_root.find("asset")
        scene_asset = scene_root.find("asset")
        if scene_asset is not None:
            if robot_asset is None:
                robot_asset = ET.SubElement(robot_root, "asset")
            for child in list(scene_asset):
                robot_asset.append(copy.deepcopy(child))

        robot_world = robot_root.find("worldbody")
        scene_world = scene_root.find("worldbody")
        if robot_world is None or scene_world is None:
            raise SOArm101SessionError("robot and private scene must both contain a worldbody")
        for child in list(scene_world):
            robot_world.append(copy.deepcopy(child))

        for section_name in ("contact", "sensor"):
            scene_section = scene_root.find(section_name)
            if scene_section is None:
                continue
            robot_section = robot_root.find(section_name)
            if robot_section is None:
                robot_section = ET.SubElement(robot_root, section_name)
            for child in list(scene_section):
                robot_section.append(copy.deepcopy(child))

        # The upstream model already has gripperframe in the admitted asset.
        # Add a private visual/reference marker only when that upstream site is
        # absent, keeping the truth frame tied to the actual gripper body.
        gripper_body = next(
            (body for body in robot_root.iter("body") if body.get("name") == "gripper"),
            None,
        )
        if gripper_body is None:
            raise SOArm101SessionError("selected official SO-ARM101 model has no gripper body")
        if not any(site.get("name") == "demo_gripper_reference_marker" for site in gripper_body.findall("site")):
            gripper_body.append(
                ET.Element(
                    "site",
                    {
                        "name": "demo_gripper_reference_marker",
                        "pos": "0.012 -0.000218 -0.098127",
                        "size": "0.004",
                        "rgba": "0.1 0.9 0.2 0.85",
                        "group": "4",
                    },
                )
            )

        self._temporary_directory = tempfile.TemporaryDirectory(prefix="autoadapter-so101-scene-")
        composed = Path(self._temporary_directory.name) / "so101_private_demo_scene.xml"
        ET.ElementTree(robot_root).write(composed, encoding="utf-8", xml_declaration=True)
        return composed

    def reset(
        self,
        *,
        phase: str,
        execution_id: str,
        initial_state: Mapping[str, Any],
    ) -> None:
        self._assert_open()
        if phase not in {"VALIDATION_B", "DEMO"}:
            raise SOArm101SessionError(f"unsupported session reset phase: {phase}")
        _require_text(execution_id, "execution_id")
        state = _deepcopy_mapping(initial_state, "initial_state")
        task_id = state.get("task_id") or state.get("task_instance_id")
        if task_id is not None:
            task_id = _require_text(task_id, "initial_state.task_id")
        if phase == "DEMO" and task_id not in self._task_records:
            raise SOArm101SessionError("Demo reset must select one of the five frozen private task instances")
        self._current_task_id = task_id if task_id in self._task_records else None
        self._current_task = copy.deepcopy(self._task_records.get(task_id)) if self._current_task_id else None

        self._translation.reset()
        self._reset_scene_state(self._current_task)
        self._simulation_time_s = self._backend_time()
        self._physics_steps = 0
        self._episode_samples = []
        self._event_state = {
            "face_contact_streak_s": 0.0,
            "face_contact_max_dwell_s": 0.0,
            "button_activation_streak_s": 0.0,
            "button_activation_max_dwell_s": 0.0,
            "max_other_object_contact_count": 0,
            "max_cube_height_increase_m": 0.0,
            "max_gripper_relative_cube_slip_m": 0.0,
            "safety_violation": False,
            "other_button_activation_count": 0,
        }
        self._last_invocation_start_s = self._simulation_time_s
        self._last_truth = self._sample_truth()
        self._episode_samples.append(copy.deepcopy(self._last_truth))
        self._reset_goal_writes = self._goal_writes()
        # Re-read independent MuJoCo state after the full private reset.  This
        # is the only place where object/mocap/qpos/qvel writes are allowed,
        # and every declared reset value is checked against the frozen 1e-9
        # tolerance before the route baseline is retained.
        self._verify_declared_reset(self._current_task)
        self._reset_snapshot = self._numeric_backend_snapshot()

    def _reset_tolerance(self) -> float:
        value = self._scene_config.get("reset_qpos_qvel_abs_tolerance", RESET_TOLERANCE)
        tolerance = _number(value, "reset_qpos_qvel_abs_tolerance")
        if tolerance < 0:
            raise SOArm101SessionError("reset tolerance must be non-negative")
        return tolerance

    def _verify_declared_reset(self, task: Mapping[str, Any] | None) -> None:
        if self._backend is None:
            raise SOArm101SessionError("backend is unavailable during reset verification")
        model = getattr(self._backend, "model", None)
        data = getattr(self._backend, "data", None)
        mj = getattr(self._backend, "_mj", None)
        if model is None or data is None or mj is None:
            # Injected unit fixtures intentionally expose only lifecycle/state;
            # their reset implementation is the bounded test seam.
            return

        tolerance = self._reset_tolerance()
        reset = self._scene_config.get("reset")
        if not isinstance(reset, Mapping) or not isinstance(reset.get("robot"), Mapping):
            raise SOArm101SessionError("private scene config reset declaration is invalid")
        robot = reset["robot"]
        expected_robot_qpos = _vector(robot.get("qpos_rad"), len(MOTOR_NAMES), "reset.robot.qpos_rad")
        expected_robot_qvel = _vector(robot.get("qvel_rad_s"), len(MOTOR_NAMES), "reset.robot.qvel_rad_s")
        for name, expected in zip(MOTOR_NAMES, expected_robot_qpos, strict=True):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
            actual = float(data.qpos[int(model.jnt_qposadr[joint_id])])
            if abs(actual - expected) > tolerance:
                raise SOArm101SessionError(f"reset joint qpos differs for {name!r}")
        for name, expected in zip(MOTOR_NAMES, expected_robot_qvel, strict=True):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
            actual = float(data.qvel[int(model.jnt_dofadr[joint_id])])
            if abs(actual - expected) > tolerance:
                raise SOArm101SessionError(f"reset joint qvel differs for {name!r}")

        selected = task.get("reset_state", {}) if isinstance(task, Mapping) else {}
        if not isinstance(selected, Mapping):
            raise SOArm101SessionError("private task reset_state is invalid")
        cube = selected.get("cube")
        if isinstance(cube, Mapping):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, "demo_cube_free")
            qpos_address = int(model.jnt_qposadr[joint_id])
            qvel_address = int(model.jnt_dofadr[joint_id])
            expected_position = _vector(cube.get("position_m"), 3, "demo_cube_free.position_m")
            expected_quaternion = _vector(cube.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0]), 4, "demo_cube_free.quaternion_wxyz")
            expected_qvel = _vector(cube.get("qvel", [0.0] * 6), 6, "demo_cube_free.qvel")
            actual_qpos = [float(value) for value in data.qpos[qpos_address : qpos_address + 7]]
            actual_qvel = [float(value) for value in data.qvel[qvel_address : qvel_address + 6]]
            expected_qpos = expected_position + expected_quaternion
            if any(abs(actual - expected) > tolerance for actual, expected in zip(actual_qpos, expected_qpos, strict=True)):
                raise SOArm101SessionError("reset cube qpos differs from the private task declaration")
            if any(abs(actual - expected) > tolerance for actual, expected in zip(actual_qvel, expected_qvel, strict=True)):
                raise SOArm101SessionError("reset cube qvel differs from the private task declaration")

        button = selected.get("button")
        if isinstance(button, Mapping):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, "demo_button_slide")
            actual_qpos = float(data.qpos[int(model.jnt_qposadr[joint_id])])
            actual_qvel = float(data.qvel[int(model.jnt_dofadr[joint_id])])
            expected_qpos = _number(button.get("qpos", 0.0), "demo_button_slide.qpos")
            expected_qvel = _number(button.get("qvel", 0.0), "demo_button_slide.qvel")
            if abs(actual_qpos - expected_qpos) > tolerance or abs(actual_qvel - expected_qvel) > tolerance:
                raise SOArm101SessionError("reset button state differs from the private task declaration")

        mocap = selected.get("mocap", {})
        if mocap:
            if not isinstance(mocap, Mapping):
                raise SOArm101SessionError("private task mocap reset is invalid")
            for body_name, values in mocap.items():
                if not isinstance(values, Mapping):
                    raise SOArm101SessionError("private task mocap reset is invalid")
                body_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_BODY, str(body_name))
                mocap_id = int(model.body_mocapid[body_id])
                if mocap_id < 0:
                    raise SOArm101SessionError("private task mocap reset references a non-mocap body")
                expected_position = _vector(values.get("position_m"), 3, f"mocap[{body_name}].position_m")
                expected_quaternion = _vector(values.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0]), 4, f"mocap[{body_name}].quaternion_wxyz")
                actual_position = [float(value) for value in data.mocap_pos[mocap_id]]
                actual_quaternion = [float(value) for value in data.mocap_quat[mocap_id]]
                if any(abs(actual - expected) > tolerance for actual, expected in zip(actual_position, expected_position, strict=True)):
                    raise SOArm101SessionError(f"reset mocap position differs for {body_name!r}")
                if any(abs(actual - expected) > tolerance for actual, expected in zip(actual_quaternion, expected_quaternion, strict=True)):
                    raise SOArm101SessionError(f"reset mocap quaternion differs for {body_name!r}")

    def _reset_scene_state(self, task: Mapping[str, Any] | None) -> None:
        if self._backend is None:
            raise SOArm101SessionError("backend is unavailable during reset")
        model = getattr(self._backend, "model", None)
        data = getattr(self._backend, "data", None)
        mj = getattr(self._backend, "_mj", None)
        if model is None or data is None or mj is None:
            # Injected unit fixtures can expose only the backend lifecycle/state
            # contract.  Their reset method remains the sole reset operation.
            return

        reset = self._scene_config.get("reset")
        if not isinstance(reset, Mapping):
            raise SOArm101SessionError("private scene config reset declaration is invalid")
        robot = reset.get("robot")
        if not isinstance(robot, Mapping):
            raise SOArm101SessionError("private scene config robot reset declaration is invalid")
        qpos = _vector(robot.get("qpos_rad"), len(MOTOR_NAMES), "reset.robot.qpos_rad")
        qvel = _vector(robot.get("qvel_rad_s"), len(MOTOR_NAMES), "reset.robot.qvel_rad_s")
        for name, value in zip(MOTOR_NAMES, qpos, strict=True):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
            data.qpos[int(model.jnt_qposadr[joint_id])] = value
        for name, value in zip(MOTOR_NAMES, qvel, strict=True):
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
            data.qvel[int(model.jnt_dofadr[joint_id])] = value

        selected = task.get("reset_state", {}) if isinstance(task, Mapping) else {}
        if not isinstance(selected, Mapping):
            raise SOArm101SessionError("private task reset_state is invalid")
        cube = selected.get("cube")
        if isinstance(cube, Mapping):
            self._set_free_body_state(model, data, mj, "demo_cube_free", cube)
        button = selected.get("button")
        if isinstance(button, Mapping):
            self._set_joint_state(model, data, mj, "demo_button_slide", button)

        mocap = selected.get("mocap", {})
        if mocap:
            if not isinstance(mocap, Mapping):
                raise SOArm101SessionError("private task mocap reset is invalid")
            for body_name, values in mocap.items():
                body_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_BODY, str(body_name))
                mocap_id = int(model.body_mocapid[body_id])
                if mocap_id < 0 or not isinstance(values, Mapping):
                    raise SOArm101SessionError("private task mocap reset references a non-mocap body")
                data.mocap_pos[mocap_id] = _vector(values.get("position_m"), 3, f"mocap[{body_name}].position_m")
                data.mocap_quat[mocap_id] = _vector(values.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0]), 4, f"mocap[{body_name}].quaternion_wxyz")

        # Reset controls to the reset qpos.  This is reset bookkeeping; normal
        # goal handling remains actuator-control-only in the checked-in backend.
        for name in MOTOR_NAMES:
            actuator_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_ACTUATOR, name)
            joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
            data.ctrl[actuator_id] = data.qpos[int(model.jnt_qposadr[joint_id])]
        mj.mj_forward(model, data)

    @staticmethod
    def _name_id(kind: Any, model: Any, object_type: Any, name: str) -> int:
        identifier = int(kind.mj_name2id(model, object_type, name)) if hasattr(kind, "mj_name2id") else -1
        if identifier < 0:
            raise SOArm101SessionError(f"MuJoCo model is missing {name!r}")
        return identifier

    def _set_joint_state(self, model: Any, data: Any, mj: Any, name: str, values: Mapping[str, Any]) -> None:
        joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, name)
        qpos = _number(values.get("qpos", 0.0), f"{name}.qpos")
        qvel = _number(values.get("qvel", 0.0), f"{name}.qvel")
        data.qpos[int(model.jnt_qposadr[joint_id])] = qpos
        data.qvel[int(model.jnt_dofadr[joint_id])] = qvel

    def _set_free_body_state(self, model: Any, data: Any, mj: Any, joint_name: str, values: Mapping[str, Any]) -> None:
        joint_id = self._name_id(mj, model, mj.mjtObj.mjOBJ_JOINT, joint_name)
        position = _vector(values.get("position_m"), 3, f"{joint_name}.position_m")
        quaternion = _vector(values.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0]), 4, f"{joint_name}.quaternion_wxyz")
        qvel = _vector(values.get("qvel", [0.0] * 6), 6, f"{joint_name}.qvel")
        qpos_address = int(model.jnt_qposadr[joint_id])
        qvel_address = int(model.jnt_dofadr[joint_id])
        data.qpos[qpos_address : qpos_address + 3] = position
        data.qpos[qpos_address + 3 : qpos_address + 7] = quaternion
        data.qvel[qvel_address : qvel_address + 6] = qvel

    def _backend_time(self) -> float:
        if self._backend is None:
            return self._simulation_time_s
        data = getattr(self._backend, "data", None)
        if data is not None and _finite(getattr(data, "time", None)):
            return float(data.time)
        state = self._backend.state() if callable(getattr(self._backend, "state", None)) else {}
        value = state.get("time", self._simulation_time_s) if isinstance(state, Mapping) else self._simulation_time_s
        return float(value) if _finite(value) else self._simulation_time_s

    def _numeric_backend_snapshot(self) -> dict[str, Any]:
        if self._backend is None or not callable(getattr(self._backend, "state", None)):
            raise SOArm101SessionError("backend does not provide independent state")
        state = self._backend.state()
        if not isinstance(state, Mapping):
            raise SOArm101SessionError("backend state is not an object")
        result: dict[str, list[float]] = {}
        for field in ("qpos", "qvel", "ctrl"):
            raw = state.get(field, [])
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise SOArm101SessionError(f"backend state {field} is not a numeric sequence")
            result[field] = [_number(value, f"backend state {field}") for value in raw]
        for field in ("named_qpos", "named_ctrl"):
            raw_named = state.get(field)
            if isinstance(raw_named, Mapping):
                result[field] = {
                    str(name): _number(value, f"backend state {field}.{name}")
                    for name, value in raw_named.items()
                }
        return result

    def _goal_writes(self) -> int:
        if self._translation is None:
            return 0
        health = self._translation.health() if callable(getattr(self._translation, "health", None)) else {}
        value = health.get("accepted_goal_writes", 0) if isinstance(health, Mapping) else 0
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    def _advance(self, seconds: float) -> None:
        self._assert_open()
        if not _finite(seconds) or float(seconds) < 0:
            raise SOArm101SessionError("physics advance duration must be finite and non-negative")
        timestep = getattr(self._backend, "timestep", DEFAULT_TIMESTEP)
        timestep = _number(timestep, "MuJoCo timestep")
        if timestep <= 0:
            raise SOArm101SessionError("MuJoCo timestep must be positive")
        steps = int(math.ceil(float(seconds) / timestep)) if seconds else 0
        for _ in range(steps):
            self._translation.step(timestep)
            self._physics_steps += 1
            self._simulation_time_s = max(self._simulation_time_s, self._backend_time())
            truth = self._sample_truth()
            self._episode_samples.append(copy.deepcopy(truth))
            if self._recording and self._capture is not None:
                self._capture.on_step()

    def _wait_for_goal_traffic(self, before: int) -> None:
        if self._translation is None:
            return
        waiter = getattr(self._translation, "wait_for_goal_writes", None)
        if callable(waiter) and self._goal_writes() <= before:
            waiter(before + 1, self._transport_wait_s)

    def invoke(
        self,
        candidate: Any,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self._assert_open()
        _require_text(capability_id, "capability_id")
        if not isinstance(arguments, Mapping):
            raise SOArm101SessionError("candidate arguments must be an object")
        invoke_method = getattr(candidate, "_invoke", None)
        if not callable(invoke_method):
            raise SOArm101SessionError("candidate is not a Framework-validated candidate handle")
        before_goal = self._goal_writes()
        try:
            result = invoke_method(capability_id, copy.deepcopy(dict(arguments)), self.sdk)
        finally:
            # Candidate exceptions still receive a terminal physics/capture
            # boundary.  The candidate itself gets no backend or MuJoCo handle.
            self._wait_for_goal_traffic(before_goal)
            self._advance(self._invocation_window_s)
            # Validation B collects the post-invocation observation window.  A
            # direct Harness call starts at the current time; a session.invoke
            # call has already consumed its bounded command/physics window.
            self._last_invocation_start_s = self.simulation_time_s
        if not isinstance(result, Mapping):
            raise SOArm101SessionError("candidate result must be a mapping")
        return copy.deepcopy(dict(result))

    def validation_evidence(self, invocation: HarnessInvocation) -> ValidationEvidence:
        self._assert_open()
        if not isinstance(invocation, HarnessInvocation):
            raise SOArm101SessionError("validation evidence requires a typed HarnessInvocation")
        start = self._last_invocation_start_s if self._last_invocation_start_s <= self.simulation_time_s else self.simulation_time_s
        duration = max(self._invocation_window_s, float(invocation.dwell_s))
        duration = min(duration, float(invocation.timeout_s))
        if duration < 0:
            raise SOArm101SessionError("validation timeout/dwell is invalid")
        # Validation B invokes the candidate directly with ``session.sdk``;
        # collect must therefore establish the same PTY boundary before it
        # advances physics and evaluates the route proof.
        self._wait_for_goal_traffic(self._reset_goal_writes)
        self._advance(duration)
        try:
            observation = self._follower.get_observation() if self._follower is not None else None
            sdk_readback_observed = isinstance(observation, Mapping)
        except Exception:
            sdk_readback_observed = False
        selected = [item for item in self._episode_samples if float(item.get("time_s", 0.0)) >= start - 1e-12]
        if not selected:
            selected = [self._last_truth or self._sample_truth()]
        samples: list[MeasurementSample] = []
        for item in selected:
            value = self._metric_value(item, invocation.metric)
            samples.append(MeasurementSample(max(0.0, float(item["time_s"]) - start), value))
        route_verified, route_detail = self._route_verification()
        guards = self._guard_results()
        guards["sdk-route-verified"] = route_verified
        guards["sdk-readback-observed"] = sdk_readback_observed
        guards["physics-progress-observed"] = route_detail["physics_progress"]
        return ValidationEvidence(
            samples=tuple(samples),
            elapsed_s=max(0.0, self.simulation_time_s - start),
            guard_results=guards,
            sdk_route_verified=route_verified,
        )

    def _metric_value(self, truth: Mapping[str, Any], metric: str) -> float:
        if metric in truth and _finite(truth[metric]):
            return float(truth[metric])
        measurements = truth.get("measurements")
        if isinstance(measurements, Mapping) and metric in measurements and _finite(measurements[metric]):
            return float(measurements[metric])
        aliases = {
            "tip_error": "tip_position_error_m",
            "tip_position_error": "tip_position_error_m",
            "tip_speed": "tip_speed_m_s",
            "cube_goal_error": "cube_center_planar_goal_error_m",
            "button_displacement": "specified_button_displacement_m",
        }
        alias = aliases.get(metric)
        if alias and alias in truth and _finite(truth[alias]):
            return float(truth[alias])
        raise SOArm101SessionError(f"independent MuJoCo truth does not provide metric {metric!r}")

    def _route_verification(self) -> tuple[bool, dict[str, Any]]:
        current = self._numeric_backend_snapshot()
        baseline = self._reset_snapshot
        named_ctrl = current.get("named_ctrl")
        baseline_named_ctrl = baseline.get("named_ctrl")
        if isinstance(named_ctrl, Mapping) and isinstance(baseline_named_ctrl, Mapping):
            control_changed = any(
                abs(float(named_ctrl.get(name, 0.0)) - float(baseline_named_ctrl.get(name, 0.0))) > 1e-12
                for name in MOTOR_NAMES
            )
        else:
            control_changed = len(current["ctrl"]) == len(baseline["ctrl"]) and any(
                abs(a - b) > 1e-12 for a, b in zip(current["ctrl"], baseline["ctrl"], strict=True)
            )
        named_qpos = current.get("named_qpos")
        baseline_named_qpos = baseline.get("named_qpos")
        if isinstance(named_qpos, Mapping) and isinstance(baseline_named_qpos, Mapping):
            qpos_changed = any(
                abs(float(named_qpos.get(name, 0.0)) - float(baseline_named_qpos.get(name, 0.0))) > 1e-12
                for name in MOTOR_NAMES
            )
            qvel_changed = False
        else:
            # The injected fixture backend has no named robot state; its
            # compact qpos/qvel arrays are its complete physical state.
            qpos_changed = len(current["qpos"]) == len(baseline["qpos"]) and any(
                abs(a - b) > 1e-12 for a, b in zip(current["qpos"], baseline["qpos"], strict=True)
            )
            qvel_changed = len(current["qvel"]) == len(baseline["qvel"]) and any(
                abs(a - b) > 1e-12 for a, b in zip(current["qvel"], baseline["qvel"], strict=True)
            )
        goal_delta = max(0, self._goal_writes() - self._reset_goal_writes)
        physics_progress = self._physics_steps > 0 and (control_changed or qpos_changed or qvel_changed)
        verified = bool(self._opened and goal_delta > 0 and physics_progress)
        return verified, {
            "goal_writes_observed": goal_delta,
            "physics_steps": self._physics_steps,
            "physics_progress": physics_progress,
            "control_changed": control_changed,
            "qpos_changed": qpos_changed,
            "qvel_changed": qvel_changed,
        }

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        self._assert_open()
        if phase not in {"VALIDATION_B", "DEMO"}:
            raise SOArm101SessionError(f"unsupported recording phase: {phase}")
        _require_text(execution_id, "execution_id")
        if self._recording:
            raise SOArm101SessionError("an external recording is already active")
        factory = self._frame_capture_factory or _load_frame_capture_factory()
        self._capture = factory(self._video_profile, self._render_rgb, self._simulation_time)
        if not callable(getattr(self._capture, "start", None)) or not callable(getattr(self._capture, "on_step", None)) or not callable(getattr(self._capture, "stop", None)):
            self._capture = None
            raise SOArm101SessionError("MuJoCoFrameCapture does not implement start/on_step/stop")
        capture = self._capture
        try:
            capture.start()
            self._recording = True
            # Coverage begins at the post-reset, pre-invocation state.  The
            # subsequent on_step calls are synchronized to physics progress.
            capture.on_step()
        except BaseException:
            self._recording = False
            self._capture = None
            try:
                capture.stop()
            except BaseException:
                pass
            raise

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        self._assert_open()
        if not self._recording or self._capture is None:
            raise SOArm101SessionError("no external recording is active")
        capture = self._capture
        self._capture = None
        self._recording = False
        frames = capture.stop()
        if not isinstance(frames, tuple):
            frames = tuple(frames) if isinstance(frames, Sequence) else ()
        if not all(isinstance(frame, RGBFrame) for frame in frames):
            raise SOArm101SessionError("MuJoCoFrameCapture returned invalid RGB frames")
        return frames

    def _simulation_time(self) -> float:
        return self.simulation_time_s

    def _render_rgb(self, *_: Any, **__: Any) -> bytes:
        """Render only the private external evaluation camera as RGB bytes."""

        self._assert_open()
        backend = self._backend
        mj = getattr(backend, "_mj", None)
        model = getattr(backend, "model", None)
        data = getattr(backend, "data", None)
        if mj is None or model is None or data is None:
            raise SOArm101SessionError("MuJoCo renderer is unavailable")
        width, height = self._video_profile.width, self._video_profile.height
        key = (width, height)
        renderer = self._renderer.get(key)
        if renderer is None:
            renderer = mj.Renderer(model, height=height, width=width)
            self._renderer[key] = renderer
        camera_id = self._first_id(
            mj,
            model,
            mj.mjtObj.mjOBJ_CAMERA,
            ("evaluation_external_camera",),
        )
        renderer.update_scene(data, camera=camera_id)
        rendered = renderer.render()
        if hasattr(rendered, "tobytes"):
            payload = rendered.tobytes()
        else:
            payload = bytes(rendered)
        expected = width * height * 3
        if len(payload) != expected:
            raise SOArm101SessionError(f"external RGB renderer returned {len(payload)} bytes, expected {expected}")
        return payload

    def _sample_truth(self) -> dict[str, Any]:
        if self._truth_provider is not None:
            raw = self._truth_provider(self)
            truth = _deepcopy_mapping(raw, "injected truth")
            truth.setdefault("time_s", self.simulation_time_s)
            truth.setdefault(
                "finite_state",
                all(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or _finite(value)
                    for value in truth.values()
                ),
            )
            self._update_events(truth)
            self._last_truth = truth
            return truth

        backend = self._backend
        mj = getattr(backend, "_mj", None)
        model = getattr(backend, "model", None)
        data = getattr(backend, "data", None)
        if mj is None or model is None or data is None:
            snapshot = self._numeric_backend_snapshot()
            numeric_values: list[Any] = []
            for values in snapshot.values():
                if isinstance(values, Mapping):
                    numeric_values.extend(values.values())
                elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    numeric_values.extend(values)
            truth = {
                "time_s": self.simulation_time_s,
                "finite_state": all(_finite(value) for value in numeric_values),
                "qpos": snapshot["qpos"],
                "qvel": snapshot["qvel"],
                "ctrl": snapshot["ctrl"],
                "tip_position_error_m": math.inf,
                "tip_speed_m_s": math.inf,
            }
            self._update_events(truth)
            self._last_truth = truth
            return truth

        tip_site = self._first_id(mj, model, mj.mjtObj.mjOBJ_SITE, ("gripperframe", "demo_gripper_reference_marker"))
        cube_body = self._first_id(mj, model, mj.mjtObj.mjOBJ_BODY, ("demo_cube",))
        button_joint = self._first_id(mj, model, mj.mjtObj.mjOBJ_JOINT, ("demo_button_slide",))
        tip_position = [float(value) for value in data.site_xpos[tip_site]]
        tip_velocity = [float(value) for value in data.site_xvelp[tip_site]]
        cube_position = [float(value) for value in data.xpos[cube_body]]
        cube_joint = self._first_id(mj, model, mj.mjtObj.mjOBJ_JOINT, ("demo_cube_free",))
        cube_qvel_address = int(model.jnt_dofadr[cube_joint])
        cube_linear_velocity = [float(value) for value in data.qvel[cube_qvel_address + 3 : cube_qvel_address + 6]]
        cube_angular_velocity = [float(value) for value in data.qvel[cube_qvel_address : cube_qvel_address + 3]]
        task = self._current_task or {}
        target = task.get("target", {}) if isinstance(task, Mapping) else {}
        tip_target = target.get("tip_position_m", tip_position) if isinstance(target, Mapping) else tip_position
        goal_target = target.get("cube_goal_center_m", cube_position[:2]) if isinstance(target, Mapping) else cube_position[:2]
        tip_target = _vector(tip_target, 3, "task tip target")
        goal_target = _vector(goal_target, 2, "task cube goal target")
        baseline_value = self._event_state.get("baseline_cube_position")
        baseline = (
            [float(value) for value in baseline_value]
            if isinstance(baseline_value, Sequence) and len(baseline_value) == 3
            else list(cube_position)
        )
        self._event_state.setdefault("baseline_cube_position", list(baseline))
        cube_table_z = float(self._scene_config.get("table_surface_z_m", 0.02))
        cube_half_height = float(self._scene_config.get("cube_half_height_m", 0.017))
        contacts = self._contacts(mj, model, data)
        face_contact = any(item["touches_cube_face"] and item["touches_gripper"] for item in contacts)
        cube_contact = any(item["touches_cube"] and item["touches_gripper"] for item in contacts)
        table_support = any(item["touches_cube"] and item["touches_table"] for item in contacts)
        other_object_contacts = sum(
            1 for item in contacts
            if item["touches_gripper"] and item["other_object"]
        )
        button_qpos = float(data.qpos[int(model.jnt_qposadr[button_joint])])
        button_reset_value = self._event_state.get("baseline_button_qpos")
        button_reset = float(button_reset_value) if _finite(button_reset_value) else button_qpos
        self._event_state.setdefault("baseline_button_qpos", button_reset)
        button_displacement = max(0.0, button_qpos - button_reset)
        cube_height_increase = cube_position[2] - baseline[2]
        tip_cube_relative = [cube_position[i] - tip_position[i] for i in range(3)]
        hold_relative_value = self._event_state.get("hold_relative_baseline")
        if not isinstance(hold_relative_value, Sequence) and cube_contact:
            self._event_state["hold_relative_baseline"] = list(tip_cube_relative)
            hold_relative_value = tip_cube_relative
        slip = (
            _distance(tip_cube_relative, hold_relative_value)
            if isinstance(hold_relative_value, Sequence) and len(hold_relative_value) == 3
            else 0.0
        )
        cube_held = bool(cube_contact and cube_position[2] > cube_table_z + cube_half_height + 0.01 and slip <= 0.005)
        qpos_state = [float(value) for value in data.qpos]
        qvel_state = [float(value) for value in data.qvel]
        ctrl_state = [float(value) for value in data.ctrl]
        state_values = (
            tip_position
            + tip_velocity
            + cube_position
            + cube_linear_velocity
            + cube_angular_velocity
            + [button_qpos]
            + qpos_state
            + qvel_state
            + ctrl_state
        )
        finite_state = all(_finite(value) for value in state_values)
        truth = {
            "time_s": self.simulation_time_s,
            "tip_position_m": tip_position,
            "tip_speed_m_s": _norm(tip_velocity),
            "tip_position_error_m": _distance(tip_position, tip_target),
            "cube_center_m": cube_position,
            "cube_center_planar_goal_error_m": _distance(cube_position[:2], goal_target),
            "cube_linear_speed_m_s": _norm(cube_linear_velocity),
            "cube_angular_speed_rad_s": _norm(cube_angular_velocity),
            "cube_height_increase_m": cube_height_increase,
            "gripper_relative_cube_slip_m": slip,
            "cube_held": cube_held,
            "cube_table_supported": table_support or abs(cube_position[2] - (cube_table_z + cube_half_height)) <= 0.004,
            "intended_tip_face_contact": face_contact,
            "specified_button_displacement_m": button_displacement,
            "specified_button_active": button_displacement >= 0.003,
            "other_button_activation_count": 0,
            "other_object_contact_count": other_object_contacts,
            "contacts": contacts,
            "finite_state": finite_state,
            "qpos": qpos_state,
            "qvel": qvel_state,
        }
        truth["safety_violation"] = self._safety_violation(model, data, mj, truth)
        self._update_events(truth)
        self._last_truth = truth
        return truth

    def _first_id(self, mj: Any, model: Any, object_type: Any, names: Sequence[str]) -> int:
        for name in names:
            value = int(mj.mj_name2id(model, object_type, name))
            if value >= 0:
                return value
        raise SOArm101SessionError(f"MuJoCo model is missing one of {tuple(names)!r}")

    def _reset_cube_position(self) -> list[float]:
        if self._reset_snapshot and self._backend is not None:
            model = getattr(self._backend, "model", None)
            mj = getattr(self._backend, "_mj", None)
            if model is not None and mj is not None:
                try:
                    joint = self._first_id(mj, model, mj.mjtObj.mjOBJ_JOINT, ("demo_cube_free",))
                    address = int(model.jnt_qposadr[joint])
                    return [float(value) for value in self._backend.data.qpos[address : address + 3]] if not self._episode_samples else self._event_state.get("baseline_cube_position", [0.33, 0.0, 0.037])
                except SOArm101SessionError:
                    pass
        return list(self._event_state.get("baseline_cube_position", [0.33, 0.0, 0.037]))

    def _reset_button_qpos(self) -> float:
        return float(self._event_state.get("baseline_button_qpos", 0.0))

    def _reset_tip_position(self) -> list[float]:
        return list(self._event_state.get("baseline_tip_position", [0.0, 0.0, 0.0]))

    def _contacts(self, mj: Any, model: Any, data: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index in range(int(data.ncon)):
            contact = data.contact[index]
            first = int(contact.geom1)
            second = int(contact.geom2)
            first_name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_GEOM, first) or f"geom-{first}"
            second_name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_GEOM, second) or f"geom-{second}"
            first_body = int(model.geom_bodyid[first])
            second_body = int(model.geom_bodyid[second])
            first_body_name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, first_body) or f"body-{first_body}"
            second_body_name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, second_body) or f"body-{second_body}"
            names = (str(first_name), str(second_name), str(first_body_name), str(second_body_name))
            touches_cube = any("demo_cube" in name for name in names)
            touches_face = any("demo_cube_target_face" in name for name in names)
            touches_table = any("demo_table" in name for name in names)
            touches_gripper = any(any(token in name.lower() for token in ("gripper", "jaw", "moving_jaw")) for name in names)
            other_object = touches_gripper and any(
                name.startswith("demo_")
                and not touches_cube
                and not touches_table
                and "demo_button" not in name
                for name in names
            )
            result.append({
                "geom1": str(first_name),
                "geom2": str(second_name),
                "body1": str(first_body_name),
                "body2": str(second_body_name),
                "touches_cube": touches_cube,
                "touches_cube_face": touches_face,
                "touches_table": touches_table,
                "touches_gripper": touches_gripper,
                "other_object": other_object,
                "distance_m": float(contact.dist),
            })
        return result

    def _safety_violation(self, model: Any, data: Any, mj: Any, truth: Mapping[str, Any]) -> bool:
        if not truth.get("finite_state", False):
            return True
        for joint_id in range(int(model.njnt)):
            if bool(model.jnt_limited[joint_id]):
                address = int(model.jnt_qposadr[joint_id])
                value = float(data.qpos[address])
                low, high = map(float, model.jnt_range[joint_id])
                if value < low - 1e-9 or value > high + 1e-9:
                    return True
        cube = truth.get("cube_center_m")
        bounds = self._scene_config.get("workspace_bounds_m", {})
        if isinstance(cube, Sequence) and isinstance(bounds, Mapping):
            for index, axis in enumerate(("x", "y", "z")):
                limits = bounds.get(axis)
                if isinstance(limits, Sequence) and len(limits) == 2 and (float(cube[index]) < float(limits[0]) or float(cube[index]) > float(limits[1])):
                    return True
        return False

    def _update_events(self, truth: Mapping[str, Any]) -> None:
        previous_time = self._event_state.get("last_time_s")
        dt = 0.0 if not _finite(previous_time) else max(0.0, float(truth.get("time_s", 0.0)) - float(previous_time))
        self._event_state["last_time_s"] = float(truth.get("time_s", 0.0))
        if truth.get("intended_tip_face_contact"):
            self._event_state["face_contact_streak_s"] = float(self._event_state.get("face_contact_streak_s", 0.0)) + dt
        else:
            self._event_state["face_contact_streak_s"] = 0.0
        self._event_state["face_contact_max_dwell_s"] = max(
            float(self._event_state.get("face_contact_max_dwell_s", 0.0)),
            float(self._event_state.get("face_contact_streak_s", 0.0)),
        )
        if truth.get("specified_button_active"):
            self._event_state["button_activation_streak_s"] = float(self._event_state.get("button_activation_streak_s", 0.0)) + dt
        else:
            self._event_state["button_activation_streak_s"] = 0.0
        self._event_state["button_activation_max_dwell_s"] = max(
            float(self._event_state.get("button_activation_max_dwell_s", 0.0)),
            float(self._event_state.get("button_activation_streak_s", 0.0)),
        )
        self._event_state["max_other_object_contact_count"] = max(
            int(self._event_state.get("max_other_object_contact_count", 0)),
            int(truth.get("other_object_contact_count", 0)),
        )
        self._event_state["max_cube_height_increase_m"] = max(
            float(self._event_state.get("max_cube_height_increase_m", 0.0)),
            float(truth.get("cube_height_increase_m", 0.0)),
        )
        self._event_state["max_gripper_relative_cube_slip_m"] = max(
            float(self._event_state.get("max_gripper_relative_cube_slip_m", 0.0)),
            float(truth.get("gripper_relative_cube_slip_m", 0.0)),
        )
        self._event_state["safety_violation"] = bool(self._event_state.get("safety_violation", False) or truth.get("safety_violation", False))
        if "baseline_cube_position" not in self._event_state and isinstance(truth.get("cube_center_m"), Sequence):
            self._event_state["baseline_cube_position"] = list(truth["cube_center_m"])
        if "baseline_button_qpos" not in self._event_state and _finite(truth.get("specified_button_displacement_m")):
            self._event_state["baseline_button_qpos"] = 0.0

    def _guard_results(self) -> dict[str, bool]:
        finite_state = bool(self._episode_samples) and all(bool(item.get("finite_state", False)) for item in self._episode_samples)
        safety = not bool(self._event_state.get("safety_violation", True))
        return {
            "trusted-external-verdict": bool(self._opened),
            "so-safety-gate": safety,
            "finite-physical-state": finite_state,
        }

    def demo_evidence(self, task_id: str) -> Mapping[str, Any]:
        self._assert_open()
        task_id = _require_text(task_id, "task_id")
        if task_id not in self._task_records or task_id != self._current_task_id:
            raise SOArm101SessionError("demo evidence task does not match the current private reset")
        if not self._episode_samples:
            self._last_truth = self._sample_truth()
            self._episode_samples.append(copy.deepcopy(self._last_truth))
        terminal = copy.deepcopy(self._episode_samples[-1])
        samples = self._episode_samples
        event_metrics = {
            "intended_tip_face_contact_dwell_s": float(self._event_state.get("face_contact_max_dwell_s", 0.0)),
            "specified_button_activation_dwell_s": float(self._event_state.get("button_activation_max_dwell_s", 0.0)),
            "other_object_contact_count": int(self._event_state.get("max_other_object_contact_count", 0)),
            "other_button_activation_count": int(self._event_state.get("other_button_activation_count", 0)),
            "cube_held": self._window_all(samples, "cube_held", 1.0),
            "continuous_sample_count": len(samples),
            "sample_start_time_s": float(samples[0].get("time_s", 0.0)),
            "sample_end_time_s": float(samples[-1].get("time_s", 0.0)),
        }
        hold_window = self._window(samples, 1.0)
        measurements = {
            "tip_position_error_m": self._window_max(samples, "tip_position_error_m", 0.5),
            "tip_speed_m_s": self._window_max(samples, "tip_speed_m_s", 0.5),
            "target_object_displacement_m": self._window_max_displacement(samples, "cube_center_m"),
            "cube_center_planar_goal_error_m": self._window_max(samples, "cube_center_planar_goal_error_m", 1.0),
            "cube_table_supported": self._window_all(samples, "cube_table_supported", 1.0),
            "cube_linear_speed_m_s": self._window_max(samples, "cube_linear_speed_m_s", 1.0),
            "cube_angular_speed_rad_s": self._window_max(samples, "cube_angular_speed_rad_s", 1.0),
            "cube_height_increase_m": max(
                (float(item.get("cube_height_increase_m", 0.0)) for item in hold_window),
                default=0.0,
            ),
            "gripper_relative_cube_slip_m": max(
                (float(item.get("gripper_relative_cube_slip_m", math.inf)) for item in hold_window),
                default=math.inf,
            ),
            "specified_button_displacement_m": max(float(item.get("specified_button_displacement_m", 0.0)) for item in samples),
        }
        measurements.update(event_metrics)
        return {
            "task_id": task_id,
            "samples": copy.deepcopy(samples),
            "terminal_metrics": terminal,
            "event_metrics": event_metrics,
            "measurements": measurements,
            **measurements,
            "guard_results": self._guard_results(),
            "terminal_time_s": self.simulation_time_s,
        }

    def _window(self, samples: Sequence[Mapping[str, Any]], dwell_s: float) -> list[Mapping[str, Any]]:
        if not samples:
            return []
        end = float(samples[-1].get("time_s", 0.0))
        return [item for item in samples if float(item.get("time_s", 0.0)) >= end - dwell_s - 1e-12]

    def _window_max(self, samples: Sequence[Mapping[str, Any]], field: str, dwell_s: float) -> float:
        values = [float(item[field]) for item in self._window(samples, dwell_s) if _finite(item.get(field))]
        return max(values) if values else math.inf

    def _window_all(self, samples: Sequence[Mapping[str, Any]], field: str, dwell_s: float) -> bool:
        window = self._window(samples, dwell_s)
        return bool(window) and all(item.get(field) is True for item in window)

    def _window_max_displacement(self, samples: Sequence[Mapping[str, Any]], field: str) -> float:
        if not samples:
            return math.inf
        baseline = samples[0].get(field)
        if not isinstance(baseline, Sequence):
            return math.inf
        values = [
            _distance(item[field], baseline)
            for item in samples
            if isinstance(item.get(field), Sequence) and len(item[field]) == len(baseline)
        ]
        return max(values) if values else math.inf

    def close(self) -> None:
        if self._closed:
            return
        errors: list[str] = []
        if self._recording and self._capture is not None:
            try:
                self._capture.stop()
            except BaseException as exc:
                errors.append(f"capture: {exc}")
            self._capture = None
            self._recording = False
        follower = self._follower
        translation = self._translation
        backend = self._backend
        self._opened = False
        self._follower = None
        self._translation = None
        self._backend = None
        if follower is not None:
            try:
                follower.disconnect()
            except BaseException as exc:
                errors.append(f"follower: {exc}")
        if translation is not None:
            try:
                translation.close()
            except BaseException as exc:
                errors.append(f"translation: {exc}")
        # The checked-in PTY translation closes its backend as part of its own
        # shutdown, and MuJoCoSO101Backend.close is idempotent.  Calling the
        # owner explicitly as well keeps the Session Runner cleanup contract
        # observable for injected tests and for partial-open failures.
        if backend is not None:
            try:
                backend.close()
            except BaseException as exc:
                errors.append(f"backend: {exc}")
        for renderer in self._renderer.values():
            try:
                renderer.close()
            except BaseException as exc:
                errors.append(f"renderer: {exc}")
        self._renderer.clear()
        if self._temporary_directory is not None:
            try:
                self._temporary_directory.cleanup()
            except BaseException as exc:
                errors.append(f"temporary scene: {exc}")
            self._temporary_directory = None
        self._closed = True
        if errors:
            raise SOArm101SessionError("SO-ARM101 session cleanup failed: " + "; ".join(errors))


def create_so_arm101_evaluation_session(
    robot: str,
    manifest_path: str | Path,
    run_directory: str | Path,
) -> SOArm101EvaluationRobotSession:
    """Factory compatible with the first-G2 Demo entrypoint."""

    if robot != ROBOT_MODEL_ID:
        raise SOArm101SessionError(f"SO-ARM101 session factory received unsupported robot {robot!r}")
    manifest = _read_json(manifest_path, "SO-ARM101 integration manifest")
    if manifest.get("robot_model_id") != ROBOT_MODEL_ID or manifest.get("robot_configuration_id") != ROBOT_CONFIGURATION_ID:
        raise SOArm101SessionError("selected integration manifest is not the SO-ARM101 stock-gripper manifest")
    model_raw = os.environ.get("AUTOADAPTER_SO101_MODEL")
    if not model_raw:
        raise SOArm101SessionError("AUTOADAPTER_SO101_MODEL must select the pinned official SO-ARM101 MJCF")
    direction = os.environ.get("AUTOADAPTER_GRIPPER_DIRECTION")
    if direction not in {"tick-increases-qpos", "tick-decreases-qpos"}:
        raise SOArm101SessionError("AUTOADAPTER_GRIPPER_DIRECTION must be explicitly selected")
    run_path = Path(run_directory).resolve()
    run_path.mkdir(parents=True, exist_ok=True)
    return SOArm101EvaluationRobotSession(
        model_raw,
        run_directory=run_path,
        gripper_tick_increases_qpos=direction == "tick-increases-qpos",
    )


def create_evaluation_robot_session(
    robot: str,
    manifest_path: str | Path,
    run_directory: str | Path,
) -> SOArm101EvaluationRobotSession:
    """Importable production factory alias used by run configuration."""

    return create_so_arm101_evaluation_session(robot, manifest_path, run_directory)


# Short alias for callers that name the concrete robot-scoped factory.
so_arm101_session_factory = create_so_arm101_evaluation_session


__all__ = [
    "EVIDENCE_SCOPE",
    "ROBOT_CONFIGURATION_ID",
    "ROBOT_MODEL_ID",
    "SOArm101EvaluationRobotSession",
    "SOArm101SessionError",
    "create_evaluation_robot_session",
    "create_so_arm101_evaluation_session",
    "so_arm101_session_factory",
]
