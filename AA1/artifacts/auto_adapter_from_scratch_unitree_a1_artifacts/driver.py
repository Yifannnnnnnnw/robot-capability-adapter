"""build() wrapper so the harness can load the from-scratch quadruped Robot
(takes model, data). Absolute scene path so MuJoCo resolves <include> files."""
import os, mujoco
from driver_from_scratch import Robot

_SCENE = os.path.realpath("mjcf.xml")

def build():
    m = mujoco.MjModel.from_xml_path(_SCENE)
    d = mujoco.MjData(m)
    return Robot(m, d)
