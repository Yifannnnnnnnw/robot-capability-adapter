#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble the anonymous supplementary.zip for the EMNLP submission.

Contents: 8 representative eval/demo videos (including one honest
failure), the 10 synthesized driver files, and the canonical results
file. Everything included must already be identity-clean; this script
copies, it does not scrub.

Output: supplementary.zip at the repo root (gitignored).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "autoadapter_bench" / "results" / "videos"
A = ROOT / "artifacts"

VIDEOS = [
    # (dest name, source, one-line provenance for the README)
    ("01_so101_swap_banana_bottle.mp4",
     V / "model_axis/so101/ours/sonnet46/swap_banana_bottle_t2.mp4",
     "SO-101 swap_banana_bottle, Auto-Adapter + Sonnet 4.6, graded trial t2 (pass); hard suite, Sec. 5.3 / Fig. 2a."),
    ("02_so101_draw_letter_L_selfdriver.mp4",
     V / "self_axis/so101/haiku45/draw_letter_L_t0.mp4",
     "SO-101 draw_letter_L on Haiku 4.5's SELF-written driver, graded trial t0; 'writes API' track, Sec. 5.3."),
    ("03_piper_reach_sequence.mp4",
     V / "robot_axis/piper/fixed_sonnet/driver_demo_reach_sequence.mp4",
     "Piper reach sequence on the synthesized driver; cross-robot simple suite, Sec. 5.5."),
    ("04_skydio_flight_showcase.mp4",
     V / "robot_axis/skydio/fixed_sonnet45/driver_demo_showcase.mp4",
     "Skydio X2 flight demo on the synthesized cascaded thrust controller; aerial suite, Appendix J."),
    ("05_anymal_walk_forward.mp4",
     V / "robot_axis/anymal/fixed_sonnet45/driver_demo_walk_forward_10cm.mp4",
     "ANYmal-C walk_forward_10cm on the shared synthesized driver; locomotion v2 suite, Sec. 5.6 / Fig. 5g."),
    ("06_h1_humanoid_showcase.mp4",
     V / "robot_axis/h1/fixed_sonnet45/driver_demo_showcase.mp4",
     "Unitree H1 stand/squat demo on the synthesized PD balance controller; Appendix J."),
    ("07_go2_walk_sit_stand.mp4",
     V / "robot_axis/go2/fixed_sonnet45/driver_demo_walk_sit_stand.mp4",
     "Go2 walk/sit/stand sequencing on the synthesized driver; Sec. 5.6."),
    ("08_FAILURE_so101_stack_lego_on_duck.mp4",
     None,  # resolved below: any ours/sonnet46 stack trial (all fail; Sec. 5.3 / App. I)
     "HONEST FAILURE: SO-101 stack_lego_on_duck (0/5 for every model); the released lego displaces the duck. Discussed in Sec. 5.3 footprint / Appendix I."),
]

DRIVERS = [
    ("so101", A / "auto_adapter_from_scratch_so101_artifacts/driver_from_scratch.py"),
    ("piper", A / "auto_adapter_from_scratch_piper_artifacts/driver_from_scratch.py"),
    ("ur5e", A / "auto_adapter_from_scratch_ur5e_artifacts/driver_from_scratch.py"),
    ("franka", A / "auto_adapter_from_scratch_franka_artifacts/driver_from_scratch.py"),
    ("kuka_iiwa14", A / "auto_adapter_from_scratch_kuka_iiwa14_artifacts/driver_from_scratch.py"),
    ("go2", A / "auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py"),
    ("unitree_a1", A / "auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py"),
    ("anymal_c", A / "auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py"),
    ("skydio_x2", A / "from_scratch_aerial_v2/skydio_x2/driver_from_scratch.py"),
    ("h1", A / "from_scratch_humanoid_v3/h1/driver_from_scratch.py"),
]


def main() -> int:
    # resolve the failure video
    fail_dir = V / "model_axis/so101/ours/sonnet46"
    fails = sorted(fail_dir.glob("stack_lego_on_duck_t*.mp4"))
    if fails:
        VIDEOS[-1] = (VIDEOS[-1][0], fails[0], VIDEOS[-1][2])

    readme = [
        "# Supplementary material (anonymous)",
        "",
        "Auto-Adapter: Physics-in-the-Loop Robot Driver Synthesis",
        "for Embodied LLM Agents",
        "",
        "## videos/",
        "Rendered MuJoCo rollouts. Each line gives provenance; graded",
        "trials come unedited from the benchmark's replay recordings.",
        "",
    ]
    out = ROOT / "supplementary.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dest, src, note in VIDEOS:
            if src is None or not src.exists():
                print(f"  SKIP {dest} (missing)")
                continue
            z.write(src, f"videos/{dest}")
            readme.append(f"- `{dest}`: {note}")
        readme += ["", "## drivers/",
                   "The ten agent-synthesized self-contained drivers (numpy +",
                   "mujoco only), exactly as exported by the pipeline.", ""]
        for rid, src in DRIVERS:
            if not src.exists():
                print(f"  SKIP driver {rid} (missing)")
                continue
            z.write(src, f"drivers/driver_{rid}.py")
            readme.append(f"- `driver_{rid}.py`")
        z.write(ROOT / "paper/results/canonical.yaml", "canonical_results.yaml")
        readme += ["", "## canonical_results.yaml",
                   "Single source of truth for every number in the paper."]
        z.writestr("README.md", "\n".join(readme) + "\n")
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
