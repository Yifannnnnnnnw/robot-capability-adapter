FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl \
    PYTHONPATH=/opt/autoadapter2-sdk/src

RUN test "$(uname -m)" = "x86_64"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates git python3.10 python3.10-dev python3-pip \
      build-essential cmake pkg-config \
      libgl1 libegl1 libglib2.0-0 libglfw3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/autoadapter2-sdk
COPY extensions/sdk/src /opt/autoadapter2-sdk/src
COPY extensions/sdk/scripts/run_unitree_go2_route_check.py /opt/autoadapter2-sdk/scripts/run_unitree_go2_route_check.py

RUN python3.10 -m pip install --no-cache-dir \
      "numpy==2.2.6" \
      "cyclonedds==0.10.2" \
      "mujoco==3.3.6"

RUN git clone https://github.com/unitreerobotics/unitree_sdk2_python.git /opt/unitree_sdk2_python \
    && git -C /opt/unitree_sdk2_python checkout 65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5 \
    && python3.10 -m pip install --no-cache-dir --no-deps -e /opt/unitree_sdk2_python

RUN git clone https://github.com/unitreerobotics/unitree_mujoco.git /opt/unitree_mujoco \
    && git -C /opt/unitree_mujoco checkout ae6a8403e272733e9996ef59990880330496177f

ENV AUTOADAPTER_GO2_MODEL=/opt/unitree_mujoco/unitree_robots/go2/scene.xml

# Import and argument parsing are build checks; the DDS route runs only at container start.
RUN python3.10 -c "import mujoco; from pathlib import Path; from unitree_sdk2py.utils.crc import CRC; import autoadapter2_sdk.unitree_go2.route_check; model = mujoco.MjModel.from_xml_path('/opt/unitree_mujoco/unitree_robots/go2/scene.xml'); assert Path('/opt/unitree_sdk2_python/unitree_sdk2py/utils/lib/crc_amd64.so').is_file(); CRC(); print(model.nq, model.nv, model.nu)" \
    && python3.10 scripts/run_unitree_go2_route_check.py --help >/dev/null

CMD ["python3.10", "scripts/run_unitree_go2_route_check.py", "--model", "/opt/unitree_mujoco/unitree_robots/go2/scene.xml"]
