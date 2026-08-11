from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autoadapter2.integrations.so_arm101.feetech_protocol import (
    GOAL_POSITION,
    INST_WRITE,
    MOTOR_IDS,
    PRESENT_POSITION,
    ChecksumError,
    ProtocolError,
    decode_packet,
    encode_packet,
    encode_sync_read,
    encode_sync_write,
)
from autoadapter2.integrations.so_arm101.translation import FeetechPTYTranslation


ROOT = Path(__file__).resolve().parents[1]


class FakePositionBackend:
    timestep = 0.005

    def __init__(self) -> None:
        self.qpos = [2048] * 6
        self.qvel = [0] * 6
        self.ctrl = [2048] * 6
        self.closed = False

    def set_goal_ticks(self, values):
        for motor_id, value in values.items():
            self.ctrl[motor_id - 1] = value

    def present_ticks(self, motor_ids):
        return {motor_id: self.qpos[motor_id - 1] for motor_id in motor_ids}

    def step(self, seconds):
        del seconds
        self.qpos = list(self.ctrl)
        self.qvel = [1 if value != 2048 else 0 for value in self.qpos]

    def reset(self):
        self.qpos = [2048] * 6
        self.qvel = [0] * 6
        self.ctrl = [2048] * 6

    def state(self):
        return {"qpos": list(self.qpos), "qvel": list(self.qvel), "ctrl": list(self.ctrl)}

    def close(self):
        self.closed = True


def test_goal_packets_change_ctrl_only_and_present_reads_current_state() -> None:
    backend = FakePositionBackend()
    translation = FeetechPTYTranslation(backend)
    target = {motor_id: 1000 + motor_id for motor_id in MOTOR_IDS.values()}
    before = backend.state()
    assert translation.handle_frame(encode_sync_write(GOAL_POSITION[0], 2, target)) == ()
    after_command = backend.state()
    assert after_command["ctrl"] == [1001, 1002, 1003, 1004, 1005, 1006]
    assert after_command["qpos"] == before["qpos"]
    assert after_command["qvel"] == before["qvel"]
    backend.step(0.1)
    responses = translation.handle_frame(
        encode_sync_read(PRESENT_POSITION[0], 2, MOTOR_IDS.values())
    )
    observed = {
        packet.motor_id: int.from_bytes(packet.params, "little")
        for packet in (decode_packet(response) for response in responses)
    }
    assert observed == target


def test_bad_checksum_read_only_write_and_wrong_width_do_not_change_control() -> None:
    backend = FakePositionBackend()
    translation = FeetechPTYTranslation(backend)
    before = backend.state()
    bad = bytearray(encode_sync_write(GOAL_POSITION[0], 2, {1: 1000}))
    bad[-1] ^= 1
    with pytest.raises(ChecksumError):
        translation.handle_frame(bad)
    with pytest.raises(ProtocolError, match="read-only"):
        translation.handle_frame(
            encode_packet(1, INST_WRITE, (PRESENT_POSITION[0], 0x01, 0x00))
        )
    with pytest.raises(ProtocolError, match="width"):
        translation.handle_frame(encode_packet(1, INST_WRITE, (GOAL_POSITION[0], 0x01)))
    assert backend.state() == before
    assert translation.accepted_goal_writes == 0


def test_reset_restores_backend_and_close_is_idempotent() -> None:
    backend = FakePositionBackend()
    translation = FeetechPTYTranslation(backend)
    translation.handle_frame(encode_sync_write(GOAL_POSITION[0], 2, {1: 1000}))
    backend.step(0.1)
    translation.reset()
    assert backend.state() == {
        "qpos": [2048] * 6,
        "qvel": [0] * 6,
        "ctrl": [2048] * 6,
    }
    translation.close()
    translation.close()
    assert backend.closed is True


def test_installed_pty_worker_closes_without_fd_race() -> None:
    for _ in range(20):
        backend = FakePositionBackend()
        translation = FeetechPTYTranslation(backend)
        translation.install()
        translation.close()
        assert translation.health()["thread_error"] is None
        assert translation.is_open is False
        assert backend.closed is True


def test_draft_records_bind_exact_implementation_and_runtime_bytes() -> None:
    repository_root = ROOT.parent
    translation_path = ROOT / "integrations/so-arm101/translation.json"
    manifest_path = ROOT / "integrations/so-arm101/integration_manifest.json"
    lock_path = ROOT / "environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json"
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert translation["status"] == "DRAFT"
    assert translation["conformance_status"] == "NOT_RUN"
    assert manifest["status"] == "DRAFT"
    assert runtime_lock["status"] == "DRAFT_UNVERIFIED_BUILD"
    for source in translation["implementation"]["source_files"]:
        assert hashlib.sha256((repository_root / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    readiness_source = translation["implementation"]["readiness_runner"]
    assert hashlib.sha256((repository_root / readiness_source["path"]).read_bytes()).hexdigest() == readiness_source["sha256"]
    assert hashlib.sha256(translation_path.read_bytes()).hexdigest() == manifest["translation_ref"]["sha256"]
    assert hashlib.sha256(lock_path.read_bytes()).hexdigest() == manifest["runtime"]["lock_sha256"]
