from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import sys
import time
import types
from typing import Any, Callable, Mapping, Sequence

import pytest

from soarm_demo.audit import sha256_json
from soarm_demo.direct_validation import (
    FrameworkEvidenceError,
    InvalidValidationCaseError,
    ValidationHarnessInfrastructureError,
    _load_module,
    _compare,
    _preflight_g3_source_errors,
    _trusted_g3_diagnostic_errors,
    preflight_validation_suite,
    run_direct_validation,
)
from soarm_demo.oracle import _LeRobotControlFacade, _release_facade


@dataclass
class FakeRuntime:
    calls: list[float] = field(default_factory=list)
    position: float = 0.0


class FakeEnvironment:
    def __init__(self, *, measurement_offset: float = 0.0) -> None:
        self.runtime = FakeRuntime()
        self.measurement_offset = measurement_offset
        self.reset_state: Mapping[str, Any] | None = None
        self.closed = False

    def reset(self, initial_state: Mapping[str, Any]) -> None:
        self.reset_state = dict(initial_state)

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
        assert requests == {"position": 0.5}
        return {"position": self.runtime.position + self.measurement_offset}

    def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]:
        return {name: False for name in conditions}

    def diagnostics(self) -> Mapping[str, Any]:
        return {
            "source": "fake_trusted_environment",
            "final_position": getattr(self.runtime, "position", None),
        }

    def close(self) -> None:
        self.closed = True


