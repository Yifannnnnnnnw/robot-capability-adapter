"""build() wrapper so the harness can load the from-scratch Go2 Robot.
Uses the absolute scene path so MuJoCo resolves the scene's <include> files
relative to assets/mjcf/go2/ rather than the workspace cwd."""
import os, mujoco
from driver_from_scratch import Robot

_SCENE = os.path.realpath("mjcf.xml")  # resolves symlink → assets/mjcf/go2/go2_scene.xml

def build():
    m = mujoco.MjModel.from_xml_path(_SCENE)
    d = mujoco.MjData(m)
    return Robot(m, d, _SCENE)
