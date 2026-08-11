"""Small, dependency-free Feetech Protocol 0 wire codec.

This is a feasibility probe, not a replacement for ``scservo_sdk``.  It
implements only the packet shapes used by the SO-101 follower probe:
ping/read/write, sync write, and sync read.
"""

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
INST_STATUS = 0x00

MODEL_NUMBER = (3, 2)
FIRMWARE_MAJOR_VERSION = (0, 1)
FIRMWARE_MINOR_VERSION = (1, 1)
GOAL_POSITION = (42, 2)
PRESENT_POSITION = (56, 2)

SO101_MOTOR_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
SO101_MOTOR_IDS = {name: index for index, name in enumerate(SO101_MOTOR_NAMES, 1)}


class FeetechProtocolError(ValueError):
    """Base error for malformed or unsupported probe packets."""


class ChecksumError(FeetechProtocolError):
    """Raised when the packet checksum does not match its bytes."""


class PacketLengthError(FeetechProtocolError):
    """Raised when a packet length field cannot describe the frame."""


@dataclass(frozen=True, slots=True)
class Packet:
    motor_id: int
    instruction: int
    params: bytes = b""


@dataclass(frozen=True, slots=True)
class StatusPacket:
    motor_id: int
    error: int
    params: bytes = b""


@dataclass(frozen=True, slots=True)
class SyncWrite:
    address: int
    data_length: int
    values: dict[int, bytes]


@dataclass(frozen=True, slots=True)
class SyncRead:
    address: int
    data_length: int
    motor_ids: tuple[int, ...]


