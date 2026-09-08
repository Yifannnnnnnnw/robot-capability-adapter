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
from autoadapter2.model_api import JsonModelClient, ModelConfig
from autoadapter2.harness.runner import _merged_private_index, _run_worker
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.pipeline import (
    ExperimentConfig, PipelineHooks, _load_fixed_inputs,
    _run_reference_positive_control, run_experiment,
)

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def model_client(config: ExperimentConfig) -> JsonModelClient:
    # Read simple dotenv assignments without executing shell code or logging secrets.
    env_file = ROOT.parent / ".env"
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
    manifest = config.model_manifest
    assert manifest is not None
    runtime = replace(
        ModelConfig.from_env(), provider="deepseek", api_protocol="openai-compatible",
        model=manifest["model_id"], base_url=manifest["base_url"],
        thinking="disabled", max_tokens=manifest["max_output_tokens"],
    )
    return JsonModelClient(runtime)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robots", nargs="+")
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = ExperimentConfig.from_path(ROOT / "configs/diagnostics/fixed-family-v1.json")
    robots = args.robots or list(config.robots)
    if not set(robots) <= set(config.robots):
        parser.error("robot is outside the approved diagnostic cohort")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "runs/diagnostic" / f"fixed-family-v1-{stamp}").resolve()
    if output.exists():
        parser.error("output must be fresh; existing cells must not be overwritten")
    output.mkdir(parents=True)
    write(output / "diagnostic_config.json", config.as_dict())
    summary = {"formal": False, "reference_only": args.reference_only, "robots": {}}
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
                client = model_client(one)
                print(f"{robot}: real model synthesis", flush=True)
                result["model_started"] = True
                report = run_experiment(
                    ROOT, config=one, output_dir=output / robot / "candidate",
                    run_id=f"fixed-family-{stamp}-{robot}", client=client,
                    fixed_inputs_from=ROOT / "references/fixed_family_v1",
                    skip_reference_calibration=True,
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
