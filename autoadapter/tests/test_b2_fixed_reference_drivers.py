from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.b2.session_runner import (
    RecapWorkerSessionConfig,
    run_recap_worker_session,
)
from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.harness.session import apply_framework_reset


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = REPOSITORY_ROOT / "autoadapter/libraries/robots"
BUNDLE_ROOT = REPOSITORY_ROOT / "experiment/experiment1b_use/validation/reference/resolved"


def _leaf(name: str, request: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasoning_summary": f"Run the next fixed canary leaf: {name}.",
        "subtasks": [
            {
                "kind": "capability",
                "capability_name": name,
                "request": request,
            }
        ],
    }


class _ScriptedModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_recap_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.responses[len(self.calls) - 1]


def _design(robot_id: str) -> dict[str, Any]:
    return json.loads(
        (BUNDLE_ROOT / robot_id / "capability_design.json").read_text(
            encoding="utf-8"
        )
    )


def _suite(robot_id: str) -> dict[str, Any]:
    return json.loads(
        (BUNDLE_ROOT / robot_id / "capability_validation_suite.json").read_text(
            encoding="utf-8"
        )
    )


def _load_fixed_driver(robot_id: str) -> Any:
    path = ROBOT_ROOT / robot_id / "1.0.0/reference/fixed_capability_driver.py"
    spec = importlib.util.spec_from_file_location(
        f"b2_fixed_reference_{robot_id.replace('-', '_')}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("robot_id", "expected_methods"),
    [
        (
            "robotstudio_so101",
            (
                "move_end_effector_to_position",
                "trace_cartesian_path",
                "set_gripper_opening",
                "approach_until_contact",
                "move_cartesian_offset_and_return",
            ),
        ),
        (
            "unitree-go2-stock-12dof",
            (
                "track_planar_twist",
                "move_body_relative_pose",
                "trace_planar_path",
                "set_body_height",
                "hold_stable_stance",
            ),
        ),
    ],
)
def test_fixed_reference_source_has_only_the_typed_capability_boundary(
    robot_id: str,
    expected_methods: tuple[str, ...],
) -> None:
    path = ROBOT_ROOT / robot_id / "1.0.0/reference/fixed_capability_driver.py"
    source = path.read_text(encoding="utf-8")

    audit = audit_driver_source(
        source,
        condition="skeleton-assisted",
        capability_methods=expected_methods,
    )

    assert audit.imports_trusted_skeleton is True
    for forbidden in (
        "fixture_task",
        "task_parameters",
        "mw_pick_place",
        "mw_push_to_goal",
        "GO2-T",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("robot_id", "scene_name", "responses", "minimum_steps"),
    [
        (
            "robotstudio_so101",
            "reach_scene.xml",
            [
                _leaf(
                    "set_gripper_opening",
                    {"opening_fraction": 0.2, "max_duration_s": 1.0},
                ),
                _leaf(
                    "move_end_effector_to_position",
                    {
                        "target_position_m": [0.4, 0.1, 0.2],
                        "max_duration_s": 4.0,
                    },
                ),
                _leaf(
                    "set_gripper_opening",
                    {"opening_fraction": 0.8, "max_duration_s": 1.0},
                ),
                {"reasoning_summary": "Canary complete.", "subtasks": []},
            ],
            500,
        ),
        (
            "unitree-go2-stock-12dof",
            "go2_scene.xml",
            [
                _leaf(
                    "set_body_height",
                    {"target_height_m": 0.28, "max_duration_s": 0.5},
                ),
                _leaf(
                    "move_body_relative_pose",
                    {
                        "translation_initial_yaw_m": [0.08, 0.0],
                        "yaw_delta_rad": 0.0,
                        "max_duration_s": 4.0,
                    },
                ),
                _leaf("hold_stable_stance", {"duration_s": 1.0}),
                {"reasoning_summary": "Canary complete.", "subtasks": []},
            ],
            1_000,
        ),
    ],
)
def test_fixed_reference_reuses_one_real_session_across_three_capability_calls(
    robot_id: str,
    scene_name: str,
    responses: list[dict[str, Any]],
    minimum_steps: int,
) -> None:
    pytest.importorskip("mujoco")
    package_root = ROBOT_ROOT / robot_id / "1.0.0"
    model = _ScriptedModel(responses)

    result = run_recap_worker_session(
        config=RecapWorkerSessionConfig(
            driver_path=package_root / "reference/fixed_capability_driver.py",
            scene_path=package_root / "assets" / scene_name,
            robot_configuration_id=robot_id,
            max_steps=5_000,
            max_sim_time_s=10.0,
            wall_timeout_s=30.0,
        ),
        capability_design=_design(robot_id),
        public_task={
            "task_template_id": "b2-persistent-reference-canary",
            "objective": "Exercise three typed leaves in one physical session.",
        },
        model=model,
    )

    controller = result["controller"]
    worker = result["worker"]
    assert controller["status"] == "CONTROLLER_FINISHED"
    assert controller["model_calls"] == 4
    assert controller["capability_calls"] == 3
    assert worker["worker_completed"] is True
    assert worker["controller_protocol_completed"] is True
    assert worker["successful_method_invocations"] == 3
    assert worker["capability_errors"] == []
    assert worker["physical_evidence"]["step_count"] >= minimum_steps
    assert len(model.calls) == 4


