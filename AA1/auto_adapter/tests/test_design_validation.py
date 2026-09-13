# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the fresh-process DESIGN validator."""

from __future__ import annotations

from pathlib import Path

from auto_adapter.design_validation import validate_design_driver


XML = """
<mujoco model="validator_fixture">
  <option timestep="0.01"/>
  <worldbody>
    <body name="arm">
      <joint name="hinge" type="hinge"/>
      <geom name="tool_geom" type="sphere" size="0.02" mass="1"/>
      <site name="tool_site" pos="0 0 0"/>
    </body>
  </worldbody>
</mujoco>
"""


def _design() -> dict:
    return {
        "robot_configuration_id": "fixture",
        "capabilities": [
            {
                "capability_id": "move_cap",
                "method_name": "move",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "number"},
                        }
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
                "criteria": [
                    {
                        "metric": "tool_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.01,
                        "temporal": {"kind": "terminal"},
                        "aggregation": {"kind": "last"},
                    }
                ],
            }
        ],
    }


def _suite(*, wall_timeout_s: float = 5.0) -> dict:
    return {
        "scenes": {"plain": {"objects": []}},
        "cases": [
            {
                "case_id": "move_case",
                "scene": "plain",
                "capability_id": "move_cap",
                "initial_state": {
                    "robot": {"qpos_by_joint": {}},
                    "free_bodies": {},
                    "ctrl_by_actuator": {},
                    "settle_s": 0.0,
                },
                "request": {"target": [0.0, 0.0, 0.0]},
                "execution": {
                    "max_sim_time_s": 0.05,
                    "wall_timeout_s": wall_timeout_s,
                    "video_width": 16,
                    "video_height": 16,
                },
                "measurements": [
                    {
                        "criterion_index": 0,
                        "operator": "site_position_error",
                        "bindings": {
                            "site": "tool_site",
                            "target_request_field": "target",
                        },
                    }
                ],
            }
        ],
    }


def _driver(path: Path, *, hang: bool = False) -> None:
    path.write_text(
        (
            "import numpy as np\n"
            "import mujoco\n"
            "\n"
            "# A named fixture renderer keeps this subprocess test portable. "
            "The production validator still requires mujoco.Renderer + imageio.\n"
            "class _FixtureRenderer:\n"
            "    def __init__(self, model, height, width):\n"
            "        self.height, self.width = height, width\n"
            "    def update_scene(self, data): pass\n"
            "    def render(self):\n"
            "        return np.zeros((self.height, self.width, 3), dtype=np.uint8)\n"
            "    def close(self): pass\n"
            "mujoco.Renderer = _FixtureRenderer\n"
            "import sys, types\n"
            "_imageio_v2 = types.ModuleType('imageio.v2')\n"
            "def _mimsave(path, frames, fps):\n"
            "    open(path, 'wb').write(b'fixture video')\n"
            "_imageio_v2.mimsave = _mimsave\n"
            "_imageio = types.ModuleType('imageio')\n"
            "_imageio.v2 = _imageio_v2\n"
            "sys.modules['imageio'] = _imageio\n"
            "sys.modules['imageio.v2'] = _imageio_v2\n"
            "\n"
            "class Robot:\n"
            "    def __init__(self, model, data):\n"
            "        self.model, self.data = model, data\n"
            "    def reset(self):\n"
            "        mujoco.mj_resetData(self.model, self.data)\n"
            "    def move(self, request):\n"
            + ("        while True: pass\n" if hang else "        mujoco.mj_step(self.model, self.data, 3)\n")
            + "\n"
            "def build():\n"
            "    model = mujoco.MjModel.from_xml_path('mjcf.xml')\n"
            "    return Robot(model, mujoco.MjData(model))\n"
        ),
        encoding="utf-8",
    )


def test_validator_uses_fresh_worker_and_scores_real_samples(tmp_path: Path) -> None:
    mjcf = tmp_path / "base.xml"
    mjcf.write_text(XML, encoding="utf-8")
    cases = tmp_path / "scene_cases.yaml"
    import yaml

    cases.write_text(yaml.safe_dump(_suite()), encoding="utf-8")
    driver = tmp_path / "driver.py"
    _driver(driver)

    first = validate_design_driver(
        driver_path=driver,
        design=_design(),
        scene_cases_path=cases,
        scene_paths={"plain": mjcf},
        output_dir=tmp_path / "validation",
    )
    second = validate_design_driver(
        driver_path=driver,
        design=_design(),
        scene_cases_path=cases,
        scene_paths={"plain": mjcf},
        output_dir=tmp_path / "validation",
    )

    assert first["all_ok"] is True
    assert first["tests"][0]["physics_steps"] == 3
    assert first["tests"][0]["metrics"]["measurements"][0]["ok"] is True
    assert Path(first["tests"][0]["video"]["path"]).is_file()
    assert Path(first["tests"][0]["paths"]["samples"]).is_file()
    assert (
        first["tests"][0]["paths"]["attempt_dir"]
        != second["tests"][0]["paths"]["attempt_dir"]
    )


def test_validator_wall_timeout_covers_hanging_capability(tmp_path: Path) -> None:
    mjcf = tmp_path / "base.xml"
    mjcf.write_text(XML, encoding="utf-8")
    cases = tmp_path / "scene_cases.yaml"
    import yaml

    cases.write_text(
        yaml.safe_dump(_suite(wall_timeout_s=0.2)),
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    _driver(driver, hang=True)

    report = validate_design_driver(
        driver_path=driver,
        design=_design(),
        scene_cases_path=cases,
        scene_paths={"plain": mjcf},
        output_dir=tmp_path / "validation",
    )

    assert report["all_ok"] is False
    assert report["tests"][0]["errors"] == ["wall timeout"]
    assert report["repairable"] is True


def test_validator_rejects_invalid_scene_mapping_before_worker(tmp_path: Path) -> None:
    mjcf = tmp_path / "base.xml"
    mjcf.write_text(XML, encoding="utf-8")
    cases = tmp_path / "scene_cases.yaml"
    import yaml

    cases.write_text(yaml.safe_dump(_suite()), encoding="utf-8")
    driver = tmp_path / "driver.py"
    _driver(driver)

    report = validate_design_driver(
        driver_path=driver,
        design=_design(),
        scene_cases_path=cases,
        scene_paths={"wrong_scene": mjcf},
        output_dir=tmp_path / "validation",
    )

    assert report["all_ok"] is False
    assert report["validation_error"] is True
    assert report["repairable"] is False
    assert report["tests"] == []
    assert "missing scene 'plain'" in report["error"]
