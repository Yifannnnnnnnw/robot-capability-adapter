# Reference positive control, NOT model generated.
from auto_adapter.tests.test_hand_fingertip_dls import _spec,SCENE_PATH,HandFingertipDLSSkeleton
def build():
    return HandFingertipDLSSkeleton.from_mjcf(str(SCENE_PATH),_spec())
