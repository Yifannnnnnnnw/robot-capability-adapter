"""Synthesize an AERIAL / multirotor driver for the Skydio X2 quadcopter
(free-flying base, 4 thrust rotors). Tests the synthesis pipeline's new
AERIAL branch: takeoff (gain altitude + stay upright) + move_to (fly to a
horizontal target without flipping), physics-validated. This is the hardest
class: the robot is underactuated and open-loop unstable, so the agent must
synthesize a cascaded feedback controller (position -> attitude -> rotor mix).
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator
cfg = FromScratchConfig(
    robot_id="skydio_x2",
    mjcf_path=Path("assets/mjcf/skydio_x2/scene.xml"),
    workspace_root=Path("artifacts/from_scratch_aerial_v2"),
    bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ci_session_timeout_sec=3000,
    max_iters_study=26,
)
print(f"[aerial] SYNTH skydio_x2 mjcf={cfg.mjcf_path}", flush=True)
with FromScratchOrchestrator(cfg) as orch:
    r = orch.run()
if r.validate_ok:
    ws = Path(r.workspace)
    (ws / "driver.py").write_text(
        "import os\nfrom driver_from_scratch import Robot  # noqa: F401\n"
        "_SCENE=os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'mjcf.xml'))\n"
        "def build():\n    return Robot.build_from_mjcf(_SCENE)\n")
    print(f"[aerial] wrote driver.py -> {ws/'driver.py'}", flush=True)
print(f"[aerial] DONE study={r.study_ok} gen={r.gen_ok} validate={r.validate_ok} "
      f"outer={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')}", flush=True)
for t in r.validate_report.get("tests", []):
    if t["test"] in ("takeoff", "move_to", "build_from_mjcf", "home"):
        print(f"   {t['test']}: ok={t['ok']} {t.get('detail','')}", flush=True)
