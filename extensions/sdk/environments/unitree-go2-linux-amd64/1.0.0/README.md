# Unitree Go2 SDK extension environment

This is the small Linux/amd64 environment for the current extension route:

`SDK2 DDS LowCmd -> MuJoCo -> DDS LowState/SportModeState`.

It contains only the pinned SDK2/CycloneDDS/MuJoCo dependencies, the pinned
`unitree_mujoco` checkout, the `autoadapter2_sdk` package, and the route-check
CLI. The Docker build checks imports, the native CRC library, and model
loading; it does not execute a DDS route.

Build from the repository root so the `COPY extensions/sdk/...` paths resolve:

```bash
docker build --platform linux/amd64 \
  -f extensions/sdk/environments/unitree-go2-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter2-sdk-go2:1.0.0 .
```

Run the real route on Linux/amd64:

```bash
docker run --rm --platform linux/amd64 autoadapter2-sdk-go2:1.0.0
```

The result is limited to this named SDK, Translation, robot configuration,
runtime, and MuJoCo route. It is not hardware or sim-to-real evidence.
