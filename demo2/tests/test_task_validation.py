from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from demo2.precision_policy import policy_record
from demo2.task_validation import (
    _SUPPORTED_INVOCATIONS,
    _SUPPORTED_MEASUREMENTS,
    task_repair_feedback,
    validate_task_driver,
)


SCENE = """
<mujoco model="task-validator-test">
  <option timestep="0.01" gravity="0 0 0"/>
  <worldbody>
    <geom name="floor" type="plane" size="1 1 0.01"/>
    <body name="target" pos="0.2 0 0.1">
      <geom name="target_geom" type="sphere" size="0.01" contype="0" conaffinity="0"/>
    </body>
    <body name="tool" pos="0 0 0.1">
      <joint name="tool_slide" type="slide" axis="1 0 0" range="-1 1"/>
      <geom name="tool_geom" type="sphere" size="0.01" contype="0" conaffinity="0"/>
      <site name="ee" size="0.005"/>
    </body>
  </worldbody>
</mujoco>
""".strip()


DRIVER = """
import mujoco


class Robot:
    def __init__(self, model, data):
        self.model = model
        self.data = data
        self._mj = mujoco

    def step(self, n=1):
        for _ in range(int(n)):
            mujoco.mj_step(self.model, self.data)

    def reach_above_object(self, target_position, duration=1.0):
        joint = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_JOINT, "tool_slide"
        )
        self.data.qpos[self.model.jnt_qposadr[joint]] = float(target_position[0])
        mujoco.mj_forward(self.model, self.data)
        self.step(1)
        return {"self_reported_success": False}

    def reject_unreachable_and_return_home(self, target_position, duration=1.0):
        self.step(1)
        return {"outcome": "rejected", "reason": "unreachable_target"}


def build(*, mjcf_path):
    model = mujoco.MjModel.from_xml_path(mjcf_path)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return Robot(model, data)
""".strip()


def _suite() -> dict:
    return {
        "artifact_type": "demo2_direct_validation_suite",
        "schema_version": "2.0.0",
        "robot_configuration_id": "test-robot",
        "precision_policy": policy_record(),
        "requirement_tests": [
            {
                "requirement_id": "req-1",
                "task_id": "T01",
                "capability_id": "cap-reach",
                "effect": "reach_above_object",
                "method": "reach_above_object",
                "case_id": "case-1",
                "scene_entrypoint": "test_robot/scene.xml",
                "reset": {"mode": "source_scene_default", "seed": 1},
                "parameters": {
                    "target_object_id": "target",
                    "target_offset_m": [0.0, 0.0, 0.0],
                },
                "invocation": {
                    "operator": "reach_above_object",
                    "effect": "reach_above_object",
                    "duration_s": 0.01,
                },
                "criteria": [
                    {
                        "criterion_id": "T01:full:target_error_m",
                        "metric": "target_error_m",
                        "comparator": "<=",
                        "threshold": 1e-6,
                        "phase": "full",
                        "dwell_s": 0.5,
                        "measurement": {
                            "metric": "target_error_m",
                            "status": "RESOLVED",
                            "operator": "distance",
                            "observation_path": "sites.ee.position",
                            "reference": {
                                "observation_path": "bodies.target.position"
                            },
                            "reference_offset_path": "parameters.target_offset_m",
                        },
                    }
                ],
                "guards": [
                    {
                        "guard_id": "trusted-external-verdict",
                        "rule": "Use external state.",
                    },
                    {
                        "guard_id": "finite-required-state",
                        "rule": "State must be finite.",
                    },
                    {
                        "guard_id": "scene-entrypoint-bound",
                        "rule": "Use the declared scene.",
                    },
                ],
            }
        ],
    }


