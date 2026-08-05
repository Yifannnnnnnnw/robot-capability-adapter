"""Pure-Python SO-ARM101 kinematics reference derived from the pinned MJCF.

This file is generation-visible morphology evidence, not a runtime dependency.
Generated packages are isolated from the library and must vendor any math they
need.  The implementation deliberately uses only :mod:`math` so the same
forward model can be copied into a generated capability without importing
MuJoCo, NumPy, the bridge, or validation code.

Transform convention
--------------------
For every hinged body in parent-to-child order, MuJoCo composes

    T_world_child = T_world_parent @ T(parent_body) @ Rz(joint_angle)

because every SO-ARM101 hinge axis is ``[0, 0, 1]`` in its local body frame and
all hinge positions are the body-frame origin.  Source quaternions are in MJCF
``w, x, y, z`` order and are normalized before conversion to a rotation matrix.
The returned point is the ``gripperframe`` site position in the fixed
``baseframe``/world frame.
"""

from __future__ import annotations

import math


JOINT_ORDER = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)

JOINT_LIMITS_RAD = (
    (-1.91986, 1.91986),
    (-1.7453293, 1.7453293),
    (-1.69, 1.69),
    (-1.658063, 1.658063),
    (-2.7438473, 2.7438473),
)

# Ordered parent-to-child fixed body transforms copied from model/so101.xml.
_BODY_POSITIONS_M = (
    (0.0388353, 0.0, 0.0624),
    (-0.0303992, -0.0182778, -0.0542),
    (-0.11257, -0.028, 0.0),
    (-0.1349, 0.0052, 0.0),
    (5.55112e-17, -0.0611, 0.0181),
)

_BODY_QUATERNIONS_WXYZ = (
    (0.0, 0.0, -1.0, 0.0),
    (1.0, -1.0, -1.0, -1.0),
    (1.0, 0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0, -1.0),
    (0.0172091, -0.0172091, 0.706897, 0.706897),
)

_GRIPPERFRAME_POSITION_M = (0.012, -0.000218, -0.098127)
_GRIPPERFRAME_QUATERNION_WXYZ = (1.0, 0.0, 1.0, 0.0)


def _matmul3(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _matvec3(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))


def _quaternion_matrix_wxyz(
    quaternion: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    w, x, y, z = quaternion
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0:
        raise ValueError("quaternion must have positive norm")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def _rotation_z(angle_rad: float) -> tuple[tuple[float, float, float], ...]:
    cosine = math.cos(angle_rad)
    sine = math.sin(angle_rad)
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))


def _forward_position_rad(
    joint_positions_rad: tuple[float, float, float, float, float],
) -> tuple[float, float, float]:
    """Return the compiled ``gripperframe`` position for five arm joints."""

    return _forward_pose_rad(joint_positions_rad)[0]


