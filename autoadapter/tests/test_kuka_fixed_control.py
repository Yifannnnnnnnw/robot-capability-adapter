"""Real MuJoCo regression for the observed KUKA gravity-induced TCP error."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "libraries/robots/kuka_iiwa_14/1.0.0"
SUITE = json.loads(
    (ROOT / "references/fixed_family_v1/kuka_iiwa_14/capability_validation_suite.json").read_text()
)
A1_CASES = [case for case in SUITE["cases"] if case["capability_id"] == "A1"]


@pytest.mark.parametrize("case", A1_CASES, ids=lambda case: case["case_role"])
def test_kuka_fixed_reference_reaches_existing_a1_targets(case: dict) -> None:
    mujoco = pytest.importorskip("mujoco")
    spec = importlib.util.spec_from_file_location(
        "kuka_fixed_reference", PACKAGE / "reference/fixed_family_driver.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = mujoco.MjModel.from_xml_path(str(PACKAGE / "assets/fixed_scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)
    driver = module.build(model, data)

    driver.move_end_effector_to_position(case["request"])

    target = np.asarray(case["request"]["target_position_m"])
    error_m = float(np.linalg.norm(data.site_xpos[model.site("attachment_site").id] - target))
    assert error_m <= 0.015, error_m
    assert data.time >= case["request"]["max_duration_s"]
    assert np.all(data.ctrl >= model.actuator_ctrlrange[:, 0])
    assert np.all(data.ctrl <= model.actuator_ctrlrange[:, 1])
