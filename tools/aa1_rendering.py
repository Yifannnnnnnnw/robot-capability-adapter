"""Shared input paths for the three AA1 robot illustration scripts."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AA1_ROOT = ROOT / "AA1"
OUTPUT_ROOT = ROOT / "outputs" / "aa1-renders"


def add_robot_arguments(parser: argparse.ArgumentParser, output_kind: str) -> None:
    parser.add_argument("--aa1-root", type=Path, default=AA1_ROOT)
    parser.add_argument(
        "--robot", action="append", default=[],
        help="AA1 robot_zoo.yaml ID; repeat to select multiple robots (default: all)",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT / output_kind)


def robot_scenes(aa1_root: Path, requested: list[str]) -> dict[str, Path]:
    aa1_root = aa1_root.resolve()
    catalog_path = aa1_root / "autoadapter_bench" / "spec" / "robot_zoo.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    scenes = {robot["id"]: aa1_root / robot["mjcf"] for robot in catalog["robots"]}
    if requested:
        missing = set(requested) - scenes.keys()
        if missing:
            raise SystemExit(f"unknown AA1 robot(s): {', '.join(sorted(missing))}")
        scenes = {name: scenes[name] for name in sorted(set(requested))}
    if not scenes:
        raise SystemExit(f"no robots found in {catalog_path}")
    for scene_path in scenes.values():
        if not scene_path.is_file():
            raise FileNotFoundError(scene_path)
    return scenes
