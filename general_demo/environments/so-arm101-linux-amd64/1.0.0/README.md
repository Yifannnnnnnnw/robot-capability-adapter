# SO-ARM101 Linux amd64 readiness environment

This is a minimal development image for the Authority 0.15.0 route:

`real SO101Follower → real FeetechMotorsBus → PTY STS3215 → MuJoCo 3.3.6`.

The checked-in runtime lock is deliberately `DRAFT_UNVERIFIED_BUILD`. The
image therefore cannot issue formal `sdk_identity_load: PASS` yet. A real
amd64 build must capture the MuJoCo wheel, complete dependency artifacts,
CPython/native identities, and OCI digest into a separate lock whose status is
`FROZEN_FROM_VERIFIED_LINUX_BUILD`. No placeholder or guessed hash is accepted.

Build from the repository root:

```bash
docker build --platform linux/amd64 \
  -f general_demo/environments/so-arm101-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter-so101-readiness:dev .
```

Run the explicit Linux integration test with a build-produced frozen lock:

```bash
docker run --rm \
  -e AUTOADAPTER_RUNTIME_LOCK=/evidence/runtime-lock.frozen.json \
  -e AUTOADAPTER_GRIPPER_DIRECTION=tick-increases-qpos \
  -v "$PWD/evidence:/evidence" \
  autoadapter-so101-readiness:dev \
  python3.12 -m pytest -q linux_tests/test_so_arm101_real_route.py
```

This test does not skip. Missing Linux identity, dependencies, frozen lock,
model closure, PTY route, or any of the six checks is a failure. A passing
report is evidence for a later reviewed manifest update; the runner never
promotes `integration_manifest.json` itself.
