#!/usr/bin/env python3
"""Run the approved fixed-family diagnostic, one independent robot at a time.

Basic physical environment checks precede model calls. Full reference control is
optional and never substitutes for a candidate result. No candidate is edited.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.driver_synthesis.generation import ModelCallEvidence, StudyResult, _validate_study
from autoadapter2.model_api import JsonModelClient, ModelConfig
from autoadapter2.provider_config import (
    HOLISTICAI_ROUTE_PROFILE_ID, HOLISTICAI_ROUTE_PROFILE_PATH,
    resolve_holisticai_route_profile,
)
from autoadapter2.harness.runner import _merged_private_index, _run_worker
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.pipeline import (
    ExperimentConfig, PipelineHooks, _load_fixed_inputs,
    _run_reference_positive_control, _has_successful_physics_probe, run_experiment,
)

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def load_reused_study(source: Path, package, design: dict, condition: str) -> StudyResult:
    """Reuse an accepted diagnostic artifact, never the old model or worker state."""
    source = source.resolve()
    output = json.loads((source / "files/study.json").read_text())
    record = json.loads((source / "study_evidence.json").read_text())
    if not record.get("evidence", {}).get("completed"):
        raise ValueError("source Study did not complete successfully")
    if (output.get("robot_configuration_id") != package.robot_configuration_id
            or output.get("package_version") != package.package_version
            or output.get("condition") != condition):
        raise ValueError("source Study robot, package version or condition does not match")
    if json.loads((source / "design/capability_design.json").read_text()) != design:
        raise ValueError("source Study capability design does not match the fixed inputs")
    requests = _validate_study(output, condition)
    probes = tuple({**item, "reused": True} for item in record.get("probe_results", []))
    if not _has_successful_physics_probe(probes):
        raise ValueError("source Study has no successful recorded physics probe")
    return StudyResult(
        condition=condition, output=output, probe_requests=requests,
        probe_results=probes, reused_from=str(source),
        call_evidence=ModelCallEvidence(
            stage="study", prompt="Reused accepted diagnostic Study",
            inputs={"reused_from": str(source)}, output=output,
        ),
    )


def model_client(
    config: ExperimentConfig,
    *,
    holistic: bool = False,
    evidence_dir: Path | None = None,
    timeout_retries: int = 0,
) -> JsonModelClient:
    manifest = config.model_manifest
    assert manifest is not None
    if not holistic and manifest["base_url"].rstrip("/") != "https://api.deepseek.com":
        raise ValueError("non-official diagnostic route requires --holistic")
    # Read simple dotenv assignments without executing shell code or logging secrets.
    env_file = ROOT.parent / (".env.company-api" if holistic else ".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.removeprefix("export ").split("=", 1)
            if key.strip().startswith("AUTOADAPTER_"):
                values = shlex.split(raw, comments=True)
                if len(values) == 1:
                    os.environ.setdefault(key.strip(), values[0])
    if holistic:
        route = resolve_holisticai_route_profile(
            {"path": HOLISTICAI_ROUTE_PROFILE_PATH, "profile_id": HOLISTICAI_ROUTE_PROFILE_ID},
            ROOT.parent,
        )
        if manifest["base_url"].rstrip("/") != route.base_url.rstrip("/"):
            raise ValueError("Holistic route does not match diagnostic model base_url")
        key = os.environ.get(route.credential_env) or os.environ.get("AUTOADAPTER_COMPANY_API_KEY")
        if not key:
            raise ValueError("missing Holistic API credential")
        return JsonModelClient(ModelConfig(
            provider=manifest["vendor"], api_protocol=route.api_protocol,
            model=manifest["model_id"], base_url=route.base_url, api_key=key,
            auth_header=route.auth_header, auth_prefix=route.auth_prefix,
            endpoint_path=route.endpoint_path, timeout_s=route.maximum_request_timeout_s,
            thinking=manifest["thinking"], max_tokens=manifest["max_output_tokens"],
            tool_history_mode=manifest["tool_history_mode"],
        ), evidence_dir=evidence_dir, timeout_retries=timeout_retries)
    runtime = replace(
        ModelConfig.from_env(), provider="deepseek", api_protocol="openai-compatible",
        model=manifest["model_id"], base_url=manifest["base_url"],
        thinking="disabled", max_tokens=manifest["max_output_tokens"],
    )
    return JsonModelClient(runtime, evidence_dir=evidence_dir, timeout_retries=timeout_retries)


_ENVIRONMENT_PROBE = '''import mujoco
import numpy as np

class Driver:
    def __init__(self, model, data):
        self.model, self.data = model, data

    def check_environment(self, request):
        m, d = self.model, self.data
        original = d.ctrl.copy()
        joint = m.joint(request['joint_name']).id
        address = int(m.jnt_qposadr[joint])
        actuator = m.actuator(request['actuator_name']).id
        start = float(d.time)
        while d.time - start < request['duration_s']:
            d.ctrl[:] = original
            if request['mode'] == 'probe' and d.time - start < .3:
                d.ctrl[actuator] = request['target_control']
            mujoco.mj_step(m, d)
            if not all(np.isfinite(v).all() for v in (d.qpos, d.qvel, d.ctrl)):
                raise RuntimeError('non-finite physical state during environment probe')

def build(model, data):
    return Driver(model, data)
'''


def check_environment(package, suite, output: Path, config: ExperimentConfig) -> dict:
    """Check real scene/reset/control usability without scoring capabilities."""
    import mujoco
    import numpy as np

    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "resets": [], "workers": []}
    try:
        instances = _merged_private_index(package, "instances", "instance_id", capability_v2=True)
        for case in suite['cases']:
            instance = instances[case['instance_id']]
            scene = package.root / instance.get('scene_entrypoint', package.morphology['mjcf_entrypoint'])
            model = mujoco.MjModel.from_xml_path(str(scene))
            data = mujoco.MjData(model)
            variants = instance.get('repetition_variants') or [{}]
            for variant in variants:
                apply_framework_reset(mujoco, model, data, variant.get('reset', instance.get('reset')))
                finite = all(np.isfinite(v).all() for v in (data.qpos, data.qvel, data.ctrl))
                bounded = all(not model.actuator_ctrllimited[i] or
                              model.actuator_ctrlrange[i, 0] - 1e-12 <= data.ctrl[i] <= model.actuator_ctrlrange[i, 1] + 1e-12
                              for i in range(model.nu))
                minimum = min((float(c.dist) for c in data.contact), default=0.)
                item = {"case_id": case['case_id'], "finite": bool(finite), "control_range": bool(bounded),
                        "minimum_contact_distance_m": minimum, "passed": bool(finite and bounded and minimum >= -.005)}
                report['resets'].append(item)
                if not item['passed']:
                    raise RuntimeError(f"invalid physical reset for {case['case_id']}: {item}")

        first = suite['cases'][0]
        instance = instances[first['instance_id']]
        scene = package.root / instance.get('scene_entrypoint', package.morphology['mjcf_entrypoint'])
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        reset = instance.get('reset', {'kind': 'default'})
        apply_framework_reset(mujoco, model, data, reset)
        controls = package.morphology['public_control']
        actuator_name = controls['actuator_names'][0]
        actuator = model.actuator(actuator_name).id
        joint = int(model.actuator_trnid[actuator, 0])
        joint_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
        if not joint_name or model.actuator_trntype[actuator] != mujoco.mjtTrn.mjTRN_JOINT:
            raise RuntimeError('environment probe requires a native named joint actuator')
        value = float(data.ctrl[actuator])
        delta = .02 if controls['actuation'] == 'joint_position' else 1.
        sign = -1. if model.actuator_ctrllimited[actuator] and value + delta > model.actuator_ctrlrange[actuator, 1] else 1.
        target = value + sign * delta
        if model.actuator_ctrllimited[actuator]:
            target = float(np.clip(target, *model.actuator_ctrlrange[actuator]))
        driver = output / 'environment_probe.py'
        driver.write_text(_ENVIRONMENT_PROBE)
        quadruped = package.morphology['morphology_kind'] == 'free_base_quadruped'
        for mode in (['probe', 'standing'] if quadruped else ['probe']):
            duration = 2. if mode == 'standing' else 1.
            payload = {
                'scene_path': str(scene), 'capability_methods': ['check_environment'],
                'method_name': 'check_environment', 'reset': reset,
                'public_arguments': {'request': {'mode': mode, 'duration_s': duration,
                    'joint_name': joint_name, 'actuator_name': actuator_name, 'target_control': target}},
                'max_steps': math.ceil((duration + .1) / model.opt.timestep),
                'max_sim_time_s': duration + .1, 'sample_hz': 20.,
                'render': {'enabled': config.record_video, 'width': 640, 'height': 480,
                           'fps': 10, 'camera': instance.get('camera', -1)},
                'video_path': str(output / f'{mode}.mp4'),
            }
            worker = _run_worker(payload, candidate=driver, source_root=ROOT / 'src',
                                 wall_timeout_s=config.worker_wall_timeout_s)
            write(output / f'{mode}_worker.json', worker)
            e = worker.get('physical_evidence', {})
            samples = e.get('samples', [])
            minimum = e.get('minimum_contact_distance_m')
            okay = bool(worker.get('worker_completed') and worker.get('canonical_model_data')
                        and not worker.get('candidate_exception') and e.get('step_count', 0) > 0
                        and not e.get('direct_state_write_detected')
                        and e.get('control_range_monitoring_complete')
                        and not e.get('control_range_violation_detected')
                        and (minimum is None or minimum >= -.005)
                        and (not config.record_video or worker.get('video', {}).get('complete')))
            response = 0.
            if mode == 'probe' and samples:
                initial = samples[0]['joint_positions'][joint_name]
                response = max(sign * (s['joint_positions'][joint_name] - initial)
                               for s in samples if s['time'] - samples[0]['time'] <= .3 + 1e-9)
                okay = okay and response > 1e-5
            if mode == 'standing':
                # A basic upright hold is distinct from the perturbed G5 capability.
                for sample in samples:
                    q = sample['qpos'][3:7]
                    w, x, y, z = q
                    roll = math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y))
                    pitch = math.asin(float(np.clip(2*(w*y-z*x), -1., 1.)))
                    okay = okay and abs(roll) <= .1745 and abs(pitch) <= .1745
            report['workers'].append({'mode': mode, 'passed': bool(okay), 'joint_response_rad': response,
                                      'minimum_contact_distance_m': minimum,
                                      'report': str(output / f'{mode}_worker.json')})
            if not okay:
                raise RuntimeError(f"basic {mode} check failed; inspect {output / (mode + '_worker.json')}")
        report['passed'] = True
        return report
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        write(output / 'environment_check.json', report)


def main(argv: list[str] | None = None, *, client_factory=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/diagnostics/fixed-family-v1.json")
    parser.add_argument("--holistic", action="store_true", help="Use the existing Holistic route and local company credential")
    parser.add_argument("--robots", nargs="+")
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--study-from", type=Path,
                        help="Reuse the accepted Study from one prior condition-cell directory; start a fresh Generate worker")
    parser.add_argument("--retry-timeout-once", action="store_true",
                        help="Allow at most one timeout retry across all model stages per robot")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config = ExperimentConfig.from_path(args.config)
    robots = args.robots or list(config.robots)
    if not set(robots) <= set(config.robots):
        parser.error("robot is outside the approved diagnostic cohort")
    if args.study_from and (len(robots) != 1 or len(config.generation_conditions) != 1 or args.reference_only):
        parser.error("--study-from requires one robot, one condition and model synthesis")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "runs/diagnostic" / f"fixed-family-v1-{stamp}").resolve()
    if output.exists():
        parser.error("output must be fresh; existing cells must not be overwritten")
    output.mkdir(parents=True)
    write(output / "diagnostic_config.json", config.as_dict())
    summary = {"formal": False, "reference_only": args.reference_only,
               "timeout_retries_per_robot": int(args.retry_timeout_once), "robots": {}}
    for robot in robots:
        result = {"prepared": False, "environment_passed": False,
                  "reference_executed": False, "reference_passed": None, "model_started": False}
        summary["robots"][robot] = result
        client = None
        stage = "preparation"
        try:
            package = load_indexed_robot_package(ROOT, robot, require_task_library=False)
            fixed, _ = _load_fixed_inputs(
                ROOT / "references/fixed_family_v1", packages={robot: package},
                hooks=PipelineHooks(), require_task_support=False,
            )
            result["prepared"] = True
            one = replace(config, robots=(robot,))
            hooks = PipelineHooks()
            if args.study_from:
                reused = load_reused_study(
                    args.study_from, package, fixed[robot]["design"], one.generation_conditions[0],
                )
                hooks = replace(hooks, study_runner=lambda *_args, **_kwargs: reused)
                result["study_reused_from"] = reused.reused_from
                result["study_model_calls_in_this_run"] = 0
            if args.reference_only:
                stage = "reference"
                result['reference_executed'] = True
                print(f"{robot}: reference control (diagnostic only)", flush=True)
                fixed_reference = package.root / "reference/fixed_family_driver.py"
                def render_fixed(design, destination):
                    destination = Path(destination)
                    destination.mkdir(parents=True, exist_ok=True)
                    return Path(shutil.copy2(fixed_reference, destination / "driver.py"))
                reference_hooks = PipelineHooks(reference_renderer=render_fixed) if fixed_reference.exists() else PipelineHooks()
                result['reference_passed'] = False
                _run_reference_positive_control(
                    package=package, design=fixed[robot]["design"], suite=fixed[robot]["suite"],
                    config=one, hooks=reference_hooks, output_dir=output / robot / "reference",
                    run_id=f"fixed-family-reference-{stamp}-{robot}",
                )
                result['reference_passed'] = True
            else:
                stage = "environment"
                print(f"{robot}: basic physical environment check", flush=True)
                environment_dir = output / robot / 'environment'
                result['environment_report'] = str(environment_dir / 'environment_check.json')
                check_environment(package, fixed[robot]['suite'], environment_dir, one)
                result['environment_passed'] = True
                stage = "model"
                result["model_request_evidence"] = str(output / robot / "model_requests")
                client = (client_factory or model_client)(
                    one, holistic=args.holistic,
                    evidence_dir=output / robot / "model_requests",
                    timeout_retries=int(args.retry_timeout_once),
                )
                print(f"{robot}: real model synthesis", flush=True)
                result["model_started"] = True
                report = run_experiment(
                    ROOT, config=one, output_dir=output / robot / "candidate",
                    run_id=f"fixed-family-{stamp}-{robot}", client=client,
                    fixed_inputs_from=ROOT / "references/fixed_family_v1",
                    skip_reference_calibration=True,
                    hooks=hooks,
                )
                result["pipeline_report"] = str(output / robot / "candidate/experiment_report.json")
                result["candidate_passed"] = bool(report.get("final_capability_validation_passed"))
                result["pipeline_success"] = bool(report.get("success"))
                result["cells"] = [{key: cell.get(key) for key in (
                    "attempt_count", "case_counts", "capability_validation_executed",
                    "final_capability_validation_passed",
                )} for cell in report.get("cells", [])]
        except Exception as exc:
            result["failure_stage"] = stage
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{robot}: {result['error']}", flush=True)
        finally:
            if client is not None:
                write(output / robot / "model_calls.json", client.calls)
                result["model_call_count"] = len(client.calls)
            write(output / "diagnostic_summary.json", summary)
    print(output, flush=True)


if __name__ == "__main__":
    main()
