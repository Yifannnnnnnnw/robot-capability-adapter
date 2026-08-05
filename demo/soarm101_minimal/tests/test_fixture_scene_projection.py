from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from soarm_demo.bridge.deterministic_tabletop import DeterministicTabletopRuntime
from soarm_demo.oracle import FixtureDemoEnvironment, FixtureValidationEnvironment


ROOT = Path(__file__).resolve().parents[1]
STATES = ROOT / "private/task_library/soarm101_tabletop/v1/initial_states"


def _state(filename: str) -> dict[str, Any]:
    return json.loads((STATES / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("filename", "object_ids", "receptacle_ids"),
    (
        ("soarm101_p0_push_cube_to_region.json", {"red_cube"}, set()),
        ("soarm101_p0_push_cylinder_lateral.json", {"orange_cylinder"}, set()),
        ("soarm101_p0_place_cube_in_tray.json", {"red_cube"}, {"tray"}),
        (
            "soarm101_p0_place_cube_in_bowl_new_region.json",
            {"purple_cube"},
            {"bowl"},
        ),
        (
            "soarm101_p0_place_two_objects_in_tray.json",
            {"red_cube", "blue_cylinder"},
            {"tray"},
        ),
        (
            "soarm101_p0_sort_two_cubes_matching_trays.json",
            {"red_cube", "blue_cube"},
            {"red_tray", "blue_tray"},
        ),
    ),
)
def test_fixture_demo_projects_asset_ref_instances_without_mutating_them(
    filename: str,
    object_ids: set[str],
    receptacle_ids: set[str],
) -> None:
    instance = _state(filename)
    frozen_copy = deepcopy(instance)
    assert all("kind" not in body for body in instance["bodies"])

    environment = FixtureDemoEnvironment()
    try:
        environment.reset(instance)
        snapshot = environment.runtime.snapshot()
    finally:
        environment.close()

    assert instance == frozen_copy
    assert set(snapshot["object_positions_m"]) == object_ids
    assert set(snapshot["receptacles"]) == receptacle_ids
    for receptacle in snapshot["receptacles"].values():
        assert receptacle["kind"] in {"tray", "bowl"}
        if receptacle["kind"] == "tray":
            assert len(receptacle["inner_size_m"]) == 2
        else:
            assert receptacle["inner_radius_m"] > 0.0


def test_fixture_validation_uses_the_same_private_catalog_projection() -> None:
    initial_state = _state("soarm101_p0_place_cube_in_tray.json")
    frozen_copy = deepcopy(initial_state)
    environment = FixtureValidationEnvironment()
    try:
        environment.reset(initial_state)
        snapshot = environment.runtime.snapshot()
    finally:
        environment.close()

    assert initial_state == frozen_copy
    assert set(snapshot["object_positions_m"]) == {"red_cube"}
    assert set(snapshot["receptacles"]) == {"tray"}
    assert snapshot["receptacles"]["tray"]["inner_size_m"] == [0.09, 0.08]


def test_deterministic_runtime_does_not_implicitly_load_the_scene_catalog() -> None:
    instance = _state("soarm101_p0_place_cube_in_tray.json")
    runtime = DeterministicTabletopRuntime()
    try:
        runtime.reset(instance)
        snapshot = runtime.snapshot()
    finally:
        runtime.disconnect()

    assert not hasattr(runtime, "_scene_catalog")
    assert set(snapshot["object_positions_m"]) == {"red_cube", "tray"}
    assert snapshot["receptacles"] == {}
