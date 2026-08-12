FROM --platform=linux/amd64 ubuntu:22.04@sha256:3b06811b2afd352be909dd088a004166d665dc76d38b13eada33522a9d915c6f

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl \
    PYTHONPATH=/opt/autoadapter/general_demo/src \
    AUTOADAPTER_RUNTIME_BUILD=/opt/autoadapter/runtime-build

# This runtime is an amd64 contract even when Docker is hosted by Apple Silicon.
RUN test "$(uname -m)" = "x86_64"

RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository --yes universe \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates git ffmpeg python3.11 python3.11-dev python3.11-venv \
      build-essential cmake pkg-config libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3.11 -m venv /opt/autoadapter-venv \
    && python -m pip install --no-cache-dir setuptools==80.9.0 wheel==0.45.1
ENV PATH=/opt/autoadapter-venv/bin:$PATH

WORKDIR /opt/autoadapter
COPY general_demo/pyproject.toml /opt/autoadapter/general_demo/pyproject.toml
COPY general_demo/src /opt/autoadapter/general_demo/src
COPY general_demo/scripts /opt/autoadapter/general_demo/scripts
COPY general_demo/contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json /opt/autoadapter/general_demo/contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json
COPY general_demo/contracts/profiles/granularity/g2-reusable-effect/1.0.0/profile.json /opt/autoadapter/general_demo/contracts/profiles/granularity/g2-reusable-effect/1.0.0/profile.json
COPY general_demo/environments/unitree-go2-linux-amd64/1.0.0/python-requirements.lock /opt/autoadapter/python-requirements.lock
COPY general_demo/integrations/unitree-go2/translation.json /opt/autoadapter/general_demo/integrations/unitree-go2/translation.json
COPY general_demo/libraries/morphology/unitree-go2/1.0.0/record.json /opt/autoadapter/general_demo/libraries/morphology/unitree-go2/1.0.0/record.json
COPY general_demo/libraries/sdks/unitree-sdk2-go2-lowlevel/1.0.0/record.json /opt/autoadapter/general_demo/libraries/sdks/unitree-sdk2-go2-lowlevel/1.0.0/record.json
COPY general_demo/libraries/tasks/index.json /opt/autoadapter/general_demo/libraries/tasks/index.json
COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/catalog.json /opt/autoadapter/general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/catalog.json
COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/demo_collection.json /opt/autoadapter/general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/demo_collection.json
COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/evaluation_private.json /opt/autoadapter/general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/evaluation_private.json
COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/stage1_projection.json /opt/autoadapter/general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/stage1_projection.json

# Source checkouts are fixed to the Authority 0.16.1 commits.  These key-file
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
    && echo "8a58eea2bc6bb8792e5b5fa76949407bd8c2ef8af07172d1a3a465bcc544e247  /opt/unitree_sdk2_python/unitree_sdk2py/core/channel.py" | sha256sum -c - \
    && test -f /opt/unitree_sdk2_python/unitree_sdk2py/utils/lib/crc_amd64.so \
    && echo "b136a91a7e99c5cf914f465e131f63c897a17107939184a6f85d1a57cf315ade  /opt/unitree_sdk2_python/unitree_sdk2py/utils/lib/crc_amd64.so" | sha256sum -c -

RUN git clone https://github.com/unitreerobotics/unitree_mujoco.git /opt/unitree_mujoco \
    && git -C /opt/unitree_mujoco checkout ae6a8403e272733e9996ef59990880330496177f \
    && echo "3ddb54ddddc6a20255e9bb77760537774b2eb77ce50073bf1f4a69bfaa77b599  /opt/unitree_mujoco/simulate_python/unitree_sdk2py_bridge.py" | sha256sum -c - \
    && echo "2014a3d76e30f17ab9447d8a67bd015291f74fa4d71ae30d005f1a32bd693d4b  /opt/unitree_mujoco/unitree_robots/go2/go2.xml" | sha256sum -c - \
    && echo "6c1fda780e7883665d1c84113b9275b6d448f586a8b1c110e438a37417cbccd0  /opt/unitree_mujoco/unitree_robots/go2/scene.xml" | sha256sum -c -

RUN python -m pip install --no-cache-dir -r /opt/autoadapter/python-requirements.lock \
    && python -m pip install --no-cache-dir --no-deps -e /opt/unitree_sdk2_python \
    && python -m pip install --no-cache-dir --no-deps /opt/autoadapter/general_demo

ENV AUTOADAPTER_GO2_MODEL=/opt/unitree_mujoco/unitree_robots/go2/scene.xml

# Build-time validation covers both the full Framework import surface and the
# real Go2 dependencies. It does not open a manifest, runtime lock, or model
# API credential, and it does not invoke a readiness attempt.
RUN python -m pip check \
    && test -x "$(command -v ffmpeg)" \
    && ffmpeg -hide_banner -version >/dev/null \
    && test -f /opt/unitree_sdk2_python/unitree_sdk2py/utils/lib/crc_amd64.so \
    && python -c "import importlib, importlib.util; import mujoco; from pathlib import Path; from autoadapter2.evaluation.ffmpeg import FFmpegVideoEncoder; from autoadapter2.orchestration.demo_runner import GeneralDemoRunner; from autoadapter2.orchestration.first_g2_demo import run_first_g2_demo; from autoadapter2.integrations.unitree_go2 import Go2DDSMuJoCoBridge, MuJoCoGo2Backend, UnitreeSDK2Transport; from autoadapter2.integrations.unitree_go2.readiness import run_readiness; from autoadapter2.integrations.unitree_go2.runtime_lock import capture_runtime_lock; from unitree_sdk2py.utils.crc import CRC; assert Path('/opt/unitree_sdk2_python/unitree_sdk2py/utils/lib/crc_amd64.so').is_file(); CRC(); model = mujoco.MjModel.from_xml_path('/opt/unitree_mujoco/unitree_robots/go2/scene.xml'); assert (model.nq, model.nv, model.nu) == (19, 18, 12); session_spec = importlib.util.find_spec('autoadapter2.integrations.unitree_go2.session'); session = importlib.import_module('autoadapter2.integrations.unitree_go2.session') if session_spec is not None else None; assert session is None or callable(getattr(session, 'create_evaluation_robot_session', None)); print('Go2 General Demo imports OK:', FFmpegVideoEncoder.__name__, GeneralDemoRunner.__name__, run_first_g2_demo.__name__, Go2DDSMuJoCoBridge.__name__, MuJoCoGo2Backend.__name__, UnitreeSDK2Transport.__name__, run_readiness.__name__, capture_runtime_lock.__name__)" \
    && python /opt/autoadapter/general_demo/scripts/run_first_g2_demo.py --help >/dev/null \
    && python /opt/autoadapter/general_demo/scripts/run_unitree_go2_readiness.py --help >/dev/null \
    && python /opt/autoadapter/general_demo/scripts/capture_unitree_go2_runtime_lock.py --help >/dev/null
WORKDIR /opt/autoadapter/general_demo
