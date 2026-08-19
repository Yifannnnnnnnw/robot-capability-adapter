# Demo3 Evidence Status

## Current Qualification

Demo3's real-model, Direct-MuJoCo orchestration is operational, but the current
robot Task Libraries and private scenes have not passed the Authority's required
human source-and-applicability review. The latest run is therefore retained as a
diagnostic engineering baseline, not as formal benchmark evidence.

The implementation includes:

- real-model TGCD over the complete public Task Library;
- AutoAdapter 1.0-style multi-turn STUDY, GENERATE/GEN_ALGO, and Repair;
- deterministic tool-event context projection retaining the initial task, one
  current driver snapshot, and at most three recent interaction groups;
- interface-only stubs generated from sealed capability names and exact
  `(self, request)` signatures;
- bounded public Python/MuJoCo development probes;
- source audit, import checks, and mandatory public physics smoke for every
  capability on the submitted revision;
- explicit submission before a candidate consumes a Harness attempt;
- Framework-owned canonical MuJoCo sessions, actuator/physics-step guards, and
  direct-state-write rejection; and
- complete IVC-authored Capability Validation followed, only after admission,
  by a separately reported random five-task Task Demo; and
- independent per-case video and separate pipeline, Capability Validation,
  Task Demo, and video verdicts.

## Latest Historical Diagnostic Run

This run predates Authority `0.19.2`: its five sampled cases were used directly
for driver validation, so it is not evidence that the restored
Capability Validation -> Task Demo flow has executed.

- Run: `deepseek-full-convergence-20260818T144502Z`
- Model/provider: `deepseek-v4-pro` through the configured DeepSeek API
- Matrix: `robotstudio_so101` and `unitree-go2-stock-12dof`, each under
  `skeleton-assisted` and `from-scratch`
- Package checks: passed
- Reference gate: 5/5 sampled cases for both robots
- Real model called: yes
- Driver generated in-run: yes in all four cells
- Physical validation executed: yes in all four cells
- Pipeline completed: yes
- Overall strict verdict: fail, because one of four cells did not pass its final
  five-case suite
- Primary report:
  [experiment_report.json](runs/deepseek-full-convergence-20260818T144502Z/experiment_report.json)

| Cell | Attempt trajectory | Final physical verdict |
|---|---|---|
| SO-101 / skeleton-assisted | `1/5 -> 3/5 -> 3/5` | fail |
| SO-101 / from-scratch | `3/5 -> 3/5 -> 5/5` | pass |
| Go2 / skeleton-assisted | `5/5` | pass on first submission |
| Go2 / from-scratch | `5/5` | pass on first submission |

These results demonstrate that the interactive generation and report-driven
Repair mechanism can complete and can improve a real generated driver. They do
not establish performance on the cited benchmarks because the current private
MuJoCo instances are local proxies.

## Video Evidence

The original SO-101 skeleton attempts were recorded at `160x120`, which is too
small for useful visual inspection. Commit `880faac` raises both robot packages,
the Harness fallback, and package render helpers to `640x480` and adds focused
regression checks. A trusted replay of the frozen SO-101 skeleton final driver
produced five complete `640x480` videos without changing its `3/5` physical
result:

`runs/deepseek-full-convergence-20260818T144502Z/cells/robotstudio_so101/skeleton-assisted/high-resolution-replay/`

SO-101 from-scratch attempt 2 and both later Go2 cells were recorded directly at
`640x480`. Visual inspection also found that the SO-101 default camera is too
distant. Camera framing must be corrected as part of the scene rebuild; raising
resolution alone is not sufficient evidence quality.

## Source And Scene Audit

The robot morphology assets have traceable pinned upstreams:

- SO-101: TheRobotStudio SO-ARM100/SO-101 MJCF asset lineage recorded in its
  `morphology.json`;
- Go2: Google DeepMind MuJoCo Menagerie commit
  `71f066ad0be9cd271f7ed58c030243ef157af9f4`.

The validation environments do not currently have equivalent fidelity:

- the 20 SO-101 tasks map to ten locally authored primitive fixture scenes,
  rather than the pinned MetaWorld task environments;
- pick-place/bin-picking, push/sweep, faucet/dial, three slide-fixture tasks,
  and three press-fixture tasks contain pairs or groups with the same scene,
  reset, and task parameters;
- faucet and dial consequently produced identical physical measurements;
- the local peg-insertion object is a cube, and its ordinary body-centre
  Euclidean metric does not reproduce MetaWorld's peg-head, axis-weighted
  distance;
- MetaWorld scoring values are generally traceable, but catalog line anchors
  refer to an older source layout and do not match the pinned v3.1.1 files; and
- Go2 uses the pinned Menagerie robot model, but its terrains and several
  progress, body-height, and joint-range thresholds are local experimental
  proxies rather than reproductions of the cited papers' full protocols.

Under `AUTOADAPTER_2_AUTHORITY.md` section 2.4, unresolved source lineage or an
adaptation that changes task meaning must fail closed. Accordingly, the current
`5/5` cell verdicts mean only "passed the current local proxy suite." They must
not be reported as MetaWorld, locomotion-paper, industrial-standard, or formal
AutoAdapter 2.0 benchmark success.

## Required Before Formal Evidence

1. Verify every task clause against a pinned primary source and correct its exact
   section, table, protocol, or source-line anchor.
2. Port or faithfully adapt task-distinct fixtures and initialization semantics;
   document every robot-specific transform without weakening the source task.
3. Bind source-equivalent metrics, temporal rules, and aggregation, including
   geometry-specific measurements such as peg-head alignment.
4. Add close evidence cameras and verify readable per-case videos.
5. Obtain the required human task-admission review, then freeze the two Task
   Library snapshots.
6. Recalibrate the references and rerun all four real-model cells from the
   beginning.

## Local Verification

- Full suite after the orchestration fixes: `129 passed, 32 subtests passed`.
- Video-resolution focused checks: `3 passed`.
- Current video-resolution commit: `880faac`.
- The unrelated dirty Demo2 worktree was not staged or modified by these Demo3
  changes.

## Historical Runs

`deepseek-v4-pro-20260818T020155Z` remains a pre-interactive-loop failure
baseline. `deepseek-react-20260818T101705Z` stopped at TGCD with HTTP 402. Neither
run is synthesis-success evidence.
