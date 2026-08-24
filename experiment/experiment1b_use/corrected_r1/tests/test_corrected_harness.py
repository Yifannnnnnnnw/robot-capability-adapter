from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
import sys

import mujoco
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CORRECTED_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for path in (REPOSITORY_ROOT, CORRECTED_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from autoadapter2.b2.task_harness import evaluate_b2_task_harness
from autoadapter2.b2.task_public_observation import (
    build_b2_public_task_projection,
    project_task_public_observation,
)
from autoadapter2.harness.measurements import aggregate_criterion, evaluate_temporal
from autoadapter2.harness.runner import HarnessError
from autoadapter2.libraries.robot_package import load_robot_package
from experiment.experiment1b_use.corrected_r1.config.build_config import (
    _task_suite,
)
from runtime.harness import (
    evaluate_corrected_task_harness,
    evaluate_scene_contact_integrity,
)


GO2_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.1"
)
SO101_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.2"
)

GO2_SCENES = {
    "GO2-T02": "assets/lee_step_up.xml",
    "GO2-T03": "assets/lee_step_down.xml",
    "GO2-T06": "assets/miki_mixed_course.xml",
    "GO2-T16": "assets/barkour_tables.xml",
    "GO2-T17": "assets/barkour_weave.xml",
}
SO101_SCENES = {
    "mw_push_to_goal": "assets/push_to_goal_scene.xml",
    "mw_sweep_into_goal": "assets/sweep_into_goal_scene.xml",
    "mw_pick_place": "assets/pick_place_scene.xml",
    "mw_pick_place_wall": "assets/pick_place_wall_scene.xml",
    "mw_dial_turn": "assets/dial_scene.xml",
}


def _contact_evidence(
    geom1: str | None = None,
    geom2: str | None = None,
    distance_m: float | None = None,
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    if geom1 is not None and geom2 is not None and distance_m is not None:
        records.append(
            {
                "geom1": geom1,
                "geom2": geom2,
                "minimum_distance_m": distance_m,
            }
        )
    return {
        "contact_monitoring_complete": True,
        "minimum_contact_distance_m": distance_m,
        "contact_pair_min_distances": records,
    }


@pytest.mark.parametrize(
    ("task_id", "root", "scene"),
    [
        *((task_id, GO2_ROOT, scene) for task_id, scene in GO2_SCENES.items()),
        *((task_id, SO101_ROOT, scene) for task_id, scene in SO101_SCENES.items()),
    ],
)
def test_all_corrected_task_scenes_load_and_accept_complete_no_contact_evidence(
    task_id: str,
    root: Path,
    scene: str,
) -> None:
    result = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence(),
        robot_configuration_id=(
            "unitree-go2-stock-12dof"
            if task_id.startswith("GO2-")
            else "robotstudio_so101"
        ),
        task_id=task_id,
        scene_path=root / scene,
    )

    assert result["passed"] is True
    assert result["pair_results"] == []


def test_real_go2_scene_uses_28mm_foot_support_proxy_but_not_for_base() -> None:
    expected = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence("FL", "lee_step", -0.026),
        robot_configuration_id="unitree-go2-stock-12dof",
        task_id="GO2-T02",
        scene_path=GO2_ROOT / GO2_SCENES["GO2-T02"],
    )
    unrelated = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence("geom_2", "lee_step", -0.0051),
        robot_configuration_id="unitree-go2-stock-12dof",
        task_id="GO2-T02",
        scene_path=GO2_ROOT / GO2_SCENES["GO2-T02"],
    )

    assert expected["passed"] is True
    assert expected["deepest_contact_pair"]["body1"] == "FL_calf"
    assert expected["deepest_contact_pair"]["applied_threshold_m"] == pytest.approx(
        0.028
    )
    assert unrelated["passed"] is False
    assert unrelated["deepest_contact_pair"]["applied_threshold_m"] == 0.005


def test_real_t17_scene_keeps_pole_base_contact_at_5mm() -> None:
    result = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence("FL", "weave_pole_base_1", -0.0051),
        robot_configuration_id="unitree-go2-stock-12dof",
        task_id="GO2-T17",
        scene_path=GO2_ROOT / GO2_SCENES["GO2-T17"],
    )

    assert result["passed"] is False
    pair = result["deepest_contact_pair"]
    assert pair["expected_pair_declared"] is False
    assert pair["applied_threshold_m"] == 0.005


