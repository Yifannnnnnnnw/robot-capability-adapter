"""
driver.py – LEAP Hand (right) dexterous-hand driver.

Robot:      LEAP Hand (right), 16-DOF, 4 fingers
Skeleton:   HandFingertipDLSSkeleton
Spec:       HandFingertipDLSSpec

Finger layout
  index  : if_mcp, if_rot, if_pip, if_dip   → fingertip geom if_tip  (body if_ds)
  middle : mf_mcp, mf_rot, mf_pip, mf_dip   → fingertip geom mf_tip  (body mf_ds)
  ring   : rf_mcp, rf_rot, rf_pip, rf_dip   → fingertip geom rf_tip  (body rf_ds)
  thumb  : th_cmc, th_axl, th_mcp, th_ipl   → fingertip geom th_tip  (body th_ds)

No gripper open/close – dexterous hand only.
"""

import os
from auto_adapter.skeletons.hand_fingertip_dls import (
    HandFingertipDLSSkeleton,
    HandFingertipDLSSpec,
)

# Resolve the MJCF path relative to this file so the driver works regardless
# of the current working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MJCF = os.path.join(_HERE, "mjcf.xml")


def build() -> HandFingertipDLSSkeleton:
    spec = HandFingertipDLSSpec(
        # ── structural names ──────────────────────────────────────────────
        palm_body_name="palm",

        joint_names=(
            "if_mcp", "if_rot", "if_pip", "if_dip",   # index finger
            "mf_mcp", "mf_rot", "mf_pip", "mf_dip",   # middle finger
            "rf_mcp", "rf_rot", "rf_pip", "rf_dip",   # ring finger
            "th_cmc", "th_axl", "th_mcp", "th_ipl",   # thumb
        ),

        actuator_names=(
            "if_mcp_act", "if_rot_act", "if_pip_act", "if_dip_act",
            "mf_mcp_act", "mf_rot_act", "mf_pip_act", "mf_dip_act",
            "rf_mcp_act", "rf_rot_act", "rf_pip_act", "rf_dip_act",
            "th_cmc_act", "th_axl_act", "th_mcp_act", "th_ipl_act",
        ),

        # ── fingertip geoms (one per finger, used for DLS IK) ─────────────
        fingertip_geom_names={
            "index":  "if_tip",
            "middle": "mf_tip",
            "ring":   "rf_tip",
            "thumb":  "th_tip",
        },

        # ── per-finger joint groupings (for selective finger control) ─────
        finger_joint_names={
            "index":  ("if_mcp", "if_rot", "if_pip", "if_dip"),
            "middle": ("mf_mcp", "mf_rot", "mf_pip", "mf_dip"),
            "ring":   ("rf_mcp", "rf_rot", "rf_pip", "rf_dip"),
            "thumb":  ("th_cmc", "th_axl", "th_mcp", "th_ipl"),
        },

        # ── home pose: model default (all zeros = natural rest) ───────────
        home_qpos=(
            0.0, 0.0, 0.0, 0.0,   # index
            0.0, 0.0, 0.0, 0.0,   # middle
            0.0, 0.0, 0.0, 0.0,   # ring
            0.0, 0.0, 0.0, 0.0,   # thumb
        ),

        # ── IK / control tuning (framework defaults are fine) ─────────────
        max_joint_command_delta=0.1,
        joint_tolerance_rad=0.03,
        fingertip_tolerance_m=0.003,
        ik_damping=0.02,
        ik_step_clamp=0.08,
        ik_position_gain=1.0,
    )

    return HandFingertipDLSSkeleton.from_mjcf(_MJCF, spec)


if __name__ == "__main__":
    skel = build()
    import pprint
    pprint.pprint(skel.describe())
