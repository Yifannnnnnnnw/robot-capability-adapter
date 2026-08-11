FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git python3.10 python3.10-dev python3-pip \
      build-essential cmake pkg-config libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/autoadapter
COPY general_demo /opt/autoadapter/general_demo

# Source checkouts are fixed to the Authority 0.15.0 commits.  These key-file
# checks do not claim a complete artifact or MJCF asset closure; runtime-lock
# remains DRAFT until a real amd64 build captures the full closure and digest.
RUN git clone https://github.com/unitreerobotics/unitree_sdk2_python.git /opt/unitree_sdk2_python \
    && git -C /opt/unitree_sdk2_python checkout 65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5 \
    && echo "f2c73b2981ee918dd8b3810267691d0294ebedf86d7897330819e8a5fefb19b5  /opt/unitree_sdk2_python/setup.py" | sha256sum -c - \
    && echo "7685d8c384f268ebfebd569dea5b6f1e40ba6ec3ae29e8535334193f257d1034  /opt/unitree_sdk2_python/example/go2/low_level/unitree_legged_const.py" | sha256sum -c - \
    && echo "b3101722e07534c667d4c673677a8a5d57643f307cfdeb11f4761ebc456b18ea  /opt/unitree_sdk2_python/unitree_sdk2py/idl/default.py" | sha256sum -c - \
    && echo "5d0098eef648cf017267d105bde5407b40496cc0df791f15ed7a3e5e77981fe8  /opt/unitree_sdk2_python/unitree_sdk2py/idl/unitree_go/msg/dds_/_LowCmd_.py" | sha256sum -c - \
    && echo "0d370ae286a529f9f727519e1b6abfa9189595d78f2b29db3316886a5b1d51e4  /opt/unitree_sdk2_python/unitree_sdk2py/idl/unitree_go/msg/dds_/_LowState_.py" | sha256sum -c - \
    && echo "8f05eea51a6822727a14572e656bb59a84a899ac264c96c8c7104d2acccf5909  /opt/unitree_sdk2_python/unitree_sdk2py/idl/unitree_go/msg/dds_/_SportModeState_.py" | sha256sum -c - \
    && echo "8a58eea2bc6bb8792e5b5fa76949407bd8c2ef8af07172d1a3a465bcc544e247  /opt/unitree_sdk2_python/unitree_sdk2py/core/channel.py" | sha256sum -c -

RUN git clone https://github.com/unitreerobotics/unitree_mujoco.git /opt/unitree_mujoco \
    && git -C /opt/unitree_mujoco checkout ae6a8403e272733e9996ef59990880330496177f \
    && echo "3ddb54ddddc6a20255e9bb77760537774b2eb77ce50073bf1f4a69bfaa77b599  /opt/unitree_mujoco/simulate_python/unitree_sdk2py_bridge.py" | sha256sum -c - \
    && echo "2014a3d76e30f17ab9447d8a67bd015291f74fa4d71ae30d005f1a32bd693d4b  /opt/unitree_mujoco/unitree_robots/go2/go2.xml" | sha256sum -c -

RUN python3.10 -m pip install cyclonedds==0.10.2 mujoco==3.3.6 pytest==8.4.2 \
    && python3.10 -m pip install /opt/unitree_sdk2_python \
    && python3.10 -m pip install /opt/autoadapter/general_demo

ENV AUTOADAPTER_GO2_MODEL=/opt/unitree_mujoco/unitree_robots/go2/go2.xml
WORKDIR /opt/autoadapter/general_demo
