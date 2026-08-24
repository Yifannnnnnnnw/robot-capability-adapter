"""Public Go2 skeleton inventory for the Demo3 package.

The generation input includes the implementation source referenced here, and
the executable import resolves to the same trusted Framework class.
"""

from autoadapter2.trusted_skeletons.quadruped_pd_gait import (
    QuadrupedPDGaitSkeleton,
    QuadrupedSpec,
)

__all__ = ["QuadrupedPDGaitSkeleton", "QuadrupedSpec"]
