from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.measurements import (
    evaluate_guards,
    evaluate_temporal,
    measure,
)
from autoadapter2.libraries import load_robot_package


PROJECT_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "unitree-go2-stock-12dof"
    / "1.0.1"
)
OLD_PACKAGE_ROOT = PACKAGE_ROOT.parent / "1.0.0"
CORRECTED_TASK_IDS = {"GO2-T02", "GO2-T03", "GO2-T06", "GO2-T16", "GO2-T17"}


def _document(relative_path: str) -> dict:
    return json.loads((PACKAGE_ROOT / relative_path).read_text(encoding="utf-8"))


def _indexed(document: dict, collection: str, key: str) -> dict[str, dict]:
    return {item[key]: item for item in document[collection]}


def _samples(points: list[tuple[float, float, float, float]]) -> list[dict]:
    return [
        {
            "time": time_s,
            "body_positions": {"base_link": [x, y, z]},
        }
        for time_s, x, y, z in points
    ]


def test_corrected_go2_package_is_isolated_and_keeps_the_fixed_driver_abi() -> None:
    package = load_robot_package(PACKAGE_ROOT)

    assert package.package_version == "1.0.1"
    assert package.snapshot_id == "unitree-go2-corrected-r1-2026-08-24-v1"
    assert len(package.tasks) == 20
    for relative_path in (
        "reference/fixed_capability_driver.py",
        "reference/driver.py",
        "skeleton/go2_velocity_policy.py",
        "skeleton/quadruped_pd_gait.py",
    ):
        assert (PACKAGE_ROOT / relative_path).read_bytes() == (
            OLD_PACKAGE_ROOT / relative_path
        ).read_bytes()


def test_t02_t03_are_outcome_based_and_use_the_true_step_edge() -> None:
    tasks = _indexed(_document("tasks/catalog.json"), "tasks", "task_id")
    instances = _indexed(
        _document("tasks/private/instances.json"), "instances", "task_id"
    )
    bindings = _indexed(
        _document("tasks/private/bindings.json"), "bindings", "binding_id"
    )

    for task_id in ("GO2-T02", "GO2-T03"):
        parameters = tasks[task_id]["invocation_schema"]["request"]["task_parameters"]
        public_parameters = instances[task_id]["public_arguments"]["request"][
            "task_parameters"
        ]
        binding_id = next(iter(instances[task_id]["clause_bindings"].values()))
        binding = bindings[binding_id]

        assert "command_speed_m_s" not in parameters["required"]
        assert "command_speed_m_s" not in parameters["properties"]
        assert "command_speed_m_s" not in public_parameters
        assert public_parameters["duration_s"] == 10.0
        assert "outcome-based adapted protocol" in tasks[task_id]["adaptation"]
        assert binding["parameters"]["finish_coordinate"] == 0.422

        def evidence(last_foot_x: float) -> dict:
            feet = {
                "FL_foot": [0.5, 0.1, 0.0],
                "FR_foot": [0.5, -0.1, 0.0],
                "RL_foot": [0.5, 0.1, 0.0],
                "RR_foot": [last_foot_x, -0.1, 0.0],
            }
            return {"samples": [{"time": 10.0, "body_positions": feet}]}

        assert measure(binding, evidence=evidence(0.422), public_arguments={}) == 1.0
        assert measure(binding, evidence=evidence(0.421999), public_arguments={}) == 0.0
        assert "go2_terminal_stability" in instances[task_id]["guard_ids"]


def test_t06_requires_far_edge_completion_and_each_obstacle_contact() -> None:
    instances = _indexed(
        _document("tasks/private/instances.json"), "instances", "task_id"
    )
    bindings = _indexed(
        _document("tasks/private/bindings.json"), "bindings", "binding_id"
    )
    guards = _indexed(_document("tasks/private/guards.json"), "guards", "guard_id")
    instance = instances["GO2-T06"]
    binding = bindings[instance["clause_bindings"]["mixed_course_completion"]]
    contact_guard_ids = [
        "go2_t06_inclined_platform_contact",
        "go2_t06_raised_platform_contact",
        "go2_t06_stairs_contact",
        "go2_t06_block_family_contact",
    ]
    contact_guards = [guards[guard_id] for guard_id in contact_guard_ids]

    assert instance["public_arguments"]["request"]["task_parameters"] == {
        "duration_s": 25.0,
        "path_waypoints_m": [
            [0.962, 0.0],
            [1.82, 0.0],
            [2.4915, 0.0],
            [4.28, 0.0],
            [4.8, 0.0],
        ],
    }
    assert [gate["coordinate"] for gate in binding["parameters"]["gates"]] == [
        0.962,
        1.82,
        2.4915,
        4.28,
        4.8,
    ]
    assert all(guard_id in instance["guard_ids"] for guard_id in contact_guard_ids)

    completed = {
        "samples": _samples(
            [
                (0.0, 0.0, 0.0, 0.3),
                (1.0, 0.962, 0.0, 0.3),
                (2.0, 1.82, 0.0, 0.3),
                (3.0, 2.4915, 0.0, 0.3),
                (4.0, 4.28, 0.0, 0.3),
                (5.0, 4.8, 0.0, 0.3),
            ]
        )
    }
    assert measure(binding, evidence=completed, public_arguments={}) == 1.0

    bypass = evaluate_guards(
        contact_guards,
        worker_result={"physical_evidence": {"contact_pair_step_counts": []}},
    )
    assert not any(bypass.values())

    actual_contacts = [
        {"geom1": "FL", "geom2": "inclined_platform", "step_count": 1},
        {"geom1": "FR", "geom2": "raised_platform", "step_count": 1},
        {"geom1": "RL", "geom2": "stair_2", "step_count": 1},
        {"geom1": "RR", "geom2": "block_3", "step_count": 1},
    ]
    contacted = evaluate_guards(
        contact_guards,
        worker_result={
            "physical_evidence": {"contact_pair_step_counts": actual_contacts}
        },
    )
    assert all(contacted.values())


