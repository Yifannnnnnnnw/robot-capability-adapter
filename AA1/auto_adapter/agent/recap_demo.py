# SPDX-License-Identifier: Apache-2.0
"""AA1 model and capability bridge for diagnostic ReCAP tasks.

Controller completion is not an independently evaluated physical task verdict.
"""
import json
from pathlib import Path
import time

from .react_loop import ReactLoop
from .recap import CapabilityAdapterError


class AA1RecapModel:
    """Send official ReCAP's JSON conversation through AA1's model transport."""

    def __init__(self, *, model, provider, region, max_tokens, trace_path):
        transport = ReactLoop(tools=[], system="", model=model, provider=provider,
                              region=region, max_tokens_per_turn=max_tokens)
        self.client, self.model = transport.client, transport.model
        self.max_tokens = max_tokens
        self.trace_path = Path(trace_path)
        self.trace_path.write_text("")

    def generate_json(self, *, messages):
        system = [
            'Return only a JSON object with "think" and "subtasks". '
            '"think" is a brief plan summary; "subtasks" is an ordered list of strings.'
        ]
        converted = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(message["content"])
                continue
            item = {"role": role, "content": message["content"]}
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"] += "\n\n" + item["content"]
            else:
                converted.append(item)
        response = self.client.messages.create(
            model=self.model, system="\n\n".join(system), messages=converted,
            max_tokens=self.max_tokens)
        content = "\n".join(block.text for block in response.content if block.type == "text")
        with self.trace_path.open("a") as stream:
            stream.write(json.dumps({"messages": messages, "response": content}) + "\n")
        return content


def passed_design(design, suite, report):
    """Require every trusted case for a capability, including boundary cases."""
    tests = report.get("tests", [])
    selected = []
    for capability in design["capabilities"]:
        cases = [c for c in suite.get("scene_cases", suite.get("cases", []))
                 if c["capability_id"] == capability["capability_id"]]
        if cases and all(
            len(matches := [t for t in tests if t.get("case_id") == c["case_id"]]) == 1
            and matches[0].get("ok") is True for c in cases
        ):
            selected.append(capability)
    if not selected:
        raise ValueError("ReCAP demo has no fully Framework-passed capability")
    return {**design, "capabilities": selected}


class AA1CapabilityAdapter:
    """AA1 public schemas omit design-time evidence_refs; validate native requests."""

    def __init__(self, design, invoke):
        self.robot_configuration_id = design["robot_configuration_id"]
        self.capability_design_id = self.robot_configuration_id + "::aa1-catalog"
        self.capabilities = {c["method_name"]: c for c in design["capabilities"]}
        self.invoke = invoke

    def public_catalog(self):
        def public_schema(value):
            if isinstance(value, dict):
                return {key: public_schema(child) for key, child in value.items()
                        if key != "evidence_refs"}
            if isinstance(value, list):
                return [public_schema(child) for child in value]
            return value

        return [{"capability_id": cap["capability_id"],
                 "method_name": cap["method_name"],
                 "capability_name": cap["method_name"],
                 "description": cap["description"],
                 "request_schema": public_schema(cap["request_schema"]),
                 "invocation_abi": "driver.<capability_name>(request=<request>)"}
                for cap in self.capabilities.values()]

    def validate_request(self, name, request):
        from auto_adapter.scene_runtime import SceneCaseError, _validate_schema_value
        if name not in self.capabilities:
            raise CapabilityAdapterError("unknown capability")
        try:
            _validate_schema_value(request, self.capabilities[name]["request_schema"],
                                   where=f"{name}.request")
        except SceneCaseError as exc:
            raise CapabilityAdapterError(str(exc)) from None
        return dict(request)

    def execute(self, name, request):
        return self.invoke(name, {"request": self.validate_request(name, request)})


