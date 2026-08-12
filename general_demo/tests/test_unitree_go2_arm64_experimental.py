from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autoadapter2.integrations.unitree_go2 import runtime_lock as go2_runtime_lock
from autoadapter2.integrations.unitree_go2 import session as go2_session


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ROOT / "environments/unitree-go2-linux-arm64-experimental/1.0.0"


def test_experimental_arm64_image_is_native_and_smokes_the_renderer() -> None:
    dockerfile = (ENVIRONMENT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM --platform=linux/arm64 ubuntu:22.04" in dockerfile
    assert 'test "$(uname -m)" = "aarch64"' in dockerfile
    assert "crc_aarch64.so" in dockerfile
    assert "mujoco.Renderer" in dockerfile
    assert "renderer.render()" in dockerfile
    assert "MUJOCO_GL=egl" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "cyclonedds-dev" in dockerfile
    assert (
        "CYCLONEDDS_HOME=/opt/cyclonedds-system" in dockerfile
        and "ln -s /usr/include /opt/cyclonedds-system/include" in dockerfile
        and "/usr/lib/aarch64-linux-gnu/libddsc.so" in dockerfile
    )
    assert "cyclonedds==0.10.2" in (ENVIRONMENT / "python-requirements.lock").read_text(
        encoding="utf-8"
    )
    assert "--platform=linux/amd64" not in dockerfile
    assert "crc_amd64.so" not in dockerfile


def test_experimental_runtime_lock_is_unresolved_and_non_formal() -> None:
    lock = json.loads((ENVIRONMENT / "runtime-lock.json").read_text(encoding="utf-8"))

    assert lock["runtime_id"] == "unitree-go2-linux-arm64-experimental"
    assert lock["status"] == "EXPERIMENTAL_DRAFT_UNVERIFIED_LINUX_ARM64_BUILD"
    assert lock["platform"] == {
        "os": "Ubuntu 22.04",
        "architecture": "arm64",
        "python": "3.10",
    }
    assert lock["oci_image_digest"] is None
    assert lock["unresolved"]
    assert all(item.startswith("EXPERIMENTAL:") for item in lock["unresolved"])


def test_arm64_selection_requires_native_machine_and_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(go2_session.sys, "platform", "linux")
    monkeypatch.setattr(go2_session.platform, "machine", lambda: "aarch64")
    monkeypatch.delenv(go2_session.GO2_EXPERIMENTAL_ARM64_ENV, raising=False)
    assert not go2_session._experimental_arm64_enabled()

    monkeypatch.setenv(go2_session.GO2_EXPERIMENTAL_ARM64_ENV, "1")
    assert go2_session._experimental_arm64_enabled()

    monkeypatch.setattr(go2_session.platform, "machine", lambda: "x86_64")
    assert not go2_session._experimental_arm64_enabled()


def test_factory_selects_only_the_explicit_experimental_lock_on_native_arm64(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    manifest_path = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "id": "unitree-go2-linux-amd64",
                    "version": "1.0.0",
                    "lock_sha256": "0" * 64,
                }
            }
        ),
        encoding="utf-8",
    )
    lock_path = root / (
        "general_demo/environments/unitree-go2-linux-arm64-experimental/1.0.0/runtime-lock.json"
    )
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"status":"EXPERIMENTAL_DRAFT"}', encoding="utf-8")

    monkeypatch.setattr(go2_session.sys, "platform", "linux")
    monkeypatch.setattr(go2_session.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv(go2_session.GO2_EXPERIMENTAL_ARM64_ENV, "1")
    monkeypatch.delenv(go2_session.GO2_EXPERIMENTAL_ARM64_LOCK_ENV, raising=False)

    selected = go2_session._factory_runner_inputs(
        robot="unitree-go2",
        manifest_path=manifest_path,
        integration_manifest_path=None,
        run_directory=None,
        project_root=root,
        integration_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        runtime_lock_path=None,
        runtime_lock_sha256=None,
    )

    assert selected[3] == lock_path.resolve()
    assert selected[4] == hashlib.sha256(lock_path.read_bytes()).hexdigest()


def test_experimental_runtime_lock_capture_rejects_non_native_machine(monkeypatch) -> None:
    monkeypatch.setattr(go2_runtime_lock.sys, "platform", "linux")
    monkeypatch.setattr(go2_runtime_lock.platform, "machine", lambda: "x86_64")

    with pytest.raises(go2_runtime_lock.RuntimeLockError, match="Linux aarch64"):
        go2_runtime_lock.capture_experimental_arm64_runtime_lock(
            image_digest="sha256:" + "0" * 64
        )
