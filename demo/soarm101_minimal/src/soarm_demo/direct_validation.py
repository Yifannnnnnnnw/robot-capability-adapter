"""Trusted direct-function validation harness.

Generated functions are imported only after static validation and are invoked
as ordinary Python callables.  No Agent and no tool dispatcher participates in
this phase.
"""

from __future__ import annotations

import json
import math
import queue
import re
import signal
import threading
import time
import traceback
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Protocol, Sequence

from .audit import atomic_write_json, sha256_json
from .generated_loader import load_isolated_generated_module
from .tool_packager import package_tree_sha256
from .validation_suite import HARNESS_TIMEOUT_CLOCK, HARNESS_TIMEOUT_FIELD


class ValidationEnvironment(Protocol):
    """Private harness contract; only ``runtime`` reaches generated code."""

    runtime: Any

    def reset(self, initial_state: Mapping[str, Any]) -> None: ...

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


EnvironmentFactory = Callable[[Mapping[str, Any]], ValidationEnvironment]
BodyExtentResolver = Callable[[Mapping[str, Any], object], float | None]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    capability_id: str
    function_name: str
    passed: bool
    elapsed_s: float
    result: Any
    observed_measurements: Mapping[str, Any]
    target_measurements: Mapping[str, Any]
    tolerances: Mapping[str, Any]
    measurement_failures: tuple[str, ...]
    forbidden_evidence: Mapping[str, Any]
    framework_diagnostics: Mapping[str, Any]
    exception_type: str | None = None
    exception_message: str | None = None
    traceback_lines: tuple[str, ...] = ()
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ARM_POSITION_KEYS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
)


def _repair_execution_evidence(item: CaseResult) -> dict[str, Any]:
    """Expose concise generated-execution facts without requiring inference.

    The complete trusted diagnostics remain available separately.  This record
    keeps the exact generated return and the last commanded-versus-observed arm
    residuals close to the failure message so a repair Agent does not guess a
    status or phase from an oracle-only mismatch.
    """

    result = item.result
    reported_phase = (
        result.get("phase_reached")
        if isinstance(result, Mapping)
        and isinstance(result.get("phase_reached"), str)
        else None
    )
    returned_status = (
        result.get("status")
        if isinstance(result, Mapping) and isinstance(result.get("status"), str)
        else None
    )
    evidence: dict[str, Any] = {
        "returned_result": result,
        "returned_status": returned_status,
        "reported_phase": reported_phase,
        "framework_wall_clock_elapsed_s": item.elapsed_s,
        "framework_hard_timeout": item.timed_out,
    }

    diagnostics = item.framework_diagnostics
    harness_timeout = diagnostics.get("validation_harness_timeout")
    if isinstance(harness_timeout, Mapping):
        evidence["framework_hard_timeout_contract"] = dict(harness_timeout)
    last_action = diagnostics.get("last_action")
    final_positions = diagnostics.get("final_joint_positions")
    if isinstance(last_action, Mapping) and isinstance(final_positions, Mapping):
        requested = last_action.get("requested")
        if isinstance(requested, Mapping):
            residuals: dict[str, float] = {}
            for key in _ARM_POSITION_KEYS:
                target = requested.get(key)
                observed = final_positions.get(key)
                if (
                    isinstance(target, (int, float))
                    and not isinstance(target, bool)
                    and math.isfinite(float(target))
                    and isinstance(observed, (int, float))
                    and not isinstance(observed, bool)
                    and math.isfinite(float(observed))
                ):
                    residuals[key] = abs(float(target) - float(observed))
            if residuals:
                evidence["last_action_arm_residuals_abs_sdk_degrees"] = residuals
        simulation_time = last_action.get("simulation_time_s")
        if (
            isinstance(simulation_time, (int, float))
            and not isinstance(simulation_time, bool)
            and math.isfinite(float(simulation_time))
        ):
            evidence["last_action_simulation_time_s"] = float(simulation_time)
    return evidence