@pytest.mark.parametrize("fixture", ["wall", "wall_pick_goal_pedestal"])
def test_real_wall_scene_keeps_wall_and_pedestal_at_5mm(fixture: str) -> None:
    result = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence("workpiece_geom", fixture, -0.0051),
        robot_configuration_id="robotstudio_so101",
        task_id="mw_pick_place_wall",
        scene_path=SO101_ROOT / SO101_SCENES["mw_pick_place_wall"],
    )

    assert result["passed"] is False
    pair = result["deepest_contact_pair"]
    assert pair["expected_pair_declared"] is False
    assert pair["applied_threshold_m"] == 0.005


def test_real_so101_scene_allows_only_declared_workpiece_support_pair() -> None:
    expected = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence(
            "work_surface", "workpiece_geom", -0.011
        ),
        robot_configuration_id="robotstudio_so101",
        task_id="mw_push_to_goal",
        scene_path=SO101_ROOT / SO101_SCENES["mw_push_to_goal"],
    )
    unrelated = evaluate_scene_contact_integrity(
        physical_evidence=_contact_evidence("floor", "workpiece_geom", -0.006),
        robot_configuration_id="robotstudio_so101",
        task_id="mw_push_to_goal",
        scene_path=SO101_ROOT / SO101_SCENES["mw_push_to_goal"],
    )

    assert expected["passed"] is True
    assert expected["deepest_contact_pair"]["applied_threshold_m"] == pytest.approx(
        0.012
    )
    assert unrelated["passed"] is False
    assert unrelated["deepest_contact_pair"]["applied_threshold_m"] == 0.005


def _sample(
    time_s: float,
    *,
    body_positions: Mapping[str, tuple[float, float, float]],
    site_positions: Mapping[str, tuple[float, float, float]] | None = None,
) -> dict[str, object]:
    return {
        "time": time_s,
        "body_positions": dict(body_positions),
        "body_quaternions": {"base_link": (1.0, 0.0, 0.0, 0.0)},
        "site_positions": dict(site_positions or {}),
    }


def _base_worker(samples: list[dict[str, object]]) -> dict[str, object]:
    return {
        "worker_completed": True,
        "method_invoked": True,
        "candidate_exception": None,
        "canonical_model_data": True,
        "physical_evidence": {
            "step_count": 100,
            "ctrl_observed_before_step": True,
            "ctrl_changed_from_reset": True,
            "direct_state_write_detected": False,
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": None,
            "contact_pair_min_distances": [],
            "contact_pair_step_counts": [],
            "samples": samples,
        },
        "video": {"requested": True, "complete": True},
    }


def _passing_worker(task_id: str, request: Mapping[str, object]) -> dict[str, object]:
    parameters = request["task_parameters"]
    assert isinstance(parameters, Mapping)
    if task_id in {"GO2-T02", "GO2-T03"}:
        feet = {
            name: (0.422, 0.0, 0.02)
            for name in ("FL_foot", "FR_foot", "RL_foot", "RR_foot")
        }
        feet["base_link"] = (0.3, 0.0, 0.3)
        worker = _base_worker([_sample(0.0, body_positions=feet)])
    elif task_id == "GO2-T06":
        positions = (0.0, 0.962, 1.82, 2.4915, 4.28, 4.8)
        worker = _base_worker(
            [
                _sample(
                    float(index),
                    body_positions={"base_link": (position, 0.0, 0.3)},
                )
                for index, position in enumerate(positions)
            ]
        )
        worker["physical_evidence"]["contact_pair_step_counts"] = [
            {"geom1": "FL", "geom2": geom, "step_count": 1}
            for geom in (
                "inclined_platform",
                "raised_platform",
                "stair_1",
                "block_1",
            )
        ]
    elif task_id == "GO2-T16":
        worker = _base_worker(
            [
                _sample(0.0, body_positions={"base_link": (0.0, 0.0, 0.3)}),
                _sample(0.05, body_positions={"base_link": (1.8, 0.0, 0.3)}),
                _sample(5.05, body_positions={"base_link": (1.8, 0.0, 0.3)}),
            ]
        )
    elif task_id == "GO2-T17":
        waypoints = parameters["path_waypoints_m"]
        assert isinstance(waypoints, list)
        worker = _base_worker(
            [
                _sample(0.0, body_positions={"base_link": (0.0, 0.0, 0.3)}),
                *[
                    _sample(
                        float(index),
                        body_positions={"base_link": (point[0], point[1], 0.3)},
                    )
                    for index, point in enumerate(waypoints, start=1)
                ],
            ]
        )
    elif task_id == "mw_dial_turn":
        target = tuple(parameters["target_position"])
        worker = _base_worker(
            [
                _sample(
                    0.0,
                    body_positions={},
                    site_positions={"dial_tip_site": target},
                )
            ]
        )
    else:
        target = tuple(parameters["target_position"])
        worker = _base_worker(
            [_sample(0.0, body_positions={"workpiece": target})]
        )
    return worker


