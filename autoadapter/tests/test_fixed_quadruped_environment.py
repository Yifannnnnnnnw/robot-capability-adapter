"""Real MuJoCo checks for the three diagnosed foot-contact/reset defects."""
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.pipeline import PipelineHooks, _load_fixed_inputs

ROOT = Path(__file__).resolve().parents[1]
ROBOTS = ['unitree_a1', 'google_barkour_vb', 'anybotics_anymal_c']


@pytest.mark.parametrize('robot', ROBOTS)
def test_fixed_quadruped_contact_and_independent_settled_reset(robot):
    package = load_indexed_robot_package(ROOT, robot, require_task_library=False)
    _load_fixed_inputs(ROOT / 'references/fixed_family_v1', packages={robot: package},
                       hooks=PipelineHooks(), require_task_support=False)
    original = mujoco.MjModel.from_xml_path(str(package.root / 'assets/scene.xml'))
    model = mujoco.MjModel.from_xml_path(str(package.mjcf_path))
    binding = json.loads((ROOT / 'references/fixed_family_v1' / robot / 'robot_bindings.json').read_text())
    feet = binding['foot_geom_ids']
    np.testing.assert_array_equal(model.key_qpos[model.key('home').id], original.key_qpos[original.key('home').id])
    np.testing.assert_array_equal(model.key_ctrl[model.key('home').id], original.key_ctrl[original.key('home').id])
    for attribute in ['body_mass', 'body_inertia', 'actuator_gainprm', 'actuator_biasprm',
                      'actuator_ctrlrange', 'geom_friction', 'geom_solref']:
        np.testing.assert_array_equal(getattr(model, attribute), getattr(original, attribute))
    assert np.flatnonzero(np.any(model.geom_solimp != original.geom_solimp, axis=1)).tolist() == sorted(feet)
    np.testing.assert_array_equal(model.geom_solimp[feet], np.tile([.9, .95, .001, .5, 2], (4, 1)))

    # Reproduce the old preparation's deep dynamic contact using the original home.
    old_data = mujoco.MjData(original)
    mujoco.mj_resetDataKeyframe(original, old_data, original.key('home').id)
    old_min_contact = 0.
    for _ in range(round(2 / original.opt.timestep)):
        mujoco.mj_step(original, old_data)
        old_min_contact = min(old_min_contact, min((float(c.dist) for c in old_data.contact), default=0.))
    assert old_min_contact < -.005

    instances = json.loads((package.root / 'capability_validation/private/instances.json').read_text())['instances']
    assert all(i['scene_entrypoint'] == package.morphology['mjcf_entrypoint'] for i in instances)
    data = mujoco.MjData(model)
    ordinary = next(i for i in instances if i['capability_id'] == 'G1')
    apply_framework_reset(mujoco, model, data, ordinary['reset'])
    reset_pose = data.qpos.copy()
    height = float(data.xpos[model.body(binding['base']).id, 2])
    assert height == pytest.approx(binding['home_height_m'])
    assert min((float(c.dist) for c in data.contact), default=0.) >= -.005
    min_contact = 0.
    for _ in range(round(2 / model.opt.timestep)):
        mujoco.mj_step(model, data)
        min_contact = min(min_contact, min((float(c.dist) for c in data.contact), default=0.))
    assert np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()
    assert min_contact >= -.005
    assert abs(float(data.xpos[model.body(binding['base']).id, 2]) - height) < .01
    assert np.linalg.norm(data.qpos[:2] - reset_pose[:2]) < .02
    assert np.max(np.abs(data.actuator_force)) > 1e-6

    inverse = reset_pose[3:7].copy()
    inverse[1:] *= -1
    for instance in (i for i in instances if i['capability_id'] == 'G5'):
        apply_framework_reset(mujoco, model, data, instance['reset'])
        relative = np.zeros(4)
        mujoco.mju_mulQuat(relative, data.qpos[3:7], inverse)
        angle = 5 if instance['case_role'] == 'nominal' else 8
        assert np.degrees(2 * np.arctan2(relative[1], relative[0])) == pytest.approx(angle)
        floor = model.geom('floor').id
        assert min(mujoco.mj_geomDistance(model, data, floor, foot, 1., None) for foot in feet) == pytest.approx(.0005, abs=1e-8)
        assert min((float(c.dist) for c in data.contact), default=0.) >= -.005