@dataclass(frozen=True)
class DirectValidationReport:
    schema_version: str
    passed: bool
    suite_sha256: str
    package_root: str
    package_sha256: str
    package_unchanged: bool
    cases: tuple[CaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "suite_sha256": self.suite_sha256,
            "package_root": self.package_root,
            "package_sha256": self.package_sha256,
            "package_unchanged": self.package_unchanged,
            "cases": [item.to_dict() for item in self.cases],
            "summary": {
                "total": len(self.cases),
                "passed": sum(item.passed for item in self.cases),
                "failed": sum(not item.passed for item in self.cases),
            },
        }

    def failure_feedback(self, *, repair_round: int) -> dict[str, Any]:
        failures = []
        if not self.package_unchanged:
            failures.append(
                {
                    "code": "PACKAGE_MUTATED_DURING_VALIDATION",
                    "message": "generated package bytes changed while direct validation executed",
                    "capability_id": None,
                    "case_id": None,
                    "observed": "changed",
                    "target": self.package_sha256,
                    "gap": None,
                    "traceback": None,
                }
            )
        for item in self.cases:
            if item.passed:
                continue
            failures.append(
                {
                    "code": (
                        "DIRECT_TIMEOUT"
                        if item.timed_out
                        else "DIRECT_EXCEPTION"
                        if item.exception_type
                        else "DIRECT_ORACLE_MISMATCH"
                    ),
                    "message": (
                        item.exception_message
                        or "; ".join(item.measurement_failures)
                        or "direct validation failed"
                    ),
                    "capability_id": item.capability_id,
                    "case_id": item.case_id,
                    "observed": item.observed_measurements,
                    "target": item.target_measurements,
                    "gap": {
                        "tolerances": item.tolerances,
                        "failures": list(item.measurement_failures),
                        "forbidden": item.forbidden_evidence,
                    },
                    "execution": _repair_execution_evidence(item),
                    "diagnostics": item.framework_diagnostics,
                    "traceback": "\n".join(item.traceback_lines) or None,
                }
            )
        return {
            "schema_version": "robot_capability.failure_feedback.v1",
            "stage": "direct_function",
            "repair_round": repair_round,
            "failures": failures,
        }


class DirectValidationError(RuntimeError):
    pass


class InvalidValidationCaseError(DirectValidationError):
    """A frozen case could not be executed by the trusted harness.

    This is a validation-suite defect, not evidence about generated code, so it
    must never be returned to the Generation Agent as repair feedback.
    """


class ValidationHarnessInfrastructureError(DirectValidationError):
    """Framework-owned environment setup or teardown failed."""


class FrameworkEvidenceError(BaseException):
    """Out-of-band failure in framework-owned evidence collection.

    This deliberately inherits from :class:`BaseException`, rather than
    :class:`Exception`, so ordinary generated-code recovery blocks cannot turn
    a broken renderer, recorder, or observer into an apparently valid function
    result.  The trusted harness catches it explicitly and terminates the run as
    infrastructure failure.
    """


