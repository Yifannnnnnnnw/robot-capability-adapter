from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.libraries.robot_package import (
    _validate_private_inputs,
    _validate_sources,
    _validate_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT
    / "libraries"
    / "robots"
    / "universal_robots_ur5e_robotiq_2f85"
    / "1.0.0"
)
TASKS_ROOT = PACKAGE_ROOT / "tasks"
PRIVATE_ROOT = TASKS_ROOT / "private"
ROBOT_ID = "universal_robots_ur5e_robotiq_2f85"
PACKAGE_VERSION = "1.0.0"
SNAPSHOT_ID = (
    "universal-robots-ur5e-robotiq-2f85-"
    "metaworld-source-protocols-2026-08-20-v1"
)
TASK_IDS = (
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_pick_place",
    "mw_pick_place_wall",
    "mw_push_wall",
    "mw_sweep_into_goal",
    "mw_drawer_open",
    "mw_drawer_close",
    "mw_button_press",
    "mw_button_press_topdown",
    "mw_handle_press",
    "mw_handle_pull",
    "mw_door_open",
    "mw_door_close",
    "mw_faucet_open",
    "mw_dial_turn",
    "mw_lever_pull",
    "mw_peg_insertion_side",
    "mw_bin_picking",
    "mw_pick_out_of_hole",
)
HOME_QPOS = np.asarray(
    [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0] + [0.0] * 8
)
HOME_CTRL = np.asarray([-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0, 0.0])
PAD_GEOMS = ("left_pad1", "left_pad2", "right_pad1", "right_pad2")
CORE_GUARD_KINDS = {
    "actuator_and_physics_step_required",
    "no_direct_state_write",
    "canonical_model_data",
    "complete_video",
}
CONTACT_TASK_GEOMS = {
    "mw_push_to_goal": ("workpiece_geom",),
    "mw_pick_place": ("workpiece_geom",),
    "mw_pick_place_wall": ("workpiece_geom",),
    "mw_push_wall": ("workpiece_geom",),
    "mw_sweep_into_goal": ("workpiece_geom",),
    "mw_drawer_open": ("drawer_handle_geom",),
    "mw_drawer_close": ("drawer_handle_geom",),
    "mw_button_press": ("front_button_geom",),
    "mw_button_press_topdown": ("top_button_geom",),
    "mw_handle_press": ("vertical_handle_geom",),
    "mw_handle_pull": ("vertical_handle_geom",),
    "mw_door_open": ("door_handle_geom",),
    "mw_door_close": ("door_handle_geom",),
    "mw_faucet_open": ("faucet_tip_geom", "faucet_arm"),
    "mw_dial_turn": ("dial_tip_geom", "dial_face"),
    "mw_lever_pull": ("lever_tip_geom", "lever_arm"),
    "mw_peg_insertion_side": ("peg_grasp_geom", "peg_shaft_geom"),
    "mw_bin_picking": ("workpiece_geom",),
    "mw_pick_out_of_hole": ("workpiece_geom",),
}
STABILITY_TASKS = {
    "mw_pick_place",
    "mw_pick_place_wall",
    "mw_pick_out_of_hole",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _static_task_geom_names(node: ET.Element, *, moving: bool = False) -> tuple[str, ...]:
    names: list[str] = []
    for child in node:
        if child.tag == "geom" and not moving:
            name = child.get("name")
            if name:
                names.append(name)
        elif child.tag == "body":
            child_moving = moving or any(
                item.tag in {"joint", "freejoint"} for item in child
            )
            names.extend(_static_task_geom_names(child, moving=child_moving))
    return tuple(names)


def _validated_public_tasks() -> tuple[dict, ...]:
    sources_path = TASKS_ROOT / "sources.json"
    catalog_path = TASKS_ROOT / "catalog.json"
    sources = _validate_sources(_read(sources_path), path=sources_path)
    return _validate_tasks(
        _read(catalog_path),
        path=catalog_path,
        source_ids={str(source["source_id"]) for source in sources},
    )


def test_private_documents_cover_the_public_snapshot_exactly() -> None:
    tasks = _validated_public_tasks()
    instances_document = _read(PRIVATE_ROOT / "instances.json")
    bindings_document = _read(PRIVATE_ROOT / "bindings.json")
    guards_document = _read(PRIVATE_ROOT / "guards.json")

    for document in (instances_document, bindings_document, guards_document):
        assert document["robot_configuration_id"] == ROBOT_ID
        assert document["package_version"] == PACKAGE_VERSION
        assert document["task_snapshot_id"] == SNAPSHOT_ID

    assert tuple(task["task_id"] for task in tasks) == TASK_IDS
    instances = instances_document["instances"]
    bindings = {
        binding["binding_id"]: binding for binding in bindings_document["bindings"]
    }
    guards = {guard["guard_id"]: guard for guard in guards_document["guards"]}
    assert tuple(instance["task_id"] for instance in instances) == TASK_IDS
    assert len({instance["instance_id"] for instance in instances}) == len(TASK_IDS)
    assert set(bindings) == {f"binding-{task_id}" for task_id in TASK_IDS}
    assert len(guards) == len(guards_document["guards"])

    tasks_by_id = {task["task_id"]: task for task in tasks}
    for instance in instances:
        task = tasks_by_id[instance["task_id"]]
        required_parameters = set(
            task["invocation_schema"]["request"]["task_parameters"]["required"]
        )
        supplied_parameters = set(
            instance["public_arguments"]["request"]["task_parameters"]
        )
        assert supplied_parameters == required_parameters
        clauses = {clause["clause_id"]: clause for clause in task["scoring"]}
        assert set(instance["clause_bindings"]) == set(clauses)
        for clause_id, binding_id in instance["clause_bindings"].items():
            binding = bindings[binding_id]
            assert binding["metric"] == clauses[clause_id]["metric"]
            assert binding["unit"] == clauses[clause_id]["unit"]
        assert all(guard_id in guards for guard_id in instance["guard_ids"])
        selected_kinds = {guards[guard_id]["kind"] for guard_id in instance["guard_ids"]}
        assert CORE_GUARD_KINDS <= selected_kinds
        assert instance["repetitions"] == 1

    _validate_private_inputs(
        package_root=PACKAGE_ROOT,
        private_dir=PRIVATE_ROOT,
        robot_configuration_id=ROBOT_ID,
        package_version=PACKAGE_VERSION,
        snapshot_id=SNAPSHOT_ID,
        tasks=tasks,
    )
    runnable = _read(ROOT / "libraries" / "robots" / "index.json")["robots"]
    assert ROBOT_ID not in runnable


def test_task_scenes_are_local_canonical_and_reset_finite() -> None:
    assert mujoco.__version__ == "3.3.6"
    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    assert len({instance["scene_entrypoint"] for instance in instances}) == 17

    for instance in instances:
        scene_path = PACKAGE_ROOT / instance["scene_entrypoint"]
        xml_root = ET.parse(scene_path).getroot()
        include_files = [element.attrib["file"] for element in xml_root.findall("include")]
        assert include_files == ["universal_robots_ur5e_robotiq_2f85.xml"]
        assert not Path(include_files[0]).is_absolute()
        worldbody = xml_root.find("worldbody")
        assert worldbody is not None

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        for name in _static_task_geom_names(worldbody):
            geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
            assert geom_id >= 0, f"{instance['task_id']}: {name}"
            contype = int(model.geom_contype[geom_id])
            conaffinity = int(model.geom_conaffinity[geom_id])
            if name.endswith("_goal_marker"):
                assert (contype, conaffinity) == (0, 0)
            else:
                assert contype & 1, f"{instance['task_id']}: {name} contype"
                assert conaffinity & 1, f"{instance['task_id']}: {name} conaffinity"
        data = mujoco.MjData(model)
        task_start_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_KEY, "task_start"
        )
        evidence_camera_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, "evidence"
        )
        assert task_start_id >= 0
        assert evidence_camera_id >= 0
        assert int(model.cam_targetbodyid[evidence_camera_id]) >= 0

        apply_framework_reset(mujoco, model, data, instance["reset"])
        mujoco.mj_forward(model, data)
        np.testing.assert_allclose(data.qpos[:14], HOME_QPOS, rtol=0.0, atol=1e-12)
        np.testing.assert_allclose(data.ctrl, HOME_CTRL, rtol=0.0, atol=1e-12)
        task_parameters = instance["public_arguments"]["request"]["task_parameters"]
        if "start_position" in task_parameters:
            workpiece_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, "workpiece"
            )
            assert workpiece_id >= 0
            np.testing.assert_allclose(
                data.xpos[workpiece_id],
                task_parameters["start_position"],
                rtol=0.0,
                atol=1e-12,
            )
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()
        assert np.isfinite(data.xpos).all()
        assert np.isfinite(data.site_xpos).all()
        minimum_distance = min(
            (float(data.contact[index].dist) for index in range(data.ncon)),
            default=float("inf"),
        )
        assert model.vis.global_.offwidth >= instance["video_width"]
        assert model.vis.global_.offheight >= instance["video_height"]

        reset_controls = np.asarray(data.ctrl).copy()
        for _ in range(100):
            mujoco.mj_step(model, data)
            step_minimum = min(
                (float(data.contact[index].dist) for index in range(data.ncon)),
                default=float("inf"),
            )
            minimum_distance = min(minimum_distance, step_minimum)
        np.testing.assert_array_equal(data.ctrl, reset_controls)
        assert minimum_distance >= -0.005, (
            f"{instance['task_id']}: minimum reset/settling contact distance "
            f"{minimum_distance}"
        )
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()