def test_t16_uses_planar_distance_and_exact_five_second_dwell() -> None:
    tasks = _indexed(_document("tasks/catalog.json"), "tasks", "task_id")
    instances = _indexed(
        _document("tasks/private/instances.json"), "instances", "task_id"
    )
    bindings = _indexed(
        _document("tasks/private/bindings.json"), "bindings", "binding_id"
    )
    instance = instances["GO2-T16"]
    task = tasks["GO2-T16"]
    clauses = {clause["clause_id"]: clause for clause in task["scoring"]}
    end_binding = bindings[instance["clause_bindings"]["end_table_hold"]]
    step_off_binding = bindings[instance["clause_bindings"]["table_step_off"]]
    public_arguments = instance["public_arguments"]

    assert public_arguments["request"]["task_parameters"] == {
        "duration_s": 15.0,
        "end_table_center_m": [1.8, 0.0, 0.37],
    }
    assert end_binding["kind"] == "body_planar_target_error"
    assert clauses["end_table_hold"]["metric"] == "end_table_center_planar_error"
    assert clauses["end_table_hold"]["temporal"] == {
        "kind": "dwell",
        "duration_s": 5.0,
    }

    planar_motion = {
        "samples": _samples(
            [(0.0, 0.0, 0.0, 0.39), (1.0, 0.7, 0.0, 8.0)]
        )
    }
    assert (
        measure(step_off_binding, evidence=planar_motion, public_arguments=public_arguments)
        == 0.7
    )

    def dwell_evidence(interval_count: int) -> dict:
        return {
            "samples": _samples(
                [
                    (index * 0.05, 1.8, 0.0, 10.0)
                    for index in range(interval_count + 1)
                ]
            )
        }

    short = evaluate_temporal(
        end_binding,
        criterion=clauses["end_table_hold"],
        evidence=dwell_evidence(99),
        public_arguments=public_arguments,
    )
    exact = evaluate_temporal(
        end_binding,
        criterion=clauses["end_table_hold"],
        evidence=dwell_evidence(100),
        public_arguments=public_arguments,
    )
    assert short["valid_duration_s"] == 4.95
    assert not short["passed"]
    assert exact["valid_duration_s"] == 5.0
    assert exact["passed"]


def test_t17_requires_five_alternating_waypoints_and_only_bases_collide() -> None:
    instances = _indexed(
        _document("tasks/private/instances.json"), "instances", "task_id"
    )
    bindings = _indexed(
        _document("tasks/private/bindings.json"), "bindings", "binding_id"
    )
    instance = instances["GO2-T17"]
    order = bindings[instance["clause_bindings"]["weave_order"]]
    clearance = bindings[instance["clause_bindings"]["pole_clearance"]]
    expected_waypoints = [
        [0.55, 0.25],
        [1.25, -0.25],
        [1.9, 0.25],
        [2.45, -0.25],
        [3.05, 0.25],
    ]

    assert instance["public_arguments"]["request"]["task_parameters"] == {
        "duration_s": 20.0,
        "path_waypoints_m": expected_waypoints,
    }
    assert order["parameters"]["waypoints"] == expected_waypoints
    assert order["parameters"]["tolerance"] == 0.12

    straight = {
        "samples": _samples(
            [(float(index), point[0], 0.0, 0.3) for index, point in enumerate(expected_waypoints)]
        )
    }
    alternating = {
        "samples": _samples(
            [
                (float(index), point[0], point[1], 0.3)
                for index, point in enumerate(expected_waypoints)
            ]
        )
    }
    assert measure(order, evidence=straight, public_arguments={}) < 1.0
    assert measure(clearance, evidence=straight, public_arguments={}) < 0.1
    assert measure(order, evidence=alternating, public_arguments={}) == 1.0
    assert measure(clearance, evidence=alternating, public_arguments={}) >= 0.1

    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets" / "barkour_weave.xml")
    )
    for index in range(1, 6):
        visual_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, f"weave_pole_{index}"
        )
        base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, f"weave_pole_base_{index}"
        )
        assert visual_id >= 0 and base_id >= 0
        assert model.geom_contype[visual_id] == 0
        assert model.geom_conaffinity[visual_id] == 0
        assert (model.geom_contype[base_id] & 1) or (model.geom_conaffinity[base_id] & 1)
        assert np.allclose(model.geom_size[base_id, :2], [0.025, 0.025])


def test_corrected_instances_use_real_time_video_and_synchronized_budgets() -> None:
    instances = _indexed(
        _document("tasks/private/instances.json"), "instances", "task_id"
    )
    expected = {
        "GO2-T02": (10.0, 12.0, 5500),
        "GO2-T03": (10.0, 12.0, 5500),
        "GO2-T06": (25.0, 27.0, 13000),
        "GO2-T16": (15.0, 17.0, 8000),
        "GO2-T17": (20.0, 22.0, 10500),
    }

    assert CORRECTED_TASK_IDS == set(expected)
    for task_id, (duration_s, timeout_sim_s, max_steps) in expected.items():
        instance = instances[task_id]
        assert instance["sample_hz"] == 20
        assert instance["video_fps"] == 20
        assert instance["public_arguments"]["request"]["task_parameters"][
            "duration_s"
        ] == duration_s
        assert instance["timeout_sim_s"] == timeout_sim_s
        assert instance["max_steps"] == max_steps
