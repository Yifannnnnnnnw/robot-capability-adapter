from __future__ import annotations

import pytest

from autoadapter2.foundation.hashing import content_hash
from autoadapter2.validation import ValidationBRunner, bind_candidate_to_suite
from tests.test_validation_repair import _FixedHarness, _a_result, _context


def _ready_candidate():
    design, design_seal, stage2, blue, a_result = _a_result()
    context = _context(design, design_seal, blue)
    candidate = bind_candidate_to_suite(a_result, blue.suite_hash)
    return stage2, blue, context, candidate


def test_object_setattr_cannot_replace_candidate_source_or_hash() -> None:
    stage2, _blue, _context_value, candidate = _ready_candidate()

    with pytest.raises(AttributeError):
        object.__setattr__(candidate, "_source", "def replacement():\n    pass\n")
    with pytest.raises(AttributeError):
        object.__setattr__(candidate, "_source_hash", content_hash(b"replacement"))
    with pytest.raises(AttributeError):
        object.__setattr__(candidate, "source_hash", content_hash(b"replacement"))

    assert not hasattr(candidate, "__dict__")
    assert candidate.source_hash == stage2.source_hash


def test_nested_contract_and_overlay_mutation_cannot_change_authority() -> None:
    stage2, blue, context, candidate = _ready_candidate()
    assert not hasattr(candidate, "_contracts")
    assert not hasattr(candidate, "_overlay")
    with pytest.raises(AttributeError):
        object.__setattr__(candidate, "_contracts", {})
    with pytest.raises(AttributeError):
        object.__setattr__(candidate, "_overlay", None)

    snapshot = candidate._framework_payload_snapshot()
    assert snapshot.overlay is not None
    assert snapshot.overlay.overlay["source_hash"] == stage2.source_hash
    assert stage2.source_hash in snapshot.overlay.seal["parents"]

    snapshot.contracts["reach-joint-target"]["function_name"] = "replacement"
    snapshot.overlay.overlay["source_hash"] = content_hash(b"replacement")
    snapshot.overlay.overlay["suite_hash"] = content_hash(b"other-suite")
    snapshot.overlay.seal["parents"].clear()

    pristine = candidate._framework_payload_snapshot()
    assert pristine.contracts["reach-joint-target"]["function_name"] == "capability_reach_joint_target"
    assert pristine.overlay is not None
    assert pristine.overlay.overlay["source_hash"] == stage2.source_hash
    assert pristine.overlay.overlay["suite_hash"] == blue.suite_hash
    assert stage2.source_hash in pristine.overlay.seal["parents"]

    result = ValidationBRunner(_FixedHarness(context.run_snapshot, ["pass", "pass"])).run(
        candidate, context
    )
    assert result.status == "PASS"
    assert result.report["source_hash"] == stage2.source_hash
    assert result.report["suite_hash"] == blue.suite_hash


def test_normal_validation_a_to_b_keeps_all_overlay_lineage() -> None:
    stage2, blue, context, candidate = _ready_candidate()

    result = ValidationBRunner(_FixedHarness(context.run_snapshot, ["pass", "pass"])).run(
        candidate, context
    )

    assert result.status == "PASS"
    assert result.report["source_hash"] == stage2.source_hash
    assert result.report["implementation_manifest_hash"] == stage2.manifest_hash
    assert result.report["implementation_bundle_hash"] == stage2.bundle_hash
    assert result.report["validation_a_report_hash"] == candidate.validation_a_report_hash
    assert result.report["suite_hash"] == blue.suite_hash