def test_corrected_harness_end_to_end_judges_all_ten_tasks(
    tmp_path: Path,
) -> None:
    suite = _task_suite()
    suite_path = tmp_path / "task_suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    packages = {
        "unitree-go2-stock-12dof": load_robot_package(GO2_ROOT),
        "robotstudio_so101": load_robot_package(SO101_ROOT),
    }

    reports = []
    for robot_suite in suite["robot_suites"]:
        package = packages[robot_suite["robot_configuration_id"]]
        for task in robot_suite["tasks"]:
            request = task["public_projection"]["request"]
            report = evaluate_corrected_task_harness(
                package=package,
                task_suite_path=suite_path,
                instance_id=task["private_instance_id"],
                replicate_id="R1",
                session_result={
                    "worker": _passing_worker(task["task_id"], request)
                },
            )
            reports.append(report)

    assert len(reports) == 10
    assert {report["task_id"] for report in reports} == {
        *GO2_SCENES,
        *SO101_SCENES,
    }
    assert all(report["formal_episode"] is False for report in reports)
    assert all(report["physical_harness_verdict"] == "PASS" for report in reports)


def test_t06_completed_gates_without_one_obstacle_contact_fails_trusted_guard(
    tmp_path: Path,
) -> None:
    suite = _task_suite()
    suite_path = tmp_path / "task_suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    task = next(
        task
        for robot in suite["robot_suites"]
        for task in robot["tasks"]
        if task["task_id"] == "GO2-T06"
    )
    worker = _passing_worker("GO2-T06", task["public_projection"]["request"])
    worker["physical_evidence"]["contact_pair_step_counts"] = [
        record
        for record in worker["physical_evidence"]["contact_pair_step_counts"]
        if record["geom2"] != "block_1"
    ]

    report = evaluate_corrected_task_harness(
        package=load_robot_package(GO2_ROOT),
        task_suite_path=suite_path,
        instance_id="go2-go2-t06",
        replicate_id="R1",
        session_result={"worker": worker},
    )

    assert report["task_metric_passed"] is True
    assert report["guard_outcomes"]["go2_t06_block_family_contact"] is False
    assert report["physical_harness_verdict"] == "FAIL"


def test_planar_dwell_boundary_uses_contiguous_simulation_time() -> None:
    binding = {
        "kind": "body_planar_target_error",
        "parameters": {
            "body_name": "base_link",
            "target_argument": "request.task_parameters.end_table_center_m",
        },
    }
    criterion = {
        "comparator": "<=",
        "threshold": 0.4,
        "temporal": {"kind": "dwell", "duration_s": 5.0},
    }
    arguments = {
        "request": {
            "task_parameters": {"end_table_center_m": [1.8, 0.0, 0.37]}
        }
    }

    def evidence(end_time_s: float) -> dict[str, object]:
        return {
            "samples": [
                _sample(0.0, body_positions={"base_link": (0.0, 0.0, 0.3)}),
                _sample(0.05, body_positions={"base_link": (1.8, 0.0, 0.3)}),
                _sample(
                    end_time_s,
                    body_positions={"base_link": (1.8, 0.0, 0.3)},
                ),
            ]
        }

    short = evaluate_temporal(
        binding,
        criterion=criterion,
        evidence=evidence(5.0),
        public_arguments=arguments,
    )
    exact = evaluate_temporal(
        binding,
        criterion=criterion,
        evidence=evidence(5.05),
        public_arguments=arguments,
    )

    assert short["valid_duration_s"] == pytest.approx(4.95)
    assert short["passed"] is False
    assert exact["valid_duration_s"] == pytest.approx(5.0)
    assert exact["passed"] is True


