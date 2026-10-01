# Public source layout

The public tree is assembled from the maintained local `AA1/` implementation
at research revision `02184d4e`. The previous public tree is retained in Git at
`975715f1`. The reorganization adds ordinary commits and does not rewrite that
history.

| Previous location | Public location or disposition |
| --- | --- |
| `AA1/auto_adapter/` | `auto_adapter/` |
| `AA1/autoadapter_bench/` | `autoadapter_bench/`, with source and task inputs retained |
| `AA1/assets/` | `assets/` |
| `AA1/scripts/run/run_stage1.py` | `scripts/run/run_stage1.py` |
| `AA1/artifacts/`, Bench results and trained checkpoints | Kept in historical revisions and/or the original local research workspace |
| `AA1/paper/`, `expriment/`, `extensions/`, `outputs/` | Outside the current public software tree |
| Thesis, website and hardware demonstration files | Remain in the original local research workspace and their existing destinations |

The ignored local `publication/auto-adapter/` draft remains untouched. Its public
provider defaults, modification notices and controller metadata corrections
were reviewed while preparing this version; it is not a second canonical source.

## Experiment boundary

This cleanup does not create an experiment, approve a protocol, rerun a cohort
or change a research claim. It publishes existing software with corrected public
configuration and documentation.

The current ReCAP/MCP path belongs to Auto Adapter. The existing AA-Bench task
executor uses the earlier TaskPlanner and its driver contract. Chapter-specific
synthesis runs and the thesis's fixed-driver comparison are not presented as
a unified reproduction command. Their historical settings and outcomes must
not be pooled into results produced by this release.

Stored historical results are not rewritten by evaluator fixes in this version.
For a source-only download without the old data in Git history, use GitHub's
archive download for the desired commit. A normal clone retains repository
history.
