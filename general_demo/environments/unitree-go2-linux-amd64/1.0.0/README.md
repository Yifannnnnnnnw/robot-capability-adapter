# Unitree Go2 Linux amd64 runtime 1.0.0

This directory defines the frozen *coordinates* from Authority 0.15.0:
Ubuntu 22.04, CPython 3.10, `unitree_sdk2py==1.0.1` at commit
`65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5`, `cyclonedds==0.10.2`, and
`mujoco==3.3.6`.

It is not yet a frozen runtime. `runtime-lock.json` deliberately remains
`DRAFT_UNVERIFIED_LINUX_BUILD`: the image has not been built in this workspace,
the complete dependency and Go2 MJCF asset closures have not been hashed, and
the real DDS/MuJoCo readiness route has not run. A source-only or injected test
cannot change that status.

Build from the repository root:

```bash
docker build -f general_demo/environments/unitree-go2-linux-amd64/1.0.0/Dockerfile -t autoadapter-go2-readiness:1.0.0 .
```

After a successful Linux amd64 build, independently capture the image digest,
installed artifact hashes, native/CPython fingerprint, and complete MJCF asset
closure into a new reviewed lock. Do not edit this DRAFT into a verified lock
by hand. Then run the explicit route with
`general_demo/scripts/run_unitree_go2_readiness_linux.sh`.

The real test uses only `rt/lowcmd`, `rt/lowstate`, and the read-only
`rt/sportmodestate` output. It contains no `SportClient`, sit, stand, move,
gait, trajectory, balance, IK, or task logic.
