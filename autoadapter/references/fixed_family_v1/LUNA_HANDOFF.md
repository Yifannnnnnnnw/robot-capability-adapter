# Bounded separate-conversation handoffs

The approved follow-up has been implemented by the current session and its
sub-agents, not by a separate Luna Max conversation. The earlier request to make
Franka's reference reach 10/10 is superseded: complete reference success is no
longer a synthesis prerequisite.

For a separate Luna Max conversation, select one batch below, read its handoff,
inspect its diff and evidence, and limit any corrective edit to that batch's
listed ownership. Do not redo already completed API samples. Root `AGENTS.md`,
the approved diagnostic amendment in this README, and the canonical-session /
trusted-Harness boundary in `thesis/C_03_framework.tex` apply. This is not a thesis
experiment or a change to formal evidence claims.

| Batch | Concrete behavior and owned implementation | Minimum evidence |
| --- | --- | --- |
| [A5](A5_TIMING_HANDOFF.md), `0bcf783` | `src/autoadapter2/harness/b1_contracts.py`, `tests/test_b1_harness.py`: return deadline starts at completed outbound hold | Timing regression and replay of xArm/UR5e ordinary versus insufficient-excursion boundary trajectories |
| [G5](G5_CONTROL_HANDOFF.md), `589fc6b` | `src/autoadapter2/harness/{session,measurements,runner}.py`, `tests/test_g5_held_control.py`: held native-servo evidence limited to G5 | Real servo/passive/no-step checks; other-capability rule and complete G5 verdict retained |
| [KUKA](KUKA_CONTROL_HANDOFF.md), `0a3a768` | KUKA package `reference/fixed_family_driver.py`, `assets/README.md`, `tests/test_kuka_fixed_control.py`: native position bias compensation | Two actual A1 Harness cases with guards and video; no requirement for 8/8 reference success |
| [Entry](ENVIRONMENT_ENTRY_HANDOFF.md), `0a3a768` | `scripts/run_fixed_family_diagnostic.py`, `tests/test_fixed_diagnostic_entry.py`, one summary field in `src/autoadapter2/driver_synthesis/repair.py` | Basic environment success starts synthesis without full reference; physical failure blocks calls; real worker smoke |
| [Quadrupeds](QUADRUPED_ENVIRONMENT_HANDOFF.md), `66a446f` | A1/Barkour/ANYmal-C package and matching fixed-input directories, `tests/test_fixed_quadruped_environment.py`: consistent contact and settled-reset adaptation | Three ordinary standing workers and six perturbed G5 workers with complete videos; retain failed capability verdicts |

The KUKA and entry changes share a commit because their reviewed files were
staged concurrently. The commit is preserved; do not rewrite it. For any new
batch, commit with explicit owned pathspecs and inspect the staged contents.

Forbidden changes: candidate source, already-run private instances or thresholds,
historical reports, policy weights/training, model budgets, thesis or archived
experiment files. No new governance, approval-state or experiment runner layer.
References must remain actuator-only through canonical MuJoCo objects.

Return changed files (or state no edits), focused check and real-run paths,
remaining limitations and a new commit ID if edits were made. Preserve complete
source revisions, worker feedback, videos and model usage. Acceptance distinguishes
environment usability, optional reference scores and actual candidate success.
