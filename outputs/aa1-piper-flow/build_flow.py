#!/usr/bin/env python3
"""Embed one cleaned AA1 Piper flow dataset into the HTML fragment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "flow-template.html"
DEFAULT_OUTPUT = HERE / "aa1-piper-flow.html"
MARKER = "__FLOW_DATA__"
MAX_BYTES = 1_000_000


def _read_dataset(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("flow dataset must be a JSON object")
    stages = value.get("stages", [])
    if not isinstance(stages, list):
        raise SystemExit("flow dataset field 'stages' must be an array")
    return value


def _script_safe_json(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build(dataset_path: Path, output_path: Path) -> int:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise SystemExit(f"template must contain exactly one {MARKER} marker")
    payload = _script_safe_json(_read_dataset(dataset_path))
    rendered = template.replace(MARKER, payload)
    size = len(rendered.encode("utf-8"))
    if size >= MAX_BYTES:
        raise SystemExit(
            f"rendered fragment is {size} bytes; reduce recorded fields below {MAX_BYTES} bytes"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="cleaned JSON flow dataset")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"HTML fragment destination (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    size = build(args.dataset, args.output)
    print(f"wrote {args.output} ({size} bytes)")


if __name__ == "__main__":
    main()
