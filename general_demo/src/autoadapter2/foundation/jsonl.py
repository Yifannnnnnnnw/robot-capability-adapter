from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from .canonical import canonical_bytes
from .errors import IntegrityError
from .hashing import content_hash


class AppendOnlyJSONL:
    """Hash-chained JSONL with a local terminal state witness."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.state_path = Path(f"{self.path}.state")

    def _read_lines(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise IntegrityError("append-only JSONL must end with a newline")
        lines = raw.splitlines()
        result: list[dict[str, Any]] = []
        for number, line in enumerate(lines, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntegrityError(f"invalid JSONL line {number}") from exc
            if not isinstance(value, dict):
                raise IntegrityError(f"JSONL line {number} is not an object")
            result.append(value)
        return result

    @staticmethod
    def _line_hash(body: dict[str, Any]) -> str:
        return content_hash(canonical_bytes(body))

    def _verify_state_witness(self, length: int, tail_hash: str | None) -> None:
        if length == 0 and not self.state_path.exists():
            return
        if not self.state_path.exists():
            raise IntegrityError("append-only JSONL terminal witness is missing")
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError("invalid JSONL terminal witness") from exc
        if state != {"length": length, "tail_hash": tail_hash}:
            raise IntegrityError("append-only JSONL was truncated or its witness changed")

    def verify(self) -> list[dict[str, Any]]:
        lines = self._read_lines()
        previous: str | None = None
        events: list[dict[str, Any]] = []
        for expected_seq, line in enumerate(lines):
            if line.get("seq") != expected_seq:
                raise IntegrityError("JSONL sequence is not contiguous")
            if line.get("prev_hash") != previous:
                raise IntegrityError("JSONL hash chain is broken")
            line_hash = line.get("line_hash")
            body = {key: value for key, value in line.items() if key != "line_hash"}
            if line_hash != self._line_hash(body):
                raise IntegrityError("JSONL line hash mismatch")
            if "event" not in line:
                raise IntegrityError("JSONL line has no event")
            events.append(line["event"])
            previous = line_hash
        self._verify_state_witness(len(lines), previous)
        return events

    def append(self, event: Any) -> int:
        events = self.verify()
        previous = None
        if self.path.exists() and self.path.read_bytes():
            previous = self._read_lines()[-1]["line_hash"]
        sequence = len(events)
        body = {"seq": sequence, "prev_hash": previous, "event": event}
        line = {**body, "line_hash": self._line_hash(body)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as stream:
            stream.write(canonical_bytes(line) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        state = {"length": sequence + 1, "tail_hash": line["line_hash"]}
        temporary = Path(f"{self.state_path}.tmp")
        temporary.write_bytes(canonical_bytes(state))
        os.replace(temporary, self.state_path)
        return sequence

    def records(self) -> list[dict[str, Any]]:
        return self.verify()

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.records())
