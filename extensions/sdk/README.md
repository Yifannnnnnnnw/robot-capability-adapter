# AutoAdapter 2.0 SDK Extension

This directory preserves the real-SDK and Translation experiments independently
from the canonical Direct-MuJoCo mainline in `../../autoadapter/`.

The Python distribution is `autoadapter2-sdk` and its import namespace is
`autoadapter2_sdk`, so installing it cannot replace or shadow the mainline
`autoadapter2` package.

The first extraction batch contains only the reusable transport boundary:

- SO-ARM101: LeRobot-compatible Feetech Protocol 0 over PTY to MuJoCo.
- Unitree Go2: SDK2 DDS `LowCmd`/`LowState` translation to and from MuJoCo.

These modules contain mechanical translation and lifecycle behavior only. They
do not provide task, pose, trajectory, gait, planning, or recovery behavior.
Readiness runners, pinned Linux environments, and historical run evidence remain
a separate migration batch and retain their original evidence classifications.

Run the local focused checks with:

```bash
python -m pytest
```
