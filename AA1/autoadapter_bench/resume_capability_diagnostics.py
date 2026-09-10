#!/usr/bin/env python3
"""Continue a bounded Framework diagnosis from an existing candidate.

This is a diagnostic helper for the first (generation/Framework) stage only.
It deliberately does not run the legacy ReAct task evaluator.  A driver that
passes the complete Framework scope is recorded as ``not_run_pending_recap``
for the separate ReCAP stage.

The normal command creates one fresh workspace per repair attempt::

    output-root/repair_1/<robot>/
    output-root/repair_2/<robot>/
    output-root/repair_3/<robot>/

Only the candidate source files and a compact failure-feedback input enter a
repair workspace.  Framework validation runs in a fresh Python subprocess so
generated-driver imports and MuJoCo state cannot leak between attempts.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


SCRIPT_PATH = Path(__file__).resolve()
AA1_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = AA1_ROOT.parent
if str(AA1_ROOT) not in sys.path:
    sys.path.insert(0, str(AA1_ROOT))

DEFAULT_MODEL = "eu.anthropic.claude-opus-4-8"
MAX_REPAIRS = 3
FRAMEWORK_TIMEOUT_SEC = 900
NATIVE_SCOPED_ROBOTS = {"unitree_a1", "anymal_c"}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _path_text(path: Path) -> str:
    """Keep pointers reviewable while remaining valid outside this cwd."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _python_executable() -> str:
    venv_python = AA1_ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.is_file() else sys.executable


def _robot_definition(robot_id: str) -> dict[str, Any]:
    from auto_adapter.robot_catalog import find_robot_definition

    definition = find_robot_definition(robot_id)
    if not definition:
        raise ValueError(f"robot is not present in the trusted zoo: {robot_id}")
    return definition


def _canonical_mjcf(robot_id: str) -> Path:
    definition = _robot_definition(robot_id)
    # Capability profiles own the fixed scene used by Framework validation;
    # falling back to the ordinary scene would silently validate another task.
    relative = definition.get("capability_mjcf") or definition["mjcf"]
    return (AA1_ROOT / relative).resolve()


def _is_from_scratch(robot_id: str) -> bool:
    return robot_id == "h1"


def _candidate_names(robot_id: str) -> tuple[str, ...]:
    if _is_from_scratch(robot_id):
        return ("driver_from_scratch.py", "driver.py", "study.json")
    return ("driver.py", "study.json")


