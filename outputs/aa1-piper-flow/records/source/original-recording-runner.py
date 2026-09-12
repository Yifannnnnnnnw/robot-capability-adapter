import ast
import json
import sys
from pathlib import Path

repo = Path("/Users/wangyifan/Projects/auto_adapter2.0/AA1")
sys.path.insert(0, str(repo))
from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig

from auto_adapter.agent.react_loop import ReactLoop
from auto_adapter.agent.holistic_client import to_openai_messages

# Diagnostic-only recording at the real framework/model boundary.
# Never record transport headers, credentials, or environment variables.
def serializable(value):
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return serializable(vars(value))
    return value

_original_invoke = ReactLoop._invoke_with_retry

def record_invoke(self, messages):
    record_path = self.trace_path.with_suffix(".messages.jsonl")
    def append(value):
        with record_path.open("a") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
    append({"event": "request", "model": self.model,
            "system": self.system, "tools": self.tool_schemas,
            "max_tokens": self.max_tokens,
            "messages": serializable(messages),
            "gateway_messages": to_openai_messages(messages, self.system)})
    try:
        response = _original_invoke(self, messages)
    except Exception as error:
        append({"event": "error", "type": type(error).__name__, "message": str(error)})
        raise
    append({"event": "response", "response": serializable(response)})
    return response

ReactLoop._invoke_with_retry = record_invoke

root = Path(__file__).parent
cfg = SelfAssembleConfig(
    robot_id="piper",
    mjcf_path=repo / "assets/mjcf/piper/scene.xml",
    workspace_root=root / "workspace",
    mode="local",
    prepare_capabilities=True,
    max_tokens_per_turn=12000,
    bedrock_model="eu.anthropic.claude-sonnet-4-6",
    model_provider="holistic",
)
with SelfAssemble(cfg) as runner:
    result = runner.run(stop_after="generate")
ws = result.workspace
phases = [{"name": p.name, "ok": p.ok, "error": p.error,
           "tokens": p.token_usage, "duration_sec": p.duration_sec}
          for p in result.phases]
report = {"model": cfg.bedrock_model, "workspace": str(ws), "phases": phases,
          "physical_validation_executed": False}
design_path = ws / "capability_inputs/capability_design.json"
if design_path.exists():
    design = json.loads(design_path.read_text())
    required = [c["method_name"] for c in design["capabilities"]]
    report["capability_count"] = len(required)
    report["supported_task_count"] = len({p["task_id"] for p in design["task_support"]})
    report["required_methods"] = required
    driver_path = ws / "driver.py"
    if driver_path.exists():
        tree = ast.parse(driver_path.read_text())
        declared = {m.name for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                    and c.name == "Robot" for m in c.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        report["missing_methods"] = sorted(set(required) - declared)
    else:
        report["missing_methods"] = required
active = [p for p in phases if p["name"] in ("study", "01_study", "generate", "02_generate")]
report["diagnostic_complete"] = (len(active) == 2 and all(p["ok"] for p in active)
    and report.get("supported_task_count") == 20
    and "missing_methods" in report and not report["missing_methods"])
(root / "canary_result.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if report["diagnostic_complete"] else 1)