def test_edge_boundary_and_t17_waypoint_order_use_trusted_measurements() -> None:
    edge_binding = {
        "kind": "named_bodies_axis_completion",
        "parameters": {
            "body_names": ["FL_foot", "FR_foot", "RL_foot", "RR_foot"],
            "axis": 0,
            "finish_coordinate": 0.422,
            "direction": 1,
        },
    }
    edge_criterion = {
        "comparator": "==",
        "threshold": 1.0,
        "temporal": {"kind": "within", "duration_s": 10.0},
    }

    def foot_evidence(x_m: float) -> dict[str, object]:
        return {
            "samples": [
                _sample(
                    0.0,
                    body_positions={
                        name: (x_m, 0.0, 0.02)
                        for name in ("FL_foot", "FR_foot", "RL_foot", "RR_foot")
                    },
                )
            ]
        }

    assert evaluate_temporal(
        edge_binding,
        criterion=edge_criterion,
        evidence=foot_evidence(0.422),
        public_arguments={},
    )["passed"] is True
    assert evaluate_temporal(
        edge_binding,
        criterion=edge_criterion,
        evidence=foot_evidence(0.421999),
        public_arguments={},
    )["passed"] is False

    waypoints = [
        [0.55, 0.25],
        [1.25, -0.25],
        [1.90, 0.25],
        [2.45, -0.25],
        [3.05, 0.25],
    ]
    weave_binding = {
        "kind": "ordered_body_waypoint_completion_ratio",
        "parameters": {
            "body_name": "base_link",
            "waypoints": waypoints,
            "tolerance": 0.12,
        },
    }
    weave_criterion = {
        "comparator": "==",
        "threshold": 1.0,
        "temporal": {"kind": "eventual"},
    }

    def path_evidence(points: list[tuple[float, float]]) -> dict[str, object]:
        return {
            "samples": [
                _sample(
                    float(index),
                    body_positions={"base_link": (x_m, y_m, 0.3)},
                )
                for index, (x_m, y_m) in enumerate(points)
            ]
        }

    straight = [(x_m, 0.0) for x_m, _y_m in waypoints]
    alternating = [(0.0, 0.0), *[(x_m, y_m) for x_m, y_m in waypoints]]
    assert evaluate_temporal(
        weave_binding,
        criterion=weave_criterion,
        evidence=path_evidence(straight),
        public_arguments={},
    )["passed"] is False
    assert evaluate_temporal(
        weave_binding,
        criterion=weave_criterion,
        evidence=path_evidence(alternating),
        public_arguments={},
    )["passed"] is True


def test_dial_trusted_metric_is_inclusive_at_7cm() -> None:
    binding = {
        "kind": "final_site_position_error",
        "parameters": {
            "site_name": "dial_tip_site",
            "target_argument": "request.task_parameters.target_position",
        },
    }
    criterion = {
        "comparator": "<=",
        "threshold": 0.07,
        "temporal": {"kind": "terminal_state_after_turn"},
    }
    arguments = {
        "request": {"task_parameters": {"target_position": [0.0, 0.0, 0.0]}}
    }

    def evidence(distance_m: float) -> dict[str, object]:
        return {
            "samples": [
                _sample(
                    0.0,
                    body_positions={},
                    site_positions={"dial_tip_site": (distance_m, 0.0, 0.0)},
                )
            ]
        }

    exact = evaluate_temporal(
        binding,
        criterion=criterion,
        evidence=evidence(0.07),
        public_arguments=arguments,
    )
    outside = evaluate_temporal(
        binding,
        criterion=criterion,
        evidence=evidence(0.070001),
        public_arguments=arguments,
    )
    assert exact["value"] == pytest.approx(0.07)
    assert outside["value"] == pytest.approx(0.070001)
    assert aggregate_criterion(
        {"kind": "single_trial"},
        comparator="<=",
        threshold=0.07,
        values=[exact["value"]],
        temporal_passes=[exact["passed"]],
    )["passed"] is True
    assert aggregate_criterion(
        {"kind": "single_trial"},
        comparator="<=",
        threshold=0.07,
        values=[outside["value"]],
        temporal_passes=[outside["passed"]],
    )["passed"] is False


