# SO-ARM101 SDK extension environment

This is the small Linux/amd64 environment for the current extension route:

`SO101Follower -> Feetech PTY -> MuJoCo -> Feetech PTY -> SO101Follower`.

It contains only the pinned SDK dependencies, the SO-ARM100 model checkout,
the `autoadapter2_sdk` package, and the route-check CLI. The Docker build
checks dependencies, imports, and CLI argument parsing only; it does not
execute a PTY route.
The image installs the published `lerobot==0.6.0` distribution. The
`source_commit` value is attribution from preserved historical records; this
image does not clone or verify that LeRobot commit.

Build from the repository root so the `COPY extensions/sdk/...` paths resolve:

```bash
docker build --platform linux/amd64 \
  -f extensions/sdk/environments/so-arm101-linux-amd64/1.0.0/Dockerfile \
  -t autoadapter2-sdk-so-arm101:1.0.0 .
```

Run the real route on Linux/amd64:

```bash
docker run --rm --platform linux/amd64 autoadapter2-sdk-so-arm101:1.0.0
```

The result is limited to this named LeRobot, Translation, robot
configuration, runtime, and MuJoCo route. It is not hardware or sim-to-real
evidence.
