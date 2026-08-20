from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
XARM_ASSETS_ROOT = ROOT / "libraries" / "robots" / "ufactory_xarm7" / "1.0.0" / "assets"
PIPER_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "piper" / "1.0.0"
PIPER_ASSETS_ROOT = PIPER_PACKAGE_ROOT / "assets"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"

SCENES = {
    "reach_scene.xml": {
        "dimensions": (8, 8, 7),
        "bodies": ("reach_goal",),
        "geoms": (),
        "cameras": (),
        "sites": (),
    },
    "push_to_goal_scene.xml": {
        "dimensions": (15, 14, 7),
        "bodies": ("workpiece", "push_goal", "evidence_target"),
        "geoms": ("work_surface",),
        "cameras": ("evidence",),
        "sites": (),
    },
}


def _has_name(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> bool:
    return mujoco.mj_name2id(model, object_type, name) >= 0


def _prescribed_transform(source_path: Path) -> str:
    return source_path.read_text(encoding="utf-8").replace(
        'model="xarm7_', 'model="piper_', 1
    ).replace(
        '<include file="xarm7.xml" />', '<include file="piper.xml" />', 1
    )


def test_piper_scenes_are_exact_structural_transforms_of_xarm_sources() -> None:
    for filename in SCENES:
        source_path = XARM_ASSETS_ROOT / filename
        scene_path = PIPER_ASSETS_ROOT / filename
        source_text = source_path.read_text(encoding="utf-8")
        scene_text = scene_path.read_text(encoding="utf-8")

        assert scene_text == _prescribed_transform(source_path)
        root = ET.fromstring(scene_text)
        assert root.get("model") == f"piper_{filename.removesuffix('_scene.xml')}"
        includes = root.findall("include")
        assert len(includes) == 1
        assert includes[0].get("file") == "piper.xml"
        assert "xarm7" not in scene_text.lower()
        assert source_text != scene_text


def test_piper_scenes_load_step_and_expose_structural_contract() -> None:
    assert mujoco.__version__ == "3.3.6"

    for filename, expected in SCENES.items():
        scene_path = PIPER_ASSETS_ROOT / filename
        root = ET.parse(scene_path).getroot()
        global_visual = root.find("visual/global")
        assert global_visual is not None
        assert int(global_visual.get("offwidth", "0")) >= 800
        assert int(global_visual.get("offheight", "0")) >= 600

        model = mujoco.MjModel.from_xml_path(str(scene_path))
        assert (model.nq, model.nv, model.nu) == expected["dimensions"]
        assert model.vis.global_.offwidth >= 800
        assert model.vis.global_.offheight >= 600

        base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        assert base_id >= 0
        np.testing.assert_allclose(model.body_pos[base_id], [0.0, 0.0, 0.0])

        for name in expected["bodies"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_BODY, name)
        for name in expected["geoms"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        for name in expected["cameras"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        for name in expected["sites"]:
            assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, name)
        assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        if filename == "reach_scene.xml":
            assert _has_name(model, mujoco.mjtObj.mjOBJ_SITE, "reach_goal_site")

        data = mujoco.MjData(model)
        mujoco.mj_step(model, data)
        assert data.time > 0.0
        assert np.isfinite(data.qpos).all()
        assert np.isfinite(data.qvel).all()
        assert np.isfinite(data.ctrl).all()


def test_piper_task_scene_package_remains_non_runtime() -> None:
    runnable_index = json.loads(RUNNABLE_INDEX_PATH.read_text(encoding="utf-8"))
    assert "piper" not in runnable_index["robots"]
    assert not (PIPER_PACKAGE_ROOT / "tasks" / "private").exists()
    assert not (PIPER_PACKAGE_ROOT / "skeleton").exists()
    assert not (PIPER_PACKAGE_ROOT / "reference").exists()
