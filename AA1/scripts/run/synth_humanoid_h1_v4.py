"""Synthesize a HUMANOID / biped driver for the Unitree H1 (free-floating
torso, 19 joint actuators, 51.4 kg). Tests the synthesis pipeline's new
HUMANOID branch: stand_balance (hold upright at height) + squat (lower then
recover without toppling), physics-validated. Bipedal balance is the hardest
class: a tall inverted pendulum that falls open-loop, so the agent must
synthesize a joint-space PD balance controller.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator
cfg = FromScratchConfig(
    robot_id="h1",
    mjcf_path=Path("assets/mjcf/h1/scene.xml"),
    workspace_root=Path("artifacts/from_scratch_humanoid_v4"),
    bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ci_session_timeout_sec=3000,
    max_iters_study=26,
)
print(f"[humanoid] SYNTH h1 mjcf={cfg.mjcf_path}", flush=True)
with FromScratchOrchestrator(cfg) as orch:
    r = orch.run()
if r.validate_ok:
    ws = Path(r.workspace)
    (ws / "driver.py").write_text(
        "import os\nfrom driver_from_scratch import Robot  # noqa: F401\n"
        "_SCENE=os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'mjcf.xml'))\n"
        "def build():\n    return Robot.build_from_mjcf(_SCENE)\n")
    print(f"[humanoid] wrote driver.py -> {ws/'driver.py'}", flush=True)
print(f"[humanoid] DONE study={r.study_ok} gen={r.gen_ok} validate={r.validate_ok} "
      f"outer={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')}", flush=True)
for t in r.validate_report.get("tests", []):
    if t["test"] in ("stand_balance", "squat", "build_from_mjcf", "home"):
        print(f"   {t['test']}: ok={t['ok']} {t.get('detail','')}", flush=True)