def test_every_framework_reset_is_not_already_a_full_pass() -> None:
    instances = _read(PRIVATE_ROOT / "instances.json")["instances"]
    tasks = {task["task_id"]: task for task in _validated_public_tasks()}
    bindings = {
        binding["binding_id"]: binding
        for binding in _read(PRIVATE_ROOT / "bindings.json")["bindings"]
    }

    for instance in instances:
        model = mujoco.MjModel.from_xml_path(
            str(PACKAGE_ROOT / instance["scene_entrypoint"])
        )
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, instance["reset"])
        sample = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=1,
            max_sim_time_s=1.0,
            sample_hz=20.0,
        ).snapshot()

        clauses = {
            clause["clause_id"]: clause
            for clause in tasks[instance["task_id"]]["scoring"]
        }
        clause_passes = []
        for clause_id, binding_id in instance["clause_bindings"].items():
            clause = clauses[clause_id]
            value = measure(
                bindings[binding_id],
                evidence={"samples": [sample], "step_count": 0},
                public_arguments=instance["public_arguments"],
            )
            assert np.isfinite(value)
            clause_passes.append(
                compare(
                    value,
                    comparator=clause["comparator"],
                    threshold=clause["threshold"],
                )
            )
        assert not all(clause_passes), f"reset already passes {instance['task_id']}"


