# Unitree Go2 Linux amd64 General Demo runtime

This is the Ubuntu 22.04/Linux amd64 image for the full first-G2 Go2 path. The
project runtime is CPython 3.11 in `/opt/autoadapter-venv`, with the pinned
`unitree_sdk2py==1.0.1` checkout, CycloneDDS `0.10.2`, MuJoCo `3.3.6`, and
FFmpeg. The pinned official Go2 scene is:

`unitree_robots/go2/scene.xml`

at Unitree MuJoCo commit
`ae6a8403e272733e9996ef59990880330496177f`, with SHA-256
`6c1fda780e7883665d1c84113b9275b6d448f586a8b1c110e438a37417cbccd0`.

The image contains the complete current `autoadapter2` Framework source, the
first-G2/readiness/lock scripts, fixed contracts, the Go2 morphology and SDK
records, the translation record, and the private Go2 evaluation task artifacts.
It deliberately does not COPY `.env` files, secrets, run directories, results,
the integration manifest, or the runtime lock. Those inputs and outputs belong
to the closed run pack mounted for an attempt.

The final build layer runs `pip check`, verifies FFmpeg and the pinned native
`crc_amd64.so`, imports the General Demo runner/CLI and Go2 bridge, and loads
the pinned MuJoCo scene. If a Go2 session module/factory exists in the source at
build time, the smoke also imports and checks its factory.

Build from the repository root:

```bash
docker build --platform linux/amd64 --progress=plain \
  -f general_demo/environments/unitree-go2-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter-go2-general-demo:1.0.0 .
```

The full first-G2 run needs outbound HTTPS for the configured model API. Run
`scripts/run_first_g2_demo.py` with normal container networking and mount the
reviewed run-pack inputs and a new output directory. The readiness launcher is
the separate fail-closed path and keeps `--network none`:

```bash
general_demo/scripts/run_unitree_go2_readiness_linux.sh \
  autoadapter-go2-general-demo:1.0.0 \
  /absolute/path/to/verified-runtime-lock.json \
  /absolute/path/to/integration_manifest.json \
  /absolute/path/to/new-go2-attempt-directory
```

Before that readiness command, capture a new runtime lock from the exact built
image and its Docker image ID. The checked-in `runtime-lock.json` remains
DRAFT/NOT_RUN; do not hand-edit it into a final digest or readiness result.
The final DGX build must recapture the Python 3.11 identity, complete SDK2 /
CycloneDDS /MuJoCo dependency hashes, CRC native-library hash, image digest,
and complete scene asset closure.

The admitted transport remains CycloneDDS domain 1 on loopback using only
`rt/lowcmd`, `rt/lowstate`, and read-only `rt/sportmodestate`. No SportClient,
gait, controller, IK, trajectory, balance, or recovery behavior is added by
this image.
