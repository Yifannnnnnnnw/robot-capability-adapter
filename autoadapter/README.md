# AutoAdapter 2.0

`autoadapter/` is the self-contained, experiment-grade Direct-MuJoCo mainline.
It does not import or copy runtime code from `demo2/`, `demo3/`,
`general_demo/`, or `extensions/`.

## Experiment

- The formal mainline cohort is the exact fourteen-configuration set in
  `AUTOADAPTER_2_AUTHORITY.md` Section 1.3: SO-101, Go2, Franka Panda, Kinova
  Gen3 + Robotiq 2F-85, xArm7, UR5e + Robotiq 2F-85, Piper, KUKA iiwa 14,
  LEAP Hand, Barkour vB, Unitree G1, Stretch 2, ALOHA 2, and Spot with arm.
- Construction remains incremental, but a formal manifest may not omit a
  declared robot. Every package needs 20 or more sourced tasks, complete local
  assets and private bindings/guards, a skeleton, package checks, and a
  video-complete reference positive control before the first full-cohort round.
- Real-model TGCD reads each public Task Library and authors 5-10 capability
  groupings, effects, methods, and typed interfaces. Each capability selects
  one source-backed primary validation contract and may add one materially
  distinct robustness contract; selected standards cannot be weakened.
- Trusted IVC receives the same task and source records, then compiles exactly
  one primary and at most one robustness case per capability as the complete
  `capability_validation_suite.json`. Independently, the Framework samples
  five original Task Library tasks and compiles all of their scoring clauses
  into `task_demo_suite.json`; generation cannot read either suite.
- Every selected package-local reference must pass its complete capability
  validation suite before that robot's dynamic Driver Synthesis starts.
- The initial all-robot shakedown crosses all fourteen robots with the preserved
  `skeleton-assisted` and `from-scratch` conditions: 28 cells per replicate,
  all using one manifest-pinned Ministral 8B configuration and empty prior
  Experience.
- Each cell runs interactive AutoAdapter 1.0-style STUDY and GENERATE/GEN_ALGO,
  complete canonical capability validation, up to two report-driven Repair
  attempts, a five-task Task Demo only after admission, and Evolution. Task Demo
  uses one fixed bounded ReAct high-level controller, has a separate verdict,
  and never triggers same-run Repair. ReAct model calls stay in the Framework
  parent while capability calls use the admitted driver in one persistent,
  credential-free MuJoCo worker per trial.
- Evolution runs after every terminal shakedown cell, including failed driver
  cells. After the round, a human review records one Experience disposition per
  cell; accepted Experience can affect only a later matched run.
- STUDY, generation, and Repair use bounded multi-turn tool conversations. The
  model can inspect staged public files, run public-only MuJoCo probes, revise
  its driver, and call one atomic `check_driver(source, checks)` operation. The
  Framework writes that complete revision, performs source audit, canonical
  import/build, and one physics smoke per capability inside that tool execution
  before explicit submission. The normal Generate or Repair path is therefore
  two remote model turns: atomic check, then submit. Once the current revision
  passes that check, the next request exposes only `submit_driver`; at most one
  rejected-submit correction is allowed.
- Each public smoke returns terminal controls, state, and positions for names
  declared in public Morphology. It is explicitly liveness/development feedback,
  not a capability pass or private Harness verdict.
- The serial-arm skeleton exposes capability-neutral closed-loop Cartesian DLS,
  optional wrist-roll pinning, arbitrary gripper targets, and physics hold. Task
  dispatch and manipulation policy remain entirely model-authored.
- The model receives the generated interface stub or previous Repair source in
  its initial context; separate `read_driver` and `write_driver` turns are not
  exposed. A driver stage has three discretionary development probes in addition
  to the mandatory import and one smoke per capability. With at most ten
  capabilities, the hard ceiling is fourteen local probe processes.
- STUDY receives the complete public package projection, selected MJCF closure,
  and eligible skeleton source in its initial context. Its normal path is two
  model turns: one real MuJoCo probe, then explicit study submission. If the
  first probe fails or that submission is rejected, one bounded recovery turn
  is available; the hard maximum is three turns and two probes.
- Before each tool-model request, the deterministic Agent Context Manager keeps
  the initial public task, one current complete driver snapshot, and at most
  three recent interaction groups. Superseded driver source and probe scripts
  become tool/revision/character-count/status metadata; full canonical history
  is not rewritten by a summarization model.
- Generation starts from an interface-only stub mechanically derived from the
  sealed capability design. It contains exact `(self, request)` signatures and
  `NotImplementedError` placeholders, but no controller or task dispatch.

The candidate receives the public robot package, public Task Library, sealed
capability design, fixed `method(request=request)` transport ABI, and condition
specific primitives. It never receives private cases, reference source, or a
Framework-owned MuJoCo session outside the Harness.

A driver becomes a formal attempt only after the model explicitly submits the
current revision. Submission requires source audit and a successful public
physics smoke for every sealed capability through the bundled public check;
development rewrites and rejected pre-submission checks do not consume one of
the three Harness attempts.

## Environment

The mainline pins Python 3.11.9 through `.python-version`, and MuJoCo 3.3.6, NumPy
2.4.6, and pytest 9.1.1 in `pyproject.toml`. `pyenv`, `ffmpeg`, and `ffprobe`
are the required system tools. Using `pyenv exec` makes the selected Python
independent of shell shim initialization.

```bash
cd autoadapter
pyenv exec python -m pip install -e '.[test]'
pyenv exec python -m autoadapter2 check-only
pyenv exec python -m pytest -q
```

The default command reads `configs/experiments/mainline.json`, whose formal
version must select the complete declared cohort. Focused single-robot package
checks use an explicit canary through
`--config configs/experiments/<canary-name>.json`; the existing SO-101 and Go2
canaries are diagnostics, not a definition of mainline scope.

The real-model command reads credentials only from environment variables. From
the repository root, the current DeepSeek configuration can be loaded without
putting a secret inside the mainline:

```bash
set -a
source .env
set +a
export AUTOADAPTER_MODEL_MAX_TOKENS=16384
export AUTOADAPTER_MODEL_TIMEOUT_S=180
export AUTOADAPTER_MODEL_HISTORY_CHARS=80000
export PYTHONPATH=autoadapter/src
export MUJOCO_GL=cgl
pyenv exec python -m autoadapter2 full --run-id <run-id>
```

To exercise only the newly generated dynamic cells without rerunning reference
calibration, add `--skip-reference-calibration`. This is explicitly diagnostic:
the report records the skip and can never claim formal mainline success.

After an API interruption,
`--reuse-sealed-inputs-from autoadapter/runs/<prior-run>` reuses that run's
audited capability designs, complete capability validation suites, and exact
sealed five-task Task Demo suites. The new report records the source run and
still keeps all private suite files outside candidate workspaces.

## Evidence

The current robot models have pinned upstream provenance, but a post-run audit
found that several task fixtures and measurements are local proxies that do not
yet satisfy the Authority's source-faithful Task Library admission rule. Current
real-model results are diagnostic only and must not be described as official
MetaWorld, locomotion-paper, or industrial benchmark results.

Each retained `runs/<run-id>/` package contains public capability designs,
Harness-private suites, reference calibration reports, condition-local model
and probe evidence, candidate attempts, capability-validation reports, Task
Demo reports, per-case videos, cell reports, and the final cohort
`experiment_report.json`. These fields keep pipeline completion, capability
admission, Task Demo, physical execution, and both video outcomes separate.

See `evidence/README.md` for the historical complete run, current local
verification, and the latest real-model launch result.
