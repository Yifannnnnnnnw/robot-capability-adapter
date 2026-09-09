"""Generalized cross-model self-synthesis runner: ANY model synthesizes its OWN
driver for ANY robot/morphology, from scratch (no skeleton), with the orchestrator's
class-appropriate validate (quad: stand/walk; aerial: takeoff/move; humanoid:
stand/squat; arm: grasp) + the VAL->GEN repair loop.

argv: <model_short> <bedrock_model_id> <robot_base> <mjcf_path> <run_n>
Writes to artifacts/from_scratch_xrobot/<robot_base>_<model_short>_rr<n>.
"""
import sys, os, json, signal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

short, model_id, robot_base, mjcf, run_n = sys.argv[1:6]
robot_id = f"{robot_base}_{short}_rr{run_n}"

# HARD wall-clock cap per cell. The per-call Bedrock/CI timeouts don't always
# catch socket-level hangs (a single LLM call has frozen for hours), which
# deadlocks the MAXJOBS gate. On SIGALRM: write a failed stub report so the cell
# counts as a synthesis failure (NOT retried) and the process exits.
_WS = Path("artifacts/from_scratch_xrobot") / robot_id
def _on_timeout(sig, frame):  # noqa: ANN001
    try:
        _WS.mkdir(parents=True, exist_ok=True)
        (_WS / "validate_report.json").write_text(json.dumps(
            {"timed_out": True, "structural_ok": False, "all_ok": False, "tests": []}))
        print(f"[xrobot] {robot_id} WALL-CLOCK TIMEOUT (3000s) — wrote failed stub, exiting",
              flush=True)
    finally:
        os._exit(1)
signal.signal(signal.SIGALRM, _on_timeout)
signal.alarm(3000)  # 50 min hard cap (orchestrator's own session cap is 2700s)
cfg = FromScratchConfig(
    robot_id=robot_id,
    mjcf_path=Path(mjcf),
    workspace_root=Path("artifacts/from_scratch_xrobot"),
    bedrock_model=model_id,
    ci_session_timeout_sec=2700,
)
print(f"[xrobot] SYNTH {robot_id} model={model_id} mjcf={mjcf}", flush=True)
with FromScratchOrchestrator(cfg) as orch:
    r = orch.run()

if r.validate_ok:
    ws = Path(r.workspace)
    (ws / "driver.py").write_text(
        "import os\n"
        "from driver_from_scratch import Robot  # noqa: F401\n"
        "_SCENE = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mjcf.xml'))\n"
        "def build():\n"
        "    return Robot.build_from_mjcf(_SCENE)\n"
    )
    print(f"[xrobot] wrote driver.py wrapper -> {ws/'driver.py'}", flush=True)

print(f"[xrobot] DONE {robot_id} study_ok={r.study_ok} gen_ok={r.gen_ok} "
      f"validate_ok={r.validate_ok} outer_attempts={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')} "
      f"dur={r.total_duration_sec:.0f}s", flush=True)
