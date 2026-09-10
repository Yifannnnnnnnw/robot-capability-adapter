# AA1 capability integration diagnostics

This file describes the current bounded Stage 1 diagnostic. It is an
integration check, not a formal experiment protocol, manifest, or governance
record. The parent repository tracks AA1; the original AA1 upstream is never a
push destination.

Stage 1 runs fresh generation for the remaining canonical robots in this
order: `franka`, `so101`, `kuka_iiwa14`, `ufactory_xarm7`,
`kinova_gen3_robotiq_2f85`, `universal_robots_ur5e_robotiq_2f85`, `go2`,
`unitree_a1`, `anymal_c`, and `h1`. The robot catalog marks the thirteen
canonical skeleton-route robots explicitly; `h1` and `skydio_x2` use the
from-scratch route. `piper`, `leap_hand`, `hello_robot_stretch_2`, `aloha_2`,
and `skydio_x2` already passed their earlier bounded diagnostic scope and are
excluded from this launcher. They are not rerun by Stage 1.

The launcher in `scripts/run/run_stage1.py` is only a thin invocation wrapper.
It calls `auto_adapter.orchestrator.run_stage1` once per selected robot with a
fresh initial generation and up to three Framework-feedback repairs. It does
not implement generation, repair, validation, Demo, evaluation, or ReCAP.
The default model is `eu.anthropic.claude-opus-4-8`; `--max-repairs` accepts
0–3. An existing output root is valid when submissions contain disjoint
robots. The pipeline decides whether a particular robot workspace may be
written again. If the pipeline reports an external model/API block, the
launcher stops model calls and records later selected robots as
`not_run_external_blocked` with the originating error.

Each robot's pipeline-owned evidence has an `initial/<robot>/` directory and,
when needed, `repair_N/<robot>/` directories. A compact `summary_<robot>.json`
records the declared outcome and paths. The launcher creates one small
`launch_<UTC timestamp>.json` per invocation with selected robots, timing,
forwarded arguments, and compact results; it does not replace those per-robot
summaries. In each launch row, `duration_sec` is the UTC wall interval between
`started_at_utc` and `finished_at_utc`; `monotonic_duration_sec` preserves the
local monotonic measurement for diagnostics. A returned API zero-response or
transport error is not a valid repair and cannot turn an existing candidate
into a successful result. Costs are `null` when the provider does not report a
known cost.

For the skeleton route, Framework evaluates every public capability condition
in its nominal and boundary cases using the trusted catalog and the same
MuJoCo model/data world. H1 retains all eight checks: driver build, home,
real two-second `stand_balance` physics with its trace and video,
`squat`, `humanoid_walk`, and the required structural checks. A missing or
failed H1 walking method remains a failed check. Stage 2 is deferred.

The 2026-09-10 A4 correction makes the existing public capability designs
explicit about the precontact dwell and measured approach origin. Scoring
uses that measured origin, nearest collision-surface relative velocity and
first-contact normal closing velocity. Its failure measurements use the same
gates and contact window as its verdict. The live A4 recorder supplies the two
new velocity fields; it does not alter actuator settings or dynamics. This is
a semantic correction to the earlier implementation, with the numerical
thresholds retained, so older verdicts remain attached to their original code.

Saved-state A4 rescoring is distinct from a fresh generation or physics run.
Reconstruct velocity observations only when the saved qpos/qvel and canonical
model reproduce the recorded poses and contacts at the same timestamps. Missing
or inconsistent evidence is reported as unavailable; it is not silently treated
as passing. Keep rescores beside the original evidence without overwriting the
original driver, report, or completed repair budget. The correction review and
real xArm7 reference checks are recorded in
[A4 correction report](diagnostics/capability_update_20260909/stage1_full_opus48_20260910T133740Z/a4_correction_20260910T142252Z/REPORT.md).

Reference controls establish whether a trusted condition is physically
reachable; they are not model-generated drivers and do not enter a generated
result. Previously recorded reference gaps include Unitree A1 and ANYmal-C G1–G3
(nominal and boundary). The earlier reports remain historical evidence:
[FIRST_ROUND_RESULTS.md](diagnostics/capability_update_20260909/FIRST_ROUND_RESULTS.md),
[HIGHER_MODEL_RESULTS.md](diagnostics/capability_update_20260909/HIGHER_MODEL_RESULTS.md),
and [FULL_REPORT_20260910.md](diagnostics/capability_update_20260909/FULL_REPORT_20260910.md).
In particular, the old PiPER generation failure is preserved in those reports
and is not presented as a current Stage 1 result.

Compact summaries, source, and generation traces are suitable for Git review.
Large per-trial videos and raw physics traces remain local. H1's current
stand evidence is written under its pipeline workspace as
`stand_balance_2s_physics_trace.json` and
`recordings/stand_balance_2s.mp4`.

Run from `AA1` with the project virtual environment. For a shared batch root,
review and commit each remaining robot as a separate invocation:

```sh
BATCH="autoadapter_bench/diagnostics/capability_update_20260909/stage1_full_opus48_$(date -u +%Y%m%dT%H%M%SZ)"

.venv/bin/python scripts/run/run_stage1.py --robots franka --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots so101 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots kuka_iiwa14 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots ufactory_xarm7 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots kinova_gen3_robotiq_2f85 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots universal_robots_ur5e_robotiq_2f85 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots go2 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots unitree_a1 --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots anymal_c --output-root "$BATCH"
.venv/bin/python scripts/run/run_stage1.py --robots h1 --output-root "$BATCH"
```

Focused launcher and local-route checks use test doubles only:

```sh
.venv/bin/python -m pytest -q \
  auto_adapter/tests/test_from_scratch_local_mode.py \
  auto_adapter/tests/test_stage1_launcher.py \
  auto_adapter/tests/test_h1_framework_duration.py
```
