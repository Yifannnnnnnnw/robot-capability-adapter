"""A minimal virtual Feetech device speaking raw Protocol 0 bytes.

The class intentionally does not implement ``scservo_sdk`` or any LeRobot
public API.  It is only a wire peer for a later Linux integration test.
"""

from __future__ import annotations

import os
import pty
import select
import threading
import tty
from collections.abc import Iterable

from .feetech_wire import (
    BROADCAST_ID,
    FIRMWARE_MAJOR_VERSION,
    FIRMWARE_MINOR_VERSION,
    GOAL_POSITION,
    INST_ACTION,
    INST_PING,
    INST_READ,
    INST_SYNC_READ,
    INST_SYNC_WRITE,
    INST_WRITE,
    MODEL_NUMBER,
    PRESENT_POSITION,
    SO101_MOTOR_IDS,
    FeetechProtocolError,
    Packet,
    decode_sync_read,
    decode_sync_write,
    decode_packet,
    encode_status_packet,
    pop_frames,
)


def _little_endian(value: int, width: int) -> bytes:
    return int(value).to_bytes(width, "little", signed=False)


class VirtualFeetechDevice:
    """Handle just enough STS3215 registers for connect/configure/read/write."""

    def __init__(
        self,
        motor_ids: Iterable[int] = SO101_MOTOR_IDS.values(),
        present_positions: dict[int, int] | None = None,
    ) -> None:
        ids = tuple(motor_ids)
        if not ids or len(set(ids)) != len(ids) or BROADCAST_ID in ids:
            raise ValueError("virtual motors need unique non-broadcast IDs")
        self.motor_ids = ids
        positions = present_positions or {motor_id: 2048 for motor_id in ids}
        if set(positions) != set(ids):
            raise ValueError("present_positions must cover exactly the virtual motor IDs")
        self._registers: dict[int, dict[int, bytes]] = {}
        for motor_id in ids:
            position = positions[motor_id]
            if not 0 <= position <= 4095:
                raise ValueError("STS3215 positions must be in the 12-bit range")
            self._registers[motor_id] = {
                MODEL_NUMBER[0]: _little_endian(777, MODEL_NUMBER[1]),
                FIRMWARE_MAJOR_VERSION[0]: b"\x01",
                FIRMWARE_MINOR_VERSION[0]: b"\x01",
                5: bytes((motor_id,)),
                6: b"\x00",  # 1,000,000 baud
                7: b"\x00",  # minimum return delay
                9: _little_endian(0, 2),
                11: _little_endian(4095, 2),
                18: b"\x00",  # phase
                21: b"\x00",  # P
                22: b"\x00",  # D
                23: b"\x00",  # I
                31: _little_endian(0, 2),
                33: b"\x00",  # position mode
                40: b"\x00",  # torque disabled while configuring
                41: b"\x00",
                GOAL_POSITION[0]: _little_endian(position, GOAL_POSITION[1]),
                44: _little_endian(0, 2),
                46: _little_endian(0, 2),
                48: _little_endian(1000, 2),
                55: b"\x00",
                PRESENT_POSITION[0]: _little_endian(position, PRESENT_POSITION[1]),
                58: _little_endian(0, 2),
                60: _little_endian(0, 2),
                62: b"\x4a",  # nominal 7.4V; informational only
                63: b"\x19",  # informational only
                65: b"\x00",
                66: b"\x00",
                69: _little_endian(0, 2),
                85: b"\x00",
            }

    @property
    def goal_positions(self) -> dict[int, int]:
        return {motor_id: self._read_int(motor_id, GOAL_POSITION[0], 2) for motor_id in self.motor_ids}

    @property
    def present_positions(self) -> dict[int, int]:
        return {
            motor_id: self._read_int(motor_id, PRESENT_POSITION[0], 2)
            for motor_id in self.motor_ids
        }

    def _read(self, motor_id: int, address: int, data_length: int) -> bytes:
        value = self._registers[motor_id].get(address, b"")
        return value[:data_length].ljust(data_length, b"\x00")

    def _read_int(self, motor_id: int, address: int, data_length: int) -> int:
        return int.from_bytes(self._read(motor_id, address, data_length), "little")

    def _write(self, motor_id: int, address: int, data: bytes) -> None:
        self._registers[motor_id][address] = bytes(data)
        if address == GOAL_POSITION[0] and len(data) == GOAL_POSITION[1]:
            # A deterministic wire probe has no dynamics; make the observed
            # position follow the command immediately for round-trip checks.
            self._registers[motor_id][PRESENT_POSITION[0]] = bytes(data)

    def handle_frame(self, frame: bytes) -> tuple[bytes, ...]:
        return self.handle_packet(decode_packet(frame))

    def handle_packet(self, packet: Packet) -> tuple[bytes, ...]:
        if packet.instruction == INST_PING:
            if packet.motor_id == BROADCAST_ID:
                return tuple(encode_status_packet(motor_id) for motor_id in self.motor_ids)
            if packet.motor_id in self._registers:
                return (encode_status_packet(packet.motor_id),)
            return ()

        if packet.instruction == INST_READ:
            if packet.motor_id not in self._registers or len(packet.params) != 2:
                return ()
            address, data_length = packet.params
            return (
                encode_status_packet(
                    packet.motor_id, params=self._read(packet.motor_id, address, data_length)
                ),
            )

        if packet.instruction == INST_WRITE:
            if packet.motor_id not in self._registers or len(packet.params) < 2:
                return ()
            self._write(packet.motor_id, packet.params[0], packet.params[1:])
            return (encode_status_packet(packet.motor_id),)

        if packet.instruction == INST_SYNC_WRITE:
            sync_write = decode_sync_write(packet)
            for motor_id, data in sync_write.values.items():
                if motor_id in self._registers:
                    if len(data) != sync_write.data_length:
                        raise FeetechProtocolError("sync write data length mismatch")
                    self._write(motor_id, sync_write.address, data)
            return ()

        if packet.instruction == INST_SYNC_READ:
            sync_read = decode_sync_read(packet)
            return tuple(
                encode_status_packet(
                    motor_id,
                    params=self._read(motor_id, sync_read.address, sync_read.data_length),
                )
                for motor_id in sync_read.motor_ids
                if motor_id in self._registers
            )

        if packet.instruction == INST_ACTION:
            return ()

        # The real SDK will report this as an invalid/unsupported instruction;
        # dropping it keeps this small probe from inventing an SDK error model.
        return ()


