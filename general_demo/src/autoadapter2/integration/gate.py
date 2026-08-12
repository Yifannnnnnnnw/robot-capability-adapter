"""Deterministic, file-only pre-Stage-1 integration gate."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..foundation.errors import AutoAdapterError, GateError
from .artifacts import (
    JsonArtifact,
    load_integration_manifest,
    load_readiness_report,
    load_run_snapshot,
    load_json_artifact,
    stable_json_sha256,
    validate_readiness_profile,
    verify_file_reference,
)
from .robot_facts import validate_robot_facts


_GO2_EXPERIMENTAL_ARM64_ENV = "AUTOADAPTER_GO2_EXPERIMENTAL_ARM64"


@dataclass(frozen=True)
class Stage1GateResult:
    """The immutable bindings verified immediately before Stage 1 starts."""

    run_id: str
    integration_manifest_sha256: str
    readiness_report_sha256: str
    runtime_sha256: str
    readiness_profile_sha256: str


class ExperimentIntegrationGate:
    """Verify one selected manifest, report, and frozen run snapshot.

    The gate is intentionally stateless.  It reads files supplied by the run and either
    returns their verified bindings or raises ``GateError``.  It does not issue a
    receipt, append an event, or keep an admission/activation state.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def inspect_manifest(self, path: str | Path = "integration_manifest.json") -> JsonArtifact:
        """Parse and hash a DRAFT/READY fixture without treating it as Stage-1 ready."""

        manifest = load_integration_manifest(self._artifact_path(path))
        self._verify_manifest_references(manifest.value)
        # A DRAFT is inspectable for generic field/hash problems, but it is not a
        # claim that the robot route is frozen.  The mechanical robot-fact check is
        # mandatory once a manifest asks to be READY.
        if manifest.value["status"] == "READY":
            facts_manifest = manifest.value
            if (
                manifest.value.get("robot_model_id") == "unitree-go2"
                and os.environ.get(_GO2_EXPERIMENTAL_ARM64_ENV) == "1"
            ):
                # The explicit native-arm64 experiment reuses the READY manifest's
                # identity and all file references, but must not claim the formal
                # source-hash lineage while the route is being exercised.
                facts_manifest = copy.deepcopy(manifest.value)
                facts_manifest["status"] = "DRAFT"
            validate_robot_facts(self.root, facts_manifest)
        return manifest

    def verify(
        self,
        integration_manifest_path: str | Path = "integration_manifest.json",
        run_snapshot_path: str | Path = "run_snapshot.json",
        readiness_report_path: str | Path = "readiness_report.json",
    ) -> Stage1GateResult:
        try:
            manifest = self.inspect_manifest(integration_manifest_path)
            if manifest.value["status"] != "READY":
                raise GateError("only a READY integration manifest can start Stage 1")

            readiness_profile_ref = manifest.value["readiness_profile_ref"]
            profile_path = verify_file_reference(self.root, readiness_profile_ref)
            profile = load_json_artifact(profile_path)
            validate_readiness_profile(profile.value)

            snapshot = load_run_snapshot(self._artifact_path(run_snapshot_path))
            report = load_readiness_report(self._artifact_path(readiness_report_path))
            self._verify_snapshot_references(snapshot.value)
            self._verify_report_references(report.value)

            manifest_ref = self._reference_for_artifact(manifest)
            report_ref = self._reference_for_artifact(report)
            if report.value["integration_manifest_ref"] != manifest_ref:
                raise GateError("readiness report does not bind the selected manifest")
            if snapshot.value["integration_manifest_ref"] != manifest_ref:
                raise GateError("run snapshot does not bind the selected manifest")
            if snapshot.value["readiness_report_ref"] != report_ref:
                raise GateError("run snapshot does not bind the selected readiness report")

            run_id = snapshot.value["run_id"]
            if report.value["run_id"] != run_id:
                raise GateError("readiness report run_id does not match run snapshot")

            runtime_sha256 = stable_json_sha256(manifest.value["runtime"])
            if report.value["runtime_sha256"] != runtime_sha256:
                raise GateError("readiness report runtime binding does not match manifest")
            if snapshot.value["runtime_sha256"] != runtime_sha256:
                raise GateError("run snapshot runtime binding does not match manifest")

            if report.value["readiness_profile_ref"] != readiness_profile_ref:
                raise GateError("readiness report profile binding does not match manifest")
            if snapshot.value["readiness_profile_ref"] != readiness_profile_ref:
                raise GateError("run snapshot profile binding does not match manifest")

            if report.value["time_limits"] != profile.value["time_limits"]:
                raise GateError("readiness report time limits do not match the frozen profile")
            if report.value["numerical_tolerances"] != profile.value["numerical_tolerances"]:
                raise GateError("readiness report numerical tolerances do not match the frozen profile")
            self._verify_dependency_hashes(manifest.value, report.value)
            self._verify_readiness_verdict(report.value)

            return Stage1GateResult(
                run_id=run_id,
                integration_manifest_sha256=manifest.sha256,
                readiness_report_sha256=report.sha256,
                runtime_sha256=runtime_sha256,
                readiness_profile_sha256=readiness_profile_ref["sha256"],
            )
        except GateError:
            raise
        except (AutoAdapterError, OSError, KeyError, TypeError) as exc:
            raise GateError(f"pre-Stage-1 integration gate rejected run: {exc}") from exc

    def permits_stage1(
        self,
        integration_manifest_path: str | Path = "integration_manifest.json",
        run_snapshot_path: str | Path = "run_snapshot.json",
        readiness_report_path: str | Path = "readiness_report.json",
    ) -> bool:
        try:
            self.verify(integration_manifest_path, run_snapshot_path, readiness_report_path)
        except GateError:
            return False
        return True

    def _artifact_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise GateError("top-level artifact path escapes the gate root") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise GateError(f"top-level artifact is not a regular file: {path}")
        return resolved

    def _reference_for_artifact(self, artifact: JsonArtifact) -> dict[str, str]:
        try:
            relative = artifact.path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise GateError("selected artifact is outside the gate root") from exc
        return {"path": relative, "sha256": artifact.sha256}

    def _verify_manifest_references(self, manifest: Mapping[str, Any]) -> None:
        for field in ("morphology_ref", "sdk_ref", "translation_ref"):
            verify_file_reference(self.root, manifest[field])
        if "readiness_profile_ref" in manifest:
            verify_file_reference(self.root, manifest["readiness_profile_ref"])

    def _verify_snapshot_references(self, snapshot: Mapping[str, Any]) -> None:
        for field in (
            "integration_manifest_ref",
            "readiness_report_ref",
            "readiness_profile_ref",
            "task_set_ref",
            "g2_profile_ref",
            "observation_profile_ref",
            "model_prompt_config_ref",
            "budget_ref",
        ):
            verify_file_reference(self.root, snapshot[field])
        for field in ("library_view_refs", "blue_line_input_refs", "sealed_artifact_refs"):
            for reference in snapshot[field]:
                verify_file_reference(self.root, reference)

    def _verify_report_references(self, report: Mapping[str, Any]) -> None:
        verify_file_reference(self.root, report["integration_manifest_ref"])
        verify_file_reference(self.root, report["readiness_profile_ref"])
        for check in report["checks"]:
            for reference in check["evidence_refs"]:
                verify_file_reference(self.root, reference)
        for reference in report["cleanup"]["evidence_refs"]:
            verify_file_reference(self.root, reference)

    @staticmethod
    def _verify_dependency_hashes(manifest: Mapping[str, Any], report: Mapping[str, Any]) -> None:
        expected = {
            "morphology": manifest["morphology_ref"]["sha256"],
            "sdk": manifest["sdk_ref"]["sha256"],
            "translation": manifest["translation_ref"]["sha256"],
            "runtime_lock": manifest["runtime"]["lock_sha256"],
            "readiness_profile": manifest["readiness_profile_ref"]["sha256"],
        }
        if report["dependency_sha256"] != expected:
            raise GateError("readiness report dependency hashes do not match the manifest")

    @staticmethod
    def _verify_readiness_verdict(report: Mapping[str, Any]) -> None:
        if report["verdict"] != "PASS":
            raise GateError("readiness report verdict is not PASS")
        if any(check["verdict"] != "PASS" for check in report["checks"]):
            raise GateError("all six readiness checks must PASS")
        if report["cleanup"]["verdict"] != "PASS":
            raise GateError("readiness cleanup must PASS")


# The short name is exported for callers that do not need the qualification.
IntegrationGate = ExperimentIntegrationGate


def verify_stage1_gate(
    root: str | Path,
    integration_manifest_path: str | Path = "integration_manifest.json",
    run_snapshot_path: str | Path = "run_snapshot.json",
    readiness_report_path: str | Path = "readiness_report.json",
) -> Stage1GateResult:
    return ExperimentIntegrationGate(root).verify(
        integration_manifest_path,
        run_snapshot_path,
        readiness_report_path,
    )
