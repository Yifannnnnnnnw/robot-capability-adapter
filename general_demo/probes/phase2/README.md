# Phase 2 feasibility probe

Status: `SOURCE_EVIDENCE_ONLY` / `PROPOSED_SOURCE_EVIDENCE` / `NOT_ADMITTED`.

This directory is a small, dependency-free technical probe. It does not add a
formal registry entry, exact reference, gate receipt, frozen record, robot
shim, LLM call, Blue Line, Validation, or Demo implementation. The
`probe_manifest.json` file is metadata only and has no writer in this code.

The SO-ARM101 portion contains a pure Python Feetech Protocol 0 wire codec and
a virtual six-motor PTY peer. The peer implements only the STS3215 registers
and packet operations needed by the real LeRobot 0.6.0 follower path:
`ping`, `read`, `write`, sync write of `Goal_Position`, and sync read of
`Present_Position`. It is not an `scservo_sdk` replacement.

`real_lerobot.py` lazily imports the pinned real `SO101Follower`,
`FeetechMotorsBus`, and `scservo_sdk`; when the optional stack is available on
Linux, the integration test passes the opened, project-owned
`VirtualFeetechPTY` object to the runner. The runner rejects strings and
external serial paths, then connects the real classes to that PTY and performs
`connect(configure)`, `send_action`, and `get_observation`. On the current
macOS environment that test is skipped explicitly because this probe's real
PTY integration is Linux-only. Missing optional packages also cause a clear
skip, never a synthetic pass.

The Unitree SDK2Py and `unitree_mujoco` pins in the manifest are source
evidence only. No Go2 shim or simulator code is included.

Run the unit tests from `general_demo/` with:

```text
pytest -q
```
