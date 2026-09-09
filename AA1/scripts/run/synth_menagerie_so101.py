"""MJCF-PROVENANCE ablation: synthesize an SO-101 driver from the OFFICIAL
MuJoCo Menagerie robotstudio_so101 spec (third-party-curated), with the fixed
orchestrator (outer VAL->GEN retry + object-perception API). Shows synthesis
works from a spec we did not author, not only our URDF-compiled one.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

cfg = FromScratchConfig(
    robot_id="menagerie_so101",
    mjcf_path=Path("assets/mjcf/robotstudio_so101/scene.xml"),
    workspace_root=Path("artifacts/from_scratch_ablation"),
    bedrock_model="us.anthropic.claude-sonnet-4-6",  # default synthesis model
)
print(f"[menagerie] SYNTH robot={cfg.robot_id} mjcf={cfg.mjcf_path}", flush=True)
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
    print(f"[menagerie] wrote driver.py wrapper -> {ws/'driver.py'}", flush=True)

print(f"[menagerie] DONE study_ok={r.study_ok} gen_ok={r.gen_ok} "
      f"validate_ok={r.validate_ok} "
      f"outer_attempts={r.validate_report.get('outer_attempts')} "
      f"n_passed={r.validate_report.get('n_passed')}/{r.validate_report.get('n_total')} "
      f"dur={r.total_duration_sec:.0f}s", flush=True)
