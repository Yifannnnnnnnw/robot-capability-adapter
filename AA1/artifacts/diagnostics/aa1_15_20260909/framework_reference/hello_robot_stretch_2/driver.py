# Reference positive control, NOT model generated.
from auto_adapter.skeletons import StretchMobileManipulationSkeleton,StretchMobileManipulationSpec
def build():
    return StretchMobileManipulationSkeleton.from_mjcf("mjcf.xml",StretchMobileManipulationSpec())
