from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ROOT / "environments/unitree-go2-linux-amd64/1.0.0"
DOCKERFILE = ENVIRONMENT / "Dockerfile"


def _copy_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.lstrip().startswith("COPY ")]


def test_go2_image_packages_full_framework_and_real_runtime() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM --platform=linux/amd64 ubuntu:22.04@" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "python3.11" in dockerfile
    assert "python3.11 -m venv /opt/autoadapter-venv" in dockerfile
    assert "PATH=/opt/autoadapter-venv/bin:$PATH" in dockerfile
    assert "python -m pip install --no-cache-dir -r /opt/autoadapter/python-requirements.lock" in dockerfile
    assert "python -m pip check" in dockerfile

    required_copies = (
        "COPY general_demo/pyproject.toml /opt/autoadapter/general_demo/pyproject.toml",
        "COPY general_demo/src /opt/autoadapter/general_demo/src",
        "COPY general_demo/scripts /opt/autoadapter/general_demo/scripts",
        "COPY general_demo/contracts/profiles/readiness/",
        "COPY general_demo/contracts/profiles/granularity/g2-reusable-effect/",
        "COPY general_demo/integrations/unitree-go2/translation.json",
        "COPY general_demo/libraries/morphology/unitree-go2/1.0.0/record.json",
        "COPY general_demo/libraries/sdks/unitree-sdk2-go2-lowlevel/1.0.0/record.json",
        "COPY general_demo/libraries/tasks/index.json",
        "COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/catalog.json",
        "COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/demo_collection.json",
        "COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/evaluation_private.json",
        "COPY general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/stage1_projection.json",
    )
    for copy_path in required_copies:
        assert copy_path in dockerfile, copy_path

    copy_lines = _copy_lines(dockerfile)
    assert not any(re.match(r"COPY\s+general_demo(?:\s|$)", line) for line in copy_lines)
    for forbidden in (".env", "secret", "runs", "run_artifacts", "results", "integration_manifest", "runtime-lock.json"):
        assert all(forbidden not in line for line in copy_lines), forbidden

    for smoke in (
        "from autoadapter2.evaluation.ffmpeg import FFmpegVideoEncoder",
        "from autoadapter2.orchestration.demo_runner import GeneralDemoRunner",
        "from autoadapter2.orchestration.first_g2_demo import run_first_g2_demo",
        "from autoadapter2.integrations.unitree_go2 import",
        "from autoadapter2.integrations.unitree_go2.readiness import run_readiness",
        "from autoadapter2.integrations.unitree_go2.runtime_lock import capture_runtime_lock",
        "from unitree_sdk2py.utils.crc import CRC",
        "mujoco.MjModel.from_xml_path",
        "run_first_g2_demo.py --help",
        "run_unitree_go2_readiness.py --help",
        "capture_unitree_go2_runtime_lock.py --help",
    ):
        assert smoke in dockerfile, smoke

    assert "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5" in dockerfile
    assert "ae6a8403e272733e9996ef59990880330496177f" in dockerfile
    assert "b136a91a7e99c5cf914f465e131f63c897a17107939184a6f85d1a57cf315ade" in dockerfile
    assert "6c1fda780e7883665d1c84113b9275b6d448f586a8b1c110e438a37417cbccd0" in dockerfile
    assert "AUTOADAPTER_GO2_MODEL=/opt/unitree_mujoco/unitree_robots/go2/scene.xml" in dockerfile


def test_go2_packaged_records_and_lock_are_not_promoted() -> None:
    lock = json.loads((ENVIRONMENT / "runtime-lock.json").read_text(encoding="utf-8"))
    translation = json.loads(
        (ROOT / "integrations/unitree-go2/translation.json").read_text(encoding="utf-8")
    )

    assert lock["status"] == "DRAFT_UNVERIFIED_LINUX_BUILD"
    assert lock["platform"]["python"] == "3.11"
    assert lock["oci_image_digest"] is None
    assert any("NOT_RUN" in item for item in lock["unresolved"])
    assert translation["dds"]["domain_id"] == 1
    assert translation["dds"]["interface"] == "lo"
    assert translation["dds"]["topics"] == {
        "command": "rt/lowcmd",
        "state": "rt/lowstate",
        "sport_state_read_only": "rt/sportmodestate",
    }
    assert {"gait", "balance", "recovery"}.issubset(set(translation["forbidden_behavior"]))


def test_go2_readiness_launcher_uses_image_python_and_keeps_network_isolation() -> None:
    launcher = (ROOT / "scripts/run_unitree_go2_readiness_linux.sh").read_text(encoding="utf-8")
    assert "python scripts/run_unitree_go2_readiness.py" in launcher
    assert "--network none" in launcher
