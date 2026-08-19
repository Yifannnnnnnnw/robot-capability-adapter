# AutoAdapter 2.0 SDK Extension

This directory preserves the real-SDK and Translation experiments independently
from the canonical Direct-MuJoCo mainline in `../../autoadapter/`.

The Python distribution is `autoadapter2-sdk` and its import namespace is
`autoadapter2_sdk`, so installing it cannot replace or shadow the mainline
`autoadapter2` package.

The extension contains the reusable transport boundary:

- SO-ARM101: LeRobot-compatible Feetech Protocol 0 over PTY to MuJoCo.
- Unitree Go2: SDK2 DDS `LowCmd`/`LowState` translation to and from MuJoCo.

These modules contain mechanical translation and lifecycle behavior only. They
do not provide task, pose, trajectory, gait, planning, or recovery behavior.
The SO-ARM101 `route_check` exercises the complete bidirectional path once and
reports whether real or injected dependencies were used. Injected test routes
are always reported with `sdk_grounded: false`. Pinned Linux environments and
historical run evidence remain a separate migration batch and retain their
original evidence classifications.

On the pinned Linux environment, run the real SO-ARM101 route with:

```bash
python scripts/run_so_arm101_route_check.py \
  --model /opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml \
  --gripper-direction tick-increases-qpos
```

Run the pinned real Unitree Go2 route with:

```bash
python scripts/run_unitree_go2_route_check.py \
  --model /opt/unitree_mujoco/unitree_robots/go2/scene.xml
```

A successful real result shows only that this named SDK/Translation/MuJoCo
route executed. It does not establish hardware equivalence or sim-to-real.

Run the local focused checks with:

```bash
python -m pytest
```
