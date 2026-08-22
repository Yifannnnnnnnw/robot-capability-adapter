# B2 sealed task suite

`task_suite.json` is the trusted-private snapshot for the exact ten tasks in
`AA2-B2` Section 3.2. `build_suite.py` mechanically selects each task from its
canonical `1.0.0` package catalog and joins the matching private instance,
source records, measurement bindings, guards, episode budget, rendering
configuration, and the exact `R1`–`R3` scene/reset mapping.

Only `public_projection` is eligible to enter ReCAP context. Scoring clauses,
source records, scene paths, resets, bindings, guards, budgets, rendering, and
replicate mappings remain Framework/Harness-private.

The selected package instances expose no task-reset seed mechanism. The suite
therefore records `reset_seed: null` and `reset_seed_applied: false` for each
distinct replicate ID and preserves the exact canonical reset instead of
inventing unsupported physics randomization.

The canonical `GO2-T02` and `GO2-T03` instances declare ten source-protocol
trials but provide neither repetition variants nor reset seeds. B2's persistent
single-reset episode evaluates one `per_trial` clause at each of `R1`–`R3`.
This snapshot preserves the ten-trial lineage and explicitly records that B2
does not reproduce that original ten-trial aggregate.

Build and check the snapshot with:

```bash
python experiment/b2_recap/task_suite/build_suite.py
python experiment/b2_recap/task_suite/build_suite.py --check
python -m unittest experiment/b2_recap/task_suite/test_task_suite.py
```
