# AA2 task-library review for the two-stage AA1 evaluation

This is a read-only source review and design discussion, not an approved formal
experiment protocol. No AA2 source, task asset, or thesis content was changed.

The user clarified the intended Exp1 boundary on 2026-09-10:

- Driver generation ends at complete Framework validation, with one initial
  generation and at most three repairs. No demo runs in this stage.
- ReCAP subsequently uses only complete-Framework-passing drivers on a
  prespecified task set. Driver code remains fixed; replanning may change
  capability calls, but task failures do not trigger driver repair.
- Current historical-candidate resumes remain diagnostic. They mix earlier
  model configurations and integration revisions and do not define a formal
  denominator. Sample size, task selection and driver selection remain open.

The canonical AA2 package index is
`autoadapter/libraries/robots/index.json`. Task packages contain public
`tasks/catalog.json`, source records in `tasks/sources.json`, and private
instances, bindings and guards. The indexed version matters: SO-101 resolves
to 1.0.4, while Go2 resolves to 1.0.0, despite newer version directories.

| AA1 robot group | Indexed AA2 task entries | Relevant source-review finding |
|---|---:|---|
| Seven serial arms | 20 per robot | Manipulation families include pushing, grasp-and-place, fixtures and obstacles. KUKA has a separate selection compatible with its missing gripper. |
| Go2 | 20 | Velocity tracking, steps and terrain/obstacle courses. Flat-ground G1-G5 reference success does not establish obstacle traversal. |
| LEAP | 20 | Fingertip reach, joint pose, object holding, fixed-axis manipulation and in-hand manipulation. Current AA1 fingertip reach is a much narrower demonstrated scope. |
| Stretch 2 | 20 | Reach, manipulation and fixtures. For example, pick-and-place places object and goal in the reachable workspace; its listed criterion alone does not require base movement. |
| ALOHA 2 | 20 | The catalogue explicitly selects one arm and keeps the other neutral. These are single-arm tasks on bimanual hardware, not coordinated bimanual tasks. |
| Unitree A1, ANYmal-C | 0 each | Their indexed `tasks/catalog.json` files contain empty task arrays. |
| H1, Skydio X2 | Not indexed | The inspected task library does not provide entries for these configurations. Unitree G1 is a different robot and cannot substitute silently. |

Thus 11 of the current 15 AA1 robots have 20 indexed entries each. Entries
shared across robot packages are not distinct source task families. Existing
catalogues and private scene specifications do not by themselves establish
that a task has passed physical execution in AA1.

For Exp1b, select task families and map their required capability calls,
observations, scenes and physical success measurements before reviewing model
outcomes. A task that repeats a single capability-validation request adds
little evidence about downstream composition. ALOHA needs an explicit
two-arm obligation if the claim concerns bimanual coordination; Stretch needs
a base-motion obligation if the claim concerns mobile manipulation.

The current AA1 fixed A1-A5/G1-G5 specification evaluates implementation and
use of prescribed capabilities. It does not evaluate autonomous capability
design or automatic test generation, both of which are also described in the
current Chapter 3. Thesis claims and a future experiment scope must be aligned
before formal evidence is collected. Chapter 4 separately addresses model
comparisons; Chapter 5 addresses configuration-specific cross-robot analysis.

A same-task reference-driver run under the same ReCAP configuration is a
useful proposed control for distinguishing generated-driver limitations from
planner/task limitations. It remains a proposal, and reference outcomes must
stay separate from generated-driver outcomes. No additional task integration
or reference-task run was started as part of this review.
