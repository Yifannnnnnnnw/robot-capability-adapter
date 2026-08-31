# Pre-thesis-realignment experiment archive

This snapshot contains the complete experiment workspaces that were active
immediately before the 2026-08-31 thesis-numbering realignment. They are
historical inputs and evidence, not active experiments and not eligible for any
future experiment denominator.

The workspaces were moved on the same filesystem. Their files, ignored run
trees, frozen source snapshots, videos, JSON identifiers, and embedded paths
were not rewritten.

| Archived directory | Workspace immediately before archival | Historical role |
| --- | --- | --- |
| `experiment1a_generation/` | `experiment/experiment2a_driver_synthesis/` | B1 driver synthesis |
| `experiment1b_use/` | `experiment/experiment2b_driver_use/` | B2 driver use |
| `experiment2_so101_cross_run_closure_primitive_v1/` | `experiment/experiment1_framework/` | SO-101 primitive-v1 source/later closure |
| `experiment3_fixed_input_cross_robot/` | `experiment/experiment3_cross_robot/` | fixed-input eleven-configuration cohort |

The snapshot also retains the old Chapter 4--5 runbook and the shared tests
that imported the archived runners. `LEGACY_WORKSPACE_README.md` is the former
active experiment-workspace index.

Before the move, the four workspaces contained 15,869 regular files and 21
symbolic links. Their reported sizes were 292 KiB, 8,936.7 MiB, 1,434.7 MiB,
and 1.7 MiB respectively. The large run trees remain ignored by Git; archival
here is a local filesystem preservation operation, not a request to add those
artifacts to the repository history.

Some frozen source snapshots contain absolute symbolic links to former active
run paths. Their link text is retained as historical evidence and may no longer
resolve after archival. Do not repair those links by editing the snapshot.

## Retained working-agreement boundary

The uncommitted working agreement immediately before archival described the
old fixed-input cohort as using "one fixed audited capability-v2 Design and
private validation suite per robot reused unchanged across replicates" and
making "no TGCD, IVC, or Evolution model call". This wording is retained here
so that the user's pre-archive change is not lost. It does not define a future
experiment.
