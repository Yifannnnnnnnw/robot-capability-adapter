# Demo3 Reviewed Evidence

## Current implementation status

Demo3 now implements a self-contained AutoAdapter 1.0-style multi-turn ReAct
loop for STUDY, GENERATE/GEN_ALGO, and Repair. The current path includes:

- an interface-only `driver.py` stub generated from sealed capability names and
  exact `(self, request)` signatures, with no control implementation;
- staged public-only file inspection and bounded Python/MuJoCo probes;
- iterative model-authored driver writes with source-audit and import feedback;
- mandatory public physics smoke for every sealed capability on the submitted
  revision; and
- formal attempt counting only after explicit model submission, so rejected
  development revisions do not consume a Harness attempt.

This implementation passed `119` tests and `31` subtests. The package check
passed under Python 3.11.9, MuJoCo 3.9.0, and NumPy 2.4.6, including a real
MuJoCo physics smoke, both runnable robot packages, and a self-containment scan
of 60 Python files with no symlinks.

## Latest real-model launch

- Run: `deepseek-react-20260818T101705Z`
- Model/provider: `deepseek-v4-pro` through the configured DeepSeek API
- Package check: passed for both robots
- Result: the first real TGCD request returned HTTP 402
- Dynamic model called: yes
- Driver generated: no
- Physical validation executed: no

The run failed closed at TGCD in about two seconds. It did not start reference
calibration or any dynamic cell, and therefore provides no new synthesis
verdict. Its local evidence is retained at
`runs/deepseek-react-20260818T101705Z/experiment_report.json`. A fresh four-cell
result for the current code requires restored DeepSeek API credit.

## Historical reviewed run

- Run: `deepseek-v4-pro-20260818T020155Z`
- Model/provider: `deepseek-v4-pro` through the DeepSeek API
- Matrix: `robotstudio_so101` and `unitree-go2-stock-12dof`, each under
  `skeleton-assisted` and `from-scratch`
- Result: the paired experiment completed, but no dynamic cell passed its final
  private suite. This run is not evidence of successful synthesis.
- Primary report: [experiment_report.json](runs/deepseek-v4-pro-20260818T020155Z/experiment_report.json)

The run used real TGCD, STUDY, GENERATE, Repair, and Evolution model calls. It
used package-local MuJoCo assets, Framework-owned canonical sessions, isolated
candidate workers, and independently recorded video for every Harness case
that reached validation.

This run predates the current interactive ReAct implementation and used the
earlier one-shot generation path. It remains useful as a failure baseline, but
it is not evidence for the current pre-submission development loop.

## Task and source basis

| Robot | Public tasks | Source basis | Qualification |
|---|---:|---|---|
| SO-101 | 20 | Meta-World, pinned at commit `7ea2b501c4a698c8533cdc55a396fe2734e2649d` | Task operations and success definitions are adapted to the package-local SO-101 MJCF and trusted measurable bindings. |
| Go2 | 22 | Unitree Go2 specifications; Hoeller et al. 2020; Miki et al. 2022; Shi et al. 2023; pinned MuJoCo Menagerie Go2 model | Each catalog clause labels its source support and any local adaptation. Local short-horizon thresholds are experiment calibrations, not claims of reproducing the papers' full benchmark protocols or industrial certification. |

TGCD generated five capabilities for each robot and covered every public task.
Trusted IVC compiled 20 private SO-101 clauses and 31 private Go2 clauses.

## Reference calibration

| Robot | Tasks | Source clauses | Physical execution | Videos | Verdict |
|---|---:|---:|---|---|---|
| SO-101 | 20/20 | 20/20 | yes | 20/20 complete | pass |
| Go2 | 22/22 | 31/31 | yes | 31/31 complete | pass |

Both references passed before the dynamic matrix started. Reference source was
kept outside every candidate-facing model context and workspace.

## Dynamic cells

| Cell | Attempts | Physical result | Video result | Final verdict |
|---|---:|---|---|---|
| SO-101 / skeleton-assisted | 1 | 17/20 tasks and clauses passed | 20/20 complete | fail: three physical criteria failed; the next Repair probe was rejected for importing `sys` |
| SO-101 / from-scratch | 0 | Harness not reached | none required | fail: STUDY returned a non-canonical condition spelling and the then-current strict parser stopped the cell |
| Go2 / skeleton-assisted | 3 | Harness not reached | none required | fail: all candidates placed capabilities outside the required `build()` object ABI |
| Go2 / from-scratch | 1 | 0/22 tasks and 0/31 clauses passed; candidate failed before a physics step | historical report says 31/31 complete, but each file has one frame and zero simulated duration; current Harness classifies these as incomplete | fail: candidate treated the request mapping as an object; Repair then received HTTP 402 from DeepSeek |

The SO-101 skeleton cell is the only generated driver in this run that both
passed source audit and produced successful physical task outcomes. It still
failed the suite and therefore is not an admitted driver.

## Post-run corrections

The run exposed several orchestration and evidence defects. The current code now:

- canonicalizes the Framework-selected condition while retaining the model's
  original spelling as evidence;
- states and audits the exact instance-method ABI, `method(self, request)`, with
  `request` as a plain mapping;
- records and rejects an unsafe optional Repair probe but still proceeds to the
  Repair generation call with the complete candidate-facing report;
- sends Evolution a bounded diagnostic projection instead of multi-megabyte
  repeated trajectory samples; and
- marks a decodable recording incomplete when candidate execution ends before
  a clean actuator-controlled physics step, closing the one-frame false-positive
  exposed by the Go2 from-scratch cell;
- runs STUDY, GENERATE/GEN_ALGO, and Repair as bounded multi-turn tool
  conversations rather than one-shot JSON generation;
- supplies the same control-free interface stub to both generation conditions;
- returns tool failures to the same model conversation and requires successful
  capability-by-capability public physics smoke before submission; and
- separates development rejections from submitted Harness attempts.

The current code also records a failed STUDY invocation as a real model call.
These corrections are covered by local and real-MuJoCo integration tests, but
are not presented as a replacement four-cell real-model run. The fresh launch
described above stopped at the first TGCD request because the DeepSeek API still
returned HTTP 402.

## Final local verification

- `python -m pytest demo3/tests -q`: 119 tests and 31 subtests passed.
- `python -m autoadapter2 check-only`: package check and MuJoCo physics smoke
  passed under Python 3.11.9, MuJoCo 3.9.0, and NumPy 2.4.6.
- Self-containment scan: 60 Python files checked, no symlinks, and no runtime
  dependency on Demo2 or `general_demo`.
- The reviewed run retains 102 per-case MP4 files across reference and dynamic
  validation attempts.

## Interpretation

Demo3 now demonstrates the complete experiment architecture and produces an
honest, inspectable failed experiment. The references, task grounding, private
Harness, actuator/physics guards, isolation, videos, bounded attempts, Repair,
and Evolution paths all have concrete evidence. The reviewed run does not yet
satisfy the Authority's synthesis-success condition because all four dynamic
cells ended with `final_validation_passed=false`.
