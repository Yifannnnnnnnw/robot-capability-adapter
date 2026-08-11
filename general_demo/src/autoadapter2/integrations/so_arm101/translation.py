"""Framework-private LeRobot→Feetech PTY→MuJoCo translation for SO-ARM101.

This module intentionally exposes lifecycle, wire handling, stepping, health,
reset, and close only.  It is not a robot-control API and contains no pose,
trajectory, IK, planning, recovery, or task behavior.
"""

from __future__ import annotations

import math
import os
import pty
import select
import sys
import threading
import time
import tty
from errno import EIO
from pathlib import Path
from typing import Iterable, Protocol

from .feetech_protocol import (
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
    MOTOR_IDS,
    MOTOR_NAMES,
    PRESENT_POSITION,
    ProtocolError,
    decode_packet,
    decode_sync_read,
    decode_sync_write,
    encode_status,
    pop_frames,
)

RAW_MIN = 0
RAW_MAX = 4095

# Exact-width STS3215 subset exercised by LeRobot 0.6.0 SO101Follower.
REGISTER_WIDTHS = {
    0: 1,
    1: 1,
    3: 2,
    5: 1,
    6: 1,
    7: 1,
    9: 2,
    11: 2,
    16: 2,
    18: 1,
    21: 1,
    22: 1,
    23: 1,
    28: 2,
    31: 2,
    33: 1,
    36: 1,
    40: 1,
    41: 1,
    42: 2,
    44: 2,
    46: 2,
    48: 2,
    55: 1,
    56: 2,
    58: 2,
    60: 2,
    62: 1,
    63: 1,
    65: 1,
    66: 1,
    69: 2,
    85: 1,
}
READ_ONLY_REGISTERS = {MODEL_NUMBER[0], FIRMWARE_MAJOR_VERSION[0], FIRMWARE_MINOR_VERSION[0], PRESENT_POSITION[0]}


class PositionBackend(Protocol):
    timestep: float

    def set_goal_ticks(self, values: dict[int, int]) -> None: ...
    def present_ticks(self, motor_ids: Iterable[int]) -> dict[int, int]: ...
    def step(self, seconds: float) -> None: ...
    def reset(self) -> None: ...
    def state(self) -> dict[str, object]: ...
    def close(self) -> None: ...


