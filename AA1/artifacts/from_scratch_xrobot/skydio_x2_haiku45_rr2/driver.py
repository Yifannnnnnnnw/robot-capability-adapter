import os
from driver_from_scratch import Robot  # noqa: F401
_SCENE = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mjcf.xml'))
def build():
    return Robot.build_from_mjcf(_SCENE)
