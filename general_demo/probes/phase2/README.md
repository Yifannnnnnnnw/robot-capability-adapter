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

The Go2 portion is a small low-level route probe. It fixes the active motor
projection to `FR_hip/thigh/calf`, `FL`, `RR`, `RL` (12 entries), while keeping
the real SDK `LowCmd_`/`LowState_` container width of 20 slots. Only the first
12 slots are consumed by the pinned bridge. The probe validates the five
bridge-visible command vectors (`q`, `dq`, `kp`, `kd`, `tau`) and evaluates the
source-bound equation
`tau + kp * (q_target - q) + kd * (dq_target - dq)`. It does not add clipping,
watchdogs, trajectories, gains, CRC/header checks, or a controller.

The MuJoCo-like sensor parser checks the pinned sensor block: position
`[0:12]`, velocity `[12:24]`, actuator force `[24:36]`, then IMU quaternion,
gyro, accelerometer, frame position, and frame linear velocity. The resulting
records only model the fields the bridge fills: LowState motor `q/dq/tau_est`
and conditional IMU, plus SportModeState `position[0:3]` and
`velocity[0:3]` on `rt/sportmodestate`. This is not complete SDK state
fidelity.

The optional real check lazily verifies `unitree_sdk2py==1.0.1`,
`cyclonedds==0.10.2`, and the pinned import symbols without starting DDS. It
requires Linux; missing packages or non-Linux are `UNAVAILABLE`. A caller may
provide an already checked-out `unitree_mujoco` path to verify the bridge and
`go2.xml` SHA256 values; the probe never downloads source. Since this worktree
does not run a real Linux DDS + MuJoCo roundtrip, that status remains
`NOT_RUN`/`UNKNOWN`.

The SDK source also contains `SportClient` methods such as `StandUp` and
`Move`; their presence is source evidence only. They are outside this
low-level probe. The pinned SDK uses the default factories
`unitree_go_msg_dds__LowCmd_()`/`LowState_()` and DDS types
`unitree_go.msg.dds_.LowCmd_`/`LowState_`/`SportModeState_`; publishers and
subscribers receive the type, and `Write` receives an instance. AutoAdapter
`sit`/`stand`/`move` must be synthesized later in G2 `capability.py`, never
inserted into Translation. No Go2 shim, gait, capability, simulator runner,
or formal record is included here.

Run the unit tests from `general_demo/` with:

```text
pytest -q
```