def _rejection_suite() -> dict:
    suite = _suite()
    test = suite["requirement_tests"][0]
    test.update({
        "effect": "reject_unreachable_and_return_home",
        "method": "reject_unreachable_and_return_home",
        "parameters": {"target_position_m": [3.0, 3.0, 3.0]},
        "invocation": {
            "operator": "reject_unreachable_and_return_home",
            "effect": "reject_unreachable_and_return_home",
            "duration_s": 0.01,
        },
        "criteria": [
            {
                "criterion_id": "T01:full:target_rejected",
                "metric": "target_rejected",
                "comparator": "==",
                "threshold": True,
                "phase": "full",
                "dwell_s": 0.0,
                "measurement": {
                    "metric": "target_rejected",
                    "status": "RESOLVED",
                    "operator": "candidate_outcome",
                    "result_path": "outcome",
                    "expected": "rejected",
                    "reason_path": "reason",
                    "expected_reason": "unreachable_target",
                },
            },
            {
                "criterion_id": "T01:full:attempt_count",
                "metric": "attempt_count",
                "comparator": "==",
                "threshold": 1,
                "phase": "full",
                "dwell_s": 0.0,
                "measurement": {
                    "metric": "attempt_count",
                    "status": "RESOLVED",
                    "operator": "invocation_count",
                },
            },
        ],
    })
    return suite


@pytest.fixture
def direct_case(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    asset_root = tmp_path / "assets"
    workspace.mkdir()
    scene_dir = asset_root / "test_robot"
    scene_dir.mkdir(parents=True)
    (workspace / "driver.py").write_text(DRIVER + "\n", encoding="utf-8")
    (scene_dir / "scene.xml").write_text(SCENE + "\n", encoding="utf-8")
    return workspace, asset_root


def test_direct_mujoco_requirement_pass_ignores_driver_self_report(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    report = validate_task_driver(
        workspace,
        _suite(),
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        expected_capability_ids=["cap-reach"],
        record_video=False,
    )

    assert report["all_ok"] is True
    assert report["structural_ok"] is True
    assert report["exact_requirement_coverage"] is True
    criterion = report["requirements"][0]["criteria"][0]
    assert criterion["actual"] == pytest.approx(0.0, abs=1e-9)
    assert criterion["comparator"] == "<="
    assert criterion["threshold"] == 1e-6
    assert report["requirements"][0]["trace_samples"] >= 3


def test_candidate_outcome_uses_mapping_but_invocation_count_is_harness_owned(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    report = validate_task_driver(
        workspace,
        _rejection_suite(),
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )

    assert report["all_ok"] is True
    rows = report["requirements"][0]["criteria"]
    assert [(row["metric"], row["actual"]) for row in rows] == [
        ("target_rejected", True),
        ("attempt_count", 1),
    ]


@pytest.mark.parametrize("variant", ["missing", "duplicate", "unknown"])
def test_requirement_coverage_is_exact_and_fail_closed(
    direct_case: tuple[Path, Path], variant: str
) -> None:
    workspace, asset_root = direct_case
    suite = _suite()
    if variant == "missing":
        suite["requirement_tests"] = []
    elif variant == "duplicate":
        suite["requirement_tests"].append(copy.deepcopy(suite["requirement_tests"][0]))
    else:
        suite["requirement_tests"][0]["requirement_id"] = "req-unknown"

    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        expected_capability_ids=["cap-reach"],
        record_video=False,
    )

    assert report["all_ok"] is False
    assert report["structural_ok"] is False
    assert report["exact_requirement_coverage"] is False
    assert report["suite_diagnostics"]


def test_unknown_measurement_fails_with_repairable_criterion_evidence(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    suite = _suite()
    suite["requirement_tests"][0]["criteria"][0]["measurement"]["operator"] = "model_says_ok"

    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )
    feedback = task_repair_feedback(report)

    assert report["all_ok"] is False
    assert report["structural_ok"] is False
    assert report["requirements"][0]["criteria"][0]["actual"] is None
    assert "unsupported measurement operator" in feedback
    assert "actual=None" in feedback
    assert "comparator=<=" in feedback
    assert "threshold=1e-06" in feedback


@pytest.mark.parametrize("mutation", ["threshold", "dwell", "policy"])
def test_precision_policy_tampering_is_rejected_before_driver_load(
    direct_case: tuple[Path, Path], mutation: str
) -> None:
    workspace, asset_root = direct_case
    suite = _suite()
    if mutation == "threshold":
        suite["requirement_tests"][0]["criteria"][0]["threshold"] = 0.011
    elif mutation == "dwell":
        suite["requirement_tests"][0]["criteria"][0]["dwell_s"] = 0.49
    else:
        suite["precision_policy"]["maximum_physical_position_error_m"] = 0.04

    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )

    assert report["all_ok"] is False
    assert report["structural_ok"] is False
    assert report["driver_load_error"] is None
    assert report["requirements"] == []
    assert "precision_policy" in " ".join(report["suite_diagnostics"])


def test_unknown_method_and_scene_escape_fail_closed(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    suite = _suite()
    suite["requirement_tests"][0]["method"] = "pretend_success"
    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )
    assert report["all_ok"] is False
    requirement = report["requirements"][0]
    assert "binding is inconsistent" in requirement["error"]
    assert requirement["effect"] == "reach_above_object"
    assert len(requirement["criteria"]) == 1
    assert requirement["criteria"][0]["actual"] is None
    assert requirement["criteria"][0]["ok"] is False
    assert "binding is inconsistent" in requirement["criteria"][0]["error"]
    assert len(requirement["guards"]) == 3

    suite = _suite()
    suite["requirement_tests"][0]["scene_entrypoint"] = "../scene.xml"
    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )
    assert report["all_ok"] is False
    assert "safe relative path" in report["requirements"][0]["error"]