def _write_direct_package(root: Path) -> None:
    package = root / "generated_capability_package"
    package.mkdir(parents=True)
    (package / "g1.py").write_text(
        """
def command_joint(runtime: object, position: float) -> dict[str, object]:
    runtime.calls.append(position)
    runtime.position = position
    return {"status": "ok", "commanded_position": position}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_direct_loader_supports_relative_import_without_init_or_module_pollution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "generated_capability_package"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise RuntimeError('generated __init__ must not execute')\n",
        encoding="utf-8",
    )
    support = package / "_kinematics.py"
    support.write_text(
        "def _increment(value: float) -> float:\n    return value + 1.0\n",
        encoding="utf-8",
    )
    (package / "g2.py").write_text(
        "from ._kinematics import _increment\n\n"
        "def probe(value: float) -> float:\n    return _increment(value)\n",
        encoding="utf-8",
    )
    user_package = types.ModuleType("generated_capability_package")
    user_support = types.ModuleType("generated_capability_package._kinematics")
    user_support._increment = lambda value: -100.0
    monkeypatch.setitem(sys.modules, "generated_capability_package", user_package)
    monkeypatch.setitem(
        sys.modules,
        "generated_capability_package._kinematics",
        user_support,
    )
    before = {name for name in sys.modules if name.startswith("_soarm_direct_")}

    module = _load_module(tmp_path, "g2")

    assert module.probe(2.0) == 3.0
    assert sys.modules["generated_capability_package"] is user_package
    assert sys.modules["generated_capability_package._kinematics"] is user_support
    assert {
        name for name in sys.modules if name.startswith("_soarm_direct_")
    } == before

    support.write_text("raise RuntimeError('support import failed')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="support import failed"):
        _load_module(tmp_path, "g2")
    assert {
        name for name in sys.modules if name.startswith("_soarm_direct_")
    } == before


def test_direct_loader_rejects_symlinked_generated_source(tmp_path: Path) -> None:
    package = tmp_path / "generated_capability_package"
    package.mkdir()
    (package / "_kinematics.py").write_text(
        "def _identity(value: float) -> float:\n    return value\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    module = package / "g2.py"
    try:
        module.symlink_to(outside)
    except OSError:
        pytest.skip("filesystem does not permit symlinks")

    with pytest.raises(RuntimeError, match="unsafe filesystem binding"):
        _load_module(tmp_path, "g2")


def _manifest() -> dict[str, Any]:
    return {
        "capabilities": [
            {
                "capability_id": "G1.command_joint",
                "granularity": "G1",
                "module": "g1",
                "function_name": "command_joint",
                "result_contract": {
                    "type": "object",
                    "required": ["status", "commanded_position"],
                    "status_values": ["ok", "error"],
                    "success_status_values": ["ok"],
                },
            }
        ]
    }


def _suite() -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.validation_suite.v2",
        "cases": [
            {
                "case_id": "G1.command_joint.nominal",
                "capability_id": "G1.command_joint",
                "module": "g1",
                "function_name": "command_joint",
                "initial_state": {"position": 0.0},
                "call_arguments": {"position": 0.5},
                "target_measurements": {"position": 0.5},
                "tolerances": {"position": 0.01},
                "forbidden_conditions": ["collision"],
                "timeout_s": 1.0,
                "test_entrypoint": "soarm_demo.direct_validation:execute_case",
                "reference_provenance": [],
            }
        ],
    }


def test_direct_validation_imports_and_calls_python_function_without_agent_or_tool(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)
    environments: list[FakeEnvironment] = []

    def factory(case: Mapping[str, Any]) -> FakeEnvironment:
        assert case["function_name"] == "command_joint"
        environment = FakeEnvironment()
        environments.append(environment)
        return environment

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=factory,
        output_path=tmp_path / "report.json",
    )

    assert report.passed is True
    assert len(report.cases) == 1 and report.cases[0].passed is True
    assert environments[0].runtime.calls == [0.5]
    assert environments[0].reset_state == {"position": 0.0}
    assert environments[0].closed is True
    assert report.cases[0].observed_measurements == {"position": 0.5}
    assert (tmp_path / "report.json").is_file()


def test_direct_validation_prepares_generated_function_before_invocation(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class PreparingEnvironment(FakeEnvironment):
        def __init__(self) -> None:
            super().__init__()
            self.prepared_functions: list[str] = []

        def prepare_generated_function(self, function: Callable[..., Any]) -> None:
            assert self.reset_state == {"position": 0.0}
            assert self.runtime.calls == []
            self.prepared_functions.append(function.__name__)

    environment = PreparingEnvironment()
    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=lambda _: environment,
    )

    assert report.passed is True
    assert environment.prepared_functions == ["command_joint"]
    assert environment.runtime.calls == [0.5]
    assert environment.closed is True


def test_generated_function_prepare_failure_is_framework_infrastructure(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class BrokenPreparingEnvironment(FakeEnvironment):
        def prepare_generated_function(self, function: Callable[..., Any]) -> None:
            del function
            raise RuntimeError("private deterministic-clock binding detail")

    environment = BrokenPreparingEnvironment()
    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="generated_function_prepare",
    ) as caught:
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: environment,
        )

    assert "private deterministic-clock binding detail" not in str(caught.value)
    assert environment.runtime.calls == []
    assert environment.closed is True


def test_direct_validation_reports_oracle_mismatch_for_repair(tmp_path: Path) -> None:
    _write_direct_package(tmp_path)
    environments: list[FakeEnvironment] = []

    def factory(case: Mapping[str, Any]) -> FakeEnvironment:
        environment = FakeEnvironment(measurement_offset=0.2)
        environments.append(environment)
        return environment

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=factory,
    )
    feedback = report.failure_feedback(repair_round=1)

    assert report.passed is False
    assert report.cases[0].passed is False
    assert environments[0].runtime.calls == [0.5]
    assert environments[0].closed is True
    assert "absolute error" in "\n".join(report.cases[0].measurement_failures)
    assert feedback["stage"] == "direct_function"
    assert feedback["failures"][0]["code"] == "DIRECT_ORACLE_MISMATCH"
    assert feedback["failures"][0]["observed"] == {"position": 0.7}
    assert feedback["failures"][0]["target"] == {"position": 0.5}
    assert feedback["failures"][0]["diagnostics"] == {
        "source": "fake_trusted_environment",
        "final_position": 0.5,
        "validation_harness_timeout": {
            "deadline_field": "timeout_s",
            "deadline_s": 1.0,
            "clock": "host_monotonic_wall",
        },
    }
    execution = feedback["failures"][0]["execution"]
    assert execution["returned_result"] == {
        "status": "ok",
        "commanded_position": 0.5,
    }
    assert execution["returned_status"] == "ok"
    assert execution["reported_phase"] is None
    assert execution["framework_hard_timeout"] is False
    assert execution["framework_hard_timeout_contract"] == {
        "deadline_field": "timeout_s",
        "deadline_s": 1.0,
        "clock": "host_monotonic_wall",
    }
    assert execution["framework_wall_clock_elapsed_s"] >= 0.0
    assert "elapsed_s" not in execution


def test_direct_feedback_exposes_last_action_arm_residuals_without_inference(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class ResidualEnvironment(FakeEnvironment):
        def diagnostics(self) -> Mapping[str, Any]:
            return {
                "source": "fake_trusted_environment",
                "last_action": {
                    "simulation_time_s": 8.54,
                    "requested": {
                        "shoulder_pan.pos": -11.33,
                        "shoulder_lift.pos": 26.064,
                        "gripper.pos": 1.1,
                    },
                },
                "final_joint_positions": {
                    "shoulder_pan.pos": -11.334,
                    "shoulder_lift.pos": 25.020,
                    "gripper.pos": 17.0,
                },
            }

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=lambda _: ResidualEnvironment(measurement_offset=0.2),
    )
    execution = report.failure_feedback(repair_round=1)["failures"][0]["execution"]

    assert execution["last_action_simulation_time_s"] == 8.54
    assert execution["last_action_arm_residuals_abs_sdk_degrees"] == pytest.approx(
        {
            "shoulder_pan.pos": 0.004,
            "shoulder_lift.pos": 1.044,
        }
    )
    assert (
        "gripper.pos"
        not in execution["last_action_arm_residuals_abs_sdk_degrees"]
    )
    assert "last_action_arm_residuals_abs" not in execution


def test_non_success_result_status_fails_even_when_physical_target_is_met(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)
    module = tmp_path / "generated_capability_package/g1.py"
    module.write_text(
        '''
def command_joint(runtime: object, position: float) -> dict[str, object]:
    runtime.position = position
    return {"status": "error", "commanded_position": position}
'''.strip()
        + "\n",
        encoding="utf-8",
    )

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=lambda _: FakeEnvironment(),
    )

    assert report.passed is False
    assert "does not attest success" in "\n".join(report.cases[0].measurement_failures)
    feedback = report.failure_feedback(repair_round=1)
    assert feedback["failures"][0]["execution"]["returned_result"] == {
        "status": "error",
        "commanded_position": 0.5,
    }
    assert feedback["failures"][0]["execution"]["returned_status"] == "error"


def test_compare_rejects_non_finite_tolerance_instead_of_bypassing_oracle() -> None:
    failures = _compare(999.0, 0.0, float("nan"), "measurements.position")

    assert failures == [
        "measurements.position: tolerance must be a finite non-negative number"
    ]


def test_executable_preflight_rejects_target_already_true_at_baseline() -> None:
    suite = _suite()
    environment = FakeEnvironment()
    environment.runtime.position = 0.5

    errors = preflight_validation_suite(
        suite,
        environment_factory=lambda _: environment,
    )

    assert len(errors) == 1
    assert "initial state already satisfies" in errors[0]
    assert environment.closed is True


def test_executable_preflight_accepts_nontrivial_safe_baseline() -> None:
    errors = preflight_validation_suite(
        _suite(),
        environment_factory=lambda _: FakeEnvironment(),
    )

    assert errors == ()


def test_executable_preflight_rejects_g3_object_that_settles_away_from_bound_source() -> None:
    class G3Environment:
        runtime = FakeRuntime()
        closed = False

        def reset(self, initial_state: Mapping[str, Any]) -> None:
            del initial_state

        def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
            assert "object_positions_m" in requests
            return {
                "object_positions_m": {"cube": [0.30, 0.0, 0.035]},
                "finite": True,
            }

        def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]:
            return {name: False for name in conditions}

        def close(self) -> None:
            self.closed = True

    suite = {
        "schema_version": "robot_capability.validation_suite.v2",
        "cases": [
            {
                "case_id": "g3-source-drift",
                "capability_id": "G3.pick_and_place",
                "module": "g3",
                "function_name": "pick_and_place",
                "initial_state": {
                    "bodies": [
                        {
                            "id": "cube",
                            "kind": "cube",
                            "position_m": [0.30, 0.0, 0.055],
                        }
                    ]
                },
                "call_arguments": {
                    "object_position_m": [0.30, 0.0, 0.055],
                    "target_position_m": [0.40, 0.0, 0.035],
                },
                "target_measurements": {
                    "object_positions_m": {"cube": [0.40, 0.0, 0.035]},
                    "finite": True,
                },
                "tolerances": {"object_positions_m": {"cube": 0.02}},
                "forbidden_conditions": ["collision"],
                "timeout_s": 12.0,
                "test_entrypoint": "soarm_demo.direct_validation:execute_case",
                "reference_provenance": [],
            }
        ],
    }
    manifest = {
        "capabilities": [
            {
                "capability_id": "G3.pick_and_place",
                "validation_binding": {
                    "effect": "object_source_to_target",
                    "source_argument": "object_position_m",
                    "target_argument": "target_position_m",
                    "target_components": "xyz",
                },
            }
        ]
    }
    environment = G3Environment()

    errors = preflight_validation_suite(
        suite,
        environment_factory=lambda _: environment,
        package_manifest=manifest,
    )

    assert any("settled away from its public source" in error for error in errors)
    assert environment.closed is True


def test_executable_preflight_rejects_missing_or_incomparable_measurements() -> None:
    class MissingMeasurementEnvironment(FakeEnvironment):
        def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
            del requests
            return {}

    missing = preflight_validation_suite(
        _suite(),
        environment_factory=lambda _: MissingMeasurementEnvironment(),
    )
    assert len(missing) == 1
    assert "incomplete or incomparable" in missing[0]
    assert "measurement missing" in missing[0]

    class WrongShapeEnvironment(FakeEnvironment):
        def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
            del requests
            return {"position": [0.0]}

    wrong_shape = preflight_validation_suite(
        _suite(),
        environment_factory=lambda _: WrongShapeEnvironment(),
    )
    assert len(wrong_shape) == 1
    assert "incomplete or incomparable" in wrong_shape[0]
    assert "not numeric" in wrong_shape[0]


def test_g3_source_preflight_rejects_dynamic_move_field_selectors() -> None:
    case = {
        "module": "g3",
        "capability_id": "G3.place_objects_sequence",
        "initial_state": {
            "bodies": [
                {
                    "id": "cube",
                    "kind": "cube",
                    "position_m": [0.30, 0.0, 0.035],
                }
            ]
        },
        "call_arguments": {
            "moves": [
                {
                    "src": [0.30, 0.0, 0.035],
                    "tgt": [0.38, 0.0, 0.035],
                    "object_extent_m": 0.03,
                }
            ],
            "source_field": "src",
            "target_field": "tgt",
        },
    }
    manifest = {
        "capabilities": [
            {
                "capability_id": "G3.place_objects_sequence",
                "signature": {
                    "parameters": [
                        {"name": "runtime"},
                        {"name": "moves"},
                        {"name": "source_field"},
                        {"name": "target_field"},
                    ]
                },
                "validation_binding": {
                    "effect": "object_move_sequence",
                    "moves_argument": "moves",
                    "source_field": "source_field",
                    "target_field": "target_field",
                    "object_extent_field": "object_extent_m",
                    "object_extent_semantics": "maximum_horizontal_extent_m",
                    "target_components": "xyz",
                },
            }
        ]
    }

    errors = _preflight_g3_source_errors(
        case,
        manifest,
        {"object_positions_m": {"cube": [0.30, 0.0, 0.035]}},
    )

    assert errors == [
        "move-sequence source/target/extent fields are fixed literal move-item "
        "keys, not public selector arguments; "
        "conflicts=['source_field', 'target_field']"
    ]


def test_g3_source_preflight_checks_extent_for_each_sequence_move() -> None:
    case = {
        "module": "g3",
        "capability_id": "G3.place_objects_sequence",
        "initial_state": {
            "bodies": [
                {
                    "id": "cube",
                    "kind": "cube",
                    "position_m": [0.30, 0.0, 0.035],
                    "size_m": [0.02, 0.04, 0.03],
                }
            ]
        },
        "call_arguments": {
            "moves": [
                {
                    "source_position_m": [0.30, 0.0, 0.035],
                    "target_position_m": [0.38, 0.0, 0.035],
                    "object_extent_m": 0.03,
                }
            ]
        },
    }
    manifest = {
        "capabilities": [
            {
                "capability_id": "G3.place_objects_sequence",
                "signature": {"parameters": [{"name": "runtime"}, {"name": "moves"}]},
                "validation_binding": {
                    "effect": "object_move_sequence",
                    "moves_argument": "moves",
                    "source_field": "source_position_m",
                    "target_field": "target_position_m",
                    "object_extent_field": "object_extent_m",
                    "object_extent_semantics": "maximum_horizontal_extent_m",
                    "target_components": "xyz",
                },
            }
        ]
    }

    errors = _preflight_g3_source_errors(
        case,
        manifest,
        {"object_positions_m": {"cube": [0.30, 0.0, 0.035]}},
    )

    assert errors == [
        "bound move extent for 'cube' does not match the authored "
        "initial-state geometry"
    ]


def test_direct_validation_rejects_package_that_mutates_while_running(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)
    module = tmp_path / "generated_capability_package/g1.py"
    module.write_text(
        '''
def command_joint(runtime: object, position: float) -> dict[str, object]:
    runtime.position = position
    with open(__file__, "a", encoding="utf-8") as handle:
        handle.write("# changed during validation\\n")
    return {"status": "ok", "commanded_position": position}
'''.strip()
        + "\n",
        encoding="utf-8",
    )

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=_suite(),
        environment_factory=lambda _: FakeEnvironment(),
    )

    assert report.passed is False
    assert report.package_unchanged is False
    feedback = report.failure_feedback(repair_round=1)
    assert feedback["failures"][0]["code"] == "PACKAGE_MUTATED_DURING_VALIDATION"


def test_direct_validation_isolates_nested_arguments_and_binds_pre_execution_suite(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)
    module = tmp_path / "generated_capability_package/g1.py"
    module.write_text(
        '''
def command_joint(runtime: object, position: dict[str, object]) -> dict[str, object]:
    position["scratch"]["mutated"] = True
    runtime.position = float(position["value"])
    return {"status": "ok", "commanded_position": runtime.position}
'''.strip()
        + "\n",
        encoding="utf-8",
    )
    suite = _suite()
    suite["cases"][0]["call_arguments"]["position"] = {
        "value": 0.5,
        "scratch": {},
    }
    frozen_before = deepcopy(suite)
    digest_before = sha256_json(suite)

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=suite,
        environment_factory=lambda _: FakeEnvironment(),
    )

    assert report.passed is True
    assert report.suite_sha256 == digest_before
    assert suite == frozen_before


def test_trusted_g3_postconditions_reject_motion_and_swept_terminal_placement() -> None:
    case = {
        "module": "g3",
        "initial_state": {
            "bodies": [
                {"id": "cylinder", "position_m": [0.30, 0.0, 0.04]}
            ]
        },
        "target_measurements": {
            "object_positions_m": {"cylinder": [0.40, 0.0, 0.04]}
        },
    }
    manifest_item = {
        "validation_binding": {"effect": "object_source_to_target"}
    }
    diagnostics = {
        "source": "trusted_mujoco_mjdata_and_bridge_trace",
        "final_object_linear_velocities_m_s": {
            "cylinder": [0.08, 0.0, 0.0]
        },
        "object_motion_summaries": {
            "cylinder": {
                "ever_grasped_by_opposing_jaws": True,
                "maximum_lift_above_initial_m": 0.18,
            }
        },
        "terminal_contact_pairs": [["cylinder", "world"]],
    }

    errors = _trusted_g3_diagnostic_errors(case, manifest_item, diagnostics)

    assert any("terminal speed" in error for error in errors)
    assert any("transient lift" in error for error in errors)


def test_direct_validation_hard_timeout_returns_without_lingering_worker(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)
    module = tmp_path / "generated_capability_package/g1.py"
    module.write_text(
        '''
import time

def command_joint(runtime: object, position: float) -> dict[str, object]:
    time.sleep(1.0)
    runtime.position = position
    return {"status": "ok", "commanded_position": position}
'''.strip()
        + "\n",
        encoding="utf-8",
    )
    suite = _suite()
    suite["cases"][0]["timeout_s"] = 0.01
    started = time.monotonic()

    report = run_direct_validation(
        package_root=tmp_path,
        package_manifest=_manifest(),
        suite=suite,
        environment_factory=lambda _: FakeEnvironment(),
    )

    assert time.monotonic() - started < 0.5
    assert report.passed is False
    assert report.cases[0].timed_out is True


def test_private_harness_failure_is_not_returned_as_generation_feedback(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class PrivateFailureEnvironment(FakeEnvironment):
        def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
            del requests
            raise RuntimeError(
                "/workspace/private/oracle.py secret_oracle_source_line"
            )

    with pytest.raises(InvalidValidationCaseError) as caught:
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: PrivateFailureEnvironment(),
        )

    serialized = str(caught.value)
    assert "/workspace/private/oracle.py" not in serialized
    assert "secret_oracle_source_line" not in serialized
    assert "measurement_oracle" in serialized


def test_reset_failure_is_invalid_suite_not_generated_function_failure(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class InvalidCaseEnvironment(FakeEnvironment):
        def reset(self, initial_state: Mapping[str, Any]) -> None:
            del initial_state
            raise ValueError("private invalid case detail")

    with pytest.raises(InvalidValidationCaseError, match="environment_reset"):
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: InvalidCaseEnvironment(),
        )


def test_framework_renderer_failure_during_reset_is_infrastructure(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class RendererFailureEnvironment(FakeEnvironment):
        def reset(self, initial_state: Mapping[str, Any]) -> None:
            del initial_state
            raise FrameworkEvidenceError("private renderer initialization detail")

    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="environment_reset_framework_evidence",
    ) as caught:
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: RendererFailureEnvironment(),
            output_path=tmp_path / "report.json",
        )

    assert "private renderer initialization detail" not in str(caught.value)
    assert not (tmp_path / "report.json").exists()

    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="preflight_framework_evidence",
    ):
        preflight_validation_suite(
            _suite(),
            environment_factory=lambda _: RendererFailureEnvironment(),
        )


def test_framework_measurement_failure_is_infrastructure(tmp_path: Path) -> None:
    _write_direct_package(tmp_path)

    class EvidenceMeasurementFailure(FakeEnvironment):
        def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
            del requests
            raise FrameworkEvidenceError("private observer failure detail")

    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="measurement_framework_evidence",
    ):
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: EvidenceMeasurementFailure(),
        )


class _FacadeBackend:
    def __init__(self) -> None:
        self.position = 0.0

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        self.position = float(action["joint.pos"])
        return dict(action)


class _FacadeEnvironment(FakeEnvironment):
    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.backend = _FacadeBackend()
        self.runtime = _LeRobotControlFacade(self.backend, callback)  # type: ignore[arg-type]

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
        assert requests == {"position": 0.5}
        return {"position": self.backend.position}

    def close(self) -> None:
        _release_facade(self.runtime)
        self.closed = True


def _write_facade_package(
    root: Path,
    *,
    sleep_after_callback_s: float = 0.0,
    catch_callback_error: bool = False,
) -> None:
    package = root / "generated_capability_package"
    package.mkdir(parents=True)
    action = 'runtime.send_action({"joint.pos": position})'
    if catch_callback_error:
        action = (
            "try:\n"
            f"        {action}\n"
            "    except BaseException:\n"
            "        pass"
        )
    (package / "g1.py").write_text(
        f'''
import time

def command_joint(runtime: object, position: float) -> dict[str, object]:
    {action}
    time.sleep({sleep_after_callback_s!r})
    return {{"status": "ok", "commanded_position": position}}
'''.strip()
        + "\n",
        encoding="utf-8",
    )


def test_framework_capture_failure_from_runtime_callback_is_infrastructure(
    tmp_path: Path,
) -> None:
    _write_facade_package(tmp_path)

    def broken_capture() -> None:
        raise RuntimeError("private capture failure detail")

    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="generated_call_framework_evidence",
    ) as caught:
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: _FacadeEnvironment(broken_capture),
            output_path=tmp_path / "report.json",
        )

    assert "private capture failure detail" not in str(caught.value)
    assert not (tmp_path / "report.json").exists()

    caught_root = tmp_path / "generated-catches-base-exception"
    _write_facade_package(caught_root, catch_callback_error=True)
    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="environment_close",
    ):
        run_direct_validation(
            package_root=caught_root,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: _FacadeEnvironment(broken_capture),
        )


@pytest.mark.skipif(
    not hasattr(__import__("signal"), "SIGALRM"), reason="requires ITIMER_REAL"
)
def test_framework_callback_time_is_paused_and_candidate_deadline_is_restored(
    tmp_path: Path,
) -> None:
    callback_delay = 0.08

    def slow_capture() -> None:
        time.sleep(callback_delay)

    passing_root = tmp_path / "passing"
    _write_facade_package(passing_root)
    passing_suite = _suite()
    passing_suite["cases"][0]["timeout_s"] = 0.03
    passed = run_direct_validation(
        package_root=passing_root,
        package_manifest=_manifest(),
        suite=passing_suite,
        environment_factory=lambda _: _FacadeEnvironment(slow_capture),
    )
    assert passed.passed is True
    assert passed.cases[0].timed_out is False

    timeout_root = tmp_path / "timeout"
    _write_facade_package(timeout_root, sleep_after_callback_s=0.08)
    timeout_suite = _suite()
    timeout_suite["cases"][0]["timeout_s"] = 0.03
    timed_out = run_direct_validation(
        package_root=timeout_root,
        package_manifest=_manifest(),
        suite=timeout_suite,
        environment_factory=lambda _: _FacadeEnvironment(slow_capture),
    )
    assert timed_out.passed is False
    assert timed_out.cases[0].timed_out is True


def test_close_failure_is_harness_infrastructure_not_generation_feedback(
    tmp_path: Path,
) -> None:
    _write_direct_package(tmp_path)

    class BrokenCloseEnvironment(FakeEnvironment):
        def close(self) -> None:
            raise FrameworkEvidenceError("private recorder teardown detail")

    with pytest.raises(ValidationHarnessInfrastructureError, match="environment_close"):
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=lambda _: BrokenCloseEnvironment(),
        )


def test_framework_factory_failure_is_harness_infrastructure(tmp_path: Path) -> None:
    _write_direct_package(tmp_path)

    def broken_factory(_: Mapping[str, Any]) -> FakeEnvironment:
        raise FrameworkEvidenceError("private writer construction detail")

    with pytest.raises(
        ValidationHarnessInfrastructureError,
        match="environment_factory",
    ):
        run_direct_validation(
            package_root=tmp_path,
            package_manifest=_manifest(),
            suite=_suite(),
            environment_factory=broken_factory,
        )