def test_so101_a3_cases_cross_half_travel_in_both_directions() -> None:
    mujoco = pytest.importorskip("mujoco")
    robot_id = "robotstudio_so101"
    package_root = ROBOT_ROOT / robot_id / "1.0.0"
    module = _load_fixed_driver(robot_id)
    cases = [
        case for case in _suite(robot_id)["cases"] if case["capability_id"] == "A3"
    ]

    for case in cases:
        model = mujoco.MjModel.from_xml_path(
            str(package_root / case["scene_entrypoint"])
        )
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, case["reset"])
        driver = module.build(model=model, data=data)
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "gripper")
        qpos_address = int(model.jnt_qposadr[joint_id])

        def opening() -> float:
            return (float(data.qpos[qpos_address]) + 0.17453) / (
                1.74533 + 0.17453
            )

        positions = [opening()]
        duration = case["request"]["max_duration_s"]
        first_extreme, second_extreme = (
            (0.0, 1.0) if positions[0] >= 0.5 else (1.0, 0.0)
        )
        expected_sequence = [
            {"opening_fraction": first_extreme, "max_duration_s": duration},
            {"opening_fraction": second_extreme, "max_duration_s": duration},
            case["request"],
        ]
        sequence = case.get("validation_request_sequence", expected_sequence)
        assert sequence == expected_sequence
        for request in sequence:
            start_time = float(data.time)
            driver.set_gripper_opening(request=request)
            assert float(data.time) - start_time <= request["max_duration_s"] + 1.0e-9
            positions.append(opening())

        signed_excursions = [
            positions[index + 1] - positions[index]
            for index in range(len(positions) - 1)
        ]
        assert max(signed_excursions) >= 0.50
        assert min(signed_excursions) <= -0.50
        assert abs(positions[-1] - case["request"]["opening_fraction"]) <= 0.10


