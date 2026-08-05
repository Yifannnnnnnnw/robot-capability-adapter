"""Strict, immutable resolution of morphology-owned tabletop scene assets.

Task/case records select only ``asset_ref``, instance ID, pose, and task-goal
metadata.  Geometry, mass, material and contact parameters come exclusively
from the versioned Morphology catalog.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import yaml

from ..audit import sha256_file, sha256_json
from ..environment_resolver import catalog_asset_descriptor_hashes


_ASSET_REF = re.compile(
    r"^morphology\.scene_asset/"
    r"(?P<asset_id>[a-z][a-z0-9_]*)@"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)#"
    r"(?P<variant_id>[a-z][a-z0-9_]*)$"
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_BUILDER = "soarm_demo.bridge.mujoco_tabletop.SO101MujocoTabletopRuntime"
_REFERENCE_FORMAT = "morphology.scene_asset/{asset_id}@{version}#{variant_id}"
_ROLE_FOR_KIND = {
    "table": "support_surface",
    "cube": "dynamic_object",
    "cylinder": "dynamic_object",
    "tray": "receptacle",
    "bowl": "receptacle",
    "planar_circle": "marker",
    "pose_target": "marker",
}
_LEGACY_DEFAULT_VARIANT = {
    "table": "brown",
    "cube": "red",
    "cylinder": "orange",
    "tray": "generic",
    "bowl": "purple",
    "planar_circle": "green",
    "pose_target": "green",
}
_INSTANCE_METADATA_FIELDS = frozenset({"asset_ref", "id", "kind", "approach_vector"})
_EXPLICIT_METADATA_FIELDS = frozenset({"asset_ref", "id", "approach_vector"})
_POSE_FIELDS_FOR_KIND = {
    "table": frozenset({"center_m"}),
    "cube": frozenset({"position_m", "quaternion_wxyz"}),
    "cylinder": frozenset({"position_m", "quaternion_wxyz"}),
    "tray": frozenset({"center_m"}),
    "bowl": frozenset({"center_m"}),
    "planar_circle": frozenset({"center_m"}),
    "pose_target": frozenset({"position_m", "approach_vector"}),
}
_AGENT_FACT_FIELDS_FOR_KIND = {
    "cube": ("position_m", "size_m"),
    "cylinder": ("position_m", "radius_m", "height_m"),
    "tray": ("center_m", "inner_size_m", "rim_height_m"),
    "bowl": ("center_m", "inner_radius_m", "rim_height_m"),
    "planar_circle": ("center_m", "radius_m"),
    "pose_target": ("position_m",),
}
_AGENT_BUCKET_FOR_ROLE = {
    "dynamic_object": "objects",
    "receptacle": "receptacles",
    "marker": "targets",
}
_PHYSICAL_AGENT_FACT_FIELDS = frozenset(
    {
        "position_m",
        "center_m",
        "quaternion_wxyz",
        "size_m",
        "radius_m",
        "height_m",
        "inner_size_m",
        "inner_radius_m",
        "rim_height_m",
        "mass_kg",
        "axis_vector",
    }
)


class SceneAssetCatalogError(ValueError):
    """A catalog, reference, freeze, or task-instance binding is unsafe."""


def _load_yaml_mapping(
    value: Mapping[str, Any] | str | Path,
    *,
    label: str,
) -> tuple[dict[str, Any], Path | None]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value)), None
    path = Path(value).resolve()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SceneAssetCatalogError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise SceneAssetCatalogError(f"{label} must contain an object: {path}")
    return deepcopy(dict(parsed)), path


def _finite_numbers(
    value: object,
    count: int,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != count
    ):
        raise SceneAssetCatalogError(f"{label} must contain exactly {count} numbers")
    output = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in output):
        raise SceneAssetCatalogError(f"{label} must contain finite numbers")
    if minimum is not None and any(item < minimum for item in output):
        raise SceneAssetCatalogError(f"{label} values must be >= {minimum:g}")
    if maximum is not None and any(item > maximum for item in output):
        raise SceneAssetCatalogError(f"{label} values must be <= {maximum:g}")
    return output


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _frozen(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_frozen(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return value


def _equivalent(left: Any, right: Any) -> bool:
    """Compare frozen catalog values with JSON task values by value."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _equivalent(left[key], right[key]) for key in left
        )
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes))
    ):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return float(left) == float(right)
    return left == right


