from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "environments/so-arm101-linux-amd64/1.0.0/Dockerfile"


def _copy_lines(dockerfile: str) -> list[str]:
    return [line.strip() for line in dockerfile.splitlines() if line.lstrip().startswith("COPY ")]


def test_so_arm101_image_contains_full_runtime_closure_and_smokes() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends" in dockerfile
    assert re.search(r"apt-get install.*?\bffmpeg\b", dockerfile, re.DOTALL)

    for requirement in (
        "torch==2.7.1",
        "torchvision==0.22.1",
        "numpy==2.2.6",
        "draccus==0.10.0",
        "huggingface-hub==1.0.1",
        "tqdm==4.67.1",
        "pyserial==3.5",
        "deepdiff==8.6.1",
        "opencv-python-headless==4.12.0.88",
        "Pillow==11.3.0",
        "einops==0.8.1",
        "requests==2.32.4",
        "gymnasium==1.1.1",
        "safetensors==0.5.3",
        "packaging==24.2",
        "termcolor==3.1.0",
        "cmake==3.31.6",
        "setuptools==75.8.2",
    ):
        assert re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(requirement)}(?![A-Za-z0-9_.-])", dockerfile), requirement

    required_copies = (
        "COPY general_demo/pyproject.toml /opt/autoadapter/general_demo/pyproject.toml",
        "COPY general_demo/src /opt/autoadapter/general_demo/src",
        "COPY general_demo/linux_tests /opt/autoadapter/general_demo/linux_tests",
        "COPY general_demo/scripts /opt/autoadapter/general_demo/scripts",
        "COPY general_demo/integrations/so-arm101/translation.json",
        "COPY general_demo/integrations/so-arm101/gripper_endpoint_evidence.json",
        "COPY general_demo/libraries/morphology/so-arm101/1.0.0/record.json",
        "COPY general_demo/libraries/morphology/so-arm101/1.0.0/private_demo_scene",
        "COPY general_demo/libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/catalog.json",
        "COPY general_demo/libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/demo_collection.json",
        "COPY general_demo/libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/evaluation_private.json",
        "COPY general_demo/libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/stage1_projection.json",
        "COPY general_demo/libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/task_instances_private.json",
        "COPY general_demo/libraries/sdks/lerobot-so101-follower/1.0.0/record.json",
    )
    for copy_path in required_copies:
        assert copy_path in dockerfile, copy_path

    for relative in (
        "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene.xml",
        "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene_config.json",
        "libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/task_instances_private.json",
    ):
        assert (ROOT / relative).is_file(), relative

    copy_lines = _copy_lines(dockerfile)
    assert not any(re.match(r"COPY\s+general_demo(?:\s|$)", line) for line in copy_lines)
    for forbidden in (".env", "run_artifacts", "runtime-lock", "integration_manifest", "formal", "secret"):
        assert all(forbidden not in line for line in copy_lines), forbidden

    for smoke in (
        "python -m pip check",
        'command -v ffmpeg',
        "ffmpeg -hide_banner -version",
        "from lerobot.motors.feetech import FeetechMotorsBus",
        "SO101Follower",
        "import mujoco",
        "FFmpegVideoEncoder",
        "GeneralDemoRunner",
        "MuJoCoFrameCapture",
        "create_evaluation_robot_session",
        "_load_frame_capture_factory",
    ):
        assert smoke in dockerfile, smoke


def test_so_arm101_image_keeps_pinned_model_and_official_route() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "7629d2ad9853d10fb903093a33ef6114099d97e5" in dockerfile
    assert "d75253eb568e8a7214db9c631ab7bed4217f608a26f7276ebe9a7636cac82580" in dockerfile
    assert "AUTOADAPTER_SO101_MODEL=/opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml" in dockerfile
    assert "COPY general_demo/src /opt/autoadapter/general_demo/src" in dockerfile