def test_so101_contact_detector_matches_the_fixed_distal_geom_whitelist() -> None:
    mujoco = pytest.importorskip("mujoco")
    robot_id = "robotstudio_so101"
    package_root = ROBOT_ROOT / robot_id / "1.0.0"
    model = mujoco.MjModel.from_xml_path(
        str(package_root / "assets/button_front_scene.xml")
    )
    data = mujoco.MjData(model)
    driver = _load_fixed_driver(robot_id).build(model=model, data=data)
    symbols = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
        or f"geom_{geom_id}"
        for geom_id in driver._tool_geoms
    }
    a4_case = next(
        case for case in _suite(robot_id)["cases"] if case["case_id"] == "A4-H1"
    )

    assert symbols == set(a4_case["binding"]["parameters"]["tool_geom_names"])
    assert "geom_37" not in symbols

    for scene_name in (
        "push_to_goal_scene.xml",
        "sweep_into_goal_scene.xml",
        "pick_place_scene.xml",
        "pick_place_wall_scene.xml",
        "bin_picking_scene.xml",
    ):
        task_model = mujoco.MjModel.from_xml_path(
            str(package_root / "assets" / scene_name)
        )
        task_data = mujoco.MjData(task_model)
        task_driver = _load_fixed_driver(robot_id).build(
            model=task_model, data=task_data
        )
        moving_meshes = {
            geom_id
            for geom_id in range(int(task_model.ngeom))
            if mujoco.mj_id2name(
                task_model,
                mujoco.mjtObj.mjOBJ_GEOM,
                geom_id,
            )
            is None
            and mujoco.mj_id2name(
                task_model,
                mujoco.mjtObj.mjOBJ_BODY,
                int(task_model.geom_bodyid[geom_id]),
            )
            == "moving_jaw_so101_v1"
            and int(task_model.geom_group[geom_id]) == 4
            and int(task_model.geom_contype[geom_id]) != 0
        }
        fixed_meshes = {
            geom_id
            for geom_id in range(int(task_model.ngeom))
            if mujoco.mj_id2name(
                task_model,
                mujoco.mjtObj.mjOBJ_GEOM,
                geom_id,
            )
            is None
            and mujoco.mj_id2name(
                task_model,
                mujoco.mjtObj.mjOBJ_BODY,
                int(task_model.geom_bodyid[geom_id]),
            )
            == "gripper"
            and int(task_model.geom_group[geom_id]) == 4
            and int(task_model.geom_contype[geom_id]) != 0
        }
        assert moving_meshes <= task_driver._tool_geoms
        assert task_driver._tool_geoms.isdisjoint(fixed_meshes)


def test_legal_short_duration_requests_never_overrun_their_sim_budget() -> None:
    mujoco = pytest.importorskip("mujoco")

    so_robot = "robotstudio_so101"
    so_root = ROBOT_ROOT / so_robot / "1.0.0"
    so_case = next(
        case for case in _suite(so_robot)["cases"] if case["case_id"] == "A4-H3"
    )
    so_model = mujoco.MjModel.from_xml_path(
        str(so_root / so_case["scene_entrypoint"])
    )
    so_data = mujoco.MjData(so_model)
    apply_framework_reset(mujoco, so_model, so_data, so_case["reset"])
    so_driver = _load_fixed_driver(so_robot).build(model=so_model, data=so_data)
    so_request = dict(so_case["request"])
    so_request["max_duration_s"] = 0.25
    so_start = float(so_data.time)
    so_driver.approach_until_contact(request=so_request)
    assert float(so_data.time) - so_start <= 0.25 + 1.0e-9

    go_robot = "unitree-go2-stock-12dof"
    go_root = ROBOT_ROOT / go_robot / "1.0.0"
    go_module = _load_fixed_driver(go_robot)
    for method_name, request in (
        (
            "move_body_relative_pose",
            {
                "translation_initial_yaw_m": [0.05, 0.0],
                "yaw_delta_rad": 0.0,
                "max_duration_s": 0.25,
            },
        ),
        (
            "trace_planar_path",
            {
                "waypoints_initial_yaw_m": [[0.0, 0.0], [0.05, 0.0]],
                "max_duration_s": 0.25,
            },
        ),
    ):
        model = mujoco.MjModel.from_xml_path(str(go_root / "assets/go2_scene.xml"))
        data = mujoco.MjData(model)
        home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(model, data, home_id)
        mujoco.mj_forward(model, data)
        driver = go_module.build(model=model, data=data)
        start_time = float(data.time)
        getattr(driver, method_name)(request=request)
        assert float(data.time) - start_time <= 0.25 + 1.0e-9