def _forward_pose_rad(
    joint_positions_rad: tuple[float, float, float, float, float],
) -> tuple[
    tuple[float, float, float],
    tuple[tuple[float, float, float], ...],
]:
    """Return ``gripperframe`` position and row-major rotation in base frame."""

    if len(joint_positions_rad) != 5:
        raise ValueError("joint_positions_rad must contain five arm joints")
    if not all(math.isfinite(float(value)) for value in joint_positions_rad):
        raise ValueError("joint positions must be finite")

    rotation: tuple[tuple[float, float, float], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    position = (0.0, 0.0, 0.0)
    for body_position, body_quaternion, joint_angle in zip(
        _BODY_POSITIONS_M,
        _BODY_QUATERNIONS_WXYZ,
        joint_positions_rad,
    ):
        offset = _matvec3(rotation, body_position)
        position = tuple(position[index] + offset[index] for index in range(3))
        rotation = _matmul3(rotation, _quaternion_matrix_wxyz(body_quaternion))
        rotation = _matmul3(rotation, _rotation_z(float(joint_angle)))

    site_offset = _matvec3(rotation, _GRIPPERFRAME_POSITION_M)
    site_position = tuple(position[index] + site_offset[index] for index in range(3))
    site_rotation = _matmul3(
        rotation,
        _quaternion_matrix_wxyz(_GRIPPERFRAME_QUATERNION_WXYZ),
    )
    return site_position, site_rotation


def _forward_tool_point_rad(
    joint_positions_rad: tuple[float, float, float, float, float],
    point_in_gripperframe_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Transform one gripper-frame tool point into the fixed base frame."""

    if len(point_in_gripperframe_m) != 3 or not all(
        math.isfinite(float(value)) for value in point_in_gripperframe_m
    ):
        raise ValueError("point_in_gripperframe_m must contain three finite values")
    position, rotation = _forward_pose_rad(joint_positions_rad)
    offset = _matvec3(
        rotation,
        tuple(float(value) for value in point_in_gripperframe_m),
    )
    return tuple(position[index] + offset[index] for index in range(3))


def _solve3(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Solve a nonsingular 3x3 system using pivoted Gaussian elimination."""

    augmented = [list(matrix[row]) + [float(vector[row])] for row in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular damped least-squares system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(4)
            ]
    return tuple(augmented[row][3] for row in range(3))


_GENERIC_IK_SEEDS_RAD = (
    (0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.55, -1.0, 1.2, 0.0),
    (0.0, -0.25, 1.15, -0.85, 0.0),
    (0.45, 0.25, -0.75, 1.1, -1.2),
    (-0.45, 0.25, -0.75, 1.1, 1.2),
    (0.0, 0.9, 0.25, -1.45, -2.2),
)


def _solve_position_ik_rad(
    target_position_m: tuple[float, float, float],
    initial_joints_rad: tuple[float, float, float, float, float] | None = None,
    *,
    tolerance_m: float = 0.0025,
    max_iterations_per_seed: int = 140,
    fixed_wrist_roll_rad: float | None = None,
    tool_point_in_gripperframe_m: tuple[float, float, float] | None = None,
) -> tuple[tuple[float, float, float, float, float], float, bool]:
    """Solve position-only IK with deterministic multistart damped least squares.

    The seeds are generic robot configurations, not validation or Demo answers.
    Callers should still execute a bounded joint trajectory and trust physical
    observations/oracles rather than treating the internal FK error as proof of
    real-world success.
    """

    target = tuple(float(value) for value in target_position_m)
    if len(target) != 3 or not all(math.isfinite(value) for value in target):
        raise ValueError("target_position_m must contain three finite values")
    if tolerance_m <= 0.0 or not math.isfinite(tolerance_m):
        raise ValueError("tolerance_m must be finite and positive")
    if fixed_wrist_roll_rad is not None:
        fixed_wrist_roll_rad = float(fixed_wrist_roll_rad)
        low, high = JOINT_LIMITS_RAD[4]
        if not math.isfinite(fixed_wrist_roll_rad) or not low <= fixed_wrist_roll_rad <= high:
            raise ValueError("fixed_wrist_roll_rad is outside the finite joint range")
    tool_point = (
        (0.0, 0.0, 0.0)
        if tool_point_in_gripperframe_m is None
        else tuple(float(value) for value in tool_point_in_gripperframe_m)
    )
    if len(tool_point) != 3 or not all(math.isfinite(value) for value in tool_point):
        raise ValueError("tool_point_in_gripperframe_m must contain three finite values")

    def controlled_point(
        joints: tuple[float, float, float, float, float],
    ) -> tuple[float, float, float]:
        return _forward_tool_point_rad(joints, tool_point)

    seeds: tuple[tuple[float, float, float, float, float], ...]
    if initial_joints_rad is None:
        seeds = _GENERIC_IK_SEEDS_RAD
    else:
        initial = tuple(float(value) for value in initial_joints_rad)
        if len(initial) != 5 or not all(math.isfinite(value) for value in initial):
            raise ValueError("initial_joints_rad must contain five finite values")
        seeds = (initial,) + _GENERIC_IK_SEEDS_RAD

    best_joints = seeds[0]
    # Keep the vendor reference compatible with the generated-code policy: do
    # not use non-finite sentinels that a capability author might copy.
    best_error = 0.0
    have_best = False
    epsilon = 1e-4
    damping_squared = 2.5e-4
    for seed in seeds:
        joints = [
            min(JOINT_LIMITS_RAD[index][1], max(JOINT_LIMITS_RAD[index][0], seed[index]))
            for index in range(5)
        ]
        if fixed_wrist_roll_rad is not None:
            joints[4] = fixed_wrist_roll_rad
        for _ in range(max_iterations_per_seed):
            current = controlled_point(tuple(joints))
            error_vector = tuple(target[index] - current[index] for index in range(3))
            error_norm = math.sqrt(sum(value * value for value in error_vector))
            if not have_best or error_norm < best_error:
                best_error = error_norm
                best_joints = tuple(joints)
                have_best = True
            if error_norm <= tolerance_m:
                return tuple(joints), error_norm, True

            jacobian = [[0.0] * 5 for _ in range(3)]
            active_columns = 4 if fixed_wrist_roll_rad is not None else 5
            for column in range(active_columns):
                plus = list(joints)
                minus = list(joints)
                plus[column] += epsilon
                minus[column] -= epsilon
                high_position = controlled_point(tuple(plus))
                low_position = controlled_point(tuple(minus))
                for row in range(3):
                    jacobian[row][column] = (
                        high_position[row] - low_position[row]
                    ) / (2.0 * epsilon)

            normal = tuple(
                tuple(
                    sum(jacobian[row][column] * jacobian[other][column] for column in range(5))
                    + (damping_squared if row == other else 0.0)
                    for other in range(3)
                )
                for row in range(3)
            )
            solved = _solve3(normal, error_vector)
            delta = [
                sum(jacobian[row][column] * solved[row] for row in range(3))
                for column in range(active_columns)
            ]
            largest = max(abs(value) for value in delta)
            if largest > 0.18:
                scale = 0.18 / largest
                delta = [value * scale for value in delta]
            for index in range(active_columns):
                low, high = JOINT_LIMITS_RAD[index]
                joints[index] = min(high, max(low, joints[index] + delta[index]))

    return best_joints, best_error, have_best and best_error <= tolerance_m
