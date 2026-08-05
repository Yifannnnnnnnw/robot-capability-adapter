"""Actual MuJoCo tabletop scene layered on the pinned SO-ARM101 MJCF.

This module is deliberately separate from ``deterministic_tabletop``.  The
fixture is useful for orchestration tests; this runtime compiles and steps a
real ``mujoco.MjModel`` with free bodies, contacts, and robot forward
kinematics.  It does not claim that a task is reachable merely because its
initial state compiles.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

from ..audit import sha256_json
from ..environment_resolver import (
    EnvironmentResolutionError,
    discover_mjcf_referenced_files,
    verify_robot_model_freeze,
)
from .lerobot_mujoco import ALL_MOTORS, SO101MujocoRobot
from .scene_catalog import (
    CatalogVerifier,
    ResolvedSceneAsset,
    SceneAssetCatalog,
    SceneAssetCatalogError,
)


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_DYNAMIC_KINDS = frozenset({"cube", "cylinder"})
_RECEPTACLE_KINDS = frozenset({"tray", "bowl"})
_ROLE_FOR_LEGACY_KIND = {
    "table": "support_surface",
    "cube": "dynamic_object",
    "cylinder": "dynamic_object",
    "tray": "receptacle",
    "bowl": "receptacle",
    "planar_circle": "marker",
    "pose_target": "marker",
}
_POSE_PARAMETER_FIELDS = {
    "table": frozenset({"center_m"}),
    "cube": frozenset({"position_m", "quaternion_wxyz"}),
    "cylinder": frozenset({"position_m", "quaternion_wxyz"}),
    "tray": frozenset({"center_m"}),
    "bowl": frozenset({"center_m"}),
    "planar_circle": frozenset({"center_m"}),
    "pose_target": frozenset({"position_m"}),
}


def _load_mapping(value: Mapping[str, Any] | str | Path | None, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    path = Path(value)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError(f"{label} must contain a JSON object")
    return parsed


def _name(value: object, *, label: str) -> str:
    result = str(value)
    if not _SAFE_NAME.fullmatch(result):
        raise ValueError(f"{label} is not a safe MuJoCo name: {result!r}")
    return result


def _numbers(value: object, count: int, *, label: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != count:
        raise ValueError(f"{label} must contain exactly {count} numbers")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain only finite numbers")
    return result


def _positive(value: object, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return result


def _vec(values: Sequence[float]) -> str:
    return " ".join(f"{value:.12g}" for value in values)


def _plain_scene_value(value: Any) -> Any:
    """Convert immutable catalog containers to detached JSON-compatible data."""

    if isinstance(value, Mapping):
        return {str(key): _plain_scene_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_scene_value(item) for item in value]
    return value


class SO101MujocoTabletopRuntime(SO101MujocoRobot):
    """LeRobot-shaped SO-101 runtime backed by actual MuJoCo tabletop physics.

    ``reset`` recompiles the small scene because MuJoCo model topology is
    immutable.  That keeps every authored object/receptacle definition exact
    instead of pretending a fixed collection of generic slots is equivalent.
    Generated capabilities only need ``send_action`` and ``get_observation``;
    snapshot/contact methods are privileged harness evidence.
    """

    name = "so101_mujoco_tabletop_runtime"

    def __init__(
        self,
        robot_model_path: str | Path,
        *,
        common_reset: Mapping[str, Any] | str | Path | None = None,
        initial_state: Mapping[str, Any] | str | Path | None = None,
        use_degrees: bool = True,
        max_relative_target: float | Mapping[str, float] | None = None,
        simulation_hz: float | None = None,
        auto_step: bool = True,
        trace_path: str | Path | None = None,
        scene_catalog: Mapping[str, Any] | str | Path | None = None,
        scene_freeze: Mapping[str, Any] | str | Path | None = None,
        scene_catalog_verifier: CatalogVerifier | None = None,
        require_scene_catalog: bool = False,
        require_explicit_asset_refs: bool = False,
    ) -> None:
        # Keep the lexical selection so freeze verification can reject a
        # symlink instead of silently erasing it with ``resolve``.
        self._selected_robot_model_path = Path(robot_model_path).absolute()
        self.robot_model_path = self._selected_robot_model_path.resolve()
        if require_scene_catalog and scene_catalog is None:
            raise SceneAssetCatalogError(
                "formal catalog mode requires a selected morphology scene catalog"
            )
        if scene_freeze is not None and scene_catalog is None:
            raise SceneAssetCatalogError(
                "scene_freeze cannot be used without the selected scene_catalog"
            )
        if require_explicit_asset_refs and scene_catalog is None:
            raise SceneAssetCatalogError(
                "explicit scene asset refs require a selected scene_catalog"
            )
        self._require_explicit_asset_refs = bool(require_explicit_asset_refs)
        self._scene_catalog = (
            None if scene_catalog is None else SceneAssetCatalog(scene_catalog)
        )
        self._scene_freeze = (
            None
            if scene_freeze is None
            else _load_mapping(scene_freeze, label="scene_freeze")
        )
        self._scene_catalog_verifier = scene_catalog_verifier
        self._assert_scene_integrity()
        self._resolved_instance_evidence: dict[str, Any] = {}
        self._common_reset = _load_mapping(common_reset, label="common_reset")
        self._current_instance: dict[str, Any] = {}
        self._dynamic_body_names: tuple[str, ...] = ()
        self._receptacle_body_names: tuple[str, ...] = ()
        self._marker_names: tuple[str, ...] = ()
        self._memory_trace: list[dict[str, Any]] = []
        self._trace_sequence = 0
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - depends on optional runtime
            raise RuntimeError(
                "mujoco and numpy are required for SO101MujocoTabletopRuntime"
            ) from exc
        self._mj = mujoco
        instance = _load_mapping(initial_state or {}, label="initial_state")
        compiled_scene = self._compile_scene(self._common_reset, instance)
        super().__init__(
            self.robot_model_path,
            use_degrees=use_degrees,
            max_relative_target=max_relative_target,
            simulation_hz=simulation_hz,
            auto_step=auto_step,
            trace_path=trace_path,
            _precompiled_model=compiled_scene[0],
        )
        self._activate_compiled_scene(
            self._common_reset,
            instance,
            compiled_scene,
            reuse_current_model_data=True,
        )

    @property
    def trace_events(self) -> list[dict[str, Any]]:
        """In-memory action/world evidence; the optional JSONL trace is canonical."""
        return deepcopy(self._memory_trace)

    @property
    def current_task_id(self) -> str | None:
        value = self._current_instance.get("task_id")
        return None if value is None else str(value)

    @property
    def scene_catalog_binding(self) -> dict[str, Any] | None:
        """Return the selected immutable catalog identity, never its contents."""

        if self._scene_catalog is None:
            return None
        return {
            "catalog_id": self._scene_catalog.catalog_id,
            "version": self._scene_catalog.version,
            "source_sha256": self._scene_catalog.source_sha256,
            "content_sha256": self._scene_catalog.content_sha256,
        }

    @property
    def resolved_instance_evidence(self) -> dict[str, Any]:
        """Return detached framework-only resolved scene authority.

        Generated capabilities receive a restricted LeRobot facade, not this
        runtime object.  Validation and Demo oracles may use this snapshot to
        interpret object extents/receptacles without duplicating physical
        parameters in task instances.
        """

        return deepcopy(self._resolved_instance_evidence)

    def evidence_scope(self) -> dict[str, Any]:
        """State exactly what this runtime establishes and what remains unproven."""
        return {
            "runtime": "actual_mujoco_mjmodel_mjdata",
            "established": [
                "pinned_soarm101_mjcf_compiles_with_authored_scene",
                "six_key_lerobot_target_commands_step_mujoco_actuators",
                "robot_forward_kinematics_is_measured_from_gripperframe",
                "free_body_motion_and_contacts_are_measured_from_mjdata",
            ],
            "capability_status": {
                "G1.joint_target_control": "physically_executable_in_mujoco",
                "G2.cartesian_motion": "runtime_compatible_but_requires_generated_ik_controller_probe",
                "G3.tabletop_tasks": "scene_physics_available_but_task_success_not_certified",
            },
            "task_id": self.current_task_id,
            "task_completion_claim": "not_probed",
            "not_claimed": [
                "collision_free_reference_trajectory",
                "object_grasp_success",
                "task_oracle_pass",
                "real_hardware_transfer",
            ],
        }

    def reset(
        self,
        initial_state: Mapping[str, Any] | str | Path | None = None,
        *,
        common_reset: Mapping[str, Any] | str | Path | None = None,
        qpos: Mapping[str, float] | None = None,
    ) -> None:
        """Compile and reset one authored state, or reset current scene by qpos.

        The ``qpos`` form retains the base bridge's harness API.  The scene form
        accepts the repository's private initial-state JSON without exposing it
        to generated code.
        """
        self._assert_scene_integrity()
        if qpos is not None:
            if initial_state is not None:
                raise TypeError("initial_state and qpos are mutually exclusive")
            # This form does not recompile topology, but it still must reject
            # model/resource drift selected before Generation.
            self._robot_compile_sources()
            super().reset(qpos=qpos)
            self._emit_world("qpos_reset")
            return

        common = _load_mapping(
            self._common_reset if common_reset is None else common_reset,
            label="common_reset",
        )
        instance = _load_mapping(initial_state, label="initial_state")
        (
            model,
            dynamic_names,
            receptacle_names,
            marker_names,
            resolved_evidence,
        ) = self._compile_scene(common, instance)
        self._activate_compiled_scene(
            common,
            instance,
            (
                model,
                dynamic_names,
                receptacle_names,
                marker_names,
                resolved_evidence,
            ),
        )

    def _activate_compiled_scene(
        self,
        common: Mapping[str, Any],
        instance: dict[str, Any],
        compiled_scene: tuple[
            Any,
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            dict[str, Any],
        ],
        *,
        reuse_current_model_data: bool = False,
    ) -> None:
        """Install one compiled scene and bind its reset/evidence atomically.

        Construction supplies the already composed model to the base bridge,
        so its fresh ``MjData`` can be reused.  Later topology-changing resets
        still allocate a fresh model/data pair.
        """

        (
            model,
            dynamic_names,
            receptacle_names,
            marker_names,
            resolved_evidence,
        ) = compiled_scene
        physics_hz = 1.0 / float(model.opt.timestep)
        if not math.isclose(physics_hz, self.simulation_hz, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                "composed tabletop timestep changed the configured clock: "
                f"simulation_hz={self.simulation_hz:g}, physics_hz={physics_hz:g}"
            )

        with self._lock:
            if reuse_current_model_data:
                if self.model is not model:
                    raise RuntimeError("precompiled tabletop model was not installed by base bridge")
            else:
                self.model = model
                self.data = self._mj.MjData(model)
            self.physics_hz = physics_hz
            self._joint_ids = {
                name: self._name_id(self._mj.mjtObj.mjOBJ_JOINT, name) for name in ALL_MOTORS
            }
            self._actuator_ids = {
                name: self._name_id(self._mj.mjtObj.mjOBJ_ACTUATOR, name) for name in ALL_MOTORS
            }
            self._dynamic_body_names = dynamic_names
            self._receptacle_body_names = receptacle_names
            self._marker_names = marker_names
            self._current_instance = instance
            self._resolved_instance_evidence = resolved_evidence
            self._apply_robot_reset(common)
            self._initial_qpos = self.data.qpos.copy()
            self._initial_qvel = self.data.qvel.copy()
            self._last_receipt = None
        self._emit(
            "tabletop_reset",
            task_id=self.current_task_id,
            resolved_instance_binding_sha256=self._resolved_instance_evidence[
                "binding_sha256"
            ],
            dynamic_bodies=list(dynamic_names),
            receptacles=list(receptacle_names),
            markers=list(marker_names),
            model_counts={
                "nq": int(model.nq),
                "nv": int(model.nv),
                "nbody": int(model.nbody),
                "ngeom": int(model.ngeom),
            },
        )
        self._emit_world("after_reset")

    def advance(self, seconds: float) -> None:
        before = float(self.data.time)
        super().advance(seconds)
        self._emit_world(
            "after_advance",
            requested_seconds=float(seconds),
            advanced_seconds=float(self.data.time) - before,
        )

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        accepted = super().send_action(action)
        self._emit_world("after_action_target_write")
        return accepted

    def get_observation(self) -> dict[str, float]:
        observation = super().get_observation()
        # Auto-stepped generated functions normally observe instead of calling
        # the privileged ``advance`` hook, so bind a world snapshot to that
        # observation as well.
        self._emit_world("after_observation")
        return observation

    def world_snapshot(self) -> dict[str, Any]:
        """Return FK, free-body, velocity, and contact evidence from ``MjData``."""
        with self._lock:
            joint_positions = {
                f"{name}.pos": self._from_model(name, self._joint_rad(name))
                for name in ALL_MOTORS
            }
            objects: dict[str, Any] = {}
            for name in self._dynamic_body_names:
                body_id = self._name_id(self._mj.mjtObj.mjOBJ_BODY, name)
                joint_id = self._name_id(self._mj.mjtObj.mjOBJ_JOINT, f"{name}_freejoint")
                qpos_address = int(self.model.jnt_qposadr[joint_id])
                dof_address = int(self.model.jnt_dofadr[joint_id])
                objects[name] = {
                    "position_m": [float(value) for value in self.data.xpos[body_id]],
                    "quaternion_wxyz": [float(value) for value in self.data.xquat[body_id]],
                    "linear_velocity_m_s": [
                        float(value) for value in self.data.qvel[dof_address : dof_address + 3]
                    ],
                    "angular_velocity_rad_s": [
                        float(value) for value in self.data.qvel[dof_address + 3 : dof_address + 6]
                    ],
                    "freejoint_qpos": [
                        float(value) for value in self.data.qpos[qpos_address : qpos_address + 7]
                    ],
                }
            receptacles = {
                name: {
                    "position_m": [
                        float(value)
                        for value in self.data.xpos[
                            self._name_id(self._mj.mjtObj.mjOBJ_BODY, name)
                        ]
                    ]
                }
                for name in self._receptacle_body_names
            }
            contacts = []
            for index in range(int(self.data.ncon)):
                contact = self.data.contact[index]
                geom1 = int(contact.geom1)
                geom2 = int(contact.geom2)
                wrench = self._np.zeros(6, dtype=float)
                self._mj.mj_contactForce(self.model, self.data, index, wrench)
                contacts.append(
                    {
                        "geom1": self._id_name(self._mj.mjtObj.mjOBJ_GEOM, geom1),
                        "geom2": self._id_name(self._mj.mjtObj.mjOBJ_GEOM, geom2),
                        "body1": self._id_name(
                            self._mj.mjtObj.mjOBJ_BODY, int(self.model.geom_bodyid[geom1])
                        ),
                        "body2": self._id_name(
                            self._mj.mjtObj.mjOBJ_BODY, int(self.model.geom_bodyid[geom2])
                        ),
                        "distance_m": float(contact.dist),
                        "normal_force_n": float(max(0.0, wrench[0])),
                    }
                )
            return {
                "simulation_time_s": float(self.data.time),
                "physics_timestep_s": float(self.model.opt.timestep),
                "joint_positions": joint_positions,
                "end_effector_position_m": [float(value) for value in self.site_position()],
                "objects": objects,
                "receptacles": receptacles,
                "contacts": contacts,
                "finite_state": bool(
                    self._np.isfinite(self.data.qpos).all()
                    and self._np.isfinite(self.data.qvel).all()
                ),
            }

    def _emit_world(self, phase: str, **payload: Any) -> None:
        self._emit("world_state", phase=phase, snapshot=self.world_snapshot(), **payload)

    def _emit(self, event: str, **payload: Any) -> None:
        super()._emit(event, **payload)
        self._trace_sequence += 1
        self._memory_trace.append(
            {
                "sequence": self._trace_sequence,
                "component": "lerobot_mujoco_tabletop_bridge",
                "event": event,
                **deepcopy(payload),
            }
        )

    def _id_name(self, object_type: Any, identifier: int) -> str:
        result = self._mj.mj_id2name(self.model, object_type, identifier)
        return f"unnamed_{identifier}" if result is None else str(result)

    def _assert_scene_integrity(self) -> None:
        if self._scene_catalog is None:
            return
        self._scene_catalog.verify_unchanged()
        self._scene_catalog.verify_environment_freeze(self._scene_freeze)
        if self._scene_catalog_verifier is not None:
            result = self._scene_catalog_verifier(
                self._scene_catalog,
                None if self._scene_freeze is None else deepcopy(self._scene_freeze),
            )
            if result is False:
                raise SceneAssetCatalogError("scene_catalog_verifier rejected selection")

    def _resolve_scene_asset(
        self,
        spec: Mapping[str, Any],
        *,
        expected_role: str,
        legacy_kind: str,
    ) -> ResolvedSceneAsset:
        if self._scene_catalog is not None:
            if self._require_explicit_asset_refs and "asset_ref" not in spec:
                raise SceneAssetCatalogError(
                    f"formal scene compilation requires explicit asset_ref for {legacy_kind!r}"
                )
            return self._scene_catalog.resolve(
                spec,
                expected_role=expected_role,
                legacy_kind=legacy_kind,
            )
        if "asset_ref" in spec:
            raise SceneAssetCatalogError(
                "an asset_ref cannot be compiled without a selected scene_catalog"
            )

        # Compatibility is intentionally limited to the pre-catalog fixture
        # states.  Formal runs select a catalog and never use these constants.
        material = {
            "table": (0.55, 0.42, 0.28, 1.0),
            "cube": (0.8, 0.15, 0.12, 1.0),
            "cylinder": (0.95, 0.45, 0.08, 1.0),
            "tray": (0.2, 0.45, 0.8, 1.0),
            "bowl": (0.55, 0.25, 0.7, 1.0),
            "planar_circle": (0.1, 0.8, 0.25, 0.35),
            "pose_target": (0.1, 0.8, 0.25, 0.6),
        }.get(legacy_kind)
        if material is None or _ROLE_FOR_LEGACY_KIND.get(legacy_kind) != expected_role:
            raise SceneAssetCatalogError(
                f"unsupported legacy scene asset kind {legacy_kind!r}"
            )
        parameters = deepcopy(dict(spec))
        if legacy_kind == "table":
            parameters.setdefault("center_m", [0.35, 0.0, 0.01])
            parameters.setdefault("half_size_m", [0.35, 0.25, 0.01])
            parameters.setdefault("surface_z_m", 0.02)
        elif legacy_kind == "tray":
            parameters.setdefault("wall_thickness_m", 0.004)
        elif legacy_kind == "bowl":
            parameters.setdefault("wall_thickness_m", 0.004)
            parameters.setdefault("floor_thickness_m", 0.002)
            parameters.setdefault("radial_segments", 16)
        elif legacy_kind == "planar_circle":
            parameters.setdefault("marker_height_m", 0.0005)
        elif legacy_kind == "pose_target":
            parameters.setdefault("marker_radius_m", 0.008)
        collision_enabled = legacy_kind not in {"planar_circle", "pose_target"}
        return ResolvedSceneAsset(
            asset_ref=f"legacy-kind-only:{legacy_kind}",
            asset_id=f"legacy_{legacy_kind}",
            variant_id="legacy",
            role=expected_role,
            runtime_kind=legacy_kind,
            parameters=parameters,
            material_rgba=material,
            physics={
                "collision_enabled": collision_enabled,
                "friction": (1.0, 0.01, 0.001) if collision_enabled else (0.0, 0.0, 0.0),
                "condim": 4 if collision_enabled else 1,
            },
        )

    @staticmethod
    def _physics_geom_attributes(physics: Mapping[str, Any]) -> dict[str, str]:
        friction = _numbers(physics.get("friction"), 3, label="asset.physics.friction")
        if any(value < 0 for value in friction):
            raise SceneAssetCatalogError("asset.physics.friction values must be non-negative")
        condim = physics.get("condim")
        if not isinstance(condim, int) or isinstance(condim, bool) or condim not in {1, 3, 4, 6}:
            raise SceneAssetCatalogError("asset.physics.condim must be one of 1, 3, 4, 6")
        collision_enabled = physics.get("collision_enabled")
        if not isinstance(collision_enabled, bool):
            raise SceneAssetCatalogError("asset.physics.collision_enabled must be boolean")
        attributes = {
            "friction": _vec(friction),
            "condim": str(condim),
        }
        if not collision_enabled:
            attributes |= {"contype": "0", "conaffinity": "0"}
        return attributes

    def _resolved_asset_record(
        self,
        identifier: str,
        asset: ResolvedSceneAsset,
    ) -> dict[str, Any]:
        parameters = _plain_scene_value(asset.parameters)
        pose_fields = _POSE_PARAMETER_FIELDS[asset.runtime_kind]
        pose = {
            key: deepcopy(parameters[key])
            for key in sorted(pose_fields)
            if key in parameters
        }
        geometry_profile = {
            key: deepcopy(value)
            for key, value in sorted(parameters.items())
            if key not in pose_fields
        }
        descriptor_hash = None
        if self._scene_catalog is not None:
            descriptor_hash = self._scene_catalog.asset_descriptor_sha256.get(
                asset.asset_ref
            )
            if descriptor_hash is None:
                raise SceneAssetCatalogError(
                    f"resolved asset {asset.asset_ref!r} lacks a catalog descriptor hash"
                )
        return {
            "id": identifier,
            "asset_ref": asset.asset_ref,
            "asset_descriptor_sha256": descriptor_hash,
            "role": asset.role,
            "runtime_kind": asset.runtime_kind,
            "geometry_profile": geometry_profile,
            "pose": pose,
            "material_rgba": [float(value) for value in asset.material_rgba],
            "physics": _plain_scene_value(asset.physics),
        }

    def _apply_robot_reset(self, common: Mapping[str, Any]) -> None:
        robot = common.get("robot", {})
        if robot and not isinstance(robot, Mapping):
            raise TypeError("common_reset.robot must be an object")
        order = list(robot.get("joint_order", ALL_MOTORS)) if isinstance(robot, Mapping) else list(ALL_MOTORS)
        if order != list(ALL_MOTORS):
            raise ValueError(f"common_reset.robot.joint_order must equal {list(ALL_MOTORS)!r}")
        qpos_values = (
            _numbers(robot["qpos_rad"], len(ALL_MOTORS), label="robot.qpos_rad")
            if isinstance(robot, Mapping) and "qpos_rad" in robot
            else [self._joint_rad(name) for name in ALL_MOTORS]
        )
        qvel_values = (
            _numbers(robot["qvel_rad_s"], len(ALL_MOTORS), label="robot.qvel_rad_s")
            if isinstance(robot, Mapping) and "qvel_rad_s" in robot
            else [0.0] * len(ALL_MOTORS)
        )
        for index, name in enumerate(ALL_MOTORS):
            joint_id = self._joint_ids[name]
            qpos_address = int(self.model.jnt_qposadr[joint_id])
            dof_address = int(self.model.jnt_dofadr[joint_id])
            low, high = map(float, self.model.jnt_range[joint_id])
            qpos_value = qpos_values[index]
            if not low <= qpos_value <= high:
                raise ValueError(
                    f"robot reset for {name!r} is outside MJCF range [{low}, {high}]: {qpos_value}"
                )
            self.data.qpos[qpos_address] = qpos_value
            self.data.qvel[dof_address] = qvel_values[index]
            self.data.ctrl[self._actuator_ids[name]] = qpos_value
        self._mj.mj_forward(self.model, self.data)

    def _compile_scene(
        self,
        common: Mapping[str, Any],
        instance: Mapping[str, Any],
    ) -> tuple[
        Any,
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        dict[str, Any],
    ]:
        # Compile only the exact bytes whose detached bindings were checked.
        # This is intentionally repeated for every topology-changing reset.
        model_bytes, robot_resources = self._robot_compile_sources()
        root = ET.fromstring(model_bytes)
        worldbody = root.find("worldbody")
        if worldbody is None:
            raise ValueError("pinned SO-ARM101 MJCF has no worldbody")

        table = common.get("table", {})
        if table and not isinstance(table, Mapping):
            raise TypeError("common_reset.table must be an object")
        table_asset = self._resolve_scene_asset(
            table,
            expected_role="support_surface",
            legacy_kind="table",
        )
        table_evidence = self._resolved_asset_record("tabletop_table", table_asset)
        table_spec = table_asset.parameters
        center = _numbers(
            table_spec.get("center_m", [0.35, 0.0, 0.01]),
            3,
            label="table.center_m",
        )
        half_size = _numbers(
            table_spec.get("half_size_m", [0.35, 0.25, 0.01]),
            3,
            label="table.half_size_m",
        )
        if any(value <= 0 for value in half_size):
            raise ValueError("table.half_size_m values must be positive")
        worldbody.insert(
            0,
            ET.Element(
                "geom",
                {
                    "name": "tabletop_table",
                    "type": "box",
                    "pos": _vec(center),
                    "size": _vec(half_size),
                    "rgba": _vec(table_asset.material_rgba),
                }
                | self._physics_geom_attributes(table_asset.physics),
            ),
        )

        dynamic_names: list[str] = []
        receptacle_names: list[str] = []
        body_evidence: list[dict[str, Any]] = []
        used_names = {
            element.get("name")
            for element in root.iter("body")
            if element.get("name") is not None
        }
        bodies = instance.get("bodies", [])
        if not isinstance(bodies, Sequence) or isinstance(bodies, (str, bytes)):
            raise TypeError("initial_state.bodies must be an array")
        for index, raw in enumerate(bodies):
            if not isinstance(raw, Mapping):
                raise TypeError(f"initial_state.bodies[{index}] must be an object")
            identifier = _name(raw.get("id"), label=f"bodies[{index}].id")
            if identifier in used_names:
                raise ValueError(f"duplicate or reserved body id: {identifier!r}")
            used_names.add(identifier)
            resolved_asset: ResolvedSceneAsset | None = None
            if "asset_ref" in raw and self._scene_catalog is not None:
                resolved_asset = self._scene_catalog.resolve(raw, expected_role=None)
                kind = resolved_asset.runtime_kind
            else:
                kind = str(raw.get("kind"))
            if kind in _DYNAMIC_KINDS:
                asset = (
                    resolved_asset
                    if resolved_asset is not None
                    else self._resolve_scene_asset(
                        raw,
                        expected_role="dynamic_object",
                        legacy_kind=kind,
                    )
                )
                self._append_dynamic_body(worldbody, identifier, asset)
                dynamic_names.append(identifier)
                body_evidence.append(self._resolved_asset_record(identifier, asset))
            elif kind in _RECEPTACLE_KINDS:
                asset = (
                    resolved_asset
                    if resolved_asset is not None
                    else self._resolve_scene_asset(
                        raw,
                        expected_role="receptacle",
                        legacy_kind=kind,
                    )
                )
                self._append_receptacle(worldbody, identifier, asset)
                receptacle_names.append(identifier)
                body_evidence.append(self._resolved_asset_record(identifier, asset))
            else:
                if resolved_asset is not None:
                    raise ValueError(
                        f"unsupported tabletop body catalog kind: {resolved_asset.runtime_kind!r}"
                    )
                raise ValueError(f"unsupported tabletop body kind: {kind!r}")

        marker_names: list[str] = []
        marker_evidence: list[dict[str, Any]] = []
        markers = instance.get("markers", [])
        if not isinstance(markers, Sequence) or isinstance(markers, (str, bytes)):
            raise TypeError("initial_state.markers must be an array")
        for index, raw in enumerate(markers):
            if not isinstance(raw, Mapping):
                raise TypeError(f"initial_state.markers[{index}] must be an object")
            identifier = _name(raw.get("id"), label=f"markers[{index}].id")
            if identifier in marker_names:
                raise ValueError(f"duplicate marker id: {identifier!r}")
            asset = self._resolve_scene_asset(
                raw,
                expected_role="marker",
                legacy_kind=str(raw.get("kind")),
            )
            self._append_marker(worldbody, identifier, asset)
            marker_names.append(identifier)
            marker_evidence.append(self._resolved_asset_record(identifier, asset))

        xml = ET.tostring(root, encoding="unicode")
        model = self._mj.MjModel.from_xml_string(xml, robot_resources)
        evidence = {
            "schema_version": "robot_capability.resolved_scene_instance_evidence.v1",
            "task_id": (
                None if instance.get("task_id") is None else str(instance.get("task_id"))
            ),
            "instance_id": (
                None
                if instance.get("instance_id") is None
                else str(instance.get("instance_id"))
            ),
            "source_scene_request_sha256": sha256_json(
                {"common_reset": common, "initial_state": instance}
            ),
            "scene_catalog": self.scene_catalog_binding,
            "table": table_evidence,
            "bodies": body_evidence,
            "markers": marker_evidence,
        }
        evidence["binding_sha256"] = sha256_json(evidence)
        return (
            model,
            tuple(dynamic_names),
            tuple(receptacle_names),
            tuple(marker_names),
            evidence,
        )

    def _robot_compile_sources(self) -> tuple[bytes, dict[str, bytes]]:
        """Return the verified model/resource bytes for one compile or reset."""

        if self._scene_freeze is not None:
            try:
                return verify_robot_model_freeze(
                    self._scene_freeze,
                    self._selected_robot_model_path,
                )
            except EnvironmentResolutionError as exc:
                raise SceneAssetCatalogError(
                    f"frozen robot model verification failed: {exc}"
                ) from exc

        try:
            model_bytes = self.robot_model_path.read_bytes()
            referenced = discover_mjcf_referenced_files(
                self.robot_model_path,
                model_bytes=model_bytes,
            )
            resources = {
                item.reference: item.path.read_bytes()
                for item in referenced
            }
        except (OSError, EnvironmentResolutionError) as exc:
            raise SceneAssetCatalogError(
                f"cannot load robot model resources: {exc}"
            ) from exc
        return model_bytes, resources

    def _append_dynamic_body(
        self,
        worldbody: ET.Element,
        identifier: str,
        asset: ResolvedSceneAsset,
    ) -> None:
        kind = asset.runtime_kind
        spec = asset.parameters
        position = _numbers(spec.get("position_m"), 3, label=f"{identifier}.position_m")
        quaternion = _numbers(
            spec.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0]),
            4,
            label=f"{identifier}.quaternion_wxyz",
        )
        norm = math.sqrt(sum(value * value for value in quaternion))
        if norm <= 1e-12:
            raise ValueError(f"{identifier}.quaternion_wxyz must be non-zero")
        quaternion = [value / norm for value in quaternion]
        mass = _positive(spec.get("mass_kg"), label=f"{identifier}.mass_kg")
        body = ET.SubElement(
            worldbody,
            "body",
            {"name": identifier, "pos": _vec(position), "quat": _vec(quaternion)},
        )
        ET.SubElement(body, "freejoint", {"name": f"{identifier}_freejoint"})
        attributes = {
            "name": f"{identifier}_collision",
            "type": "box" if kind == "cube" else "cylinder",
            "mass": f"{mass:.12g}",
            "rgba": _vec(asset.material_rgba),
        } | self._physics_geom_attributes(asset.physics)
        if kind == "cube":
            full_size = _numbers(spec.get("size_m"), 3, label=f"{identifier}.size_m")
            if any(value <= 0 for value in full_size):
                raise ValueError(f"{identifier}.size_m values must be positive")
            attributes["size"] = _vec([value / 2.0 for value in full_size])
        else:
            radius = _positive(spec.get("radius_m"), label=f"{identifier}.radius_m")
            height = _positive(spec.get("height_m"), label=f"{identifier}.height_m")
            attributes["size"] = _vec([radius, height / 2.0])
        ET.SubElement(body, "geom", attributes)

    def _append_receptacle(
        self,
        worldbody: ET.Element,
        identifier: str,
        asset: ResolvedSceneAsset,
    ) -> None:
        kind = asset.runtime_kind
        spec = asset.parameters
        center = _numbers(spec.get("center_m"), 3, label=f"{identifier}.center_m")
        rim_height = _positive(spec.get("rim_height_m"), label=f"{identifier}.rim_height_m")
        body = ET.SubElement(worldbody, "body", {"name": identifier, "pos": _vec(center)})
        if kind == "tray":
            inner = _numbers(spec.get("inner_size_m"), 2, label=f"{identifier}.inner_size_m")
            if any(value <= 0 for value in inner):
                raise ValueError(f"{identifier}.inner_size_m values must be positive")
            thickness = _positive(
                spec.get("wall_thickness_m"),
                label=f"{identifier}.wall_thickness_m",
            )
            common = {
                "type": "box",
                "rgba": _vec(asset.material_rgba),
            } | self._physics_geom_attributes(asset.physics)
            walls = (
                ("east", [inner[0] / 2 + thickness / 2, 0.0, rim_height / 2], [thickness / 2, inner[1] / 2 + thickness, rim_height / 2]),
                ("west", [-inner[0] / 2 - thickness / 2, 0.0, rim_height / 2], [thickness / 2, inner[1] / 2 + thickness, rim_height / 2]),
                ("north", [0.0, inner[1] / 2 + thickness / 2, rim_height / 2], [inner[0] / 2, thickness / 2, rim_height / 2]),
                ("south", [0.0, -inner[1] / 2 - thickness / 2, rim_height / 2], [inner[0] / 2, thickness / 2, rim_height / 2]),
            )
            for suffix, position, size in walls:
                ET.SubElement(
                    body,
                    "geom",
                    common | {
                        "name": f"{identifier}_rim_{suffix}",
                        "pos": _vec(position),
                        "size": _vec(size),
                    },
                )
        else:
            inner_radius = _positive(
                spec.get("inner_radius_m"), label=f"{identifier}.inner_radius_m"
            )
            thickness = _positive(
                spec.get("wall_thickness_m"),
                label=f"{identifier}.wall_thickness_m",
            )
            floor_thickness = _positive(
                spec.get("floor_thickness_m"),
                label=f"{identifier}.floor_thickness_m",
            )
            ET.SubElement(
                body,
                "geom",
                {
                    "name": f"{identifier}_floor",
                    "type": "cylinder",
                    "pos": _vec([0.0, 0.0, floor_thickness / 2.0]),
                    "size": _vec([inner_radius, floor_thickness / 2.0]),
                    "rgba": _vec(asset.material_rgba),
                }
                | self._physics_geom_attributes(asset.physics),
            )
            raw_segments = spec.get("radial_segments")
            if (
                not isinstance(raw_segments, int)
                or isinstance(raw_segments, bool)
                or raw_segments < 8
            ):
                raise ValueError(f"{identifier}.radial_segments must be an integer >= 8")
            segments = raw_segments
            wall_radius = inner_radius + thickness / 2
            half_length = math.pi * wall_radius / segments * 1.08
            for index in range(segments):
                theta = 2.0 * math.pi * index / segments
                position = [
                    wall_radius * math.cos(theta),
                    wall_radius * math.sin(theta),
                    rim_height / 2,
                ]
                ET.SubElement(
                    body,
                    "geom",
                    {
                        "name": f"{identifier}_rim_{index:02d}",
                        "type": "box",
                        "pos": _vec(position),
                        "euler": _vec([0.0, 0.0, theta + math.pi / 2.0]),
                        "size": _vec([half_length, thickness / 2, rim_height / 2]),
                        "rgba": _vec(asset.material_rgba),
                    }
                    | self._physics_geom_attributes(asset.physics),
                )

    def _append_marker(
        self,
        worldbody: ET.Element,
        identifier: str,
        asset: ResolvedSceneAsset,
    ) -> None:
        kind = asset.runtime_kind
        spec = asset.parameters
        if kind == "planar_circle":
            center = _numbers(spec.get("center_m"), 3, label=f"{identifier}.center_m")
            radius = _positive(spec.get("radius_m"), label=f"{identifier}.radius_m")
            marker_height = _positive(
                spec.get("marker_height_m"),
                label=f"{identifier}.marker_height_m",
            )
            ET.SubElement(
                worldbody,
                "geom",
                {
                    "name": f"marker_{identifier}",
                    "type": "cylinder",
                    "pos": _vec(center),
                    "size": _vec([radius, marker_height / 2.0]),
                    "rgba": _vec(asset.material_rgba),
                    "group": "4",
                }
                | self._physics_geom_attributes(asset.physics),
            )
        elif kind == "pose_target":
            position = _numbers(spec.get("position_m"), 3, label=f"{identifier}.position_m")
            marker_radius = _positive(
                spec.get("marker_radius_m"),
                label=f"{identifier}.marker_radius_m",
            )
            ET.SubElement(
                worldbody,
                "site",
                {
                    "name": f"marker_{identifier}",
                    "type": "sphere",
                    "pos": _vec(position),
                    "size": f"{marker_radius:.12g}",
                    "rgba": _vec(asset.material_rgba),
                    "group": "4",
                },
            )
        else:
            raise ValueError(f"unsupported marker kind: {kind!r}")


__all__ = ["SO101MujocoTabletopRuntime"]
