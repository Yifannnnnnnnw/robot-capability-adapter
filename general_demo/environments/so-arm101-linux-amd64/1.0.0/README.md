# SO-ARM101 Linux amd64 General Demo environment

This is the pinned Linux/amd64 image for the full first-G2 SO-ARM101 General
Demo route:

`real SO101Follower → real FeetechMotorsBus → PTY STS3215 → MuJoCo 3.3.6`.

The image also contains the General Demo Python source, FFmpeg, the private
SO task scene/configuration, and the fixed SO Library records. Its final build
layer runs `pip check`, verifies FFmpeg, and imports the real SDK symbols,
MuJoCo, `GeneralDemoRunner`, the shared frame capture, and the production SO
session factory.

The closed run-pack inputs and attempt outputs remain outside the image. Mount
the mutable integration manifest, profile, and runtime lock into a unique
attempt directory. This keeps the measured OCI digest independent from the
records that bind it. A verified lock is captured from the running image; no
placeholder or copied hash is accepted.

Build from the repository root:

```bash
docker build --platform linux/amd64 \
  -f general_demo/environments/so-arm101-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter-so101-general-demo:dev .
```

Capture the lock from that exact local image, supplying its validated Docker
image ID:

```bash
docker image inspect autoadapter-so101-general-demo:dev \
  --format '{{.Id}}'
docker run --rm --platform linux/amd64 --network none \
  -e AUTOADAPTER_IMAGE_DIGEST=sha256:<image-id> \
  -v "$PWD/general_demo/environments/so-arm101-linux-amd64/1.0.0:/run/autoadapter:ro" \
  -v "$PWD/evidence:/evidence" \
  autoadapter-so101-general-demo:dev \
  python3.12 scripts/run_so_arm101_readiness.py --capture-runtime-lock \
    --model /opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml \
    --reference-root /opt/autoadapter \
    --report /evidence/runtime-lock.frozen.json
```

Run one explicit Linux attempt with the verified lock, manifest, and profile:

```bash
general_demo/scripts/run_so_arm101_readiness_linux.sh \
  autoadapter-so101-general-demo:dev \
  /absolute/path/to/runtime-lock.frozen.json \
  /absolute/path/to/integration_manifest.json \
  /absolute/path/to/readiness-profile.json \
  /absolute/path/to/new-attempt-directory
```

The runner refuses an existing attempt directory, uses only a project-created
PTY, records one immutable report plus seven evidence files, and never edits or
promotes `integration_manifest.json`. The report is `PASS` only when the six
ordered checks and cleanup pass; the manifest may remain `DRAFT` pending the
separate project review gate. The final DGX build must capture the runtime lock
and image digest; this image definition does not generate `READY` or `PASS`.