def _cylinder_axis(parameters: Mapping[str, Any]) -> list[float]:
    quaternion = _finite_numbers(
        parameters.get("quaternion_wxyz"),
        4,
        label="cylinder.quaternion_wxyz",
    )
    norm = math.sqrt(sum(value * value for value in quaternion))
    if norm <= 1e-12:
        raise SceneAssetCatalogError("cylinder quaternion must be non-zero")
    w, x, y, z = (value / norm for value in quaternion)
    return [
        2.0 * (x * z + w * y),
        2.0 * (y * z - w * x),
        1.0 - 2.0 * (x * x + y * y),
    ]


@dataclass(frozen=True)
class ResolvedSceneAsset:
    """One catalog asset and variant merged with an approved instance spec."""

    asset_ref: str
    asset_id: str
    variant_id: str
    role: str
    runtime_kind: str
    parameters: Mapping[str, Any]
    material_rgba: tuple[float, float, float, float]
    physics: Mapping[str, Any]


CatalogVerifier = Callable[["SceneAssetCatalog", Mapping[str, Any] | None], None]


class SceneAssetCatalog:
    """Validated catalog snapshot that re-hashes its source before every compile."""

    def __init__(self, source: Mapping[str, Any] | str | Path) -> None:
        catalog, source_path = _load_yaml_mapping(source, label="scene asset catalog")
        self._source_path = source_path
        self._source_sha256 = (
            sha256_file(source_path) if source_path is not None else sha256_json(catalog)
        )
        self._content_sha256 = sha256_json(catalog)
        self._catalog = _frozen(catalog)
        self._assets = self._validate_and_index(catalog)
        try:
            descriptor_hashes = catalog_asset_descriptor_hashes(catalog)
        except (RuntimeError, ValueError, TypeError) as exc:
            raise SceneAssetCatalogError(
                f"cannot hash scene asset descriptors: {exc}"
            ) from exc
        self._asset_descriptor_sha256 = MappingProxyType(descriptor_hashes)

    @property
    def catalog_id(self) -> str:
        return str(self._catalog["catalog_id"])

    @property
    def version(self) -> str:
        return str(self._catalog["version"])

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    @property
    def content_sha256(self) -> str:
        return self._content_sha256

    @property
    def asset_descriptor_sha256(self) -> dict[str, str]:
        return dict(self._asset_descriptor_sha256)

    def verify_unchanged(self) -> None:
        """Fail if a path-backed catalog changed after runtime construction."""

        if self._source_path is None:
            return
        actual = sha256_file(self._source_path)
        if actual != self._source_sha256:
            raise SceneAssetCatalogError(
                "scene asset catalog changed after selection: "
                f"expected {self._source_sha256}, got {actual}"
            )

    def resolve(
        self,
        spec: Mapping[str, Any],
        *,
        expected_role: str | None,
        legacy_kind: str | None = None,
    ) -> ResolvedSceneAsset:
        """Resolve an explicit ref or a deterministic legacy kind projection."""

        if not isinstance(spec, Mapping):
            raise SceneAssetCatalogError("scene asset instance must be an object")
        explicit_ref = spec.get("asset_ref")
        if explicit_ref is None:
            runtime_kind = str(spec.get("kind", legacy_kind))
            asset = self._asset_for_legacy_kind(runtime_kind, spec)
            variant_id = self._legacy_variant(asset, spec)
            asset_ref = self._format_ref(str(asset["asset_id"]), variant_id)
            explicit = False
        else:
            if not isinstance(explicit_ref, str):
                raise SceneAssetCatalogError("asset_ref must be a string")
            match = _ASSET_REF.fullmatch(explicit_ref)
            if match is None:
                raise SceneAssetCatalogError(f"unsafe or malformed asset_ref: {explicit_ref!r}")
            if match.group("version") != self.version:
                raise SceneAssetCatalogError(
                    f"asset_ref version {match.group('version')!r} does not match "
                    f"selected catalog version {self.version!r}"
                )
            asset_id = match.group("asset_id")
            asset = self._assets.get(asset_id)
            if asset is None:
                raise SceneAssetCatalogError(
                    f"asset_ref selects unknown asset {asset_id!r}"
                )
            variant_id = match.group("variant_id")
            asset_ref = explicit_ref
            explicit = True

        role = str(asset["role"])
        runtime_kind = str(asset["runtime_kind"])
        if expected_role is not None and role != expected_role:
            raise SceneAssetCatalogError(
                f"asset_ref role mismatch: expected {expected_role!r}, got {role!r}"
            )
        if explicit and "kind" in spec:
            raise SceneAssetCatalogError(
                "unsafe inline kind is forbidden because asset_ref owns runtime_kind"
            )
        supplied_kind = None if explicit else spec.get("kind", legacy_kind)
        if supplied_kind is not None and str(supplied_kind) != runtime_kind:
            raise SceneAssetCatalogError(
                f"asset_ref kind mismatch: ref is {runtime_kind!r}, instance says "
                f"{str(supplied_kind)!r}"
            )

        variants = {
            str(item["variant_id"]): item for item in asset["variants"]
        }
        variant = variants.get(variant_id)
        if variant is None:
            raise SceneAssetCatalogError(
                f"asset_ref selects unknown variant {variant_id!r} for "
                f"{str(asset['asset_id'])!r}"
            )
        material_rgba = _finite_numbers(
            variant["material_rgba"],
            4,
            label=f"{asset_ref}.material_rgba",
            minimum=0.0,
            maximum=1.0,
        )
        if explicit and "material_rgba" in spec:
            raise SceneAssetCatalogError(
                "unsafe inline material_rgba is forbidden when asset_ref is explicit"
            )
        if not explicit and "material_rgba" in spec:
            supplied_rgba = _finite_numbers(
                spec["material_rgba"],
                4,
                label="instance.material_rgba",
                minimum=0.0,
                maximum=1.0,
            )
            if supplied_rgba != material_rgba:
                raise SceneAssetCatalogError(
                    "unsafe material_rgba override does not match the selected "
                    f"asset variant {asset_ref!r}"
                )

        contract = asset["instance_contract"]
        required = {str(value) for value in contract["required_fields"]}
        optional = {str(value) for value in contract["optional_fields"]}
        if explicit:
            permitted = set(_EXPLICIT_METADATA_FIELDS) | set(
                _POSE_FIELDS_FOR_KIND[runtime_kind]
            )
        else:
            permitted = (
                required
                | optional
                | set(asset["geometry_profile"])
                | set(_INSTANCE_METADATA_FIELDS)
                | {"material_rgba"}
            )
        unknown = sorted(str(key) for key in spec if str(key) not in permitted)
        if unknown:
            raise SceneAssetCatalogError(
                f"unsafe instance override fields for {asset_ref!r}: {unknown}"
            )

        parameters = deepcopy(dict(asset["geometry_profile"]))
        instance_parameter_fields = (
            _POSE_FIELDS_FOR_KIND[runtime_kind]
            if explicit
            else required | optional | set(asset["geometry_profile"])
        )
        for key in instance_parameter_fields:
            if key in spec and key not in {"material_rgba", "approach_vector"}:
                parameters[key] = deepcopy(spec[key])
        missing = sorted(key for key in required if key not in parameters)
        if missing:
            raise SceneAssetCatalogError(
                f"asset instance {asset_ref!r} is missing required fields: {missing}"
            )
        return ResolvedSceneAsset(
            asset_ref=asset_ref,
            asset_id=str(asset["asset_id"]),
            variant_id=variant_id,
            role=role,
            runtime_kind=runtime_kind,
            parameters=_frozen(parameters),
            material_rgba=material_rgba,
            physics=_frozen(dict(asset["physics"])),
        )

    def derive_agent_input(self, instance: Mapping[str, Any]) -> dict[str, Any]:
        """Build scene facts from asset refs; retain only nonphysical task facts.

        Legacy authored files still carry an ``agent_input`` cache for the
        scripted fixture.  This method deliberately ignores every physical
        value in that cache, so Validation/Demo facts cannot become a second
        geometry authority.
        """

        authored = instance.get("agent_input", {})
        if not isinstance(authored, Mapping):
            raise SceneAssetCatalogError("agent_input must be an object")
        result: dict[str, Any] = {
            "coordinate_frame": str(authored.get("coordinate_frame", "robot_base")),
            "position_unit": str(authored.get("position_unit", "m")),
        }
        goals = authored.get("goals")
        if isinstance(goals, Mapping):
            result["goals"] = deepcopy(dict(goals))

        seen_ids: set[str] = set()
        for collection, expected_role in (("bodies", None), ("markers", "marker")):
            records = instance.get(collection, [])
            if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
                raise SceneAssetCatalogError(f"{collection} must be an array")
            for raw in records:
                if not isinstance(raw, Mapping):
                    raise SceneAssetCatalogError(f"{collection} must contain objects")
                identifier = raw.get("id")
                if not isinstance(identifier, str) or identifier in seen_ids:
                    raise SceneAssetCatalogError("scene asset IDs must be strings and unique")
                seen_ids.add(identifier)
                asset = self.resolve(raw, expected_role=expected_role)
                bucket_name = _AGENT_BUCKET_FOR_ROLE.get(asset.role)
                if bucket_name is None:
                    raise SceneAssetCatalogError(
                        f"asset role {asset.role!r} has no agent scene-fact projection"
                    )
                facts = {
                    name: _plain(asset.parameters[name])
                    for name in _AGENT_FACT_FIELDS_FOR_KIND[asset.runtime_kind]
                }
                if asset.runtime_kind == "cylinder":
                    facts["axis_vector"] = _cylinder_axis(asset.parameters)

                authored_bucket = authored.get(bucket_name, {})
                authored_entity = (
                    authored_bucket.get(identifier, {})
                    if isinstance(authored_bucket, Mapping)
                    else {}
                )
                if isinstance(authored_entity, Mapping):
                    for name, value in authored_entity.items():
                        if name not in _PHYSICAL_AGENT_FACT_FIELDS:
                            facts[str(name)] = deepcopy(value)
                result.setdefault(bucket_name, {})[identifier] = facts
        return result

    def verify_environment_freeze(self, freeze: Mapping[str, Any] | None) -> None:
        """Verify the freeze envelope and its exact selected-catalog binding.

        The resolver owns the binding layout.  Keeping this check here makes
        the runtime independently re-establish that it compiles the catalog
        selected before Generation, rather than trusting a caller's path.
        """

        if freeze is None:
            return
        if freeze.get("schema_version") != "robot_capability.generation_environment_freeze.v1":
            raise SceneAssetCatalogError("unsupported generation environment freeze schema")
        manifest = freeze.get("manifest")
        if not isinstance(manifest, Mapping):
            raise SceneAssetCatalogError("generation environment freeze.manifest must be an object")
        expected_manifest_hash = freeze.get("manifest_sha256")
        actual_manifest_hash = sha256_json(manifest)
        if expected_manifest_hash != actual_manifest_hash:
            raise SceneAssetCatalogError(
                "generation environment freeze manifest_sha256 mismatch"
            )
        bindings = manifest.get("bindings")
        if not isinstance(bindings, Mapping):
            raise SceneAssetCatalogError("generation environment freeze bindings are missing")
        morphology = bindings.get("morphology")
        if not isinstance(morphology, Mapping):
            raise SceneAssetCatalogError("generation environment freeze morphology binding is missing")
        binding = morphology.get("scene_asset_catalog")
        if not isinstance(binding, Mapping):
            raise SceneAssetCatalogError(
                "generation environment freeze scene_asset_catalog binding is missing"
            )
        expected = {
            "catalog_id": self.catalog_id,
            "version": self.version,
            "sha256": self.source_sha256,
        }
        for key, value in expected.items():
            if binding.get(key) != value:
                raise SceneAssetCatalogError(
                    f"generation environment freeze catalog binding {key!r} mismatch"
                )
        frozen_descriptors = binding.get("asset_descriptor_sha256")
        if not isinstance(frozen_descriptors, Mapping):
            raise SceneAssetCatalogError(
                "generation environment freeze catalog descriptor binding is missing"
            )
        if dict(frozen_descriptors) != self.asset_descriptor_sha256:
            raise SceneAssetCatalogError(
                "generation environment freeze catalog asset descriptors mismatch"
            )
        binding_path = binding.get("path")
        if not isinstance(binding_path, str):
            raise SceneAssetCatalogError(
                "generation environment freeze catalog binding path is missing"
            )
        relative = PurePosixPath(binding_path)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise SceneAssetCatalogError(
                "generation environment freeze catalog binding path is unsafe"
            )
        if self.source_path is None:
            raise SceneAssetCatalogError(
                "a freeze-bound catalog must be loaded from its selected file path"
            )
        expected_suffix = tuple(relative.parts)
        if tuple(self.source_path.parts[-len(expected_suffix) :]) != expected_suffix:
            raise SceneAssetCatalogError(
                "selected scene catalog path does not match the environment freeze"
            )

    def _asset_for_legacy_kind(
        self,
        runtime_kind: str,
        spec: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        candidates = [
            asset for asset in self._assets.values()
            if str(asset["runtime_kind"]) == runtime_kind
        ]
        pose_fields = _POSE_FIELDS_FOR_KIND.get(runtime_kind, frozenset())
        metadata = set(_INSTANCE_METADATA_FIELDS) | {"material_rgba"} | set(pose_fields)
        physical_fields = {str(key) for key in spec} - metadata
        matched = []
        for asset in candidates:
            defaults = asset["geometry_profile"]
            if all(
                key in defaults and _equivalent(defaults[key], spec[key])
                for key in physical_fields
            ):
                matched.append(asset)
        if len(matched) != 1:
            raise SceneAssetCatalogError(
                f"legacy kind {runtime_kind!r} and authored geometry do not resolve "
                "to exactly one catalog asset"
            )
        return matched[0]

    def _legacy_variant(
        self,
        asset: Mapping[str, Any],
        spec: Mapping[str, Any],
    ) -> str:
        variants = {
            str(item["variant_id"]): item for item in asset["variants"]
        }
        if "material_rgba" in spec:
            supplied = _finite_numbers(
                spec["material_rgba"],
                4,
                label="instance.material_rgba",
                minimum=0.0,
                maximum=1.0,
            )
            matches = [
                identifier
                for identifier, variant in variants.items()
                if _finite_numbers(
                    variant["material_rgba"],
                    4,
                    label=f"variant {identifier}.material_rgba",
                    minimum=0.0,
                    maximum=1.0,
                ) == supplied
            ]
            if len(matches) != 1:
                raise SceneAssetCatalogError(
                    "legacy material_rgba does not select exactly one catalog variant"
                )
            return matches[0]

        identifier = str(spec.get("id", ""))
        tokens = set(re.split(r"[_.-]+", identifier.lower()))
        named = sorted(tokens & set(variants))
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise SceneAssetCatalogError(
                f"legacy instance id {identifier!r} ambiguously selects variants {named}"
            )
        runtime_kind = str(asset["runtime_kind"])
        default = _LEGACY_DEFAULT_VARIANT.get(runtime_kind)
        if default in variants:
            return str(default)
        if len(variants) == 1:
            return next(iter(variants))
        raise SceneAssetCatalogError(
            f"legacy instance {identifier!r} cannot select a deterministic variant"
        )

    def _format_ref(self, asset_id: str, variant_id: str) -> str:
        return (
            f"morphology.scene_asset/{asset_id}@{self.version}#{variant_id}"
        )

    def _validate_and_index(
        self,
        catalog: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        if catalog.get("schema_version") != "robot_capability.morphology_scene_asset_catalog.v1":
            raise SceneAssetCatalogError("unsupported scene asset catalog schema")
        catalog_id = catalog.get("catalog_id")
        version = catalog.get("version")
        if not isinstance(catalog_id, str) or _IDENTIFIER.fullmatch(catalog_id) is None:
            raise SceneAssetCatalogError("scene asset catalog_id is invalid")
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise SceneAssetCatalogError("scene asset catalog version is invalid")
        if catalog.get("ownership") != "morphology_scene_registry":
            raise SceneAssetCatalogError("scene asset catalog ownership is invalid")
        if catalog.get("reference_format") != _REFERENCE_FORMAT:
            raise SceneAssetCatalogError("scene asset catalog reference_format is invalid")
        raw_assets = catalog.get("assets")
        if not isinstance(raw_assets, Sequence) or isinstance(raw_assets, (str, bytes)):
            raise SceneAssetCatalogError("scene asset catalog assets must be an array")
        index: dict[str, Mapping[str, Any]] = {}
        for position, raw_asset in enumerate(raw_assets):
            if not isinstance(raw_asset, Mapping):
                raise SceneAssetCatalogError(f"assets[{position}] must be an object")
            asset = deepcopy(dict(raw_asset))
            asset_id = asset.get("asset_id")
            if not isinstance(asset_id, str) or _IDENTIFIER.fullmatch(asset_id) is None:
                raise SceneAssetCatalogError(f"assets[{position}].asset_id is invalid")
            if asset_id in index:
                raise SceneAssetCatalogError(f"duplicate scene asset_id {asset_id!r}")
            runtime_kind = str(asset.get("runtime_kind"))
            expected_role = _ROLE_FOR_KIND.get(runtime_kind)
            if expected_role is None or asset.get("role") != expected_role:
                raise SceneAssetCatalogError(
                    f"asset {asset_id!r} has an invalid role/runtime_kind binding"
                )
            if asset.get("builder") != _BUILDER:
                raise SceneAssetCatalogError(f"asset {asset_id!r} selects an unknown builder")
            contract = asset.get("instance_contract")
            defaults = asset.get("geometry_profile")
            variants = asset.get("variants")
            physics = asset.get("physics")
            if not isinstance(contract, Mapping):
                raise SceneAssetCatalogError(f"asset {asset_id!r} contract is missing")
            if not isinstance(defaults, Mapping):
                raise SceneAssetCatalogError(f"asset {asset_id!r} defaults are missing")
            if not isinstance(variants, Sequence) or isinstance(variants, (str, bytes)) or not variants:
                raise SceneAssetCatalogError(f"asset {asset_id!r} variants are missing")
            if not isinstance(physics, Mapping):
                raise SceneAssetCatalogError(f"asset {asset_id!r} physics are missing")
            required = contract.get("required_fields")
            optional = contract.get("optional_fields")
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise SceneAssetCatalogError(f"asset {asset_id!r} required_fields are invalid")
            if not isinstance(optional, list) or not all(isinstance(item, str) for item in optional):
                raise SceneAssetCatalogError(f"asset {asset_id!r} optional_fields are invalid")
            if set(required) & set(optional):
                raise SceneAssetCatalogError(f"asset {asset_id!r} contract fields overlap")
            if contract.get("asset_ref_required") is not True:
                raise SceneAssetCatalogError(f"asset {asset_id!r} lacks explicit-ref policy")
            if contract.get("inline_physical_overrides_allowed") is not False:
                raise SceneAssetCatalogError(
                    f"asset {asset_id!r} permits unsafe inline physical overrides"
                )
            self._validate_physics(physics, label=f"asset {asset_id!r}.physics")
            seen_variants: set[str] = set()
            for variant in variants:
                if not isinstance(variant, Mapping):
                    raise SceneAssetCatalogError(f"asset {asset_id!r} variant is not an object")
                variant_id = variant.get("variant_id")
                if not isinstance(variant_id, str) or _IDENTIFIER.fullmatch(variant_id) is None:
                    raise SceneAssetCatalogError(f"asset {asset_id!r} variant_id is invalid")
                if variant_id in seen_variants:
                    raise SceneAssetCatalogError(
                        f"asset {asset_id!r} has duplicate variant {variant_id!r}"
                    )
                seen_variants.add(variant_id)
                _finite_numbers(
                    variant.get("material_rgba"),
                    4,
                    label=f"asset {asset_id!r} variant {variant_id!r}.material_rgba",
                    minimum=0.0,
                    maximum=1.0,
                )
            index[asset_id] = _frozen(asset)
        return index

    @staticmethod
    def _validate_physics(physics: Mapping[str, Any], *, label: str) -> None:
        collision_enabled = physics.get("collision_enabled")
        if not isinstance(collision_enabled, bool):
            raise SceneAssetCatalogError(f"{label}.collision_enabled must be boolean")
        _finite_numbers(
            physics.get("friction"),
            3,
            label=f"{label}.friction",
            minimum=0.0,
        )
        condim = physics.get("condim")
        if not isinstance(condim, int) or isinstance(condim, bool) or condim not in {1, 3, 4, 6}:
            raise SceneAssetCatalogError(f"{label}.condim must be one of 1, 3, 4, 6")


__all__ = [
    "CatalogVerifier",
    "ResolvedSceneAsset",
    "SceneAssetCatalog",
    "SceneAssetCatalogError",
]
