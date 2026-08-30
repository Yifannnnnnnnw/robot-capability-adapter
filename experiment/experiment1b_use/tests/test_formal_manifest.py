from __future__ import annotations

from collections import Counter
import sys
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from runtime.b2 import DEFAULT_MANIFEST_PATH, resolve_manifest


def test_formal_manifest_has_mechanical_readiness_and_exact_210_units() -> None:
    manifest = resolve_manifest(DEFAULT_MANIFEST_PATH)

    assert manifest.provider_formal_ready is True
    assert manifest.formal_dispatch_enabled is True
    assert manifest.blockers == ()
    m8 = manifest.provider_pins["M8"]
    assert m8["exact_model_id"] == "openai.gpt-5.6-sol"
    assert m8["provider_model_revision"] is None
    assert m8["upstream_revision_status"] == "not_independently_verifiable"
    assert m8["context_limit_tokens"] == 1_050_000
    assert m8["provider_max_output_tokens"] == 128_000
    assert "endpoint_base_url" not in m8
    route = manifest.provider_route_profiles["M8"]
    assert route.profile_id == "holisticai-gateway-long-request-eu-west-2-v1"
    assert route.endpoint_url.endswith("/v1/chat/completions")
    assert route.maximum_request_timeout_s == 120
    assert m8["price_snapshot"]["cost_basis"] == (
        "public_standard_reference_estimate"
    )
    assert manifest.readiness_evidence["interface_calibration"]["case_count"] == 33
    assert manifest.readiness_evidence["interface_calibration"]["video_count"] == 33
    assert manifest.readiness_evidence["pick_place_v6"]["task_snapshot_id"].endswith(
        "-v6"
    )
    assert manifest.readiness_evidence["pick_place_v6"][
        "object_goal_distance_m"
    ] <= 0.07

    assert len(manifest.units) == 210
    assert len({unit.unit_id for unit in manifest.units}) == 210
    assert manifest.units[0].unit_id == (
        "b2::robotstudio_so101::mw_push_to_goal::M1::R1"
    )
    assert manifest.units[-1].unit_id == (
        "b2::unitree-go2-stock-12dof::GO2-T17::M8::R3"
    )

    by_model = Counter(unit.model_id for unit in manifest.units)
    assert by_model == {model_id: 30 for model_id in manifest.models}
    by_robot_model = Counter(
        (unit.robot_configuration_id, unit.model_id) for unit in manifest.units
    )
    assert set(by_robot_model.values()) == {15}
    by_robot_task_model = Counter(
        (unit.robot_configuration_id, unit.task_id, unit.model_id)
        for unit in manifest.units
    )
    assert len(by_robot_task_model) == 70
    assert set(by_robot_task_model.values()) == {3}


def test_formal_unit_order_is_replicate_robot_task_model() -> None:
    manifest = resolve_manifest(DEFAULT_MANIFEST_PATH)
    observed = [
        (
            unit.replicate_id,
            unit.robot_configuration_id,
            unit.task_id,
            unit.model_id,
        )
        for unit in manifest.units[:14]
    ]
    assert observed[:7] == [
        ("R1", "robotstudio_so101", "mw_push_to_goal", model_id)
        for model_id in manifest.models
    ]
    assert observed[7:] == [
        ("R1", "robotstudio_so101", "mw_sweep_into_goal", model_id)
        for model_id in manifest.models
    ]
