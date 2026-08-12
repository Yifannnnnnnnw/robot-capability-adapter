# EXPERIMENTAL Unitree Go2 native Linux arm64 runtime

This image is an isolated, native Linux arm64 route for the NVIDIA DGX GB10
renderer failure. It is explicitly **EXPERIMENTAL**. It does not change the
formal `unitree-go2-linux-amd64` image, manifest, Authority, or readiness
status.

The Dockerfile requires `uname -m` to be `aarch64`, installs CPython 3.10,
FFmpeg, CycloneDDS, MuJoCo 3.3.6, and the pinned SDK2 checkout, and requires
the SDK's native `crc_aarch64.so`. Its final build check constructs
`mujoco.Renderer` and renders the official Unitree Go2 scene with `MUJOCO_GL=egl`.

Build on the DGX host:

```bash
docker build --platform linux/arm64 --progress=plain \
  -f general_demo/environments/unitree-go2-linux-arm64-experimental/1.0.0/Dockerfile \
  -t autoadapter-go2-general-demo:1.0.0-arm64-experimental .
```

Capture a lock only after that native build and renderer smoke pass. The
checked-in lock is intentionally an unresolved draft and must not be edited
into a verified lock by hand:

```bash
mkdir -p /absolute/path/to/experimental-lock
image_digest="$(docker image inspect autoadapter-go2-general-demo:1.0.0-arm64-experimental --format '{{.Id}}')"
docker run --rm --platform linux/arm64 --gpus all \
  -e MUJOCO_GL=egl \
  -v /absolute/path/to/experimental-lock:/out \
  autoadapter-go2-general-demo:1.0.0-arm64-experimental \
  python3.10 scripts/capture_unitree_go2_runtime_lock.py \
    --experimental-arm64 \
    --image-digest "$image_digest" \
    --output /out/runtime-lock.json
```

For an experimental production-session or first-G2 invocation, mount that
captured lock at `/run/autoadapter/runtime-lock.json` and keep both explicit
environment values below. The session branch accepts the arm64 lock only when
the process is native `aarch64`; it does not act as an amd64 fallback:

```bash
docker run --rm --platform linux/arm64 --gpus all \
  -e MUJOCO_GL=egl \
  -e AUTOADAPTER_GO2_EXPERIMENTAL_ARM64=1 \
  -e AUTOADAPTER_GO2_EXPERIMENTAL_RUNTIME_LOCK=/run/autoadapter/runtime-lock.json \
  -v /absolute/path/to/experimental-lock/runtime-lock.json:/run/autoadapter/runtime-lock.json:ro \
  -v /absolute/path/to/selected-run-pack:/opt/autoadapter/selected-run-pack:ro \
  -v /absolute/path/to/experimental-output:/opt/autoadapter/general_demo/runs/first_g2_demo \
  autoadapter-go2-general-demo:1.0.0-arm64-experimental \
  python3.10 scripts/run_first_g2_demo.py \
    --root /opt/autoadapter \
    --robot unitree-go2 \
    --manifest /opt/autoadapter/selected-run-pack/integration_manifest.json \
    --run-snapshot /opt/autoadapter/selected-run-pack/run_snapshot.json \
    --readiness-report /opt/autoadapter/selected-run-pack/readiness_report.json
```

The run-pack inputs remain caller-owned and are not copied into the image.
This experimental route has no formal `READY`/`PASS` promotion; after the DGX
run, retain the captured lock and video evidence as experimental artifacts and
have the project owner decide whether any later Authority/runtime update is
warranted.
