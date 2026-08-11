from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..contracts.registry import ProfileRegistry, RecordRegistry, RegistryEntry
from ..foundation.errors import ContractError, GateError
from ..foundation.identifiers import ExactReference, _id
from .state import GateReceipt

if TYPE_CHECKING:
    from .run_index import RunIndex


@dataclass(frozen=True)
class RunSelection:
    run_id: str
    rim_ref: ExactReference
    granularity_profile_ref: ExactReference
    campaign: str = "first"

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunSelection":
        if not isinstance(value, dict):
            raise ContractError("run selection must be an object")
        forbidden_plural = {
            key for key in ("rim_refs", "rims", "granularity_profile_refs", "profiles")
            if key in value
        }
        if forbidden_plural:
            raise ContractError("a run must contain exactly one RIM and one profile")
        required = {"run_id", "rim_ref", "granularity_profile_ref"}
        missing = required - set(value)
        if missing:
            raise ContractError(f"run selection is missing: {sorted(missing)}")
        extra = set(value) - required - {"campaign"}
        if extra:
            raise ContractError(f"unexpected run selection fields: {sorted(extra)}")
        if isinstance(value["rim_ref"], list) or isinstance(value["granularity_profile_ref"], list):
            raise ContractError("a run must contain exactly one RIM and one profile")
        _id(value["run_id"], "run_id")
        campaign = value.get("campaign", "first")
        _id(campaign, "campaign")
        if campaign != "first":
            raise GateError("only the first campaign is admitted")
        return cls(
            run_id=value["run_id"],
            rim_ref=ExactReference.from_value(value["rim_ref"], expected_kind="rim"),
            granularity_profile_ref=ExactReference.from_value(
                value["granularity_profile_ref"], expected_kind="profile"
            ),
            campaign=campaign,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "rim_ref": self.rim_ref.to_dict(),
            "granularity_profile_ref": self.granularity_profile_ref.to_dict(),
            "campaign": self.campaign,
        }


class RunSelectionGate:
    def __init__(
        self,
        rim_registry: RecordRegistry,
        profile_registry: ProfileRegistry,
        run_index: "RunIndex",
    ):
        self.rim_registry = rim_registry
        self.profile_registry = profile_registry
        self.run_index = run_index

    def admit(self, selection: RunSelection | dict[str, Any]) -> tuple[RegistryEntry, RegistryEntry]:
        if not isinstance(selection, RunSelection):
            selection = RunSelection.from_mapping(selection)
        selection_hash = self.run_index.selection_hash(selection)
        if selection.campaign != "first":
            raise GateError("only the first campaign is admitted")
        rim = self.rim_registry.resolve(selection.rim_ref, require_frozen=True)
        profile = self.profile_registry.resolve(
            selection.granularity_profile_ref, require_frozen=True
        )
        if profile.status != "FROZEN_FIXTURE":
            raise GateError("first campaign requires a FROZEN_FIXTURE profile")
        if rim.status != "FROZEN_FIXTURE":
            raise GateError("first campaign requires a FROZEN_FIXTURE RIM")
        if not rim.payload.get("fixture_only") or rim.payload.get("authority_status") != "OPEN":
            raise GateError("first campaign RIM must remain explicitly synthetic")
        payload = profile.payload
        if payload.get("profile_family") != "granularity" or payload.get("granularity") != "G2":
            raise GateError("first campaign accepts only the frozen G2 fixture")
        if payload.get("authority_status") != "OPEN" or not payload.get("fixture_only"):
            raise GateError("first campaign profile must remain explicitly synthetic")
        return rim, profile

    def receipts(self, selection: RunSelection | dict[str, Any]) -> tuple[GateReceipt, GateReceipt]:
        if not isinstance(selection, RunSelection):
            selection = RunSelection.from_mapping(selection)
        selection_hash = self.run_index.selection_hash(selection)
        rim, profile = self.admit(selection)
        evidence = {
            "rim_ref": rim.ref.to_dict(),
            "granularity_profile_ref": profile.ref.to_dict(),
        }
        return (
            GateReceipt._from_registered(
                selection.run_id,
                "rim_resolved",
                selection_hash,
                {"rim_ref": rim.ref.to_dict()},
            ),
            GateReceipt._from_registered(
                selection.run_id,
                "ready_for_stage1",
                selection_hash,
                evidence,
            ),
        )
