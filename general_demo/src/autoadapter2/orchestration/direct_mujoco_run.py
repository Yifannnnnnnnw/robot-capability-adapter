"""Registry-free entry point for one DIRECT_MUJOCO_EXPERIMENTAL run.

The morphology Library is the only selection mechanism here.  A caller gives
one configuration ID and version; this module resolves the one canonical
record path, points the shared direct-MuJoCo adapter at the external asset
cache, and constructs the session plus development sandbox from that record.
No robot-specific names or registry are consulted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..integrations.direct_mujoco import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoDevelopmentSandbox,
    DirectMuJoCoEvaluationRobotSession,
    DirectMuJoCoLibraryConfig,
    create_direct_mujoco_development_sandbox,
    create_direct_mujoco_session,
    load_morphology_record,
)
from ..libraries.no_sdk_direct_mujoco import (
    NoSDKDirectMuJoCoRecord,
    load_no_sdk_direct_mujoco_record,
)


DIRECT_MUJOCO_EXPERIMENTAL = "DIRECT_MUJOCO_EXPERIMENTAL"


class DirectMuJoCoRunResolutionError(ValueError):
    """The requested Library route cannot be resolved safely."""


def _path_component(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DirectMuJoCoRunResolutionError(
            f"{label} must be one non-empty path component"
        )
    component = value.strip()
    path = Path(component)
    if (
        path.is_absolute()
        or component in {".", ".."}
        or len(path.parts) != 1
        or "\\" in component
    ):
        raise DirectMuJoCoRunResolutionError(
            f"{label} must be one safe path component, not {value!r}"
        )
    return component


@dataclass(frozen=True)
class ResolvedDirectMuJoCoPackage:
    """The exact morphology record and adapter configuration for one run."""

    robot_configuration_id: str
    version: str
    repo_root: Path
    record_path: Path
    asset_cache_root: Path
    record: Mapping[str, Any]
    config: DirectMuJoCoLibraryConfig
    sdk_record: NoSDKDirectMuJoCoRecord


def resolve_morphology_record(
    robot_configuration_id: str,
    version: str,
    repo_root: str | Path,
    asset_cache_root: str | Path,
) -> ResolvedDirectMuJoCoPackage:
    """Resolve exactly one versioned morphology Library record.

    ``repo_root`` must contain the repository route
    ``general_demo/libraries/morphology``.  The external cache is deliberately
    kept separate from the repository and becomes the adapter's asset root;
    there is no fallback to the record directory or another robot route.
    """

    configuration_id = _path_component(robot_configuration_id, "robot_configuration_id")
    record_version = _path_component(version, "version")

    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        raise DirectMuJoCoRunResolutionError(f"repo_root is not a directory: {root}")
    morphology_root = root / "general_demo" / "libraries" / "morphology"
    if not morphology_root.is_dir():
        raise DirectMuJoCoRunResolutionError(
            "repo_root must contain the exact route "
            "general_demo/libraries/morphology"
        )

    record_path = morphology_root / configuration_id / record_version / "record.json"
    if record_path.is_symlink() or not record_path.is_file():
        raise DirectMuJoCoRunResolutionError(
            "morphology record is missing at the exact route: "
            f"{record_path}"
        )

    sdk_record = load_no_sdk_direct_mujoco_record(root)

    cache_root = Path(asset_cache_root).expanduser().resolve()
    if not cache_root.is_dir():
        raise DirectMuJoCoRunResolutionError(
            f"asset_cache_root is not a directory: {cache_root}"
        )

    try:
        record, loaded_path = load_morphology_record(record_path)
        config = DirectMuJoCoLibraryConfig.from_record(record, asset_root=cache_root)
    except DirectMuJoCoConfigurationError:
        raise

    if loaded_path != record_path:
        raise DirectMuJoCoRunResolutionError(
            f"adapter loaded an unexpected morphology record path: {loaded_path}"
        )
    if record.get("robot_configuration_id") != configuration_id:
        raise DirectMuJoCoRunResolutionError(
            "morphology record robot_configuration_id does not match the requested path: "
            f"{record.get('robot_configuration_id')!r} != {configuration_id!r}"
        )
    if record.get("version") != record_version:
        raise DirectMuJoCoRunResolutionError(
            "morphology record version does not match the requested path: "
            f"{record.get('version')!r} != {record_version!r}"
        )

    return ResolvedDirectMuJoCoPackage(
        robot_configuration_id=configuration_id,
        version=record_version,
        repo_root=root,
        record_path=record_path,
        asset_cache_root=cache_root,
        record=record,
        config=config,
        sdk_record=sdk_record,
    )


SessionFactory = Callable[[DirectMuJoCoLibraryConfig], DirectMuJoCoEvaluationRobotSession]
SandboxFactory = Callable[..., DirectMuJoCoDevelopmentSandbox]


@dataclass
class DirectMuJoCoExperiment:
    """One experiment handle containing the shared session and dev sandbox."""

    package: ResolvedDirectMuJoCoPackage
    session: DirectMuJoCoEvaluationRobotSession
    development_sandbox: DirectMuJoCoDevelopmentSandbox

    @property
    def sandbox(self) -> DirectMuJoCoDevelopmentSandbox:
        """Short alias for callers that treat the sandbox as the probe handle."""

        return self.development_sandbox

    def _result_envelope(self, **values: Any) -> dict[str, Any]:
        return {
            "mode": DIRECT_MUJOCO_EXPERIMENTAL,
            "status": "EXPERIMENTAL",
            "robot_configuration_id": self.package.robot_configuration_id,
            "version": self.package.version,
            "record_path": str(self.package.record_path),
            "asset_cache_root": str(self.package.asset_cache_root),
            "sdk_record_path": str(self.package.sdk_record.record_path),
            **values,
        }

    def run_action(
        self,
        action: Mapping[str, Any] | None = None,
        *,
        seconds: float = 0.01,
    ) -> dict[str, Any]:
        """Run one direct actuator action and a short physics step."""

        selected_action = (
            {name: 0.0 for name in self.session.actuator_names}
            if action is None
            else dict(action)
        )
        accepted = self.session.sdk.send_action(selected_action)
        observation = self.session.sdk.step(seconds)
        return self._result_envelope(
            action=selected_action,
            seconds=seconds,
            accepted=accepted,
            observation=observation,
        )

    def run_probe(self, source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        """Run caller-supplied source through the shared development sandbox."""

        return self._result_envelope(
            probe=self.development_sandbox.run(source, probe),
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "DirectMuJoCoExperiment":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def create_direct_mujoco_experiment(
    robot_configuration_id: str,
    version: str,
    repo_root: str | Path,
    asset_cache_root: str | Path,
    *,
    session_factory: SessionFactory | None = None,
    sandbox_factory: SandboxFactory | None = None,
) -> DirectMuJoCoExperiment:
    """Resolve a Library record and construct its shared experimental handles."""

    package = resolve_morphology_record(
        robot_configuration_id,
        version,
        repo_root,
        asset_cache_root,
    )
    session_builder = session_factory or create_direct_mujoco_session
    sandbox_builder = sandbox_factory or create_direct_mujoco_development_sandbox
    session = session_builder(package.config)
    try:
        sandbox = sandbox_builder(package.config, session_factory=session_builder)
    except Exception:
        session.close()
        raise
    return DirectMuJoCoExperiment(
        package=package,
        session=session,
        development_sandbox=sandbox,
    )


__all__ = [
    "DIRECT_MUJOCO_EXPERIMENTAL",
    "DirectMuJoCoExperiment",
    "DirectMuJoCoRunResolutionError",
    "ResolvedDirectMuJoCoPackage",
    "create_direct_mujoco_experiment",
    "resolve_morphology_record",
]
