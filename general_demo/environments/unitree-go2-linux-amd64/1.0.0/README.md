# Unitree Go2 Linux amd64 runtime 1.0.0

This directory defines the frozen *coordinates* from Authority 0.15.0:
Ubuntu 22.04, CPython 3.10, `unitree_sdk2py==1.0.1` at commit
`65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5`, `cyclonedds==0.10.2`, and
`mujoco==3.3.6`.

It is not yet a frozen runtime. `runtime-lock.json` deliberately remains
`DRAFT_UNVERIFIED_LINUX_BUILD`: the complete dependency and Go2 MJCF asset
closures have not been hashed, and the real DDS/MuJoCo readiness route has not
run. A source-only or injected test cannot change that status.

The 2026-08-11 attempt confirmed that Docker can execute an Ubuntu 22.04 amd64
container (`x86_64`) on this Apple-Silicon host. The image build itself then
stopped before construction because Docker Desktop/containerd returned an
input/output error while writing `meta.db`; a subsequent read also returned an
input/output error for an existing content blob. No cache was pruned, no image
was accepted, and this is infrastructure evidence only—not a Go2 check result.

Static preflight also found and repaired two deterministic build blockers in
the Dockerfile: the wider project wheel requires Python 3.11 while this frozen
runner is Python 3.10, and an SDK2 wheel built from the pinned source omits
`utils/lib/crc_amd64.so`. The container therefore loads only the compatible Go2
runner from `PYTHONPATH` and installs the exact pinned SDK checkout editable,
with explicit CRC and runner import smoke checks. This is a Demo-runtime choice;
the later formal artifact must capture and review the native CRC closure.

Build from the repository root:

```bash
docker build --platform linux/amd64 --progress=plain \
  -f general_demo/environments/unitree-go2-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter-go2-readiness:1.0.0 .
```

If the same containerd input/output error recurs, stop: repair or restart Docker
Desktop outside this repository. Do not prune/delete Docker data as part of this
workflow. After a successful Linux amd64 build, independently capture the image
digest, installed artifact hashes, native/CPython fingerprint, and complete
MJCF asset closure into a new reviewed lock. Do not edit this DRAFT into a
verified lock by hand.

Then make a new, empty attempt directory and run the exact external lock and
manifest (the manifest is mounted so freezing it does not change the runtime
image digest):

```bash
general_demo/scripts/run_unitree_go2_readiness_linux.sh \
  autoadapter-go2-readiness:1.0.0 \
  /absolute/path/to/verified-runtime-lock.json \
  /absolute/path/to/integration_manifest.json \
  /absolute/path/to/new-go2-attempt-directory
```

The runner remains fail-closed until the lock has verified artifact hashes and
the exact manifest is READY. A failed or interrupted attempt gets a new output
directory; it is not overwritten or relabelled PASS.

The static review also found work that cannot honestly be closed without the
real environment: correlate `LowState` and `SportModeState` to one explicit
MuJoCo publication step, verify the complete free-base reset state and cleanup,
and bind the loaded model/asset and installed native dependency closures to the
reviewed lock. These remain preconditions for changing either DRAFT/NOT_RUN
state; the passing injected tests do not satisfy them.

The real test uses only `rt/lowcmd`, `rt/lowstate`, and the read-only
`rt/sportmodestate` output. It contains no `SportClient`, sit, stand, move,
gait, trajectory, balance, IK, or task logic.
