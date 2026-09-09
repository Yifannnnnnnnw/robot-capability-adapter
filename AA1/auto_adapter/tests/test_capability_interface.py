"""Focused generated-subclass and shared request-ABI checks (named fixtures)."""
import numpy as np
import pytest

from auto_adapter import robot_catalog
from auto_adapter.agent.task_planner import capability_tool_registry
from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


@pytest.fixture
def contract(monkeypatch):
    design = {"capabilities": [{
        "method_name": "move_end_effector_to_position", "description": "fixture reach",
        "request_schema": {"type": "object", "properties": {"target": {"type": "number"}}},
    }]}
    monkeypatch.setattr(robot_catalog, "load_capability_design", lambda robot: design)
    return {"id": "fixture", "class": "arm", "capability_skeleton": "ArmSerialDLSSkeleton"}


def test_generated_subclass_tool_advances_its_real_world(tmp_path, contract):
    scene = tmp_path / "fixture.xml"
    scene.write_text('''<mujoco><worldbody><body><joint name="j" type="slide"/>
        <geom type="sphere" size=".03" mass="1"/><site name="ee"/></body></worldbody>
        <actuator><position name="a" joint="j" kp="100"/></actuator></mujoco>''')

    class GeneratedFixture(ArmSerialDLSSkeleton):
        def move_end_effector_to_position(self, request):
            self.data.ctrl[0] = request["target"]
            self.step(10)

    driver = GeneratedFixture.from_mjcf(str(scene), ArmSpec(
        ee_site_name="ee", arm_joint_names=["j"], arm_actuator_names=["a"],
        joint_limits={"j": (-1., 1.)}))
    tool = capability_tool_registry(driver, contract)["move_end_effector_to_position"]
    request = {"target": .1}
    before = driver.data.qpos.copy()
    driver.move_end_effector_to_position(*tool["args"](request), **tool["kwargs"](request))
    assert driver.data.time > 0
    assert not np.array_equal(driver.data.qpos, before)
    assert "move_cartesian" not in capability_tool_registry(driver, contract)


def test_spec_only_and_missing_method_are_rejected(contract):
    with pytest.raises(ValueError, match="Spec-only"):
        robot_catalog.validate_capability_driver(ArmSerialDLSSkeleton.__new__(ArmSerialDLSSkeleton), contract)

    class IncompleteFixture(ArmSerialDLSSkeleton):
        pass

    with pytest.raises(ValueError, match="missing required capabilities"):
        robot_catalog.validate_capability_driver(IncompleteFixture.__new__(IncompleteFixture), contract)


def test_candidate_name_cannot_select_its_validator(contract):
    Fake = type("ArmSerialDLSSkeleton", (), {
        "move_end_effector_to_position": lambda self, request: True,
        "capability_skeleton": "ArmSerialDLSSkeleton",
    })
    with pytest.raises(ValueError, match="catalog requires"):
        robot_catalog.validate_capability_driver(Fake(), contract)