def _package_task(root: Path, task_id: str) -> tuple[dict[str, object], dict[str, object]]:
    catalog = json.loads((root / "tasks/catalog.json").read_text(encoding="utf-8"))
    instances = json.loads(
        (root / "tasks/private/instances.json").read_text(encoding="utf-8")
    )
    task = next(item for item in catalog["tasks"] if item["task_id"] == task_id)
    instance = next(
        item for item in instances["instances"] if item["task_id"] == task_id
    )
    return task, instance


def test_go2_step_public_projection_omits_fixed_speed_but_old_input_still_works() -> None:
    task, instance = _package_task(GO2_ROOT, "GO2-T02")
    projection = build_b2_public_task_projection(
        task_definition=task,
        public_arguments=instance["public_arguments"],
    )

    assert "command_speed_m_s" not in projection["task_parameters"]
    model = mujoco.MjModel.from_xml_path(
        str((GO2_ROOT / GO2_SCENES["GO2-T02"]).resolve())
    )
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    observation = project_task_public_observation(
        public_task=projection,
        robot_configuration_id="unitree-go2-stock-12dof",
        mujoco=mujoco,
        model=model,
        data=data,
    )
    assert "forward_speed_m_s" not in observation["task_state"]["step_command"]

    legacy_arguments = json.loads(json.dumps(instance["public_arguments"]))
    legacy_arguments["request"]["task_parameters"]["command_speed_m_s"] = 0.2
    legacy_projection = build_b2_public_task_projection(
        task_definition=task,
        public_arguments=legacy_arguments,
    )
    assert legacy_projection["task_parameters"]["command_speed_m_s"] == 0.2


def test_dial_public_observation_exposes_feedback_without_harness_state() -> None:
    task, instance = _package_task(SO101_ROOT, "mw_dial_turn")
    projection = build_b2_public_task_projection(
        task_definition=task,
        public_arguments=instance["public_arguments"],
    )
    model = mujoco.MjModel.from_xml_path(
        str((SO101_ROOT / SO101_SCENES["mw_dial_turn"]).resolve())
    )
    data = mujoco.MjData(model)
    reset = instance["reset"]
    for name, value in reset["joint_positions"].items():
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        data.qpos[model.jnt_qposadr[joint_id]] = value
    mujoco.mj_forward(model, data)

    observation = project_task_public_observation(
        public_task=projection,
        robot_configuration_id="robotstudio_so101",
        mujoco=mujoco,
        model=model,
        data=data,
    )

    dial = observation["task_state"]["dial_tip"]
    assert set(dial) == {
        "position_world_m",
        "target_distance_m",
        "gripper_contact_detected",
    }
    assert set(
        observation["task_state"]["end_effector_public_waypoint_distances_m"]
    ) == {"contact", "route", "tool_target"}
    flattened_keys: list[str] = []

    def collect_keys(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                flattened_keys.append(str(key).lower())
                collect_keys(child)
        elif isinstance(value, list):
            for child in value:
                collect_keys(child)

    collect_keys(observation)
    assert not any(
        token in key
        for key in flattened_keys
        for token in ("verdict", "passed", "threshold", "maximum_goal")
    )


def test_default_b2_harness_identity_still_rejects_corrected_suite(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "corrected.json"
    suite_path.write_text(json.dumps(_task_suite()), encoding="utf-8")
    package = load_robot_package(GO2_ROOT)

    with pytest.raises(HarnessError, match="incompatible identity"):
        evaluate_b2_task_harness(
            package=package,
            task_suite_path=suite_path,
            instance_id="go2-go2-t02",
            replicate_id="R1",
            session_result={"worker": _passing_worker("GO2-T02", {
                "task_parameters": {"duration_s": 10.0, "step_height_m": 0.054}
            })},
        )


def test_corrected_wrapper_rejects_suite_marked_as_formal(tmp_path: Path) -> None:
    suite = _task_suite()
    suite["formal_episode"] = True
    suite_path = tmp_path / "incorrectly-formal.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")

    with pytest.raises(HarnessError, match="formal_episode=false"):
        evaluate_corrected_task_harness(
            package=load_robot_package(GO2_ROOT),
            task_suite_path=suite_path,
            instance_id="go2-go2-t02",
            replicate_id="R1",
            session_result={"worker": _passing_worker("GO2-T02", {
                "task_parameters": {"duration_s": 10.0, "step_height_m": 0.054}
            })},
        )