def test_contact_neutrality_and_stability_guards_are_task_specific() -> None:
    instances = {
        instance["task_id"]: instance
        for instance in _read(PRIVATE_ROOT / "instances.json")["instances"]
    }
    guards = {
        guard["guard_id"]: guard
        for guard in _read(PRIVATE_ROOT / "guards.json")["guards"]
    }
    contact_guards = {
        guard_id.removeprefix("guard-contact-"): guard
        for guard_id, guard in guards.items()
        if guard["kind"] == "named_geom_contact_pair_required"
    }
    assert set(contact_guards) == set(CONTACT_TASK_GEOMS)

    for task_id, task_geoms in CONTACT_TASK_GEOMS.items():
        guard = contact_guards[task_id]
        assert tuple(guard["robot_geom_names"]) == PAD_GEOMS
        assert "robot_geom_name" not in guard
        assert tuple(guard["task_geom_names"]) == task_geoms
        assert guard["minimum_steps"] == 1
        assert f"guard-contact-{task_id}" in instances[task_id]["guard_ids"]

        model = mujoco.MjModel.from_xml_path(
            str(PACKAGE_ROOT / instances[task_id]["scene_entrypoint"])
        )
        for geom_name in (*PAD_GEOMS, *task_geoms):
            assert (
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name) >= 0
            )

    assert not any(
        guards[guard_id]["kind"] == "named_geom_contact_pair_required"
        for guard_id in instances["mw_reach_target"]["guard_ids"]
    )
    neutral = guards["guard_reach_gripper_neutral"]
    assert neutral == {
        "guard_id": "guard_reach_gripper_neutral",
        "kind": "named_joints_remain_near_reset",
        "joint_tolerances": {
            "right_driver_joint": 0.02,
            "left_driver_joint": 0.02,
        },
    }
    assert "guard_reach_gripper_neutral" in instances["mw_reach_target"]["guard_ids"]

    stability_guards = {
        guard_id.removeprefix("guard-stability-"): guard
        for guard_id, guard in guards.items()
        if guard["kind"] == "terminal_body_stability"
    }
    assert set(stability_guards) == STABILITY_TASKS
    for task_id, guard in stability_guards.items():
        assert guard["body_name"] == "workpiece"
        assert 0.0 < guard["minimum_height_m"] < 0.5
        assert guard["minimum_upright_cosine"] == 0.8
        assert f"guard-stability-{task_id}" in instances[task_id]["guard_ids"]
