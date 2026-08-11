from __future__ import annotations

from pathlib import Path
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ImmutableError, IntegrityError
from ..foundation.hashing import content_hash
from ..foundation.jsonl import AppendOnlyJSONL
from .selection import RunSelection


class RunIndex:
    """Append-only run registrations; tamper and truncation are detected on read."""

    def __init__(self, path: str | Path):
        self.log = AppendOnlyJSONL(path)

    def register(self, selection: RunSelection | dict[str, Any]) -> str:
        if not isinstance(selection, RunSelection):
            selection = RunSelection.from_mapping(selection)
        selection_hash = content_hash(canonical_bytes(selection.to_dict()))
        for event in self.log.records():
            if event.get("type") == "run_registered" and event.get("run_id") == selection.run_id:
                raise ImmutableError(f"run {selection.run_id!r} is already registered")
        self.log.append({
            "type": "run_registered",
            "run_id": selection.run_id,
            "selection": selection.to_dict(),
            "selection_hash": selection_hash,
        })
        return selection_hash

    def selection_hash(self, selection: RunSelection) -> str:
        expected = content_hash(canonical_bytes(selection.to_dict()))
        matches = [
            event for event in self.log.records()
            if event.get("type") == "run_registered"
            and event.get("run_id") == selection.run_id
        ]
        if not matches:
            raise KeyError(selection.run_id)
        event = matches[0]
        if event.get("selection") != selection.to_dict() or event.get("selection_hash") != expected:
            raise IntegrityError("registered selection does not match the requested selection")
        return expected

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        events = self.log.records()
        if not any(item.get("run_id") == run_id for item in events
                   if item.get("type") == "run_registered"):
            raise KeyError(run_id)
        if any(item.get("run_id") == run_id for item in events
               if item.get("type") == "run_closed"):
            raise ImmutableError(f"closed run {run_id!r} rejects new events")
        self.log.append({"type": "run_event", "run_id": run_id, "event": event})

    def close(self, run_id: str) -> None:
        events = self.log.records()
        if not any(item.get("run_id") == run_id for item in events
                   if item.get("type") == "run_registered"):
            raise KeyError(run_id)
        if any(item.get("run_id") == run_id for item in events
               if item.get("type") == "run_closed"):
            raise ImmutableError(f"run {run_id!r} is already closed")
        self.append_event(run_id, {"closed": True})
        self.log.append({"type": "run_closed", "run_id": run_id})

    def records(self) -> list[dict[str, Any]]:
        return [
            event for event in self.log.records()
            if event.get("type") == "run_registered"
        ]

    def get(self, run_id: str) -> dict[str, Any]:
        matches = [event for event in self.records() if event["run_id"] == run_id]
        if not matches:
            raise KeyError(run_id)
        return matches[0]["selection"]
