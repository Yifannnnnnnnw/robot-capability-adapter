from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from soarm_demo.audit import sha256_json
from soarm_demo.bridge.mujoco_tabletop import SO101MujocoTabletopRuntime
from soarm_demo.bridge.scene_catalog import SceneAssetCatalogError
from soarm_demo.environment_resolver import resolve_generation_environment


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
STATES = ROOT / "private/task_library/soarm101_tabletop/v1/initial_states"
COMMON = STATES / "_common_reset.json"
CATALOG = (
    ROOT
    / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
)
SCENE = ROOT / "libraries/morphology/scenes/soarm101_tabletop/v1/scene.yaml"
DEMO_STATES = (
    "soarm101_p0_push_cube_to_region.json",
    "soarm101_p0_push_cylinder_lateral.json",
    "soarm101_p0_place_cube_in_tray.json",
    "soarm101_p0_place_cube_in_bowl_new_region.json",
    "soarm101_p0_place_two_objects_in_tray.json",
    "soarm101_p0_sort_two_cubes_matching_trays.json",
)


def _resolved_scene_freeze(tmp_path: Path) -> dict:
    result = resolve_generation_environment(
        {
            "morphology": ROOT / "libraries/morphology/soarm101/v1",
            "sdk_runtime": ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0",
            "tasks": ROOT / "libraries/tasks/soarm101_tabletop/v1",
            "experience": ROOT / "libraries/experience/v1",
        },
        morphology_scene=(
            ROOT / "libraries/morphology/scenes/soarm101_tabletop/v1"
        ),
        libraries_root=ROOT / "libraries",
        output_path=tmp_path / "generation_environment_freeze.json",
        schemas_root=ROOT / "schemas",
    )
    return json.loads(result.freeze_path.read_text(encoding="utf-8"))


def test_actual_mujoco_tabletop_resets_all_six_frozen_demo_instances() -> None:
    pytest.importorskip("mujoco")
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    runtime.connect(calibrate=False)
    try:
        for filename in DEMO_STATES:
            state = json.loads((STATES / filename).read_text(encoding="utf-8"))
            runtime.reset(state)
            runtime.advance(0.025)
            snapshot = runtime.world_snapshot()
            expected_objects = {
                item["id"]
                for item in state["bodies"]
                if _spec_runtime_kind(catalog, item) in {"cube", "cylinder"}
            }
            expected_receptacles = {
                item["id"]
                for item in state["bodies"]
                if _spec_runtime_kind(catalog, item) in {"tray", "bowl"}
            }
            assert runtime.current_task_id == state["task_id"]
            assert set(snapshot["objects"]) == expected_objects
            assert set(snapshot["receptacles"]) == expected_receptacles
            assert snapshot["finite_state"] is True
            assert snapshot["simulation_time_s"] == pytest.approx(0.025)
            assert runtime.model.nq == 6 + 7 * len(expected_objects)
    finally:
        runtime.disconnect()


def test_actual_mujoco_tabletop_records_action_fk_contact_and_world_trace(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    trace = tmp_path / "mujoco_tabletop_trace.jsonl"
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        initial_state=STATES / "soarm101_p0_push_cube_to_region.json",
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
        trace_path=trace,
    )
    runtime.connect(calibrate=False)
    try:
        accepted = runtime.send_action({"shoulder_pan.pos": 5.0, "gripper.pos": 50.0})
        runtime.advance(0.25)
        snapshot = runtime.world_snapshot()
        observation = runtime.get_observation()
    finally:
        runtime.disconnect()

    assert accepted == {"shoulder_pan.pos": 5.0, "gripper.pos": 50.0}
    assert observation["shoulder_pan.pos"] == pytest.approx(5.0, abs=0.1)
    assert len(snapshot["end_effector_position_m"]) == 3
    assert all(isinstance(value, float) for value in snapshot["end_effector_position_m"])
    assert any(
        {contact["body1"], contact["body2"]} == {"world", "red_cube"}
        for contact in snapshot["contacts"]
    )
    assert any(contact["normal_force_n"] >= 0.0 for contact in snapshot["contacts"])

    memory_events = runtime.trace_events
    assert any(event["event"] == "action" for event in memory_events)
    phases = {
        event.get("phase")
        for event in memory_events
        if event["event"] == "world_state"
    }
    assert {"after_action_target_write", "after_advance"} <= phases
    disk_events = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "action" for event in disk_events)
    assert any(
        event["event"] == "world_state" and event.get("phase") == "after_advance"
        for event in disk_events
    )