def _byte(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise FeetechProtocolError(f"{name} must be an integer byte")
    return value


def _bytes(values: Iterable[int], name: str = "bytes") -> bytes:
    return bytes(_byte(value, name) for value in values)


def _address_and_length(address: int, data_length: int) -> tuple[int, int]:
    _byte(address, "address")
    if isinstance(data_length, bool) or not isinstance(data_length, int) or not 1 <= data_length <= 0xFF:
        raise FeetechProtocolError("data_length must be an integer from 1 to 255")
    return address, data_length


def calculate_checksum(motor_id: int, length: int, instruction: int, params: Iterable[int] = ()) -> int:
    """Return the Protocol 0 one's-complement checksum."""

    motor_id = _byte(motor_id, "motor_id")
    length = _byte(length, "length")
    instruction = _byte(instruction, "instruction")
    param_bytes = _bytes(params, "params")
    return (~sum((motor_id, length, instruction, *param_bytes))) & 0xFF


def encode_packet(motor_id: int, instruction: int, params: Iterable[int] = ()) -> bytes:
    """Encode one Protocol 0 instruction packet."""

    motor_id = _byte(motor_id, "motor_id")
    instruction = _byte(instruction, "instruction")
    param_bytes = _bytes(params, "params")
    length = len(param_bytes) + 2  # instruction + params + checksum
    if length > 0xFF:
        raise PacketLengthError("packet is too large for Protocol 0")
    body = bytes((motor_id, length, instruction)) + param_bytes
    return HEADER + body + bytes((calculate_checksum(motor_id, length, instruction, param_bytes),))


def decode_packet(frame: bytes | bytearray | memoryview) -> Packet:
    """Decode and checksum-verify exactly one Protocol 0 packet."""

    raw = bytes(frame)
    if len(raw) < 6:
        raise PacketLengthError("packet is shorter than the Protocol 0 minimum")
    if raw[:2] != HEADER:
        raise FeetechProtocolError("packet header is not 0xFFFF")
    length = raw[3]
    if length < 2:
        raise PacketLengthError("Protocol 0 length must include instruction and checksum")
    expected_size = 4 + length
    if len(raw) != expected_size:
        raise PacketLengthError(f"length field expects {expected_size} bytes, got {len(raw)}")
    motor_id = raw[2]
    instruction = raw[4]
    params = raw[5:-1]
    expected_checksum = calculate_checksum(motor_id, length, instruction, params)
    if raw[-1] != expected_checksum:
        raise ChecksumError(
            f"checksum mismatch: received 0x{raw[-1]:02x}, expected 0x{expected_checksum:02x}"
        )
    return Packet(motor_id=motor_id, instruction=instruction, params=params)


def encode_status_packet(motor_id: int, error: int = 0, params: Iterable[int] = ()) -> bytes:
    """Encode the Protocol 0 status layout (error shares byte[4]'s slot)."""

    motor_id = _byte(motor_id, "motor_id")
    error = _byte(error, "error")
    param_bytes = _bytes(params)
    length = len(param_bytes) + 2  # error + params + checksum
    if length > 0xFF:
        raise PacketLengthError("status packet is too large for Protocol 0")
    body = bytes((motor_id, length, error)) + param_bytes
    checksum = calculate_checksum(motor_id, length, error, param_bytes)
    return HEADER + body + bytes((checksum,))


def decode_status_packet(frame: bytes | bytearray | memoryview) -> StatusPacket:
    """Decode a status frame without treating ``error`` as an instruction."""

    raw = bytes(frame)
    if len(raw) < 6:
        raise PacketLengthError("status packet is shorter than the Protocol 0 minimum")
    if raw[:2] != HEADER:
        raise FeetechProtocolError("status packet header is not 0xFFFF")
    length = raw[3]
    if length < 2:
        raise PacketLengthError("status length must include error and checksum")
    expected_size = 4 + length
    if len(raw) != expected_size:
        raise PacketLengthError(f"status length expects {expected_size} bytes, got {len(raw)}")
    motor_id = raw[2]
    error = raw[4]
    params = raw[5:-1]
    expected_checksum = calculate_checksum(motor_id, length, error, params)
    if raw[-1] != expected_checksum:
        raise ChecksumError(
            f"status checksum mismatch: received 0x{raw[-1]:02x}, "
            f"expected 0x{expected_checksum:02x}"
        )
    return StatusPacket(motor_id, error, params)


def _coerce_register_bytes(value: bytes | bytearray | memoryview | Iterable[int], data_length: int) -> bytes:
    raw = _bytes(value, "register data")
    if len(raw) != data_length:
        raise FeetechProtocolError(
            f"register data has length {len(raw)}, expected {data_length}"
        )
    return raw


def encode_sync_write(
    address: int,
    data_length: int,
    values: Mapping[int, bytes | bytearray | memoryview | Iterable[int]],
) -> bytes:
    """Encode a Protocol 0 broadcast sync-write packet."""

    address, data_length = _address_and_length(address, data_length)
    if not values:
        raise FeetechProtocolError("sync write needs at least one motor")
    params = bytearray((address, data_length))
    seen: set[int] = set()
    for motor_id, value in values.items():
        motor_id = _byte(motor_id, "motor_id")
        if motor_id in seen or motor_id == BROADCAST_ID:
            raise FeetechProtocolError("sync write motor IDs must be unique non-broadcast IDs")
        seen.add(motor_id)
        params.append(motor_id)
        params.extend(_coerce_register_bytes(value, data_length))
    return encode_packet(BROADCAST_ID, INST_SYNC_WRITE, params)


def decode_sync_write(packet_or_frame: Packet | bytes | bytearray | memoryview) -> SyncWrite:
    packet = packet_or_frame if isinstance(packet_or_frame, Packet) else decode_packet(packet_or_frame)
    if packet.motor_id != BROADCAST_ID or packet.instruction != INST_SYNC_WRITE:
        raise FeetechProtocolError("packet is not a broadcast sync write")
    if len(packet.params) < 2:
        raise FeetechProtocolError("sync write has no address and data length")
    address, data_length = _address_and_length(packet.params[0], packet.params[1])
    entries = packet.params[2:]
    stride = data_length + 1
    if not entries or len(entries) % stride:
        raise FeetechProtocolError("sync write payload is not aligned to motor records")
    values: dict[int, bytes] = {}
    for offset in range(0, len(entries), stride):
        motor_id = entries[offset]
        if motor_id == BROADCAST_ID or motor_id in values:
            raise FeetechProtocolError("sync write contains a duplicate or broadcast motor ID")
        values[motor_id] = bytes(entries[offset + 1 : offset + stride])
    return SyncWrite(address, data_length, values)


def encode_sync_read(address: int, data_length: int, motor_ids: Iterable[int]) -> bytes:
    """Encode a Protocol 0 broadcast sync-read packet."""

    address, data_length = _address_and_length(address, data_length)
    ids = tuple(_byte(motor_id, "motor_id") for motor_id in motor_ids)
    if not ids or len(set(ids)) != len(ids) or BROADCAST_ID in ids:
        raise FeetechProtocolError("sync read needs unique non-broadcast motor IDs")
    return encode_packet(BROADCAST_ID, INST_SYNC_READ, bytes((address, data_length, *ids)))


def decode_sync_read(packet_or_frame: Packet | bytes | bytearray | memoryview) -> SyncRead:
    packet = packet_or_frame if isinstance(packet_or_frame, Packet) else decode_packet(packet_or_frame)
    if packet.motor_id != BROADCAST_ID or packet.instruction != INST_SYNC_READ:
        raise FeetechProtocolError("packet is not a broadcast sync read")
    if len(packet.params) < 3:
        raise FeetechProtocolError("sync read needs an address, length, and motor ID")
    address, data_length = _address_and_length(packet.params[0], packet.params[1])
    ids = tuple(packet.params[2:])
    if len(set(ids)) != len(ids) or BROADCAST_ID in ids:
        raise FeetechProtocolError("sync read contains duplicate or broadcast motor IDs")
    return SyncRead(address, data_length, ids)


def pop_frames(buffer: bytearray) -> list[bytes]:
    """Pop complete frames from a serial byte buffer without validating them."""

    frames: list[bytes] = []
    while True:
        header_at = buffer.find(HEADER)
        if header_at < 0:
            if buffer[-1:] == HEADER[:1]:
                del buffer[:-1]
            else:
                buffer.clear()
            return frames
        if header_at:
            del buffer[:header_at]
        if len(buffer) < 4:
            return frames
        frame_size = 4 + buffer[3]
        if len(buffer) < frame_size:
            return frames
        frames.append(bytes(buffer[:frame_size]))
        del buffer[:frame_size]
