"""Leaderboard-B cross-model SYNTHESIS: one model synthesizes its own SO-101
driver from scratch, with the outer VAL->GEN retry loop. argv: <short> <model_id>
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

short, model_id = sys.argv[1], sys.argv[2]
# optional argv[3] overrides robot_id (used for failure reruns into distinct
# workspaces so attempts don't overwrite each other — for the k/3 metric).
robot_id = sys.argv[3] if len(sys.argv) > 3 else f"so101_{short}"
cfg = FromScratchConfig(
    robot_id=robot_id,
    mjcf_path=Path("assets/mjcf/so101_mujoco.xml"),
    workspace_root=Path("artifacts/from_scratch_xmodel"),
    bedrock_model=model_id,
    ci_session_timeout_sec=2700,   # 45 min — room for first gen + 2 repairs
)
print(f"[xsynth] SYNTHESIZER={short} ({model_id}) robot={cfg.robot_id}", flush=True)
with FromScratchOrchestrator(cfg) as orch:
    r = orch.run()

# If synthesis passed, write the driver.py wrapper that TaskPlanner loads. The
# wrapper passes the ABSOLUTE MJCF path so include-resolution works regardless
# of whether the model's build_from_mjcf realpaths internally. Guaranteed here
# (not a separate manual step) so every passing diagonal target has it.
if r.validate_ok:
    ws = Path(r.workspace)
    (ws / "driver.py").write_text(
        "import os\n"
        "from driver_from_scratch import Robot  # noqa: F401\n"
        "_SCENE = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mjcf.xml'))\n"
        "def build():\n"
        "    return Robot.build_from_mjcf(_SCENE)\n"
    )
    print(f"[xsynth] wrote driver.py wrapper -> {ws/'driver.py'}", flush=True)

print(f"[xsynth] DONE short={short} study_ok={r.study_ok} gen_ok={r.gen_ok} "
      f"validate_ok={r.validate_ok} "
      f"outer_attempts={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')} "
      f"dur={r.total_duration_sec:.0f}s cost_tok_out={r.total_tokens.get('out')}", flush=True)
