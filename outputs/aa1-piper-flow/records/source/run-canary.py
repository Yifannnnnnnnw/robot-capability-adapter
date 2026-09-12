"""Real TGCD file-tool continuation from an already completed real STUDY."""
import time
import subprocess
from pathlib import Path

root = Path(__file__).parent
exec((root / "original_recording_runner.py").read_text().split("root = Path(__file__).parent")[0])

cfg = SelfAssembleConfig(
    robot_id="piper", mjcf_path=repo / "assets/mjcf/piper/scene.xml",
    workspace_root=root / "workspace", mode="local", prepare_capabilities=True,
    max_tokens_per_turn=12000, max_iters_generate=30,
    bedrock_model="eu.anthropic.claude-sonnet-4-6", model_provider="holistic",
)
ws = root / "workspace/piper"
report = {"model": cfg.bedrock_model, "workspace": str(ws.resolve()),
    "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
    "study_reuse": json.loads((root / "study_reuse.json").read_text()),
    "phases": [], "physical_validation_executed": False, "diagnostic_complete": False}
try:
    with SelfAssemble(cfg) as runner:
        started = time.time()
        prep = runner._prepare_capability_design(json.loads((ws / "study.json").read_text()))
        report["phases"].append({"name":"capability_preparation", "ok":True,
            "error":None, "tokens":prep.get("token_usage",{}), "duration_sec":time.time()-started})
        phase = runner._phase_generate()
        report["phases"].append({"name":phase.name,"ok":phase.ok,"error":phase.error,
            "tokens":phase.token_usage,"duration_sec":phase.duration_sec})
        design = runner.capability_design
        required = [c["method_name"] for c in design["capabilities"]]
        report["capability_count"] = len(required)
        report["supported_task_count"] = len({s["task_id"] for s in design["task_support"]})
        report["required_methods"] = required
        tree = ast.parse((ws / "driver.py").read_text())
        methods = {m.name:m for c in ast.walk(tree) if isinstance(c,ast.ClassDef)
            and c.name=="Robot" for m in c.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef))}
        report["missing_methods"] = sorted(set(required)-set(methods))
        report["request_keyword_incompatible_methods"] = [name for name in required
            if name in methods and methods[name].args.kwarg is None
            and "request" not in {a.arg for a in methods[name].args.args+methods[name].args.kwonlyargs}]
        report["diagnostic_complete"] = (all(p["ok"] for p in report["phases"])
            and report["supported_task_count"]==20 and not report["missing_methods"]
            and not report["request_keyword_incompatible_methods"])
except Exception as error:
    report["error"] = f"{type(error).__name__}: {error}"
(root / "canary_result.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2),flush=True)
sys.exit(0 if report["diagnostic_complete"] else 1)