def _copy_candidate(source: Path, destination: Path, robot_id: str) -> list[Path]:
    """Copy the bounded public candidate subset into a new attempt."""
    source = source.resolve()
    if source.is_file():
        source = source.parent
    if not source.is_dir():
        raise FileNotFoundError(f"candidate source directory not found: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    copied: list[Path] = []
    for name in _candidate_names(robot_id):
        src = source / name
        if src.is_file():
            dst = destination / name
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


def _candidate_is_resumable(source: Path, robot_id: str) -> tuple[bool, str | None]:
    source = source.resolve()
    if source.is_file():
        source = source.parent
    if not source.is_dir():
        return False, f"candidate source directory not found: {source}"
    required = ("driver_from_scratch.py", "driver.py") if _is_from_scratch(robot_id) else ("driver.py",)
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        return False, "missing candidate file(s): " + ", ".join(missing)
    return True, None


def _source_report(source: Path, robot_id: str) -> tuple[Path | None, dict[str, Any] | None]:
    source = source.resolve()
    if source.is_file():
        source = source.parent
    candidates = [source / "validate_report.json"]
    if robot_id in NATIVE_SCOPED_ROBOTS:
        candidates.append(source / "capability_validation_canary" / "suite_result.json")
    for path in candidates:
        if path.is_file():
            report = _read_json(path)
            if report is not None:
                return path, report
    return None, None


def _full_framework_pass(report: Mapping[str, Any], robot_id: str) -> bool:
    """Native scoped canaries never count as complete Framework passes."""
    return robot_id not in NATIVE_SCOPED_ROBOTS and report.get("all_ok") is True


def _scope_stop(report: Mapping[str, Any], robot_id: str) -> bool:
    return _full_framework_pass(report, robot_id) or (
        robot_id in NATIVE_SCOPED_ROBOTS and report.get("scope_all_ok") is True
    )


def _task_status(report: Mapping[str, Any], robot_id: str) -> str:
    if _full_framework_pass(report, robot_id):
        return "not_run_pending_recap"
    if robot_id in NATIVE_SCOPED_ROBOTS and report.get("scope_all_ok") is True:
        return "not_eligible_scope_incomplete"
    return "not_eligible_framework_failed"


def _phase_result_payload(result: Any, workspace: Path, started: float) -> dict[str, Any]:
    token_usage = getattr(result, "token_usage", None)
    if token_usage is None:
        token_usage = getattr(result, "total_tokens", {})
    trace = getattr(result, "trace_path", None)
    if trace is None:
        trace_candidates = sorted((workspace / "traces").glob("*repair*.jsonl"))
        trace = trace_candidates[-1] if trace_candidates else None
    artifacts = getattr(result, "artifact_paths", None) or getattr(result, "artifacts", [])

    def pointer(value: Any) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = workspace / path
        return _path_text(path)

    return {
        "ok": bool(getattr(result, "ok", False)),
        "error": getattr(result, "error", None),
        "duration_sec": time.time() - started,
        "token_usage": token_usage or {},
        "trace_path": pointer(trace) if trace else None,
        "artifacts": [pointer(item) for item in artifacts if item],
    }


def _run_repair(
    robot_id: str,
    workspace: Path,
    model: str,
    attempt: int,
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Call exactly one approved repair phase in the current process."""
    started = time.time()
    from auto_adapter.orchestrator import validate_failure_feedback

    feedback = validate_failure_feedback(dict(report))
    if _is_from_scratch(robot_id):
        from auto_adapter.orchestrator_from_scratch import (
            FromScratchConfig,
            FromScratchOrchestrator,
        )

        cfg = FromScratchConfig(
            robot_id=robot_id,
            mjcf_path=_canonical_mjcf(robot_id),
            workspace_root=workspace.parent,
            bedrock_model=model,
            model_provider="holistic",
            max_outer_retries=0,
        )
        # phase_gen_repair supports a local call with no AgentCore session.
        orchestrator = FromScratchOrchestrator(cfg)
        (workspace / "feedback_input.json").write_text(
            json.dumps(feedback, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        phase = orchestrator.phase_gen_repair(feedback, attempt)
        return _phase_result_payload(phase, workspace, started), feedback

    from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig

    cfg = SelfAssembleConfig(
        robot_id=robot_id,
        mjcf_path=_canonical_mjcf(robot_id),
        workspace_root=workspace.parent,
        mode="local",
        validate_mode="framework",
        bedrock_model=model,
        model_provider="holistic",
        max_outer_gen_val_iters=1,
    )
    with SelfAssemble(cfg) as orchestrator:
        (workspace / "feedback_input.json").write_text(
            json.dumps(feedback, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        phase = orchestrator._phase_repair(feedback, attempt)
    return _phase_result_payload(phase, workspace, started), feedback


def _load_driver_module(workspace: Path, filename: str) -> Any:
    import importlib.util

    original_cwd = os.getcwd()
    added = False
    try:
        os.chdir(workspace)
        if str(workspace) not in sys.path:
            sys.path.insert(0, str(workspace))
            added = True
        sys.modules.pop("driver", None)
        module_name = "resume_capability_driver"
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, str(workspace / filename))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {filename}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(original_cwd)
        if added:
            sys.path.remove(str(workspace))


def _native_scoped_framework(robot_id: str, workspace: Path) -> dict[str, Any]:
    """Run only interface + calibrated G4 nominal for native quadrupeds."""
    from auto_adapter.robot_catalog import (
        load_capability_suite,
        validate_capability_driver,
    )
    from autoadapter_bench.capability_eval import run_capability_suite

    report: dict[str, Any] = {
        "validation_scope": "interface_plus_g4_nominal",
        "conditions_checked": 1,
        "required_conditions_total": 10,
        "tests": [],
        # A scoped native canary is useful, but it cannot enter the full-pass
        # denominator.  Keep this false even when the scoped condition passes.
        "all_ok": False,
        "scope_all_ok": False,
    }
    mjcf = workspace / "mjcf.xml"
    if mjcf.exists() or mjcf.is_symlink():
        mjcf.unlink()
    mjcf.symlink_to(_canonical_mjcf(robot_id))
    driver_path = workspace / "driver.py"
    if not driver_path.is_file():
        report["error"] = "driver.py missing"
        _write_json(workspace / "validate_report.json", report)
        return report
    try:
        module = _load_driver_module(workspace, "driver.py")
        build = getattr(module, "build", None)
        if not callable(build):
            raise AttributeError("driver.py has no callable build()")
        skel = build()
        report["tests"].append({
            "test": "driver_build",
            "ok": True,
            "metric": 1.0,
            "detail": f"build() returned {type(skel).__name__}",
        })
        definition = _robot_definition(robot_id)
        validate_capability_driver(skel, definition)
        report["tests"].append({
            "test": "capability_interface",
            "ok": True,
            "metric": 1.0,
            "detail": "trusted capability interface is complete",
        })
        suite = load_capability_suite(definition)
        cases = suite.get("cases", suite.get("tests", []))
        g4 = next(
            item for item in cases
            if str(item.get("capability_id", "")) == "G4"
            and "nominal" in str(item.get("condition", item.get("case_id", ""))).lower()
        )
        case_id = str(g4.get("case_id", g4.get("id")))
        outcome = run_capability_suite(
            skel,
            definition,
            workspace / "capability_validation",
            case_ids=[case_id],
            driver_origin="real_model_generation",
        )
        report["tests"].extend(outcome.get("tests", []))
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests"].append({
            "test": "native_scoped_framework",
            "ok": False,
            "metric": 0.0,
            "detail": report["error"],
        })
    report["scope_all_ok"] = bool(report["tests"]) and all(
        bool(item.get("ok")) for item in report["tests"]
        if isinstance(item, Mapping)
    )
    # This remains false by construction, even if interface and G4 pass.
    report["all_ok"] = False
    report["n_passed"] = sum(1 for item in report["tests"] if item.get("ok"))
    report["n_total"] = len(report["tests"])
    _write_json(workspace / "validate_report.json", report)
    return report


def _framework_in_process(robot_id: str, workspace: Path) -> dict[str, Any]:
    if robot_id in NATIVE_SCOPED_ROBOTS:
        return _native_scoped_framework(robot_id, workspace)

    if _is_from_scratch(robot_id):
        from auto_adapter.orchestrator_from_scratch import (
            FromScratchConfig,
            FromScratchOrchestrator,
        )

        cfg = FromScratchConfig(
            robot_id=robot_id,
            mjcf_path=_canonical_mjcf(robot_id),
            workspace_root=workspace.parent,
            max_outer_retries=0,
        )
        orchestrator = FromScratchOrchestrator(cfg)
        report = orchestrator._validate_from_scratch_driver()
        _write_json(workspace / "validate_report.json", report)
        return report

    from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig

    cfg = SelfAssembleConfig(
        robot_id=robot_id,
        mjcf_path=_canonical_mjcf(robot_id),
        workspace_root=workspace.parent,
        mode="local",
        validate_mode="framework",
    )
    with SelfAssemble(cfg) as orchestrator:
        phase = orchestrator._phase_validate_framework()
    report = _read_json(workspace / "validate_report.json")
    if report is None:
        report = {
            "tests": [],
            "all_ok": False,
            "error": phase.error or "Framework phase did not write validate_report.json",
        }
        _write_json(workspace / "validate_report.json", report)
    return report


def _framework_child(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    original_cwd = os.getcwd()
    try:
        # Candidate build methods commonly resolve ``mjcf.xml`` relative to
        # cwd.  Keep the complete validation process in the candidate world.
        os.chdir(workspace)
        report = _framework_in_process(args.robot, workspace)
        print(json.dumps({"all_ok": report.get("all_ok"), "scope_all_ok": report.get("scope_all_ok")}))
        return 0
    except Exception as exc:  # noqa: BLE001
        report = {
            "tests": [],
            "all_ok": False,
            "error": f"framework subprocess exception: {type(exc).__name__}: {exc}",
        }
        _write_json(workspace / "validate_report.json", report)
        print(report["error"], file=sys.stderr)
        return 2
    finally:
        os.chdir(original_cwd)


def _run_framework_subprocess(
    robot_id: str,
    workspace: Path,
    attempt: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = [
        _python_executable(),
        str(SCRIPT_PATH),
        "--_framework-only",
        "--robot",
        robot_id,
        "--workspace",
        str(workspace),
    ]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(AA1_ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    started = time.time()
    info: dict[str, Any] = {
        "command": command,
        "timeout_sec": FRAMEWORK_TIMEOUT_SEC,
    }
    report_path = workspace / "validate_report.json"
    # Never accept a report left by a previous validation process in this
    # workspace if the new child fails before replacing it.
    if report_path.exists():
        report_path.unlink()
    try:
        completed = subprocess.run(
            command,
            cwd=AA1_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=FRAMEWORK_TIMEOUT_SEC,
            check=False,
        )
        info.update(
            {
                "returncode": completed.returncode,
                "duration_sec": time.time() - started,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )
    except subprocess.TimeoutExpired as exc:
        info.update(
            {
                "returncode": None,
                "duration_sec": time.time() - started,
                "timed_out": True,
                "stdout_tail": str(exc.stdout)[-4000:] if exc.stdout else "",
                "stderr_tail": str(exc.stderr)[-4000:] if exc.stderr else "",
            }
        )
    except OSError as exc:
        info.update(
            {
                "returncode": None,
                "duration_sec": time.time() - started,
                "spawn_error": f"{type(exc).__name__}: {exc}",
            }
        )
    report = _read_json(report_path) or {
        "tests": [],
        "all_ok": False,
        "error": info.get("spawn_error")
        or ("Framework subprocess timed out" if info.get("timed_out") else "Framework subprocess did not write a report"),
    }
    child_ok = info.get("returncode") == 0 and not info.get("timed_out") and not info.get("spawn_error")
    info["child_ok"] = child_ok
    if not child_ok:
        report = dict(report)
        report["all_ok"] = False
        if "scope_all_ok" in report:
            report["scope_all_ok"] = False
        report["error"] = (
            "Framework subprocess failed; report is not accepted: "
            + str(info.get("spawn_error") or info.get("stderr_tail") or "nonzero returncode")
        )
        _write_json(report_path, report)
    info["report_path"] = _path_text(report_path)
    _write_json(workspace / "framework_subprocess.json", info)
    return report, info


def _max_repairs(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max-repairs must be an integer from 0 to 3") from exc
    if not 0 <= number <= MAX_REPAIRS:
        raise argparse.ArgumentTypeError("max-repairs must be from 0 to 3")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-repairs", type=_max_repairs, default=MAX_REPAIRS)
    # Private child entry point.  It is intentionally absent from help.
    parser.add_argument("--_framework-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    return parser


def _fresh_output_check(output_root: Path, robot_id: str, max_repairs: int) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / f"summary_{robot_id}.json"
    reserved = [summary_path, output_root / "baseline" / robot_id]
    for attempt in range(1, max_repairs + 1):
        reserved.append(output_root / f"repair_{attempt}" / robot_id)
    existing = [path for path in reserved if path.exists()]
    if existing:
        paths = ", ".join(_path_text(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing diagnostic output: {paths}")
    return summary_path


def run_diagnostics(args: argparse.Namespace) -> int:
    if not args.robot or args.source is None or args.output_root is None:
        raise ValueError("--robot, --source and --output-root are required")
    robot_id = str(args.robot)
    source = args.source.resolve()
    output_root = args.output_root.resolve()
    summary_path = _fresh_output_check(output_root, robot_id, args.max_repairs)
    summary: dict[str, Any] = {
        "schema_version": "capability_repair_diagnostic_v1",
        "robot": robot_id,
        "source": _path_text(source),
        "model": args.model,
        "max_repairs": args.max_repairs,
        "task_status": "not_run_pending_recap",
        "attempts": [],
    }

    resumable, reason = _candidate_is_resumable(source, robot_id)
    if not resumable:
        summary.update({
            "status": "not_resumable",
            "reason": reason,
            "task_status": "not_run_not_resumable",
        })
        _write_json(summary_path, summary)
        print(json.dumps({"status": summary["status"], "reason": reason}))
        return 0

    report_path, report = _source_report(source, robot_id)
    summary["source_report"] = _path_text(report_path) if report_path else None
    summary["status"] = "starting"
    _write_json(summary_path, summary)

    if report is not None and _scope_stop(report, robot_id):
        full_pass = _full_framework_pass(report, robot_id)
        summary.update({
            "status": "source_framework_pass" if full_pass else "source_scoped_canary_pass",
            "final_framework_report": _path_text(report_path) if report_path else None,
            "framework_ok": full_pass,
            "scope_all_ok": report.get("scope_all_ok"),
            "task_status": _task_status(report, robot_id),
        })
        _write_json(summary_path, summary)
        print(json.dumps({"status": summary["status"], "framework_ok": full_pass}))
        return 0

    if args.max_repairs == 0:
        summary.update({
            "status": "framework_failed_no_repairs_requested",
            "framework_ok": False,
            "task_status": "not_eligible_framework_failed",
        })
        _write_json(summary_path, summary)
        print(json.dumps({"status": summary["status"], "framework_ok": False}))
        return 0

    current_source = source
    current_report = report
    final_report_path: Path | None = report_path
    for attempt_number in range(1, args.max_repairs + 1):
        workspace = output_root / f"repair_{attempt_number}" / robot_id
        attempt: dict[str, Any] = {
            "attempt": attempt_number,
            "workspace": _path_text(workspace),
            "input_candidate": _path_text(current_source),
            "source_report": _path_text(final_report_path) if final_report_path else None,
        }
        summary["attempts"].append(attempt)
        _write_json(summary_path, summary)
        copied = _copy_candidate(current_source, workspace, robot_id)
        attempt["copied_files"] = [_path_text(path) for path in copied]
        _write_json(summary_path, summary)

        # A missing source report is resolved by one real baseline Framework
        # validation in the new workspace before any model repair is called.
        if current_report is None:
            baseline_workspace = output_root / "baseline" / robot_id
            _copy_candidate(current_source, baseline_workspace, robot_id)
            current_report, baseline_info = _run_framework_subprocess(
                robot_id, baseline_workspace, attempt
            )
            baseline_copy = baseline_workspace / "validate_report.json"
            attempt["baseline_workspace"] = _path_text(baseline_workspace)
            attempt["baseline_framework_report"] = _path_text(baseline_copy)
            attempt["baseline_framework_subprocess"] = baseline_info
            _write_json(summary_path, summary)
            final_report_path = baseline_copy
            if _scope_stop(current_report, robot_id):
                full_pass = _full_framework_pass(current_report, robot_id)
                attempt["framework_ok"] = full_pass
                attempt["scope_all_ok"] = current_report.get("scope_all_ok")
                attempt["task_status"] = _task_status(current_report, robot_id)
                summary.update({
                    "status": "baseline_framework_pass" if full_pass else "baseline_scoped_canary_pass",
                    "framework_ok": full_pass,
                    "final_framework_report": _path_text(baseline_copy),
                    "scope_all_ok": current_report.get("scope_all_ok"),
                    "task_status": attempt["task_status"],
                })
                _write_json(summary_path, summary)
                print(json.dumps({"status": summary["status"], "framework_ok": full_pass}))
                return 0

        # The repair phase sees only this compact feedback input.  In
        # particular, no source validate_report/suite/reference is copied in.
        try:
            repair_result, feedback = _run_repair(
                robot_id, workspace, args.model, attempt_number, current_report or {}
            )
            attempt["feedback_input"] = _path_text(workspace / "feedback_input.json")
            attempt["repair"] = repair_result
            _write_json(workspace / "repair_result.json", repair_result)
        except Exception as exc:  # noqa: BLE001
            attempt["repair"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_sec": 0.0,
                "token_usage": {},
                "trace_path": None,
                "artifacts": [],
            }
            attempt["repair_error"] = attempt["repair"]["error"]
            _write_json(workspace / "repair_result.json", attempt["repair"])
        _write_json(summary_path, summary)

        candidate_file = workspace / ("driver_from_scratch.py" if _is_from_scratch(robot_id) else "driver.py")
        if not candidate_file.is_file():
            attempt["framework_ok"] = False
            attempt["task_status"] = "not_eligible_framework_failed"
            summary.update({
                "status": "candidate_missing_after_repair",
                "framework_ok": False,
                "task_status": "not_eligible_framework_failed",
            })
            _write_json(summary_path, summary)
            break

        current_report, framework_info = _run_framework_subprocess(robot_id, workspace, attempt)
        final_report_path = workspace / "validate_report.json"
        framework_ok = _full_framework_pass(current_report, robot_id)
        scoped_stop = _scope_stop(current_report, robot_id)
        attempt.update({
            "framework_report": _path_text(final_report_path),
            "framework_subprocess": framework_info,
            "framework_ok": framework_ok,
            "all_ok": current_report.get("all_ok"),
            "scope_all_ok": current_report.get("scope_all_ok"),
            "task_status": _task_status(current_report, robot_id),
        })
        summary["framework_ok"] = framework_ok
        summary["task_status"] = attempt["task_status"]
        summary["final_framework_report"] = _path_text(final_report_path)
        if scoped_stop:
            summary["status"] = "framework_pass" if framework_ok else "native_scoped_pass"
            _write_json(summary_path, summary)
            print(json.dumps({"status": summary["status"], "framework_ok": framework_ok}))
            return 0
        summary["status"] = (
            "framework_failed_max_repairs"
            if attempt_number == args.max_repairs
            else "repairing"
        )
        _write_json(summary_path, summary)
        current_source = workspace
        final_report_path = workspace / "validate_report.json"

    summary.setdefault("framework_ok", False)
    summary.setdefault("task_status", "not_eligible_framework_failed")
    _write_json(summary_path, summary)
    print(json.dumps({"status": summary["status"], "framework_ok": summary["framework_ok"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args._framework_only:
        if not args.robot or args.workspace is None:
            parser.error("--_framework-only requires --robot and --workspace")
        return _framework_child(args)
    try:
        return run_diagnostics(args)
    except Exception as exc:  # noqa: BLE001
        print(f"resume_capability_diagnostics: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