def test_actual_mujoco_tabletop_does_not_overclaim_task_reachability() -> None:
    pytest.importorskip("mujoco")
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        initial_state=STATES / "soarm101_p0_place_cube_in_bowl_new_region.json",
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    scope = runtime.evidence_scope()
    assert scope["runtime"] == "actual_mujoco_mjmodel_mjdata"
    assert scope["capability_status"]["G1.joint_target_control"] == (
        "physically_executable_in_mujoco"
    )
    assert scope["capability_status"]["G3.tabletop_tasks"] == (
        "scene_physics_available_but_task_success_not_certified"
    )
    assert scope["task_completion_claim"] == "not_probed"


def _asset_ref(catalog: dict, runtime_kind: str, variant_id: str) -> str:
    matches = [
        item
        for item in catalog["assets"]
        if item["runtime_kind"] == runtime_kind
        and variant_id in {variant["variant_id"] for variant in item["variants"]}
    ]
    assert matches
    asset = matches[0]
    return (
        f"morphology.scene_asset/{asset['asset_id']}@"
        f"{catalog['version']}#{variant_id}"
    )


def _spec_runtime_kind(catalog: dict, spec: dict) -> str:
    if "kind" in spec:
        return str(spec["kind"])
    ref = str(spec["asset_ref"])
    asset_id = ref.split("/", 1)[1].split("@", 1)[0]
    matches = [
        asset["runtime_kind"]
        for asset in catalog["assets"]
        if asset["asset_id"] == asset_id
    ]
    assert len(matches) == 1
    return str(matches[0])


def test_catalog_asset_variants_drive_distinct_mujoco_model_rgba() -> None:
    mujoco = pytest.importorskip("mujoco")
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    state = {
        "task_id": "catalog_variant_probe",
        "bodies": [
            {
                "asset_ref": _asset_ref(catalog, "cube", variant),
                "id": f"{variant}_cube",
                "position_m": [0.28 + index * 0.045, -0.08, 0.037],
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            }
            for index, variant in enumerate(("red", "blue", "green"))
        ],
        "markers": [
            {
                "asset_ref": _asset_ref(catalog, "planar_circle", "green"),
                "id": "goal",
                "center_m": [0.40, 0.06, 0.0205],
            }
        ],
    }
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        initial_state=state,
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        auto_step=False,
    )
    expected = {
        "red_cube_collision": [0.80, 0.15, 0.12, 1.0],
        "blue_cube_collision": [0.12, 0.32, 0.82, 1.0],
        "green_cube_collision": [0.12, 0.65, 0.25, 1.0],
        "marker_goal": [0.10, 0.80, 0.25, 0.35],
    }
    actual = {}
    for geom_name, rgba in expected.items():
        geom_id = mujoco.mj_name2id(
            runtime.model,
            mujoco.mjtObj.mjOBJ_GEOM,
            geom_name,
        )
        assert geom_id >= 0
        actual[geom_name] = [float(value) for value in runtime.model.geom_rgba[geom_id]]
        assert actual[geom_name] == pytest.approx(rgba)
    assert len({tuple(values) for values in actual.values()}) == len(actual)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"asset_ref": "morphology.scene_asset/not_in_catalog@1.0.0#red"}, "unknown asset"),
        ({"kind": "tray"}, "inline kind"),
        ({"size_m": [0.034, 0.034, 0.034]}, "unsafe instance override"),
        ({"material_rgba": [0.80, 0.15, 0.12, 1.0]}, "inline material_rgba"),
        ({"physics": {"friction": [0.0, 0.0, 0.0]}}, "unsafe instance override"),
    ),
)
def test_catalog_mode_rejects_unknown_mismatched_or_inline_asset_authority(
    mutation: dict,
    message: str,
) -> None:
    pytest.importorskip("mujoco")
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    body = {
        "asset_ref": _asset_ref(catalog, "cube", "red"),
        "id": "probe_cube",
        "position_m": [0.30, 0.0, 0.037],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
    }
    body.update(mutation)
    with pytest.raises(SceneAssetCatalogError, match=message):
        SO101MujocoTabletopRuntime(
            MODEL,
            common_reset=COMMON,
            initial_state={"task_id": "unsafe_asset", "bodies": [body], "markers": []},
            scene_catalog=CATALOG,
            require_scene_catalog=True,
            auto_step=False,
        )