def _contains_framework_evidence_error(error: BaseException) -> bool:
    """Return whether an exception chain contains the framework sentinel."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, FrameworkEvidenceError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _phase_error(
    error_type: type[DirectValidationError],
    *,
    case_id: str,
    phase: str,
    cause: BaseException,
) -> DirectValidationError:
    # Do not include the underlying message: reset/oracle/transport exceptions
    # can contain private paths or validation details.  The terminal report
    # records only this classification and cause type.
    return error_type(
        f"case {case_id!r} failed trusted phase {phase!r} "
        f"with {type(cause).__name__}"
    )


_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s:'\"]+/)*[^\s:'\"]+")


def _sanitized_exception_message(exception: BaseException) -> str:
    text = str(exception).replace("\n", " ")[:500]
    return _ABSOLUTE_PATH.sub("<redacted-path>", text)


def _generated_traceback_lines(exception: BaseException, package_root: Path) -> tuple[str, ...]:
    """Expose only generated-package frame metadata, never harness source."""

    lines: list[str] = []
    for frame in traceback.extract_tb(exception.__traceback__):
        try:
            relative = Path(frame.filename).resolve().relative_to(package_root.resolve())
        except (OSError, ValueError):
            continue
        lines.append(f"{relative.as_posix()}:{frame.lineno} in {frame.name}")
    return tuple(lines[-4:])


def _load_module(package_root: Path, module_label: str) -> ModuleType:
    return load_isolated_generated_module(
        package_root,
        module_label,
        namespace_label="direct",
    )


def _invoke_with_timeout(
    function: Callable[..., Any],
    runtime: Any,
    arguments: Mapping[str, Any],
    timeout_s: float,
) -> tuple[Any, BaseException | None, bool, float]:
    """Invoke under a trusted host-monotonic wall-clock hard deadline."""

    if (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGALRM")
    ):
        class _DirectDeadline(BaseException):
            pass

        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)

        def deadline_handler(_: int, __: object) -> None:
            raise _DirectDeadline()

        started = time.monotonic()
        signal.signal(signal.SIGALRM, deadline_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        try:
            return function(runtime, **dict(arguments)), None, False, time.monotonic() - started
        except _DirectDeadline:
            return None, None, True, time.monotonic() - started
        except BaseException as exc:
            return None, exc, False, time.monotonic() - started
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)

    # Non-POSIX/non-main-thread fallback is retained for library portability;
    # the production P0 path above is the hard-interrupting implementation.
    output: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            output.put(("result", function(runtime, **dict(arguments))))
        except BaseException as exc:  # captured and sanitized below
            output.put(("exception", exc))

    thread = threading.Thread(target=target, name="direct-capability-call", daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(timeout_s)
    elapsed = time.monotonic() - started
    if thread.is_alive():
        return None, None, True, elapsed
    try:
        kind, value = output.get_nowait()
    except queue.Empty:
        return None, RuntimeError("capability thread exited without a result"), False, elapsed
    if kind == "exception":
        return None, value, False, elapsed
    return value, None, False, elapsed


def _json_serializable(value: Any) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False


def _result_contract_errors(result: Any, contract: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(result, Mapping):
        return ["result must be a JSON object"]
    if not _json_serializable(result):
        return ["result is not finite JSON-serializable data"]
    if not isinstance(contract, Mapping):
        return []
    errors: list[str] = []
    required = contract.get("required", [])
    if isinstance(required, list):
        missing = [name for name in required if name not in result]
        if missing:
            errors.append(f"result lacks required fields {missing}")
    status_values = contract.get("status_values")
    if isinstance(status_values, list) and result.get("status") not in status_values:
        errors.append(f"result.status is not one of {status_values}")
    success_status_values = contract.get("success_status_values")
    if (
        isinstance(success_status_values, list)
        and result.get("status") not in success_status_values
    ):
        errors.append(
            f"result.status does not attest success; expected one of {success_status_values}"
        )
    return errors


def _compare(
    observed: Any,
    target: Any,
    tolerance: Any,
    path: str,
) -> list[str]:
    if isinstance(target, Mapping):
        if not isinstance(observed, Mapping):
            return [f"{path}: observed value is not an object"]
        failures: list[str] = []
        for key, expected in target.items():
            if key not in observed:
                failures.append(f"{path}.{key}: measurement missing")
                continue
            nested_tolerance = tolerance.get(key, 0) if isinstance(tolerance, Mapping) else tolerance
            failures.extend(_compare(observed[key], expected, nested_tolerance, f"{path}.{key}"))
        return failures
    if isinstance(target, Sequence) and not isinstance(target, (str, bytes)):
        if not isinstance(observed, Sequence) or isinstance(observed, (str, bytes)):
            return [f"{path}: observed value is not an array"]
        if len(observed) != len(target):
            return [f"{path}: length {len(observed)} != expected {len(target)}"]
        failures = []
        for index, expected in enumerate(target):
            nested_tolerance = (
                tolerance[index]
                if isinstance(tolerance, Sequence)
                and not isinstance(tolerance, (str, bytes))
                and index < len(tolerance)
                else tolerance
            )
            failures.extend(_compare(observed[index], expected, nested_tolerance, f"{path}[{index}]"))
        return failures
    if isinstance(target, bool):
        return [] if observed is target else [f"{path}: {observed!r} != {target!r}"]
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return [f"{path}: observed value is not numeric"]
        allowed = float(tolerance or 0)
        if not math.isfinite(allowed) or allowed < 0:
            return [f"{path}: tolerance must be a finite non-negative number"]
        if not math.isfinite(float(observed)) or not math.isfinite(float(target)):
            return [f"{path}: observed and target values must be finite"]
        error = abs(float(observed) - float(target))
        if not math.isfinite(error) or error > allowed:
            return [f"{path}: absolute error {error:.9g} exceeds {allowed:.9g}"]
        return []
    return [] if observed == target else [f"{path}: {observed!r} != {target!r}"]


def _trusted_g3_diagnostic_errors(
    case: Mapping[str, Any],
    manifest_item: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> list[str]:
    """Enforce fixed physical G3 postconditions omitted from model-authored cases.

    The validation LLM still authors one compact goal/tolerance case per
    capability.  Actual MuJoCo diagnostics then add non-negotiable dynamics,
    contact, and path checks so a moving, still-grasped, or swept placement
    cannot pass merely because its terminal center is near the target.
    """

    if case.get("module") != "g3":
        return []
    if diagnostics.get("source") != "trusted_mujoco_mjdata_and_bridge_trace":
        return []
    binding = manifest_item.get("validation_binding")
    if not isinstance(binding, Mapping):
        raise TypeError("trusted G3 diagnostics require a validation binding")
    effect = binding.get("effect")
    if effect not in {
        "object_source_to_target",
        "object_source_plus_height_delta",
        "object_move_sequence",
    }:
        raise TypeError("trusted G3 diagnostics received an unsupported effect")
    target_measurements = case.get("target_measurements")
    targets = (
        target_measurements.get("object_positions_m")
        if isinstance(target_measurements, Mapping)
        else None
    )
    velocities = diagnostics.get("final_object_linear_velocities_m_s")
    motion = diagnostics.get("object_motion_summaries")
    terminal_pairs = diagnostics.get("terminal_contact_pairs")
    if (
        not isinstance(targets, Mapping)
        or not targets
        or not isinstance(velocities, Mapping)
        or not isinstance(motion, Mapping)
        or not isinstance(terminal_pairs, list)
    ):
        raise TypeError("trusted G3 diagnostics are incomplete")

    normalized_pairs: set[frozenset[str]] = set()
    for pair in terminal_pairs:
        if (
            not isinstance(pair, Sequence)
            or isinstance(pair, (str, bytes))
            or len(pair) != 2
            or not all(isinstance(item, str) and item for item in pair)
        ):
            raise TypeError("trusted G3 terminal contact pair is malformed")
        normalized_pairs.add(frozenset((str(pair[0]), str(pair[1]))))

    bodies = case.get("initial_state", {}).get("bodies", [])
    initial_z = {
        str(body.get("id")): float(body["position_m"][2])
        for body in bodies
        if isinstance(body, Mapping)
        and isinstance(body.get("id"), str)
        and isinstance(body.get("position_m"), Sequence)
        and not isinstance(body.get("position_m"), (str, bytes))
        and len(body["position_m"]) == 3
    }
    errors: list[str] = []
    placement_effect = effect in {"object_source_to_target", "object_move_sequence"}
    speed_limit_m_s = 0.03 if placement_effect else 0.04
    fixed_jaw_bodies = {"gripper", "camera_mount"}
    moving_jaw_body = "moving_jaw_so101_v1"

    for object_id, target_position in targets.items():
        if not isinstance(object_id, str):
            raise TypeError("trusted G3 target object ID is malformed")
        velocity = velocities.get(object_id)
        summary = motion.get(object_id)
        if (
            not isinstance(velocity, Sequence)
            or isinstance(velocity, (str, bytes))
            or len(velocity) != 3
            or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in velocity
            )
            or not isinstance(summary, Mapping)
        ):
            raise TypeError(f"trusted G3 diagnostics lack object {object_id!r}")
        speed = math.sqrt(sum(float(value) ** 2 for value in velocity))
        if speed > speed_limit_m_s + 1e-9:
            errors.append(
                f"trusted G3 terminal speed for {object_id!r} is {speed:.9g} m/s, "
                f"above {speed_limit_m_s:.9g} m/s"
            )
        if summary.get("ever_grasped_by_opposing_jaws") is not True:
            errors.append(
                f"trusted G3 trace never proved opposing-jaw grasp for {object_id!r}"
            )

        has_moving = frozenset((object_id, moving_jaw_body)) in normalized_pairs
        has_fixed = any(
            frozenset((object_id, body)) in normalized_pairs
            for body in fixed_jaw_bodies
        )
        if placement_effect:
            if has_moving or has_fixed:
                errors.append(
                    f"trusted G3 placement left {object_id!r} in terminal gripper contact"
                )
            if frozenset((object_id, "world")) not in normalized_pairs:
                errors.append(
                    f"trusted G3 placement did not leave {object_id!r} supported"
                )
            maximum_lift = summary.get("maximum_lift_above_initial_m")
            if (
                not isinstance(maximum_lift, (int, float))
                or isinstance(maximum_lift, bool)
                or not math.isfinite(float(maximum_lift))
                or object_id not in initial_z
                or not isinstance(target_position, Sequence)
                or isinstance(target_position, (str, bytes))
                or len(target_position) != 3
            ):
                raise TypeError(
                    f"trusted G3 lift diagnostics are incomplete for {object_id!r}"
                )
            # The verified tabletop profile lifts 0.09 m.  The extra 0.02 m is
            # deterministic validation slack, not a generated trajectory knob.
            allowed_lift = max(
                0.0, float(target_position[2]) - initial_z[object_id]
            ) + 0.11
            if float(maximum_lift) > allowed_lift + 1e-9:
                errors.append(
                    f"trusted G3 transient lift for {object_id!r} is "
                    f"{float(maximum_lift):.9g} m, above {allowed_lift:.9g} m; "
                    "interpolate lift/transport instead of sending a swept jump"
                )
        elif not (has_moving and has_fixed):
            errors.append(
                f"trusted G3 lift did not retain opposing-jaw terminal contact for "
                f"{object_id!r}"
            )
    return errors


def _measurement_shape_errors(observed: Any, target: Any, path: str) -> list[str]:
    """Check that an oracle response completely and comparably covers a target."""

    if isinstance(target, Mapping):
        if not isinstance(observed, Mapping):
            return [f"{path}: observed value is not an object"]
        failures: list[str] = []
        for key, expected in target.items():
            if key not in observed:
                failures.append(f"{path}.{key}: measurement missing")
                continue
            failures.extend(
                _measurement_shape_errors(observed[key], expected, f"{path}.{key}")
            )
        return failures
    if isinstance(target, Sequence) and not isinstance(target, (str, bytes)):
        if not isinstance(observed, Sequence) or isinstance(observed, (str, bytes)):
            return [f"{path}: observed value is not an array"]
        if len(observed) != len(target):
            return [f"{path}: length {len(observed)} != expected {len(target)}"]
        failures = []
        for index, expected in enumerate(target):
            failures.extend(
                _measurement_shape_errors(observed[index], expected, f"{path}[{index}]")
            )
        return failures
    if isinstance(target, bool):
        return (
            []
            if isinstance(observed, bool)
            else [f"{path}: observed value is not boolean"]
        )
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return [f"{path}: observed value is not numeric"]
        if not math.isfinite(float(observed)):
            return [f"{path}: observed numeric value is not finite"]
        return []
    if target is None:
        return [] if observed is None else [f"{path}: observed value is not null"]
    return (
        []
        if isinstance(observed, type(target))
        else [f"{path}: observed value has incompatible type"]
    )


def _preflight_body_extent_m(
    body: Mapping[str, Any],
    semantics: object,
) -> float | None:
    kind = body.get("kind")
    if kind == "cube":
        raw_size = body.get("size_m", [0.03, 0.03, 0.03])
        if isinstance(raw_size, (int, float)) and not isinstance(raw_size, bool):
            sizes = [float(raw_size)] * 3
        elif (
            isinstance(raw_size, Sequence)
            and not isinstance(raw_size, (str, bytes))
            and len(raw_size) == 3
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in raw_size
            )
        ):
            sizes = [float(value) for value in raw_size]
        else:
            return None
        if not all(math.isfinite(value) and value > 0.0 for value in sizes):
            return None
        if semantics == "cube_edge_m":
            return sizes[0] if max(sizes) - min(sizes) <= 1e-9 else None
        if semantics == "maximum_horizontal_extent_m":
            return max(sizes[0], sizes[1])
        return None
    if kind == "cylinder":
        radius = body.get("radius_m", 0.015)
        if (
            semantics in {"cylinder_diameter_m", "maximum_horizontal_extent_m"}
            and isinstance(radius, (int, float))
            and not isinstance(radius, bool)
            and math.isfinite(float(radius))
            and float(radius) > 0.0
        ):
            return 2.0 * float(radius)
    return None


def _preflight_g3_source_errors(
    case: Mapping[str, Any],
    package_manifest: Mapping[str, Any],
    observed: Mapping[str, Any],
    *,
    tolerance_m: float = 0.005,
    body_extent_resolver: BodyExtentResolver | None = None,
) -> list[str]:
    if case.get("module") != "g3":
        return []
    capability_id = case.get("capability_id")
    capabilities = package_manifest.get("capabilities", [])
    api = next(
        (
            item
            for item in capabilities
            if isinstance(item, Mapping)
            and item.get("capability_id") == capability_id
        ),
        None,
    ) if isinstance(capabilities, list) else None
    if not isinstance(api, Mapping):
        return ["public API capability is absent during source-baseline preflight"]
    binding = api.get("validation_binding")
    arguments = case.get("call_arguments")
    initial_state = case.get("initial_state")
    if not isinstance(binding, Mapping) or not isinstance(arguments, Mapping):
        return ["G3 source-baseline preflight lacks a structured binding"]
    if not isinstance(initial_state, Mapping):
        return ["G3 source-baseline preflight lacks an initial state"]

    raw_sources: list[Any] = []
    sequence_extent_by_source: dict[tuple[float, float, float], Any] = {}
    effect = binding.get("effect")
    if effect in {"object_source_to_target", "object_source_plus_height_delta"}:
        raw_sources.append(arguments.get(binding.get("source_argument")))
    elif effect == "object_move_sequence":
        moves = arguments.get(binding.get("moves_argument"))
        source_field = binding.get("source_field")
        target_field = binding.get("target_field")
        object_extent_field = binding.get("object_extent_field")
        signature = api.get("signature")
        parameters = (
            signature.get("parameters", [])
            if isinstance(signature, Mapping)
            else []
        )
        parameter_names = {
            str(parameter.get("name"))
            for parameter in parameters
            if isinstance(parameter, Mapping)
            and isinstance(parameter.get("name"), str)
        } if isinstance(parameters, Sequence) and not isinstance(
            parameters, (str, bytes)
        ) else set()
        field_parameter_collisions = sorted(
            {
                name
                for name in (source_field, target_field, object_extent_field)
                if isinstance(name, str) and name in parameter_names
            }
        )
        if field_parameter_collisions:
            return [
                "move-sequence source/target/extent fields are fixed literal "
                "move-item keys, not public selector arguments; "
                f"conflicts={field_parameter_collisions!r}"
            ]
        move_item_fields = (source_field, target_field, object_extent_field)
        if (
            not all(isinstance(name, str) for name in move_item_fields)
            or len(set(move_item_fields)) != len(move_item_fields)
        ):
            return [
                "move-sequence source_field, target_field, and "
                "object_extent_field must be distinct literal keys"
            ]
        if isinstance(moves, list):
            for move in moves:
                if not isinstance(move, Mapping):
                    continue
                raw_source = move.get(source_field)
                raw_sources.append(raw_source)
                if (
                    isinstance(raw_source, Sequence)
                    and not isinstance(raw_source, (str, bytes))
                    and len(raw_source) == 3
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                        for value in raw_source
                    )
                ):
                    sequence_extent_by_source[
                        tuple(float(value) for value in raw_source)
                    ] = move.get(object_extent_field)

    sources = [
        tuple(float(value) for value in source)
        for source in raw_sources
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes))
        and len(source) == 3
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in source
        )
    ]
    bodies = initial_state.get("bodies", [])
    authored: list[
        tuple[str, tuple[float, float, float], Mapping[str, Any]]
    ] = []
    if isinstance(bodies, list):
        for body in bodies:
            if not isinstance(body, Mapping):
                continue
            object_id = body.get("id")
            position = body.get("position_m")
            if (
                isinstance(object_id, str)
                and isinstance(position, Sequence)
                and not isinstance(position, (str, bytes))
                and len(position) == 3
                and all(isinstance(value, (int, float)) for value in position)
            ):
                authored.append(
                    (object_id, tuple(float(value) for value in position), body)
                )
    observed_positions = observed.get("object_positions_m")
    if not isinstance(observed_positions, Mapping):
        return ["baseline object_positions_m measurement is unavailable"]

    errors: list[str] = []
    used_objects: set[str] = set()
    for source in sources:
        match = next(
            (
                (object_id, position, body)
                for object_id, position, body in authored
                if object_id not in used_objects
                and all(
                    math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)
                    for left, right in zip(position, source, strict=True)
                )
            ),
            None,
        )
        if match is None:
            errors.append(f"bound source {list(source)!r} has no authored free object")
            continue
        object_id, _, body = match
        used_objects.add(object_id)
        if effect == "object_move_sequence":
            supplied_extent = sequence_extent_by_source.get(source)
            expected_extent = (
                body_extent_resolver(
                    body,
                    binding.get("object_extent_semantics"),
                )
                if body_extent_resolver is not None
                else _preflight_body_extent_m(
                    body,
                    binding.get("object_extent_semantics"),
                )
            )
            if (
                expected_extent is None
                or not isinstance(supplied_extent, (int, float))
                or isinstance(supplied_extent, bool)
                or not math.isfinite(float(supplied_extent))
                or float(supplied_extent) <= 0.0
                or not math.isclose(
                    float(supplied_extent),
                    expected_extent,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                errors.append(
                    f"bound move extent for {object_id!r} does not match the "
                    "authored initial-state geometry"
                )
        actual = observed_positions.get(object_id)
        if (
            not isinstance(actual, Sequence)
            or isinstance(actual, (str, bytes))
            or len(actual) != 3
            or not all(isinstance(value, (int, float)) for value in actual)
        ):
            errors.append(f"bound source object {object_id!r} lacks a measured baseline")
            continue
        gaps = [
            abs(float(value) - source[index])
            for index, value in enumerate(actual)
        ]
        if max(gaps) > tolerance_m:
            errors.append(
                f"bound source object {object_id!r} settled away from its public "
                f"source by {max(gaps):.6f}m (limit {tolerance_m:.6f}m)"
            )
    return errors


def preflight_validation_suite(
    suite: Mapping[str, Any],
    *,
    environment_factory: EnvironmentFactory,
    package_manifest: Mapping[str, Any] | None = None,
    body_extent_resolver: BodyExtentResolver | None = None,
) -> tuple[str, ...]:
    """Execute framework-only reset/baseline checks before a suite can freeze.

    The generated function is deliberately not imported or invoked.  A valid
    action case must start outside its success tolerance and in a safe state;
    otherwise a no-op implementation could pass.  Invalid case details become
    deterministic suite-review feedback, while environment construction or
    teardown failures remain infrastructure errors.
    """

    errors: list[str] = []
    cases = suite.get("cases", [])
    if not isinstance(cases, list):
        return ("cases must be an array before executable preflight",)
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            errors.append(f"cases[{index}] is not executable because it is not an object")
            continue
        case_id = str(case.get("case_id", f"cases[{index}]"))
        try:
            environment = environment_factory(case)
        except BaseException as exc:
            raise _phase_error(
                ValidationHarnessInfrastructureError,
                case_id=case_id,
                phase="preflight_environment_factory",
                cause=exc,
            ) from exc
        invalid: tuple[str, BaseException] | None = None
        try:
            try:
                environment.reset(case.get("initial_state", {}))
                observed = dict(environment.measure(case.get("target_measurements", {})))
                forbidden = dict(
                    environment.forbidden(case.get("forbidden_conditions", []))
                )
            except BaseException as exc:
                if _contains_framework_evidence_error(exc):
                    raise _phase_error(
                        ValidationHarnessInfrastructureError,
                        case_id=case_id,
                        phase="preflight_framework_evidence",
                        cause=exc,
                    ) from exc
                invalid = ("reset_or_measure", exc)
            else:
                target_measurements = case.get("target_measurements", {})
                shape_failures = _measurement_shape_errors(
                    observed, target_measurements, "baseline"
                )
                if shape_failures:
                    errors.append(
                        f"cases[{index}] {case_id!r}: baseline measurements are "
                        "incomplete or incomparable: " + "; ".join(shape_failures)
                    )
                else:
                    if package_manifest is not None:
                        errors.extend(
                            f"cases[{index}] {case_id!r}: {message}"
                            for message in _preflight_g3_source_errors(
                                case,
                                package_manifest,
                                observed,
                                body_extent_resolver=body_extent_resolver,
                            )
                        )
                    baseline_failures = _compare(
                        observed,
                        target_measurements,
                        case.get("tolerances", {}),
                        "baseline",
                    )
                    if not baseline_failures:
                        errors.append(
                            f"cases[{index}] {case_id!r}: initial state already satisfies "
                            "the complete target within tolerance"
                        )
                tripped = sorted(
                    name for name, evidence in forbidden.items() if bool(evidence)
                )
                if tripped:
                    errors.append(
                        f"cases[{index}] {case_id!r}: initial state trips forbidden "
                        f"conditions {tripped}"
                    )
        finally:
            try:
                environment.close()
            except BaseException as exc:
                raise _phase_error(
                    ValidationHarnessInfrastructureError,
                    case_id=case_id,
                    phase="preflight_environment_close",
                    cause=exc,
                ) from exc
        if invalid is not None:
            phase, cause = invalid
            errors.append(
                f"cases[{index}] {case_id!r}: executable preflight {phase} failed "
                f"with {type(cause).__name__}"
            )
    return tuple(errors)


def execute_case(
    *,
    package_root: str | Path,
    package_manifest: Mapping[str, Any],
    case: Mapping[str, Any],
    environment_factory: EnvironmentFactory,
) -> CaseResult:
    """Import and directly call one function in a fresh private environment."""

    root = Path(package_root)
    module_name = str(case["module"])
    function_name = str(case["function_name"])
    capability_id = str(case["capability_id"])
    case_id = str(case["case_id"])
    try:
        environment = environment_factory(case)
    except BaseException as exc:
        raise _phase_error(
            ValidationHarnessInfrastructureError,
            case_id=case_id,
            phase="environment_factory",
            cause=exc,
        ) from exc
    result: Any = None
    exception: BaseException | None = None
    timed_out = False
    elapsed = 0.0
    observed: Mapping[str, Any] = {}
    forbidden: Mapping[str, Any] = {}
    diagnostics: Mapping[str, Any] = {}
    measurement_failures: list[str] = []
    manifest_item = next(
        (
            item
            for item in package_manifest.get("capabilities", [])
            if item.get("capability_id") == capability_id
        ),
        {},
    )
    invalid_case_error: BaseException | None = None
    invalid_case_phase: str | None = None
    infrastructure_error: BaseException | None = None
    infrastructure_phase: str | None = None
    try:
        try:
            environment.reset(case.get("initial_state", {}))
        except BaseException as exc:
            if _contains_framework_evidence_error(exc):
                infrastructure_error = exc
                infrastructure_phase = "environment_reset_framework_evidence"
            else:
                invalid_case_error = exc
                invalid_case_phase = "environment_reset"
        if invalid_case_error is None and infrastructure_error is None:
            try:
                module = _load_module(root, module_name)
                function = getattr(module, function_name)
            except BaseException as exc:
                # Import/name errors originate in the generated package and are
                # therefore legitimate repair feedback.
                exception = exc
            else:
                preparer = getattr(environment, "prepare_generated_function", None)
                if preparer is not None:
                    if not callable(preparer):
                        infrastructure_error = TypeError(
                            "environment prepare_generated_function hook is not callable"
                        )
                        infrastructure_phase = "generated_function_prepare"
                    else:
                        try:
                            preparer(function)
                        except BaseException as exc:
                            # Preparation is framework-owned even if its
                            # implementation raises an ordinary exception.  It
                            # must never become generated-package repair feedback.
                            infrastructure_error = exc
                            infrastructure_phase = "generated_function_prepare"
                if infrastructure_error is None:
                    harness_timeout_s = float(case[HARNESS_TIMEOUT_FIELD])
                    result, exception, timed_out, elapsed = _invoke_with_timeout(
                        function,
                        environment.runtime,
                        # Generated code owns neither the frozen case nor its
                        # nested move records.  A shallow kwargs copy still
                        # lets a function mutate lists/dicts inside the suite,
                        # breaking its evidence hash after execution.
                        deepcopy(case.get("call_arguments", {})),
                        harness_timeout_s,
                    )
                    if exception is not None and _contains_framework_evidence_error(exception):
                        infrastructure_error = exception
                        infrastructure_phase = "generated_call_framework_evidence"
        if (
            invalid_case_error is None
            and infrastructure_error is None
            and exception is None
            and not timed_out
        ):
            try:
                observed = dict(environment.measure(case["target_measurements"]))
                forbidden = dict(
                    environment.forbidden(case.get("forbidden_conditions", []))
                )
                measurement_failures.extend(
                    _compare(
                        observed,
                        case["target_measurements"],
                        case["tolerances"],
                        "measurements",
                    )
                )
                measurement_failures.extend(
                    _result_contract_errors(result, manifest_item.get("result_contract"))
                )
                tripped = [
                    name for name, evidence in forbidden.items() if bool(evidence)
                ]
                if tripped:
                    measurement_failures.append(
                        f"forbidden conditions observed: {tripped}"
                    )
            except BaseException as exc:
                # Measurement selection and oracle execution are owned by the
                # frozen case/harness, not by the generated function.
                if _contains_framework_evidence_error(exc):
                    infrastructure_error = exc
                    infrastructure_phase = "measurement_framework_evidence"
                else:
                    invalid_case_error = exc
                    invalid_case_phase = "measurement_oracle"
        if invalid_case_error is None and infrastructure_error is None:
            diagnostics_callback = getattr(environment, "diagnostics", None)
            if callable(diagnostics_callback):
                try:
                    value = diagnostics_callback()
                    if not isinstance(value, Mapping) or not _json_serializable(value):
                        raise TypeError(
                            "framework diagnostics must be a finite JSON object"
                        )
                    diagnostics = dict(value)
                    if exception is None and not timed_out:
                        measurement_failures.extend(
                            _trusted_g3_diagnostic_errors(
                                case,
                                manifest_item,
                                diagnostics,
                            )
                        )
                except BaseException as exc:
                    infrastructure_error = exc
                    infrastructure_phase = "framework_diagnostics"
            if infrastructure_error is None:
                diagnostics = dict(diagnostics)
                diagnostics["validation_harness_timeout"] = {
                    "deadline_field": HARNESS_TIMEOUT_FIELD,
                    "deadline_s": float(case[HARNESS_TIMEOUT_FIELD]),
                    "clock": HARNESS_TIMEOUT_CLOCK,
                }
    finally:
        try:
            environment.close()
        except BaseException as close_error:
            if infrastructure_error is None:
                infrastructure_error = close_error
                infrastructure_phase = "environment_close"
    if infrastructure_error is not None:
        raise _phase_error(
            ValidationHarnessInfrastructureError,
            case_id=case_id,
            phase=infrastructure_phase or "framework_infrastructure",
            cause=infrastructure_error,
        ) from infrastructure_error
    if invalid_case_error is not None:
        raise _phase_error(
            InvalidValidationCaseError,
            case_id=case_id,
            phase=invalid_case_phase or "validation_case",
            cause=invalid_case_error,
        ) from invalid_case_error
    traceback_lines = (
        () if exception is None else _generated_traceback_lines(exception, root)
    )
    passed = exception is None and not timed_out and not measurement_failures
    return CaseResult(
        case_id=case_id,
        capability_id=capability_id,
        function_name=function_name,
        passed=passed,
        elapsed_s=elapsed,
        result=result if _json_serializable(result) else repr(result),
        observed_measurements=observed,
        target_measurements=case["target_measurements"],
        tolerances=case["tolerances"],
        measurement_failures=tuple(measurement_failures),
        forbidden_evidence=forbidden,
        framework_diagnostics=diagnostics,
        exception_type=None if exception is None else type(exception).__name__,
        exception_message=None if exception is None else _sanitized_exception_message(exception),
        traceback_lines=traceback_lines,
        timed_out=timed_out,
    )


def run_direct_validation(
    *,
    package_root: str | Path,
    package_manifest: Mapping[str, Any],
    suite: Mapping[str, Any],
    environment_factory: EnvironmentFactory,
    output_path: str | Path | None = None,
) -> DirectValidationReport:
    """Run the complete frozen suite from clean environments."""

    suite_digest_before = sha256_json(suite)
    package_digest_before = package_tree_sha256(package_root)
    results = tuple(
        execute_case(
            package_root=package_root,
            package_manifest=package_manifest,
            case=deepcopy(case),
            environment_factory=environment_factory,
        )
        for case in suite.get("cases", [])
    )
    package_digest_after = package_tree_sha256(package_root)
    package_unchanged = package_digest_before == package_digest_after
    report = DirectValidationReport(
        schema_version="robot_capability.direct_validation_report.v1",
        passed=bool(results) and all(item.passed for item in results) and package_unchanged,
        suite_sha256=suite_digest_before,
        package_root=str(Path(package_root).resolve()),
        package_sha256=package_digest_before,
        package_unchanged=package_unchanged,
        cases=results,
    )
    if output_path is not None:
        atomic_write_json(output_path, report.to_dict())
    return report