def _bounded_tick(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not RAW_MIN <= value <= RAW_MAX:
        raise ProtocolError("position tick must be an integer in 0..4095")
    return value


class MuJoCoSO101Backend:
    """Map raw servo ticks only to actuator controls and current qpos to reads."""

    def __init__(self, model_path: str | Path, *, gripper_tick_increases_qpos: bool) -> None:
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - Linux integration dependency
            raise RuntimeError("mujoco==3.3.6 is required") from exc
        if getattr(mujoco, "__version__", None) != "3.3.6":
            raise RuntimeError(f"required mujoco==3.3.6, found {getattr(mujoco, '__version__', 'unknown')}")
        self._mj = mujoco
        self.model_path = Path(model_path).resolve()
        self.model = mujoco.MjModel.from_xml_path(str(self.model_path))
        self.data = mujoco.MjData(self.model)
        self.timestep = float(self.model.opt.timestep)
        self._lock = threading.RLock()
        self._gripper_tick_increases_qpos = bool(gripper_tick_increases_qpos)
        self._joint_ids = {name: self._id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in MOTOR_NAMES}
        self._actuator_ids = {name: self._id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in MOTOR_NAMES}
        self._closed = False
        self.reset()

    def _id(self, kind: object, name: str) -> int:
        identifier = int(self._mj.mj_name2id(self.model, kind, name))
        if identifier < 0:
            raise ValueError(f"MuJoCo model is missing {name!r}")
        return identifier

    def _qpos(self, name: str) -> float:
        address = int(self.model.jnt_qposadr[self._joint_ids[name]])
        return float(self.data.qpos[address])

    def _tick_to_qpos(self, name: str, tick: int) -> float:
        fraction = (_bounded_tick(tick) - RAW_MIN) / (RAW_MAX - RAW_MIN)
        if name != "gripper":
            return math.radians(-180.0 + 360.0 * fraction)
        actuator_id = self._actuator_ids[name]
        low, high = map(float, self.model.actuator_ctrlrange[actuator_id])
        if not self._gripper_tick_increases_qpos:
            fraction = 1.0 - fraction
        return low + fraction * (high - low)

    def _qpos_to_tick(self, name: str, qpos: float) -> int:
        if name != "gripper":
            fraction = (math.degrees(qpos) + 180.0) / 360.0
        else:
            actuator_id = self._actuator_ids[name]
            low, high = map(float, self.model.actuator_ctrlrange[actuator_id])
            if high <= low:
                raise ValueError("gripper actuator range is invalid")
            fraction = (qpos - low) / (high - low)
            if not self._gripper_tick_increases_qpos:
                fraction = 1.0 - fraction
        return min(RAW_MAX, max(RAW_MIN, int(round(fraction * RAW_MAX))))

    def set_goal_ticks(self, values: dict[int, int]) -> None:
        with self._lock:
            before_qpos = self.data.qpos.copy()
            before_qvel = self.data.qvel.copy()
            for motor_id, tick in values.items():
                if motor_id not in MOTOR_IDS.values():
                    raise ProtocolError(f"unknown motor ID {motor_id}")
                name = MOTOR_NAMES[motor_id - 1]
                self.data.ctrl[self._actuator_ids[name]] = self._tick_to_qpos(name, tick)
            if not (self.data.qpos == before_qpos).all() or not (self.data.qvel == before_qvel).all():
                raise RuntimeError("normal goal handling changed qpos/qvel directly")

    def present_ticks(self, motor_ids: Iterable[int]) -> dict[int, int]:
        with self._lock:
            return {
                motor_id: self._qpos_to_tick(MOTOR_NAMES[motor_id - 1], self._qpos(MOTOR_NAMES[motor_id - 1]))
                for motor_id in motor_ids
            }

    def step(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("step seconds must be finite and non-negative")
        steps = int(math.ceil(seconds / self.timestep)) if seconds else 0
        with self._lock:
            for _ in range(steps):
                self._mj.mj_step(self.model, self.data)

    def reset(self) -> None:
        with self._lock:
            self._mj.mj_resetData(self.model, self.data)
            self._mj.mj_forward(self.model, self.data)
            for name in MOTOR_NAMES:
                self.data.ctrl[self._actuator_ids[name]] = self._qpos(name)

    def state(self) -> dict[str, object]:
        with self._lock:
            gripper_actuator = self._actuator_ids["gripper"]
            return {
                "qpos": [float(value) for value in self.data.qpos],
                "qvel": [float(value) for value in self.data.qvel],
                "ctrl": [float(value) for value in self.data.ctrl],
                "named_qpos": {name: self._qpos(name) for name in MOTOR_NAMES},
                "named_ctrl": {
                    name: float(self.data.ctrl[self._actuator_ids[name]]) for name in MOTOR_NAMES
                },
                "gripper_control_range": [
                    float(self.model.actuator_ctrlrange[gripper_actuator][0]),
                    float(self.model.actuator_ctrlrange[gripper_actuator][1]),
                ],
            }

    def close(self) -> None:
        self._closed = True


class FeetechPTYTranslation:
    """Raw PTY peer beneath the real FeetechMotorsBus."""

    def __init__(self, backend: PositionBackend) -> None:
        self.backend = backend
        self._registers = self._initial_registers()
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._stop = threading.Event()
        self._worker_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._goal_condition = threading.Condition(self._lock)
        self._thread_error: BaseException | None = None
        self._closed = False
        self.accepted_goal_writes = 0
        self.rejected_frames = 0
        self.packet_count = 0

    @staticmethod
    def _initial_registers() -> dict[int, dict[int, bytes]]:
        registers: dict[int, dict[int, bytes]] = {}
        for motor_id in MOTOR_IDS.values():
            values = {address: bytes(width) for address, width in REGISTER_WIDTHS.items()}
            values.update(
                {
                    0: b"\x01",
                    1: b"\x01",
                    3: int(777).to_bytes(2, "little"),
                    5: bytes((motor_id,)),
                    9: int(RAW_MIN).to_bytes(2, "little"),
                    11: int(RAW_MAX).to_bytes(2, "little"),
                    16: int(1000).to_bytes(2, "little"),
                    28: int(1000).to_bytes(2, "little"),
                    48: int(1000).to_bytes(2, "little"),
                    62: b"\x4a",
                    63: b"\x19",
                }
            )
            registers[motor_id] = values
        return registers

    @property
    def port(self) -> str:
        if self._slave_fd is None:
            raise RuntimeError("PTY hook is not installed")
        port = os.ttyname(self._slave_fd)
        if sys.platform == "linux" and not port.startswith("/dev/pts/"):
            raise RuntimeError(f"translation did not create a Linux PTY: {port}")
        return port

    @property
    def is_open(self) -> bool:
        return self._master_fd is not None and self._slave_fd is not None and not self._stop.is_set()

    def install(self) -> str:
        if self._closed:
            raise RuntimeError("closed PTY translation instances cannot be reused")
        if self.is_open:
            raise RuntimeError("PTY hook is already installed")
        master_fd, slave_fd = pty.openpty()
        try:
            tty.setraw(slave_fd)
            os.set_blocking(master_fd, False)
        except BaseException:
            os.close(slave_fd)
            os.close(master_fd)
            raise
        self._master_fd, self._slave_fd = master_fd, slave_fd
        self._stop.clear()
        self._worker_ready.clear()
        self._thread_error = None
        self._thread = threading.Thread(target=self._serve, name="so101-feetech-pty", daemon=True)
        self._thread.start()
        if not self._worker_ready.wait(timeout=1.0):
            self.close()
            raise RuntimeError("Feetech PTY worker did not become ready")
        return self.port

    def __enter__(self) -> "FeetechPTYTranslation":
        self.install()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _validate_access(self, motor_id: int, address: int, width: int, *, write: bool) -> None:
        if motor_id not in self._registers:
            raise ProtocolError(f"unknown motor ID {motor_id}")
        expected_width = REGISTER_WIDTHS.get(address)
        if expected_width is None or expected_width != width:
            raise ProtocolError(f"register {address} width must be {expected_width}, got {width}")
        if write and address in READ_ONLY_REGISTERS:
            raise ProtocolError(f"register {address} is read-only")

    def _read(self, motor_id: int, address: int, width: int) -> bytes:
        self._validate_access(motor_id, address, width, write=False)
        if address == PRESENT_POSITION[0]:
            value = self.backend.present_ticks((motor_id,))[motor_id]
            return value.to_bytes(width, "little")
        return self._registers[motor_id][address]

    def _write_batch(self, address: int, width: int, values: dict[int, bytes]) -> None:
        decoded: dict[int, int] = {}
        for motor_id, payload in values.items():
            self._validate_access(motor_id, address, width, write=True)
            if len(payload) != width:
                raise ProtocolError("write payload width mismatch")
            if address == GOAL_POSITION[0]:
                decoded[motor_id] = _bounded_tick(int.from_bytes(payload, "little"))
        # Validate the full operation before mutating registers or controls.
        if decoded:
            self.backend.set_goal_ticks(decoded)
        for motor_id, payload in values.items():
            self._registers[motor_id][address] = bytes(payload)
        self.accepted_goal_writes += len(decoded)
        if decoded:
            self._goal_condition.notify_all()

    def handle_frame(self, frame: bytes) -> tuple[bytes, ...]:
        packet = decode_packet(frame)
        with self._lock:
            self.packet_count += 1
            if packet.instruction == INST_PING:
                if packet.motor_id == BROADCAST_ID:
                    return tuple(encode_status(motor_id) for motor_id in MOTOR_IDS.values())
                return (encode_status(packet.motor_id),) if packet.motor_id in self._registers else ()
            if packet.instruction == INST_READ:
                if len(packet.params) != 2:
                    raise ProtocolError("read requires address and width")
                if packet.motor_id not in self._registers:
                    return ()
                address, width = packet.params
                return (encode_status(packet.motor_id, self._read(packet.motor_id, address, width)),)
            if packet.instruction == INST_WRITE:
                if packet.motor_id not in self._registers:
                    return ()
                if not packet.params:
                    raise ProtocolError("write requires an address")
                address, payload = packet.params[0], packet.params[1:]
                self._write_batch(address, len(payload), {packet.motor_id: payload})
                return (encode_status(packet.motor_id),)
            if packet.instruction == INST_SYNC_WRITE:
                address, width, values = decode_sync_write(packet)
                self._write_batch(address, width, values)
                return ()
            if packet.instruction == INST_SYNC_READ:
                address, width, motor_ids = decode_sync_read(packet)
                return tuple(encode_status(motor_id, self._read(motor_id, address, width)) for motor_id in motor_ids)
            if packet.instruction == INST_ACTION:
                return ()
            return ()

    def step(self, seconds: float) -> None:
        self.backend.step(seconds)

    def wait_for_goal_writes(self, minimum_count: int, timeout_s: float) -> bool:
        """Wait for PTY bytes already written by the SDK to reach the backend."""
        deadline = time.monotonic() + timeout_s
        with self._goal_condition:
            while self.accepted_goal_writes < minimum_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._goal_condition.wait(remaining)
            return True

    def reset(self) -> None:
        with self._lock:
            self.backend.reset()
            present = self.backend.present_ticks(MOTOR_IDS.values())
            for motor_id, tick in present.items():
                self._registers[motor_id][GOAL_POSITION[0]] = tick.to_bytes(2, "little")

    def health(self) -> dict[str, object]:
        return {
            "open": self.is_open,
            "thread_error": None if self._thread_error is None else repr(self._thread_error),
            "packet_count": self.packet_count,
            "accepted_goal_writes": self.accepted_goal_writes,
            "rejected_frames": self.rejected_frames,
        }

    def _serve(self) -> None:
        assert self._master_fd is not None
        master_fd = self._master_fd
        buffer = bytearray()
        try:
            self._worker_ready.set()
            while not self._stop.is_set():
                # The pinned Feetech SDK gives a 6-byte status response roughly
                # 10 ms at 1 Mbaud.  A 50 ms polling interval made valid pings
                # intermittently time out on Linux.  Poll below that transport
                # deadline; this is response scheduling, not a hidden retry.
                readable, _, _ = select.select([master_fd], [], [], 0.001)
                if not readable:
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if self._stop.is_set() or exc.errno == EIO:
                        return
                    raise
                if not chunk:
                    return
                buffer.extend(chunk)
                for frame in pop_frames(buffer):
                    try:
                        responses = self.handle_frame(frame)
                    except ProtocolError:
                        self.rejected_frames += 1
                        continue
                    for response in responses:
                        os.write(master_fd, response)
        except Exception as exc:
            if not self._stop.is_set():
                self._thread_error = exc

    def close(self) -> None:
        if self._closed:
            if self._thread_error is not None:
                raise RuntimeError(f"Feetech PTY worker failed: {self._thread_error!r}")
            return
        self._closed = True
        self._stop.set()
        worker = self._thread
        try:
            if worker is not None:
                worker.join(timeout=5.0)
                if worker.is_alive():
                    raise RuntimeError("Feetech PTY worker did not stop within 5 seconds")
        finally:
            self._thread = None
            for attribute in ("_slave_fd", "_master_fd"):
                fd = getattr(self, attribute)
                if fd is not None:
                    try:
                        os.close(fd)
                    finally:
                        setattr(self, attribute, None)
            self.backend.close()
        if self._thread_error is not None:
            raise RuntimeError(f"Feetech PTY worker failed: {self._thread_error!r}")
