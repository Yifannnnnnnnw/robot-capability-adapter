FROM --platform=linux/amd64 ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl \
    PYTHONPATH=/opt/autoadapter/general_demo/src \
    AUTOADAPTER_RUNTIME_BUILD=/opt/autoadapter/runtime-build

RUN test "$(uname -m)" = "x86_64"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git python3.12 python3.12-dev python3.12-venv build-essential \
      libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/autoadapter-venv
ENV PATH=/opt/autoadapter-venv/bin:$PATH

WORKDIR /opt/autoadapter

# The two SDK distributions are checked against the Authority's published
# artifact hashes before installation.  LeRobot is installed without its
# unrelated training/vision stack; this route only needs the real SOFollower,
# Feetech bus, and their direct import/runtime dependencies.  PyTorch comes from
# the official CPU index so this integration image never downloads CUDA wheels.
# The complete closure remains DRAFT until the build receipt and OCI digest are
# captured into a build-produced runtime lock.
RUN mkdir -p /tmp/wheels /opt/autoadapter/runtime-build \
    && python -m pip download --no-deps --dest /tmp/wheels lerobot==0.6.0 feetech-servo-sdk==1.0.0 \
    && echo "b38a564fbc441d98380576863bf68635dde5fc2c42ddc2a39d0486640dc9e9a8  /tmp/wheels/lerobot-0.6.0-py3-none-any.whl" | sha256sum -c - \
    && FEETECH_ARCHIVE="$(find /tmp/wheels -maxdepth 1 -type f -name 'feetech-servo-sdk-1.0.0*' -print -quit)" \
    && test -n "$FEETECH_ARCHIVE" \
    && echo "d4d3832e4b1b22a8222133a414db9f868224c2fb639426a1b11d96ddfe84e69c  $FEETECH_ARCHIVE" | sha256sum -c - \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu \
         --report /opt/autoadapter/runtime-build/01-torch-install-report.json torch==2.7.1 \
    && python -m pip install --report /opt/autoadapter/runtime-build/02-lerobot-install-report.json \
         --no-deps /tmp/wheels/lerobot-0.6.0-py3-none-any.whl "$FEETECH_ARCHIVE" \
    && python -m pip install --report /opt/autoadapter/runtime-build/03-direct-install-report.json \
         numpy==2.2.6 draccus==0.10.0 huggingface-hub==1.0.1 \
         tqdm==4.67.1 pyserial==3.5 deepdiff==8.6.1 \
    && python -m pip download --no-deps --dest /tmp/wheels mujoco==3.3.6 \
    && MUJOCO_WHEEL="$(find /tmp/wheels -maxdepth 1 -type f -name 'mujoco-3.3.6-*.whl' -print -quit)" \
    && test -n "$MUJOCO_WHEEL" \
    && sha256sum /tmp/wheels/lerobot-0.6.0-py3-none-any.whl "$FEETECH_ARCHIVE" "$MUJOCO_WHEEL" \
         > /opt/autoadapter/runtime-build/artifact-sha256.txt \
    && python -m pip install --report /opt/autoadapter/runtime-build/04-mujoco-install-report.json \
         "$MUJOCO_WHEEL" pytest \
    && rm -rf /tmp/wheels

# Keep mutable manifest/profile/runtime-lock files outside the image.  The launcher
# mounts them into a unique attempt directory, so a captured OCI digest does not
# become self-referential when those records bind the image.
COPY general_demo/pyproject.toml /opt/autoadapter/general_demo/pyproject.toml
COPY general_demo/src /opt/autoadapter/general_demo/src
COPY general_demo/linux_tests /opt/autoadapter/general_demo/linux_tests
COPY general_demo/scripts /opt/autoadapter/general_demo/scripts
COPY general_demo/integrations/so-arm101/translation.json /opt/autoadapter/general_demo/integrations/so-arm101/translation.json
COPY general_demo/integrations/so-arm101/gripper_endpoint_evidence.json /opt/autoadapter/general_demo/integrations/so-arm101/gripper_endpoint_evidence.json
COPY general_demo/libraries/morphology/so-arm101/1.0.0/record.json /opt/autoadapter/general_demo/libraries/morphology/so-arm101/1.0.0/record.json
COPY general_demo/libraries/sdks/lerobot-so101-follower/1.0.0/record.json /opt/autoadapter/general_demo/libraries/sdks/lerobot-so101-follower/1.0.0/record.json
RUN python -m pip install --report /opt/autoadapter/runtime-build/05-general-demo-install-report.json \
    --no-deps /opt/autoadapter/general_demo

# Capture the canonical model source at the frozen commit.  This is not a
# complete admitted asset-closure record; the manifest therefore remains DRAFT.
RUN git clone https://github.com/TheRobotStudio/SO-ARM100.git /opt/SO-ARM100 \
    && git -C /opt/SO-ARM100 checkout 7629d2ad9853d10fb903093a33ef6114099d97e5 \
    && echo "d75253eb568e8a7214db9c631ab7bed4217f608a26f7276ebe9a7636cac82580  /opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml" | sha256sum -c -

ENV AUTOADAPTER_SO101_MODEL=/opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml
WORKDIR /opt/autoadapter/general_demo