def test_strict_catalog_compiles_every_current_task_instance() -> None:
    pytest.importorskip("mujoco")
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    for state_path in sorted(STATES.glob("soarm101_*.json")):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        runtime.reset(state)
        snapshot = runtime.world_snapshot()
        expected_objects = {
            body["id"]
            for body in state["bodies"]
            if _spec_runtime_kind(catalog, body) in {"cube", "cylinder"}
        }
        expected_receptacles = {
            body["id"]
            for body in state["bodies"]
            if _spec_runtime_kind(catalog, body) in {"tray", "bowl"}
        }
        assert set(snapshot["objects"]) == expected_objects
        assert set(snapshot["receptacles"]) == expected_receptacles
        assert snapshot["finite_state"] is True


def test_formal_catalog_mode_requires_explicit_refs_for_table_bodies_and_markers() -> None:
    pytest.importorskip("mujoco")
    bad_common = json.loads(COMMON.read_text(encoding="utf-8"))
    bad_common["table"].pop("asset_ref")
    with pytest.raises(SceneAssetCatalogError, match="requires explicit asset_ref"):
        SO101MujocoTabletopRuntime(
            MODEL,
            common_reset=bad_common,
            scene_catalog=CATALOG,
            require_scene_catalog=True,
            require_explicit_asset_refs=True,
            auto_step=False,
        )


def test_kind_only_legacy_fixture_remains_backward_compatible_without_catalog() -> None:
    pytest.importorskip("mujoco")
    common = json.loads(COMMON.read_text(encoding="utf-8"))
    common["table"].pop("asset_ref")
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=common,
        initial_state={
            "task_id": "legacy_kind_only",
            "bodies": [
                {
                    "id": "legacy_cube",
                    "kind": "cube",
                    "position_m": [0.30, 0.0, 0.037],
                    "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "size_m": [0.034, 0.034, 0.034],
                    "mass_kg": 0.025,
                }
            ],
            "markers": [],
        },
        auto_step=False,
    )
    assert set(runtime.world_snapshot()["objects"]) == {"legacy_cube"}


def test_formal_catalog_scene_compiles_refs_without_inline_physical_fields() -> None:
    pytest.importorskip("mujoco")
    scene = yaml.safe_load(SCENE.read_text(encoding="utf-8"))
    public = scene["public_simulation"]
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=public["common_reset"],
        initial_state=public["smoke_instance"],
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    snapshot = runtime.world_snapshot()
    assert set(snapshot["objects"]) == {"public_red_cube", "public_blue_cylinder"}
    assert set(snapshot["receptacles"]) == {"public_tray", "public_bowl"}
    assert snapshot["finite_state"] is True
    assert runtime.model.nq == 6 + 2 * 7


def test_path_backed_catalog_is_rehashed_before_each_isolated_compile(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    selected = tmp_path / "primitive_catalog.yaml"
    original = CATALOG.read_bytes()
    selected.write_bytes(original)
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        scene_catalog=selected,
        auto_step=False,
    )
    selected.write_bytes(original + b"\n")
    with pytest.raises(SceneAssetCatalogError, match="changed after selection"):
        runtime.reset({})


