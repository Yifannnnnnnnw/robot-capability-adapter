# SPDX-License-Identifier: Apache-2.0
"""Local AA1 diagnostic demo using the canonical AutoAdapter 2 ReCAP runtime.

Requires the sibling autoadapter package on PYTHONPATH (or installed). Controller
completion is not an independently evaluated physical task verdict.
"""
from dataclasses import asdict
import json
from pathlib import Path
import time

from .react_loop import ReactLoop
from .task_planner import TaskPlanner, _FrameCapture
from auto_adapter.robot_catalog import load_capability_suite


class AA1RecapModel:
    """Adapt AA1's existing model transport to ReCAP's native tool turns."""

    def __init__(self, *, model, provider, region, max_tokens, trace_path):
        transport = ReactLoop(tools=[], system="", model=model, provider=provider,
                              region=region, max_tokens_per_turn=max_tokens)
        self.client, self.model = transport.client, transport.model
        self.max_tokens = max_tokens
        self.trace_path = Path(trace_path)
        self.trace_path.write_text("")

    def generate_tool_turn(self, *, stage, system_prompt, messages, tools):
        from autoadapter2.react import ToolCall, ToolTurn
        converted = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                item = {"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": message["tool_call_id"], "content": message["content"]}]}
            elif role == "assistant":
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": call["id"],
                                   "name": call["function"]["name"],
                                   "input": json.loads(call["function"]["arguments"])})
                item = {"role": role, "content": blocks}
            else:
                item = {"role": role, "content": message["content"]}
            if (converted and item["role"] == "user" and converted[-1]["role"] == "user"
                    and isinstance(item["content"], list) and isinstance(converted[-1]["content"], list)):
                converted[-1]["content"].extend(item["content"])
            else:
                converted.append(item)
        response = self.client.messages.create(
            model=self.model, system=system_prompt, messages=converted,
            max_tokens=self.max_tokens,
            tools=[{"name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"]} for t in tools])
        calls, texts = [], []
        for block in response.content:
            if block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, block.input, json.dumps(block.input)))
            elif block.type == "text":
                texts.append(block.text)
        turn = ToolTurn("\n".join(texts) or None, tuple(calls), response.stop_reason)
        with self.trace_path.open("a") as stream:
            stream.write(json.dumps({"stage": stage, "turn": asdict(turn)}) + "\n")
        return turn


def passed_design(design, suite, report):
    """Require every trusted case for a capability, including boundary cases."""
    tests = report.get("tests", [])
    selected = []
    for capability in design["capabilities"]:
        cases = [c for c in suite["cases"] if c["capability_id"] == capability["capability_id"]]
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
        from autoadapter2.b2.capability_adapter import CapabilityContract
        return [CapabilityContract(**{key: cap[key] for key in
                ("capability_id", "method_name", "description", "request_schema")}).public_definition()
                for cap in self.capabilities.values()]

    def validate_request(self, name, request):
        from autoadapter2.b2.capability_adapter import CapabilityAdapterError
        from autoadapter2.capability_design.protocol import CapabilityProtocolError, validate_schema_value
        if name not in self.capabilities:
            raise CapabilityAdapterError("unknown capability")
        try:
            validate_schema_value(request, self.capabilities[name]["request_schema"])
        except CapabilityProtocolError as exc:
            raise CapabilityAdapterError(str(exc)) from None
        return dict(request)

    def execute(self, name, request):
        return self.invoke(name, {"request": self.validate_request(name, request)})


def run_demo(*, workspace, robot_id, task_description, model, provider="holistic",
             region="us-east-1", max_tokens=6000):
    from autoadapter2.task_demo.recap import run_recap
    import imageio.v2 as imageio

    workspace = Path(workspace).resolve()
    started = time.monotonic()
    trace_path = workspace / "traces" / "recap_demo.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    video_path = workspace / "demo.mp4"
    # A previous recording cannot make this attempt appear complete.
    video_path.unlink(missing_ok=True)
    with TaskPlanner(workspace=workspace, robot_id=robot_id) as planner:
        if not planner.capability_design:
            raise ValueError("ReCAP demo requires a trusted capability profile")
        design = passed_design(planner.capability_design,
                               load_capability_suite(planner.robot_definition),
                               json.loads((workspace / "validate_report.json").read_text()))
        driver = planner._load_driver()
        capture = _FrameCapture(driver, capture_every=16)
        calls = []
        try:
            tools = {t.name: t for t in planner._build_tools(driver, capture, calls)}
            def observations():
                # Only existing no-argument public observation methods are exposed.
                values = {}
                for name, tool in tools.items():
                    if name.startswith("get_") and not tool.input_schema.get("required"):
                        try:
                            values[name] = tool.handler({})
                        except Exception:
                            values[name] = {"available": False}
                return values

            def invoke(name, envelope):
                try:
                    tools[name].handler(envelope["request"])
                    status = "EXECUTED"
                except Exception:
                    status = "ERROR"
                return {"operation": {"status": status}, "observations": observations()}

            capture.snapshot()
            result = run_recap(public_task={"description": task_description},
                               adapter=AA1CapabilityAdapter(design, invoke),
                               model=AA1RecapModel(model=model, provider=provider, region=region,
                                                  max_tokens=max_tokens, trace_path=trace_path),
                               initial_public_state=observations())
        finally:
            capture.uninstall()
            close = getattr(driver, "close", None)
            if callable(close):
                close()
        if len(capture.frames) > 1:
            imageio.mimsave(str(video_path), capture.frames, format="FFMPEG", fps=30,
                           codec="libx264", pixelformat="yuv420p")
    report = {"controller": "autoadapter2.task_demo.recap.run_recap",
              "task_description": task_description, "controller_result": asdict(result),
              "physical_task_success": None, "scope": "diagnostic demo; no task predicate evaluated",
              "capability_whitelist": [c["method_name"] for c in design["capabilities"]],
              "tool_call_log": calls, "n_frames": len(capture.frames),
              "video_path": str(video_path) if video_path.exists() else None,
              "trace_path": str(trace_path), "duration_sec": time.monotonic() - started}
    report["ok"] = (result.status == "CONTROLLER_FINISHED" and result.capability_calls > 0
                    and len(capture.frames) > 1 and video_path.exists()
                    and all(c["ok"] for c in calls))
    (workspace / "recap_demo_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--task", dest="task_description", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="holistic")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-tokens", type=int, default=6000)
    args = vars(parser.parse_args())
    report = run_demo(**args)
    print(json.dumps({key: report[key] for key in ("ok", "video_path", "n_frames", "scope")}))
    raise SystemExit(0 if report["ok"] else 1)