def _task_inputs(*, workspace, robot_id, capability_design=None,
                 scene_cases_path=None, demo_config_path=None, from_scratch=False):
    """Resolve generation artifacts once; never replace an explicitly selected design."""
    import yaml
    from auto_adapter.robot_catalog import (
        REPO_ROOT, find_robot_definition, load_capability_design, load_capability_suite,
    )
    from auto_adapter.scene_runtime import load_scene_cases

    workspace = Path(workspace).resolve()
    robot = find_robot_definition(robot_id)
    config_path = Path(demo_config_path or REPO_ROOT / "auto_adapter/demo_tasks.yaml").resolve()
    config = yaml.safe_load(config_path.read_text())["robots"].get(robot_id)
    if not config:
        raise ValueError(f"no fixed demo configured for {robot_id}")
    design = capability_design
    if design is None:
        saved = workspace / "design/capability_design.json"
        if not saved.is_file():
            saved = workspace / "capability_design.json"
        design = json.loads(saved.read_text()) if saved.is_file() else load_capability_design(robot)
    if not design:
        raise ValueError(f"{robot_id}: no capability design supplied for the existing driver")
    if design.get("robot_configuration_id") != robot_id:
        raise ValueError("task robot does not match capability design")
    cases_path = Path(scene_cases_path).resolve() if scene_cases_path else workspace / "design/scene_cases.yaml"
    if scene_cases_path is not None and not cases_path.is_file():
        raise ValueError(f"supplied scene_cases_path does not exist: {cases_path}")
    if scene_cases_path is None and not cases_path.is_file():
        cases_path = workspace / "scene_cases.yaml"
    if cases_path.is_file():
        suite = load_scene_cases(cases_path, design=design)
    else:
        # The catalog suite is valid only for that exact public contract.
        if design != load_capability_design(robot):
            raise ValueError("current capability design requires its corresponding scene_cases_path")
        suite = load_capability_suite(robot)
    scene = Path(config["scene"])
    if not scene.is_absolute():
        scene = REPO_ROOT / scene
    return dict(
        driver_path=str(workspace / ("driver_from_scratch.py" if from_scratch else "driver.py")),
        from_scratch=from_scratch, robot_id=robot_id, capability_design=design,
        validation_suite=suite,
        validation_report=json.loads((workspace / "validate_report.json").read_text()),
        task_description=config["task"], scene_path=str(scene.resolve()),
        initial_state=config.get("initial_state", {}), parameters=config.get("parameters", {}),
        required_capabilities=config.get("required_capabilities", []),
    )


def run_configured_demo(*, workspace, robot_id, capability_design, scene_cases_path=None,
                        from_scratch=False, model, provider, region, max_tokens,
                        demo_config_path=None):
    """Both orchestrators dispatch the same isolated, existing-driver task process."""
    import os
    import subprocess
    import sys
    import tempfile
    from auto_adapter.robot_catalog import REPO_ROOT

    started = time.monotonic()
    root = Path(workspace).resolve() / "demos"
    root.mkdir(parents=True, exist_ok=True)
    invocation_dir = Path(tempfile.mkdtemp(prefix="recap-", dir=root))
    output_dir = invocation_dir / "task"
    report_path = output_dir / "task_report.json"
    try:
        inputs = _task_inputs(workspace=workspace, robot_id=robot_id,
                              capability_design=capability_design, scene_cases_path=scene_cases_path,
                              from_scratch=from_scratch, demo_config_path=demo_config_path)
        inputs.update(output_dir=str(output_dir), model=model, provider=provider,
                      region=region, max_tokens=max_tokens)
        payload = invocation_dir / "request.json"
        payload.write_text(json.dumps(inputs, indent=2) + "\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        with (invocation_dir / "worker.log").open("w") as log:
            completed = subprocess.run(
                [sys.executable, "-m", "auto_adapter.agent.recap_demo", "--input-json", str(payload)],
                env=env, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                timeout=2400, check=False,
            )
        if not report_path.is_file():
            raise RuntimeError(f"task worker exited {completed.returncode}; see {invocation_dir / 'worker.log'}")
        report = json.loads(report_path.read_text())
        if completed.returncode and report.get("ok"):
            report.update(ok=False, error=f"task worker exited {completed.returncode}")
        return report
    except Exception as exc:
        report = {"ok": False, "error": str(exc), "controller_result": None,
                  "physical_task_success": None, "duration_sec": time.monotonic() - started,
                  "report_path": str(report_path), "trace_path": None, "video_path": None}
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        return report


def run_demo(*, workspace, robot_id, task_description=None, model, provider="holistic",
             region="us-east-1", max_tokens=6000, from_scratch=False,
             demo_config_path=None, output_dir=None):
    """Compatibility wrapper; use run_task/--input-json for arbitrary explicit tasks and scenes."""
    import tempfile
    from .task_execution import run_task

    inputs = _task_inputs(workspace=workspace, robot_id=robot_id,
                          from_scratch=from_scratch, demo_config_path=demo_config_path)
    if task_description:
        inputs.update(task_description=task_description, required_capabilities=[])
    if output_dir is None:
        root = Path(workspace).resolve() / "demos"
        root.mkdir(parents=True, exist_ok=True)
        output_dir = Path(tempfile.mkdtemp(prefix="recap-", dir=root)) / "task"
    return run_task(**inputs, output_dir=output_dir, model=model, provider=provider,
                    region=region, max_tokens=max_tokens)


def main():
    import argparse
    from .task_execution import run_task

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path,
                        help="Explicit run_task keyword inputs; never generates or repairs a driver.")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--robot-id")
    parser.add_argument("--task", dest="task_description")
    parser.add_argument("--model")
    parser.add_argument("--provider", default="holistic")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--from-scratch", action="store_true")
    parser.add_argument("--demo-config-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = vars(parser.parse_args())
    input_json = args.pop("input_json")
    if input_json:
        report = run_task(**json.loads(input_json.read_text()))
    else:
        if not all(args[key] for key in ("workspace", "robot_id", "model")):
            parser.error("supply --input-json or --workspace, --robot-id and --model")
        report = run_demo(**args)
    print(json.dumps({key: report.get(key) for key in ("ok", "error", "video_path", "report_path")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
