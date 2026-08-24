#!/usr/bin/env python3
"""Semantically gate and rejudge the 19 retained SO-101 R1 episodes.

The source formal terminals, episode records, provider records, and videos are
read-only inputs.  Each corrected result is written as a separate sidecar.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
AUTOADAPTER_SOURCE = REPOSITORY_ROOT / "autoadapter/src"
for source_root in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from autoadapter2.b2.recap import (  # noqa: E402
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RecapBudgets,
)
from experiment.experiment1b_use.corrected_r1.runtime.harness import (  # noqa: E402
    evaluate_scene_contact_integrity,
)


AUDIT_IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R1",
    "revision": "1.0.0",
}
DEFAULT_MANIFEST = CORRECTED_ROOT / "config/manifest.json"
DEFAULT_OUTPUT = HERE / "rejudication"
DEFAULT_EQUIVALENCE = HERE / "semantic_equivalence.json"
OLD_PACKAGE = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.0"
)
NEW_PACKAGE = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.2"
)


class RejudicationError(RuntimeError):
    """Raised when retained evidence is absent or not semantically reusable."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RejudicationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RejudicationError(f"{label} must contain one JSON object: {path}")
    return value


def _write_object(path: Path, value: Mapping[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise RejudicationError(f"refusing to overwrite existing sidecar: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _repo_path(raw: str | Path) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.resolve())


def _items(path: Path, key: str) -> list[dict[str, Any]]:
    value = _read_object(path, label=path.name).get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RejudicationError(f"{path} has no valid {key!r} list")
    return value


def _one(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        raise RejudicationError(f"expected one {key}={value!r}; found {len(matches)}")
    return matches[0]


def _design_semantics(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    for key in ("package_version", "task_snapshot_id", "corrected_audit_note"):
        result.pop(key, None)
    return result


def _task_semantics(package: Path, task_id: str) -> dict[str, Any]:
    tasks_root = package / "tasks"
    instance = _one(
        _items(tasks_root / "private/instances.json", "instances"),
        "task_id",
        task_id,
    )
    task = _one(_items(tasks_root / "catalog.json", "tasks"), "task_id", task_id)
    binding_ids = set(instance.get("clause_bindings", {}).values())
    guard_ids = set(instance.get("guard_ids", []))
    bindings = [
        item
        for item in _items(tasks_root / "private/bindings.json", "bindings")
        if item.get("binding_id") in binding_ids
    ]
    guards = [
        item
        for item in _items(tasks_root / "private/guards.json", "guards")
        if item.get("guard_id") in guard_ids
    ]
    # Encoding cadence is intentionally changed for future/realtime copies; it
    # does not alter the public task, reset, physics sampling, or task metric.
    instance_semantics = {
        key: deepcopy(value)
        for key, value in instance.items()
        if key not in {"video_fps", "video_width", "video_height", "camera"}
    }
    scene_path = package / str(instance.get("scene_entrypoint"))
    return {
        "catalog_task": task,
        "instance_without_video_encoding": instance_semantics,
        "bindings": sorted(bindings, key=lambda item: str(item.get("binding_id"))),
        "guards": sorted(guards, key=lambda item: str(item.get("guard_id"))),
        "scene_path": scene_path,
        "driver_path": package / "reference/fixed_capability_driver.py",
    }


def _provider_pin(manifest: Mapping[str, Any], model_id: str) -> tuple[Path, dict[str, Any]]:
    paths = manifest.get("paths")
    if not isinstance(paths, Mapping):
        raise RejudicationError("corrected manifest paths are absent")
    provider_manifest_path = _repo_path(str(paths.get("provider_manifest")))
    provider_manifest = _read_object(provider_manifest_path, label="provider manifest")
    configs = provider_manifest.get("provider_runtime_configs")
    if not isinstance(configs, Mapping) or not isinstance(configs.get(model_id), str):
        raise RejudicationError(f"provider config is absent for {model_id}")
    source = (provider_manifest_path.parent / str(configs[model_id])).resolve()
    return source, _read_object(source, label=f"provider config {model_id}")


def _semantic_checks(
    *,
    manifest: Mapping[str, Any],
    unit: Mapping[str, Any],
    terminal: Mapping[str, Any],
    episode_record: Mapping[str, Any],
    provider_record: Mapping[str, Any],
) -> dict[str, Any]:
    task_id = str(unit["task_id"])
    old = _task_semantics(OLD_PACKAGE, task_id)
    new = _task_semantics(NEW_PACKAGE, task_id)
    old_design_path = (
        REPOSITORY_ROOT
        / "experiment/experiment1b_use/validation/reference/resolved"
        / "robotstudio_so101/capability_design.json"
    )
    new_design_path = (
        CORRECTED_ROOT
        / "config/reference/resolved/robotstudio_so101/capability_design.json"
    )
    old_design = _read_object(old_design_path, label="old capability design")
    new_design = _read_object(new_design_path, label="corrected capability design")
    old_contract = episode_record.get("fixed_controller_contract")
    old_budgets = episode_record.get("fixed_controller_budgets")
    paths = manifest.get("paths")
    if not isinstance(paths, Mapping):
        raise RejudicationError("manifest paths are absent")
    provider_manifest = _read_object(
        _repo_path(str(paths.get("provider_manifest"))), label="provider manifest"
    )
    current_contract = provider_manifest.get("common_controller_contract")
    model_id = str(unit["model_id"])
    provider_source, provider_pin = _provider_pin(manifest, model_id)
    identity = terminal.get("model_identity")
    returned_models = identity.get("returned_models") if isinstance(identity, Mapping) else None
    expected_model = provider_pin.get("exact_model_id")
    checks: dict[str, dict[str, Any]] = {
        "public_task_and_metric": {
            "equal": all(old[key] == new[key] for key in (
                "catalog_task",
                "instance_without_video_encoding",
                "bindings",
                "guards",
            )),
            "old_package": _relative(OLD_PACKAGE),
            "corrected_package": _relative(NEW_PACKAGE),
            "video_encoding_difference_excluded": True,
        },
        "scene": {
            "equal": old["scene_path"].read_bytes() == new["scene_path"].read_bytes(),
            "old_path": _relative(old["scene_path"]),
            "corrected_path": _relative(new["scene_path"]),
        },
        "reset": {
            "equal": (
                old["instance_without_video_encoding"].get("reset")
                == new["instance_without_video_encoding"].get("reset")
            ),
            "reset": old["instance_without_video_encoding"].get("reset"),
        },
        "fixed_capability_driver": {
            "equal": old["driver_path"].read_bytes() == new["driver_path"].read_bytes(),
            "old_path": _relative(old["driver_path"]),
            "corrected_path": _relative(new["driver_path"]),
        },
        "capability_interface": {
            "equal": _design_semantics(old_design) == _design_semantics(new_design),
            "old_path": _relative(old_design_path),
            "corrected_path": _relative(new_design_path),
        },
        "recap_contract": {
            "equal": (
                isinstance(old_contract, Mapping)
                and isinstance(current_contract, Mapping)
                and old_contract == current_contract
                and old_contract.get("system_prompt") == RECAP_B2_SYSTEM_PROMPT
                and old_contract.get("response_schema") == RECAP_RESPONSE_SCHEMA
                and old_budgets == asdict(RecapBudgets())
            ),
            "provider_manifest": _relative(
                _repo_path(str(paths.get("provider_manifest")))
            ),
        },
        "model_pin": {
            "equal": bool(
                provider_source.resolve()
                == Path(str(terminal.get("fixed_inputs", {}).get("provider_source_path"))).resolve()
                and provider_record.get("backbone_id") == model_id
                and provider_record.get("requested_model") == expected_model
                and isinstance(identity, Mapping)
                and identity.get("requested_model") == expected_model
                and identity.get("exact_match") is True
                and isinstance(returned_models, list)
                and returned_models
                and all(value == expected_model for value in returned_models)
            ),
            "provider_source_path": _relative(provider_source),
            "expected_model": expected_model,
        },
    }
    return {
        "passed": all(item["equal"] is True for item in checks.values()),
        "checks": checks,
        "allowed_nonsemantic_difference": {
            "package_identity": "1.0.0 -> isolated 1.0.2",
            "future_video_fps": "10 -> 20; retained source video is not replaced",
            "contact_verdict_policy": "corrected sidecar only",
        },
    }


def _rejudge(
    *,
    manifest: Mapping[str, Any],
    unit: Mapping[str, Any],
    terminal_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    terminal = _read_object(terminal_path, label="original terminal")
    if (
        terminal.get("artifact_type") != "b2_formal_unit_terminal"
        or terminal.get("formal_episode") is not True
        or terminal.get("unit_id") != unit.get("original_unit_id")
        or terminal.get("evaluable") is not True
    ):
        raise RejudicationError(f"retained terminal is not one complete formal unit: {terminal_path}")
    episode_path = _repo_path(str(terminal.get("episode_record_path")))
    provider_path = _repo_path(str(terminal.get("provider_record_path")))
    episode_record = _read_object(episode_path, label="original episode record")
    provider_record = _read_object(provider_path, label="original provider record")
    equivalence = _semantic_checks(
        manifest=manifest,
        unit=unit,
        terminal=terminal,
        episode_record=episode_record,
        provider_record=provider_record,
    )
    if equivalence["passed"] is not True:
        failed = [
            name
            for name, result in equivalence["checks"].items()
            if result.get("equal") is not True
        ]
        raise RejudicationError(
            f"semantic-equivalence gate failed for {unit['unit_id']}: {failed}"
        )
    episode_result = episode_record.get("episode")
    if not isinstance(episode_result, Mapping):
        raise RejudicationError(f"original episode payload is absent: {episode_path}")
    worker = episode_result.get("worker")
    old_harness = episode_result.get("harness")
    if not isinstance(worker, Mapping) or not isinstance(old_harness, Mapping):
        raise RejudicationError(f"original worker/Harness evidence is absent: {episode_path}")
    physical_evidence = worker.get("physical_evidence")
    if not isinstance(physical_evidence, Mapping):
        raise RejudicationError(f"physical evidence is absent: {episode_path}")
    old_scene = OLD_PACKAGE / str(old_harness.get("scene_entrypoint"))
    contact = evaluate_scene_contact_integrity(
        physical_evidence=physical_evidence,
        robot_configuration_id="robotstudio_so101",
        task_id=str(unit["task_id"]),
        scene_path=old_scene,
    )
    guards = old_harness.get("guard_outcomes")
    physical_integrity = bool(
        old_harness.get("physical_execution_passed") is True
        and contact.get("passed") is True
        and old_harness.get("guard_error") is None
        and isinstance(guards, Mapping)
        and guards
        and all(value is True for value in guards.values())
    )
    success = bool(
        old_harness.get("task_metric_passed") is True
        and physical_integrity
        and old_harness.get("video_complete") is True
    )
    cost = provider_record.get("total_cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        cost = None
    sidecar = {
        "artifact_type": "b2_corrected_r1_rejudication_sidecar",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "formal_denominator_entry": False,
        "unit_id": unit["unit_id"],
        "robot_configuration_id": unit["robot_configuration_id"],
        "task_id": unit["task_id"],
        "model_id": unit["model_id"],
        "replicate_id": unit["replicate_id"],
        "execution_origin": "retained_rejudged",
        "source_evidence": {
            "original_unit_id": unit["original_unit_id"],
            "original_terminal_path": _relative(terminal_path),
            "original_episode_record_path": _relative(episode_path),
            "original_provider_record_path": _relative(provider_path),
            "original_records_modified": False,
        },
        "semantic_equivalence": equivalence,
        "original_result": {
            "classification": terminal.get("classification"),
            "success": terminal.get("success"),
            "physical_harness_verdict": old_harness.get("physical_harness_verdict"),
            "contact_integrity": old_harness.get("contact_integrity"),
        },
        "corrected_result": {
            "classification": "harness_pass" if success else "harness_fail",
            "evaluable": True,
            "success": success,
            "model_identity": terminal.get("model_identity"),
            "harness": {
                "physical_harness_verdict": "PASS" if success else "FAIL",
                "task_metric_passed": old_harness.get("task_metric_passed"),
                "physical_execution_passed": old_harness.get(
                    "physical_execution_passed"
                ),
                "guard_outcomes": guards,
                "guard_error": old_harness.get("guard_error"),
                "contact_integrity": contact,
                "physical_integrity_passed": physical_integrity,
                "video_complete": old_harness.get("video_complete"),
                "video": old_harness.get("video"),
            },
        },
        "cost": {
            "currency": "USD",
            "original_execution_cost_usd": cost,
            "additional_rejudication_model_cost_usd": 0.0,
            "charged_again": False,
        },
    }
    return sidecar, equivalence


def rejudge_retained(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_dir: Path = DEFAULT_OUTPUT,
    equivalence_path: Path = DEFAULT_EQUIVALENCE,
    force: bool = False,
) -> list[dict[str, Any]]:
    manifest = _read_object(manifest_path.resolve(), label="corrected manifest")
    if (
        manifest.get("artifact_type") != "b2_corrected_r1_manifest"
        or manifest.get("audit_identity") != AUDIT_IDENTITY
        or manifest.get("formal_episode") is not False
    ):
        raise RejudicationError("corrected manifest identity is invalid")
    retained = manifest.get("retained_units")
    if not isinstance(retained, list) or len(retained) != 19:
        raise RejudicationError("corrected manifest must declare 19 retained units")
    sidecars: list[dict[str, Any]] = []
    equivalences: list[dict[str, Any]] = []
    for unit in retained:
        if not isinstance(unit, Mapping):
            raise RejudicationError("retained unit is invalid")
        terminal_path = _repo_path(str(unit.get("original_terminal_path")))
        sidecar, equivalence = _rejudge(
            manifest=manifest,
            unit=unit,
            terminal_path=terminal_path,
        )
        output_path = output_dir.resolve() / f"{str(unit['unit_id']).replace('::', '__')}.json"
        _write_object(output_path, sidecar, force=force)
        sidecars.append(sidecar)
        equivalences.append(
            {
                "unit_id": unit["unit_id"],
                "task_id": unit["task_id"],
                "model_id": unit["model_id"],
                **equivalence,
            }
        )
    report = {
        "artifact_type": "b2_corrected_r1_semantic_equivalence_report",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "passed": all(item["passed"] is True for item in equivalences),
        "unit_count": len(equivalences),
        "units": equivalences,
    }
    _write_object(equivalence_path.resolve(), report, force=force)
    return sidecars


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--equivalence", type=Path, default=DEFAULT_EQUIVALENCE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        sidecars = rejudge_retained(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            equivalence_path=args.equivalence,
            force=args.force,
        )
    except RejudicationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    passes = sum(
        item["corrected_result"]["success"] is True for item in sidecars
    )
    print(
        json.dumps(
            {"rejudged": len(sidecars), "successes": passes, "failures": len(sidecars) - passes},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
