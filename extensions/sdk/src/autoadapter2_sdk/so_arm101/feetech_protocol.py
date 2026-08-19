"""Minimal Feetech Protocol 0 codec required by the SO-101 public SDK path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

HEADER = b"\xff\xff"
BROADCAST_ID = 0xFE
INST_PING = 0x01
INST_READ = 0x02
INST_WRITE = 0x03
INST_ACTION = 0x05
INST_SYNC_READ = 0x82
INST_SYNC_WRITE = 0x83

MODEL_NUMBER = (3, 2)
FIRMWARE_MAJOR_VERSION = (0, 1)
FIRMWARE_MINOR_VERSION = (1, 1)
GOAL_POSITION = (42, 2)
PRESENT_POSITION = (56, 2)

MOTOR_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
MOTOR_IDS = {name: index for index, name in enumerate(MOTOR_NAMES, 1)}


class ProtocolError(ValueError):
    pass


class ChecksumError(ProtocolError):
    pass


@dataclass(frozen=True, slots=True)
class Packet:
    motor_id: int
    instruction: int
    params: bytes


def _byte(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ProtocolError(f"{name} must be an integer byte")
    return value


def checksum(motor_id: int, length: int, instruction: int, params: bytes = b"") -> int:
    return (~sum((_byte(motor_id, "motor_id"), _byte(length, "length"), _byte(instruction, "instruction"), *params))) & 0xFF


def encode_packet(motor_id: int, instruction: int, params: Iterable[int] = ()) -> bytes:
    motor_id = _byte(motor_id, "motor_id")
    instruction = _byte(instruction, "instruction")
    payload = bytes(_byte(value, "params") for value in params)
    length = len(payload) + 2
    if length > 255:
        raise ProtocolError("packet is too large")
    return HEADER + bytes((motor_id, length, instruction)) + payload + bytes((checksum(motor_id, length, instruction, payload),))


def decode_packet(frame: bytes | bytearray | memoryview) -> Packet:
    raw = bytes(frame)
    if len(raw) < 6 or raw[:2] != HEADER:
        raise ProtocolError("malformed Protocol 0 frame")
    expected = 4 + raw[3]
    if raw[3] < 2 or len(raw) != expected:
        raise ProtocolError(f"packet length expects {expected}, got {len(raw)}")
    expected_checksum = checksum(raw[2], raw[3], raw[4], raw[5:-1])
    if raw[-1] != expected_checksum:
        raise ChecksumError("packet checksum mismatch")
    return Packet(raw[2], raw[4], raw[5:-1])


def encode_status(motor_id: int, params: Iterable[int] = (), error: int = 0) -> bytes:
    motor_id = _byte(motor_id, "motor_id")
    error = _byte(error, "error")
    payload = bytes(_byte(value, "params") for value in params)
    length = len(payload) + 2
    return HEADER + bytes((motor_id, length, error)) + payload + bytes((checksum(motor_id, length, error, payload),))


def encode_sync_write(address: int, width: int, values: Mapping[int, int]) -> bytes:
    payload = bytearray((_byte(address, "address"), _byte(width, "width")))
    if width not in (1, 2, 4) or not values:
        raise ProtocolError("sync write requires a supported width and values")
    for motor_id, value in values.items():
        payload.append(_byte(motor_id, "motor_id"))
        payload.extend(int(value).to_bytes(width, "little", signed=False))
    return encode_packet(BROADCAST_ID, INST_SYNC_WRITE, payload)


def encode_sync_read(address: int, width: int, motor_ids: Iterable[int]) -> bytes:
    ids = tuple(_byte(value, "motor_id") for value in motor_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ProtocolError("sync read requires unique motor IDs")
    return encode_packet(BROADCAST_ID, INST_SYNC_READ, (_byte(address, "address"), _byte(width, "width"), *ids))


def decode_sync_write(packet: Packet) -> tuple[int, int, dict[int, bytes]]:
    if packet.motor_id != BROADCAST_ID or packet.instruction != INST_SYNC_WRITE or len(packet.params) < 3:
        raise ProtocolError("not a sync-write packet")
    address, width = packet.params[:2]
    records = packet.params[2:]
    stride = width + 1
    if width < 1 or len(records) % stride:
        raise ProtocolError("misaligned sync-write payload")
    values: dict[int, bytes] = {}
    for offset in range(0, len(records), stride):
        motor_id = records[offset]
        if motor_id in values or motor_id == BROADCAST_ID:
            raise ProtocolError("duplicate or broadcast motor ID")
        values[motor_id] = bytes(records[offset + 1 : offset + stride])
    return address, width, values


def decode_sync_read(packet: Packet) -> tuple[int, int, tuple[int, ...]]:
    if packet.motor_id != BROADCAST_ID or packet.instruction != INST_SYNC_READ or len(packet.params) < 3:
        raise ProtocolError("not a sync-read packet")
    address, width, *ids = packet.params
    if len(ids) != len(set(ids)) or BROADCAST_ID in ids:
        raise ProtocolError("invalid sync-read motor IDs")
    return address, width, tuple(ids)


def pop_frames(buffer: bytearray) -> list[bytes]:
    frames: list[bytes] = []
    while True:
        start = buffer.find(HEADER)
        if start < 0:
            del buffer[:-1]
            return frames
        if start:
            del buffer[:start]
        if len(buffer) < 4:
            return frames
        frame_size = 4 + buffer[3]
        if len(buffer) < frame_size:
            return frames
        frames.append(bytes(buffer[:frame_size]))
        del buffer[:frame_size]