class VirtualFeetechPTY:
    """Linux/macOS PTY server that exposes ``VirtualFeetechDevice`` as bytes."""

    def __init__(self, device: VirtualFeetechDevice | None = None) -> None:
        self.device = device or VirtualFeetechDevice()
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> str:
        if self._slave_fd is None:
            raise RuntimeError("PTY is not open")
        return os.ttyname(self._slave_fd)

    def __enter__(self) -> "VirtualFeetechPTY":
        self._master_fd, self._slave_fd = pty.openpty()
        tty.setraw(self._slave_fd)
        os.set_blocking(self._master_fd, False)
        self._thread = threading.Thread(target=self._serve, name="virtual-feetech", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        for fd_name in ("_slave_fd", "_master_fd"):
            fd = getattr(self, fd_name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, fd_name, None)
        self._thread = None

    def _serve(self) -> None:
        assert self._master_fd is not None
        buffer = bytearray()
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self._master_fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                chunk = os.read(self._master_fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer.extend(chunk)
            for frame in pop_frames(buffer):
                try:
                    responses = self.device.handle_frame(frame)
                except FeetechProtocolError:
                    # A real device does not act on a frame with a bad wire
                    # checksum.  There is deliberately no synthetic response.
                    continue
                for response in responses:
                    try:
                        os.write(self._master_fd, response)
                    except OSError:
                        return
