"""Re-synthesize the 3 quadruped drivers with the FIXED infra (outer VAL->GEN
retry + STRICT locomotion validation: sit must lower the body, walk must
advance). The old go2/a1/anymal drivers were made by the pre-fix orchestrator
(lax 'no-crash' sit/walk validation) and have broken sit primitives.
argv: <short> ; runs one robot. Writes driver.py wrapper if it validates.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

MJCF = {
    "go2":     "assets/mjcf/go2/go2_scene.xml",
    "a1":      "assets/mjcf/unitree_a1/scene.xml",
    "anymal":  "assets/mjcf/anybotics_anymal_c/scene.xml",
}
short = sys.argv[1]
# Optional cross-model args: argv[2]=model_short, argv[3]=model_id.
# When given, synthesize into a per-model workspace (mirrors the SO-101
# cross-model Leaderboard-B: each model writes its OWN quadruped driver).
model_short = sys.argv[2] if len(sys.argv) > 2 else "sonnet46"
model_id = sys.argv[3] if len(sys.argv) > 3 else "us.anthropic.claude-sonnet-4-6"
ws_root = ("artifacts/from_scratch_quad_v2" if len(sys.argv) <= 2
           else "artifacts/from_scratch_quad_xmodel")
robot_id = short if len(sys.argv) <= 2 else f"{short}_{model_short}"
cfg = FromScratchConfig(
    robot_id=robot_id,
    mjcf_path=Path(MJCF[short]),
    workspace_root=Path(ws_root),
    bedrock_model=model_id,
    ci_session_timeout_sec=2700,
)
print(f"[quadv2] SYNTH {robot_id} model={model_id} mjcf={cfg.mjcf_path}", flush=True)
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
    print(f"[quadv2] wrote driver.py wrapper -> {ws/'driver.py'}", flush=True)

print(f"[quadv2] DONE {short} study_ok={r.study_ok} gen_ok={r.gen_ok} "
      f"validate_ok={r.validate_ok} outer_attempts={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')} "
      f"dur={r.total_duration_sec:.0f}s", flush=True)
# print the quad-specific test outcomes
for t in r.validate_report.get("tests", []):
    if t["test"] in ("stand_up", "sit", "walk_forward"):
        print(f"   {t['test']}: ok={t['ok']} {t.get('detail','')}", flush=True)