def test_unknown_capability_is_rejected_before_driver_load(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    suite = _suite()
    suite["requirement_tests"][0]["capability_id"] = "cap-unknown"
    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        expected_capability_ids=["cap-reach"],
        record_video=False,
    )

    assert report["all_ok"] is False
    assert report["driver_load_error"] is None
    assert report["requirements"] == []
    assert "unknown capability" in " ".join(report["suite_diagnostics"])


def test_driver_load_failure_still_reports_every_declared_criterion(
    direct_case: tuple[Path, Path],
) -> None:
    workspace, asset_root = direct_case
    (workspace / "driver.py").write_text("raise RuntimeError('load boom')\n", encoding="utf-8")
    suite = _suite()
    second = copy.deepcopy(suite["requirement_tests"][0]["criteria"][0])
    second["criterion_id"] = "T01:full:second"
    second["metric"] = "second"
    second["measurement"]["metric"] = "second"
    suite["requirement_tests"][0]["criteria"].append(second)

    report = validate_task_driver(
        workspace,
        suite,
        asset_root=asset_root,
        expected_requirement_ids=["req-1"],
        record_video=False,
    )

    assert report["all_ok"] is False
    assert report["exact_requirement_coverage"] is True
    assert len(report["requirements"]) == 1
    assert len(report["requirements"][0]["criteria"]) == 2
    assert all(item["actual"] is None and item["ok"] is False for item in report["requirements"][0]["criteria"])
    assert all("load boom" in item["error"] for item in report["requirements"][0]["criteria"])


def test_all_three_current_direct_adapters_use_supported_operators() -> None:
    task_root = Path(__file__).resolve().parents[1] / "libraries" / "tasks"
    configurations = (
        "robotstudio_so101",
        "franka_panda",
        "unitree-go2-stock-12dof",
    )
    observed_invocations: set[str] = set()
    observed_measurements: set[str] = set()
    for configuration in configurations:
        adapter = json.loads(
            (task_root / configuration / "1.0.0" / "direct_mujoco_adapter.json").read_text(
                encoding="utf-8"
            )
        )
        for task in adapter["tasks"]:
            invocation = task["invocation"]
            observed_invocations.add(invocation["operator"])
            assert _SUPPORTED_INVOCATIONS[invocation["operator"]] == invocation["effect"]
            for measurement in task["measurements"]:
                observed_measurements.add(measurement["operator"])
                assert measurement["operator"] in _SUPPORTED_MEASUREMENTS

    assert observed_invocations <= set(_SUPPORTED_INVOCATIONS)
    assert observed_measurements <= set(_SUPPORTED_MEASUREMENTS)