def test_catalog_compile_verifies_detached_freeze_and_every_asset_variant_hash(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    freeze = _resolved_scene_freeze(tmp_path)
    scene = yaml.safe_load(SCENE.read_text(encoding="utf-8"))["public_simulation"]
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=scene["common_reset"],
        initial_state=scene["smoke_instance"],
        scene_catalog=CATALOG,
        scene_freeze=freeze,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    assert runtime.scene_catalog_binding is not None
    runtime.reset(scene["smoke_instance"])
    assert runtime.world_snapshot()["finite_state"] is True

    tampered = json.loads(json.dumps(freeze))
    descriptors = tampered["manifest"]["bindings"]["morphology"][
        "scene_asset_catalog"
    ]["asset_descriptor_sha256"]
    first_ref = sorted(descriptors)[0]
    descriptors[first_ref] = "0" * 64
    tampered["manifest_sha256"] = sha256_json(tampered["manifest"])
    with pytest.raises(SceneAssetCatalogError, match="asset descriptors mismatch"):
        SO101MujocoTabletopRuntime(
            MODEL,
            common_reset=scene["common_reset"],
            initial_state=scene["smoke_instance"],
            scene_catalog=CATALOG,
            scene_freeze=tampered,
            require_scene_catalog=True,
            require_explicit_asset_refs=True,
            auto_step=False,
        )


@pytest.mark.parametrize("target", ("model", "mesh"))
def test_freeze_bound_runtime_rejects_robot_bytes_tampered_after_construction(
    tmp_path: Path,
    target: str,
) -> None:
    pytest.importorskip("mujoco")
    freeze = _resolved_scene_freeze(tmp_path)
    selected_libraries = tmp_path / "selected/libraries"
    copied_model_root = (
        selected_libraries / "morphology/soarm101/v1/model"
    )
    shutil.copytree(MODEL.parent, copied_model_root)
    copied_model = copied_model_root / MODEL.name
    scene = yaml.safe_load(SCENE.read_text(encoding="utf-8"))["public_simulation"]
    runtime = SO101MujocoTabletopRuntime(
        copied_model,
        common_reset=scene["common_reset"],
        initial_state=scene["smoke_instance"],
        scene_catalog=CATALOG,
        scene_freeze=freeze,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )

    if target == "model":
        tampered = copied_model
        tampered.write_bytes(tampered.read_bytes() + b"\n<!-- post-freeze drift -->\n")
    else:
        frozen_mesh = freeze["manifest"]["bindings"]["morphology"][
            "robot_model"
        ]["referenced_files"][0]
        tampered = selected_libraries.joinpath(*Path(frozen_mesh["path"]).parts)
        tampered.write_bytes(tampered.read_bytes() + b"\npost-freeze-drift\n")

    with pytest.raises(SceneAssetCatalogError, match="hash mismatch"):
        runtime.reset(scene["smoke_instance"])


def test_resolved_instance_evidence_is_hash_bound_detached_and_reset_specific() -> None:
    pytest.importorskip("mujoco")
    runtime = SO101MujocoTabletopRuntime(
        MODEL,
        common_reset=COMMON,
        initial_state=STATES / "soarm101_p0_place_cube_in_tray.json",
        scene_catalog=CATALOG,
        require_scene_catalog=True,
        require_explicit_asset_refs=True,
        auto_step=False,
    )
    first = runtime.resolved_instance_evidence
    detached_first = dict(first)
    first_binding = detached_first.pop("binding_sha256")
    assert first_binding == sha256_json(detached_first)
    red_cube = next(item for item in first["bodies"] if item["id"] == "red_cube")
    assert red_cube["runtime_kind"] == "cube"
    assert red_cube["geometry_profile"] == {
        "mass_kg": 0.025,
        "size_m": [0.034, 0.034, 0.034],
    }
    assert red_cube["pose"] == {
        "position_m": [0.31, 0.09, 0.037],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
    }
    assert len(red_cube["asset_descriptor_sha256"]) == 64

    first["bodies"][0]["geometry_profile"]["mass_kg"] = 999.0
    assert runtime.resolved_instance_evidence["bodies"][0]["geometry_profile"].get(
        "mass_kg"
    ) != 999.0

    runtime.reset(STATES / "soarm101_p0_place_cylinder_in_tray.json")
    second = runtime.resolved_instance_evidence
    assert second["binding_sha256"] != first_binding
    detached_second = dict(second)
    second_binding = detached_second.pop("binding_sha256")
    assert second_binding == sha256_json(detached_second)
    assert {item["runtime_kind"] for item in second["bodies"]} == {
        "cylinder",
        "tray",
    }
    reset_events = [
        event for event in runtime.trace_events if event["event"] == "tabletop_reset"
    ]
    assert reset_events[-1]["resolved_instance_binding_sha256"] == second_binding
