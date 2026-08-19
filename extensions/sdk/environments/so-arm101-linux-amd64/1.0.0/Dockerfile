FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl \
    PYTHONPATH=/opt/autoadapter2-sdk/src

RUN test "$(uname -m)" = "x86_64"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates git python3.12 python3.12-venv \
      libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/autoadapter2-sdk-venv
ENV PATH=/opt/autoadapter2-sdk-venv/bin:$PATH

RUN python -m pip install --no-cache-dir \
      "lerobot==0.6.0" \
      "feetech-servo-sdk==1.0.0" \
      "mujoco==3.3.6"

# Fail during image construction if the real pinned SDK public surface is unavailable.
RUN python -m pip check \
    && python -c "exec('from lerobot.motors.feetech import FeetechMotorsBus\\nimport scservo_sdk\\ntry:\\n    from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig\\nexcept ImportError:\\n    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig\\nprint(FeetechMotorsBus.__name__, SO101Follower.__name__, SO101FollowerConfig.__name__, scservo_sdk.__name__)')"

WORKDIR /opt/autoadapter2-sdk
COPY extensions/sdk/src /opt/autoadapter2-sdk/src
COPY extensions/sdk/scripts/run_so_arm101_route_check.py /opt/autoadapter2-sdk/scripts/run_so_arm101_route_check.py

RUN git clone https://github.com/TheRobotStudio/SO-ARM100.git /opt/SO-ARM100 \
    && git -C /opt/SO-ARM100 checkout 7629d2ad9853d10fb903093a33ef6114099d97e5

ENV AUTOADAPTER_SO101_MODEL=/opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml

# Import and argument parsing are build checks; the PTY and MuJoCo route runs only at container start.
RUN python -c "import mujoco; import autoadapter2_sdk.so_arm101.route_check; print(mujoco.__version__)" \
    && python scripts/run_so_arm101_route_check.py --help >/dev/null

CMD ["python", "scripts/run_so_arm101_route_check.py", "--model", "/opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml", "--gripper-direction", "tick-increases-qpos"]
