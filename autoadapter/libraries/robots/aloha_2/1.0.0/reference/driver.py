"""ALOHA 2 calibration controller for the private Direct-MuJoCo Harness.

The reference controls only the selected right arm through actuator targets and
advances the Framework-owned state only with ``mujoco.mj_step``.  It is a
calibration positive control, not a runtime dependency of generated drivers.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import mujoco
import numpy as np


ARM_JOINTS = (
    "right/waist",
    "right/shoulder",
    "right/elbow",
    "right/forearm_roll",
    "right/wrist_angle",
    "right/wrist_rotate",
)
ARM_ACTUATORS = ARM_JOINTS
GRIPPER_ACTUATOR = "right/gripper"
GRIPPER_CLOSED = 0.002
GRIPPER_OPEN = 0.037


CONTACT_JOINT_PATHS = {
    "mw_push_to_goal": (
        (0.510783, 0.501977, 0.686914, 2.050327, -1.754013, -0.336997),
        (0.510783, 0.764345, 0.612730, 2.073566, -1.665047, -0.169499),
        (0.482044, 0.761173, 0.630169, 2.046221, -1.653639, -0.159371),
        (0.406330, 0.754819, 0.669045, 1.973208, -1.628693, -0.135119),
        (0.325351, 0.750417, 0.700912, 1.893987, -1.608902, -0.113255),
        (0.239703, 0.747572, 0.725439, 1.809397, -1.593977, -0.095007),
        (0.150322, 0.745910, 0.742332, 1.720614, -1.583146, -0.081628),
        (0.058461, 0.745125, 0.751364, 1.629097, -1.575134, -0.074181),
        (-0.034395, 0.745040, 0.752404, 1.536493, -1.568276, -0.073309),
        (-0.126664, 0.745631, 0.745438, 1.444530, -1.560735, -0.079091),
        (-0.216820, 0.747041, 0.730567, 1.354888, -1.550777, -0.091019),
    ),
    "mw_push_wall": (
        (0.480782, 0.462510, 0.627286, 2.003931, -1.786434, -0.433381),
        (0.480782, 0.754229, 0.556534, 2.037695, -1.689986, -0.231661),
        (0.453196, 0.750271, 0.573565, 2.011980, -1.678036, -0.222892),
        (0.352943, 0.739489, 0.623771, 1.916762, -1.642080, -0.195076),
        (0.244726, 0.732478, 0.660771, 1.811824, -1.613602, -0.172363),
        (0.130353, 0.728540, 0.683910, 1.699537, -1.591294, -0.157024),
        (0.012460, 0.727140, 0.692706, 1.583115, -1.572670, -0.150938),
        (-0.105779, 0.728049, 0.686960, 1.466289, -1.554414, -0.154931),
        (-0.202235, 0.730668, 0.671147, 1.371365, -1.537010, -0.165601),
    ),
    "mw_sweep_into_goal": (
        (-0.342788, -0.141761, 0.599998, 0.000000, 1.112560, -1.913585),
        (-0.342788, 0.038819, 0.735501, 0.000000, 0.796477, -1.913585),
        (-0.396637, 0.055171, 0.713554, 0.000000, 0.802071, -1.967434),
        (-0.438051, 0.069745, 0.693710, 0.000000, 0.807341, -2.008847),
        (-0.487639, 0.089682, 0.666143, 0.000000, 0.814971, -2.058435),
        (-0.534751, 0.111375, 0.635609, 0.000000, 0.823813, -2.105548),
        (-0.570648, 0.129890, 0.609114, 0.000000, 0.831792, -2.141445),
        (-0.613299, 0.154373, 0.573487, 0.000000, 0.842937, -2.184096),
        (-0.645678, 0.174958, 0.543027, 0.000000, 0.852812, -2.216475),
        (-0.684040, 0.201856, 0.502556, 0.000000, 0.866384, -2.254837),
        (-0.720144, 0.229983, 0.459460, 0.000000, 0.881354, -2.290941),
        (-0.747474, 0.253331, 0.423107, 0.000000, 0.894358, -2.318271),
        (-0.779791, 0.283551, 0.375315, 0.000000, 0.911930, -2.350587),
    ),
    "mw_soccer": (
        (0.480782, 0.462510, 0.627286, 2.003931, -1.786434, -0.433381),
        (0.480782, 0.693513, 0.578229, 2.033207, -1.707474, -0.266831),
        (0.406926, 0.682329, 0.620955, 1.964722, -1.675608, -0.246578),
        (0.328058, 0.673761, 0.656164, 1.890022, -1.647734, -0.228480),
        (0.227594, 0.666568, 0.688017, 1.793267, -1.619221, -0.210801),
        (0.140060, 0.662857, 0.705537, 1.708033, -1.598863, -0.200474),
        (0.050316, 0.661009, 0.714601, 1.620158, -1.580551, -0.194946),
        (-0.058325, 0.661101, 0.714143, 1.513581, -1.559469, -0.195228),
        (-0.147927, 0.663108, 0.704323, 1.425875, -1.541024, -0.201204),
        (-0.235205, 0.666993, 0.686064, 1.340949, -1.520433, -0.211925),
    ),
}

BUTTON_PRESS_PATH = (
    (0.380315, 0.377966, 0.834065, 1.928975, -1.701508, -0.335070),
    (0.380315, 0.532642, 0.810364, 1.942175, -1.654724, -0.212015),
    (0.330504, 0.528253, 0.828313, 1.894267, -1.639843, -0.202961),
    (0.278933, 0.524638, 0.843709, 1.844315, -1.626185, -0.194825),
    (0.214997, 0.521259, 0.858696, 1.782006, -1.611275, -0.186549),
    (0.160295, 0.519195, 0.868189, 1.728448, -1.599911, -0.181113),
    (0.104610, 0.517773, 0.874893, 1.673763, -1.589299, -0.177177),
    (0.036949, 0.516877, 0.879195, 1.607183, -1.577218, -0.174608),
    (-0.019721, 0.516787, 0.879631, 1.551375, -1.567375, -0.174345),
    (-0.076264, 0.517291, 0.877201, 1.495710, -1.557433, -0.175803),
)

PICK_PLACE_PATH = (
    (0.009081, -0.725505, 1.006727, -0.387239, 0.596821, 0.678966),
    (0.437989, -0.677838, 0.983652, -0.705389, 0.796321, 1.248688),
    (0.692983, -0.590454, 0.936158, -0.797992, 0.933274, 1.516284),
    (0.692983, -0.512197, 1.035732, -0.910402, 0.816110, 1.691026),
    (0.692983, -0.426305, 1.099609, -1.037487, 0.731655, 1.868332),
    (0.692983, -0.416285, 1.105401, -1.052842, 0.723721, 1.888895),
)

DOOR_HANDLE_TOUCH_PATH = (
    (-1.678899, 0.765518, 1.333613, -1.664251, -1.516382, -0.525790),
    (-1.378690, 0.760568, 1.330310, -1.403585, -1.665821, -0.512112),
    (-1.109122, 0.726131, 1.304524, -1.151400, -1.769807, -0.417370),
    (-0.960369, 0.695645, 1.276353, -0.998647, -1.796565, -0.334535),
    (-0.895711, 0.680251, 1.259437, -0.929404, -1.798100, -0.293100),
    (-0.877514, 0.675731, 1.254029, -0.909671, -1.797232, -0.280967),
    (-0.865681, 0.672753, 1.250342, -0.896788, -1.796342, -0.272979),
)

DOOR_OPEN_PATH = (
    (0.162767, 0.376129, 0.680454, 1.712817, -1.650585, -0.508535),
    (0.162767, 0.646142, 0.637525, 1.727010, -1.616705, -0.283536),
    (0.070600, 0.618749, 0.749904, 1.444003, -1.544886, -0.200499),
    (-0.059513, 0.604880, 0.823369, 1.115323, -1.507747, -0.127931),
    (-0.215410, 0.600916, 0.849415, 0.759017, -1.483197, -0.082799),
    (-0.371593, 0.604717, 0.824376, 0.402823, -1.440302, -0.055393),
    (-0.502393, 0.618349, 0.751784, 0.069799, -1.370609, -0.013901),
    (-0.595342, 0.645447, 0.640089, -0.233668, -1.292891, 0.065205),
    (-0.612550, 0.654578, 0.607157, -0.305398, -1.275200, 0.091582),
    (-0.627495, 0.664665, 0.572489, -0.375658, -1.258891, 0.120441),
    (-0.640281, 0.675700, 0.536202, -0.444510, -1.244237, 0.151619),
    (-0.651023, 0.687666, 0.498404, -0.511994, -1.231467, 0.184920),
    (-0.659840, 0.700545, 0.459190, -0.578120, -1.220771, 0.220125),
)

DOOR_CLOSE_TOUCH_PATH = (
    (-1.040165, 1.256640, -0.845711, 1.264653, 2.174532, -2.077981),
    (-1.026230, 1.225958, -0.779918, 1.250549, 2.153006, -2.113541),
    (-0.997970, 1.168977, -0.658527, 1.230462, 2.108365, -2.175836),
    (-0.975985, 1.133299, -0.581715, 1.223013, 2.076639, -2.213121),
    (-0.960946, 1.111957, -0.535443, 1.220736, 2.056171, -2.234814),
    (-0.953311, 1.101906, -0.513557, 1.220285, 2.046120, -2.244880),
    (-0.948692, 1.096055, -0.500792, 1.220216, 2.040146, -2.250695),
    (-0.944046, 1.090334, -0.488289, 1.220291, 2.034212, -2.256350),
    (-0.937807, 1.082899, -0.472008, 1.220607, 2.026361, -2.263657),
)

NEUTRAL_ARM_PATH = ((0.0, -0.96, 1.16, 0.0, -0.3, 0.0),)

DOOR_CLOSE_PATH = (
    (-0.483663, 0.097576, 0.730537, 0.166793, -0.436307, -1.004437),
    (-0.498397, 0.262049, 0.657473, 0.146910, -0.272818, -0.627834),
    (-0.504829, 0.181389, 0.711166, 0.345982, -0.154222, -0.281255),
    (-0.520971, 0.104027, 0.761811, 0.556798, -0.027595, 0.002318),
    (-0.544980, 0.034468, 0.814302, 0.740695, 0.098961, 0.236631),
    (-0.559964, -0.024069, 0.867326, 0.846636, 0.210677, 0.459737),
    (-0.549563, -0.069954, 0.912424, 0.888236, 0.298333, 0.673906),
    (-0.526797, -0.093043, 0.937565, 0.917330, 0.347058, 0.801838),
)


def _vector(value: Any, *, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite 3-vector")
    return result


def _request(value: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("request must be an object")
    task_id = value.get("task_id")
    parameters = value.get("task_parameters")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("request.task_id must be a non-empty string")
    if not isinstance(parameters, Mapping):
        raise ValueError("request.task_parameters must be an object")
    if parameters.get("task_arm") != "right":
        raise ValueError("ALOHA 2 calibration instances must select the right arm")
    return task_id, parameters


class ReferenceAloha2Driver:
    """Small actuator-only controller used to calibrate ALOHA 2 fixtures."""

    def __init__(self, *, model: Any, data: Any) -> None:
        self.model = model
        self.data = data
        self._ee_site = self._id(mujoco.mjtObj.mjOBJ_SITE, "right/gripper")
        self._joint_ids = [
            self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ARM_JOINTS
        ]
        self._qpos_addresses = [
            int(model.jnt_qposadr[index]) for index in self._joint_ids
        ]
        self._qvel_addresses = [
            int(model.jnt_dofadr[index]) for index in self._joint_ids
        ]
        self._actuator_ids = [
            self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            for name in ARM_ACTUATORS
        ]
        self._gripper_id = self._id(
            mujoco.mjtObj.mjOBJ_ACTUATOR, GRIPPER_ACTUATOR
        )
        self._lower = np.asarray(
            [model.jnt_range[index, 0] for index in self._joint_ids], dtype=float
        )
        self._upper = np.asarray(
            [model.jnt_range[index, 1] for index in self._joint_ids], dtype=float
        )
        self._arm_target = self._current_q()

    def _id(self, object_type: Any, name: str) -> int:
        identifier = int(mujoco.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"canonical model is missing {name!r}")
        return identifier

    def _current_q(self) -> np.ndarray:
        return np.asarray(
            [self.data.qpos[address] for address in self._qpos_addresses],
            dtype=float,
        )

    def _ee_position(self) -> np.ndarray:
        return np.asarray(self.data.site_xpos[self._ee_site], dtype=float).copy()

    def _ee_rotation(self) -> np.ndarray:
        return np.asarray(
            self.data.site_xmat[self._ee_site], dtype=float
        ).copy().reshape(3, 3)

    def _body_position(self, name: str) -> np.ndarray:
        body_id = self._id(mujoco.mjtObj.mjOBJ_BODY, name)
        return np.asarray(self.data.xpos[body_id], dtype=float).copy()

    def _set_arm_target(self, target: np.ndarray) -> None:
        self._arm_target = np.asarray(target, dtype=float).copy()
        for actuator_id, value in zip(self._actuator_ids, self._arm_target):
            self.data.ctrl[actuator_id] = float(value)

    def _set_gripper(self, value: float) -> None:
        self.data.ctrl[self._gripper_id] = float(
            np.clip(value, GRIPPER_CLOSED, GRIPPER_OPEN)
        )

    def _hold(self, steps: int = 30) -> None:
        for _ in range(int(steps)):
            for actuator_id, value in zip(self._actuator_ids, self._arm_target):
                self.data.ctrl[actuator_id] = float(value)
            mujoco.mj_step(self.model, self.data)

    def _follow_joint_path(
        self,
        path: Any,
        *,
        gripper: float,
        waypoint_steps: int,
        initial_steps: int = 0,
        settle_steps: int = 0,
        hold_steps: int = 0,
    ) -> None:
        waypoints = np.asarray(path, dtype=float)
        if (
            waypoints.ndim != 2
            or waypoints.shape[1:] != (len(ARM_JOINTS),)
            or not np.isfinite(waypoints).all()
        ):
            raise ValueError("joint path must contain finite six-joint waypoints")
        if np.any(waypoints < self._lower) or np.any(waypoints > self._upper):
            raise ValueError("joint path exceeds canonical joint limits")
        if int(waypoint_steps) <= 0:
            raise ValueError("waypoint_steps must be positive")

        self._set_gripper(gripper)
        self._hold(initial_steps)
        for waypoint in waypoints:
            start = self._arm_target.copy()
            for step in range(1, int(waypoint_steps) + 1):
                alpha = step / int(waypoint_steps)
                self._set_arm_target((1.0 - alpha) * start + alpha * waypoint)
                self._set_gripper(gripper)
                mujoco.mj_step(self.model, self.data)
            self._hold(settle_steps)
        self._hold(hold_steps)

    def _move(
        self,
        target: np.ndarray,
        *,
        rotation: np.ndarray | None = None,
        steps: int = 1200,
        tolerance: float = 0.006,
        residual_tolerance: float = 0.04,
        gain: float = 1.4,
        max_joint_delta: float = 0.07,
    ) -> None:
        target = _vector(target, name="target_position")
        if rotation is not None:
            rotation = np.asarray(rotation, dtype=float)
            if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
                raise ValueError("target_rotation must be a finite 3x3 matrix")
        if int(steps) <= 0:
            raise ValueError("steps must be positive")
        if not math.isfinite(gain) or gain <= 0.0:
            raise ValueError("gain must be positive and finite")
        if not math.isfinite(max_joint_delta) or max_joint_delta <= 0.0:
            raise ValueError("max_joint_delta must be positive and finite")

        damping = 0.025
        orientation_weight = 0.25
        for _ in range(int(steps)):
            position_error = target - self._ee_position()
            position_jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            rotation_jacobian = np.zeros((3, int(self.model.nv)), dtype=float)
            mujoco.mj_jacSite(
                self.model,
                self.data,
                position_jacobian,
                rotation_jacobian if rotation is not None else None,
                self._ee_site,
            )
            if rotation is None:
                error = position_error
                jacobian = position_jacobian[:, self._qvel_addresses]
            else:
                current_rotation = self._ee_rotation()
                rotation_error = 0.5 * sum(
                    np.cross(current_rotation[:, axis], rotation[:, axis])
                    for axis in range(3)
                )
                error = np.concatenate(
                    (position_error, orientation_weight * rotation_error)
                )
                jacobian = np.vstack(
                    (
                        position_jacobian[:, self._qvel_addresses],
                        orientation_weight
                        * rotation_jacobian[:, self._qvel_addresses],
                    )
                )
            system = jacobian @ jacobian.T + (damping * damping) * np.eye(
                jacobian.shape[0]
            )
            delta = jacobian.T @ np.linalg.solve(system, error)
            delta_norm = float(np.linalg.norm(delta))
            if delta_norm > max_joint_delta:
                delta *= max_joint_delta / delta_norm
            desired = np.clip(
                self._current_q() + gain * delta,
                self._lower,
                self._upper,
            )
            self._set_arm_target(desired)
            mujoco.mj_step(self.model, self.data)
            if float(np.linalg.norm(position_error)) <= tolerance:
                self._hold(30)
                return
        residual = float(np.linalg.norm(target - self._ee_position()))
        if residual > residual_tolerance:
            raise RuntimeError(
                f"reference controller did not reach target; residual={residual:.5f}"
            )

    def _tool_target(self, parameters: Mapping[str, Any]) -> np.ndarray:
        return _vector(
            parameters.get("tool_target_position", parameters["target_position"]),
            name="tool_target_position",
        )

    def _approach(
        self,
        contact: np.ndarray,
        *,
        offset: tuple[float, float, float] = (0.0, 0.0, 0.08),
    ) -> None:
        self._move(contact + np.asarray(offset, dtype=float), residual_tolerance=0.10)
        self._move(contact, residual_tolerance=0.10)

    def _move_route(
        self,
        parameters: Mapping[str, Any],
        *,
        target: np.ndarray | None = None,
    ) -> None:
        if "route_position" in parameters:
            self._move(
                _vector(parameters["route_position"], name="route_position"),
                residual_tolerance=0.25,
                gain=1.1,
                max_joint_delta=0.04,
            )
        self._move(
            self._tool_target(parameters) if target is None else target,
            residual_tolerance=0.25,
            gain=1.1,
            max_joint_delta=0.04,
        )

    def reach_task(self, request: Any) -> None:
        _, parameters = _request(request)
        self._arm_target = self._current_q()
        self._set_gripper(GRIPPER_OPEN)
        self._move(_vector(parameters["target_position"], name="target_position"))

    def contact_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        self._arm_target = self._current_q()
        if task_id in CONTACT_JOINT_PATHS:
            sweep = task_id == "mw_sweep_into_goal"
            self._follow_joint_path(
                CONTACT_JOINT_PATHS[task_id],
                gripper=GRIPPER_CLOSED,
                initial_steps=120,
                waypoint_steps=400 if sweep else 45,
                hold_steps=150 if sweep else 100,
            )
            return
        contact = _vector(parameters["contact_position"], name="contact_position")
        self._set_gripper(GRIPPER_CLOSED)
        self._hold(40)
        self._approach(contact)
        target = self._tool_target(parameters)
        if task_id == "mw_push_to_goal":
            target = target + np.asarray((0.0, 0.07, 0.0))
        elif task_id == "mw_push_wall":
            target = target + np.asarray((0.0, 0.10, 0.0))
        elif task_id == "mw_soccer":
            target = target + np.asarray((0.0, 0.09, 0.0))
        self._move_route(parameters, target=target)
        self._hold(80)

    def object_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        self._arm_target = self._current_q()
        grasp = _vector(parameters["grasp_position"], name="grasp_position")
        release = _vector(parameters["release_position"], name="release_position")
        self._set_gripper(GRIPPER_OPEN)
        self._hold(80)
        self._move(grasp + np.asarray((0.0, 0.0, 0.09)))
        self._move(grasp, residual_tolerance=0.05)
        grasp_gripper = float(parameters.get("grasp_gripper", 0.012))
        if not math.isfinite(grasp_gripper):
            raise ValueError("grasp_gripper must be finite")
        self._set_gripper(grasp_gripper)
        self._hold(220)
        self._move(
            grasp + np.asarray((0.0, 0.0, 0.11)),
            residual_tolerance=0.07,
        )
        if task_id == "mw_pick_place":
            self._follow_joint_path(
                PICK_PLACE_PATH,
                gripper=grasp_gripper,
                waypoint_steps=300,
                settle_steps=100,
            )
            self._set_gripper(GRIPPER_OPEN)
            self._hold(300)
            return
        if "route_position" in parameters:
            self._move(
                _vector(parameters["route_position"], name="route_position"),
                residual_tolerance=0.08,
            )
        self._move(release, residual_tolerance=0.08)
        self._move(
            self._tool_target(parameters),
            residual_tolerance=0.08,
        )
        self._set_gripper(GRIPPER_OPEN)
        self._hold(160)

    def fixture_task(self, request: Any) -> None:
        task_id, parameters = _request(request)
        self._arm_target = self._current_q()
        if task_id == "mw_button_press":
            self._follow_joint_path(
                BUTTON_PRESS_PATH,
                gripper=GRIPPER_CLOSED,
                initial_steps=120,
                waypoint_steps=35,
                hold_steps=80,
            )
            return
        if task_id == "mw_door_open":
            self._follow_joint_path(
                DOOR_HANDLE_TOUCH_PATH,
                gripper=GRIPPER_CLOSED,
                initial_steps=200,
                waypoint_steps=180,
                hold_steps=150,
            )
            self._follow_joint_path(
                DOOR_HANDLE_TOUCH_PATH[-2::-1],
                gripper=GRIPPER_CLOSED,
                initial_steps=20,
                waypoint_steps=120,
                hold_steps=80,
            )
            self._follow_joint_path(
                DOOR_OPEN_PATH,
                gripper=GRIPPER_CLOSED,
                initial_steps=20,
                waypoint_steps=80,
                hold_steps=160,
            )
            return
        if task_id == "mw_door_close":
            self._follow_joint_path(
                DOOR_CLOSE_TOUCH_PATH,
                gripper=0.012,
                initial_steps=200,
                waypoint_steps=160,
                hold_steps=100,
            )
            self._follow_joint_path(
                NEUTRAL_ARM_PATH,
                gripper=0.012,
                initial_steps=20,
                waypoint_steps=400,
                hold_steps=150,
            )
            self._follow_joint_path(
                DOOR_CLOSE_PATH,
                gripper=GRIPPER_CLOSED,
                initial_steps=20,
                waypoint_steps=100,
                hold_steps=120,
            )
            return
        contact = _vector(parameters["contact_position"], name="contact_position")
        grasp_tasks = {"mw_drawer_open", "mw_drawer_close", "mw_handle_pull"}
        self._set_gripper(0.026 if task_id in grasp_tasks else GRIPPER_CLOSED)
        self._hold(60)
        if task_id in {"mw_drawer_open", "mw_drawer_close"}:
            offset = (0.0, -0.07, 0.0)
        elif task_id == "mw_button_press":
            offset = (0.0, -0.07, 0.0)
        elif task_id in {"mw_window_open", "mw_window_close"}:
            offset = (0.0, -0.06, 0.0)
        else:
            offset = (0.0, 0.0, 0.07)
        self._approach(contact, offset=offset)
        if task_id in grasp_tasks:
            self._set_gripper(GRIPPER_CLOSED)
            self._hold(160)
        if task_id == "mw_window_close":
            target = self._tool_target(parameters) + np.asarray((-0.04, 0.0, 0.0))
            self._move_route(parameters, target=target)
        else:
            self._move_route(parameters)
        self._hold(100)

    def rotation_task(self, request: Any) -> None:
        _, parameters = _request(request)
        self._arm_target = self._current_q()
        contact = _vector(parameters["contact_position"], name="contact_position")
        self._set_gripper(GRIPPER_CLOSED)
        self._hold(50)
        self._approach(contact)
        self._move_route(parameters)
        self._hold(120)


def build(*, model: Any, data: Any) -> ReferenceAloha2Driver:
    return ReferenceAloha2Driver(model=model, data=data)
