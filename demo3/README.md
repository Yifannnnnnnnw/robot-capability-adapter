# AutoAdapter 2.0 Demo3

Demo3 is the self-contained, experiment-grade Direct-MuJoCo mainline. It does
not import or copy runtime code from Demo2 or `general_demo`.

## Experiment

- `robotstudio_so101`: 20 sourced manipulation tasks.
- `unitree-go2-stock-12dof`: 22 sourced locomotion tasks.
- Real-model TGCD reads each public Task Library and authors 5-10 capability
  methods plus source-grounded validation contracts.
- Trusted IVC compiles a private suite that generation cannot read.
- Both package-local references must pass the generated suites before dynamic
  Driver Synthesis starts.
- The four dynamic cells are the two robots crossed with the preserved
  `skeleton-assisted` and `from-scratch` conditions.
- Each cell runs interactive AutoAdapter 1.0-style STUDY and GENERATE/GEN_ALGO,
  canonical isolated validation, up to two interactive Repair attempts, and
  Evolution.
- STUDY, generation, and Repair use bounded multi-turn tool conversations. The
  model can inspect staged public files, run public-only MuJoCo probes, revise
  its driver, and react to audit/import/smoke diagnostics before submission.
- Generation starts from an interface-only stub mechanically derived from the
  sealed capability design. It contains exact `(self, request)` signatures and
  `NotImplementedError` placeholders, but no controller or task dispatch.

The candidate receives the public robot package, public Task Library, sealed
capability design, fixed `method(request=request)` transport ABI, and condition
specific primitives. It never receives private cases, reference source, or a
Framework-owned MuJoCo session outside the Harness.

A driver becomes a formal attempt only after the model explicitly submits the
current revision. Submission requires source audit and a successful public
physics smoke for every sealed capability; development rewrites and rejected
pre-submission checks do not consume one of the three Harness attempts.

## Environment

Demo3 pins Python 3.11, MuJoCo 3.9.0, NumPy 2.4.6, and pytest 9.1.1 in
`pyproject.toml`. `ffmpeg` and `ffprobe` are the only system tools required for
per-case video evidence.

```bash
cd demo3
python -m pip install -e '.[test]'
python -m autoadapter2 check-only
python -m pytest -q
```

The real-model command reads credentials only from environment variables. From
the repository root, the current DeepSeek configuration can be loaded without
putting a secret inside Demo3:

```bash
set -a
source .env
set +a
export AUTOADAPTER_MODEL_MAX_TOKENS=32768
export PYTHONPATH=demo3/src
export MUJOCO_GL=cgl
python -m autoadapter2 full --run-id <run-id>
```

## Evidence

The current robot models have pinned upstream provenance, but a post-run audit
found that several task fixtures and measurements are local proxies that do not
yet satisfy the Authority's source-faithful Task Library admission rule. Current
real-model results are diagnostic only and must not be described as official
MetaWorld, locomotion-paper, or industrial benchmark results.

Each retained `runs/<run-id>/` package contains public capability designs,
Harness-private suites, reference calibration reports, condition-local model
and probe evidence, candidate attempts, validation reports, per-case videos,
cell reports, and the final paired `experiment_report.json`. These fields keep
pipeline completion, physical execution, validation verdicts, and video
completeness separate.

See `EVIDENCE.md` for the historical complete run, current local verification,
and the latest real-model launch result.
