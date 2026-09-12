# Public task inputs for Auto-Adapter

These are public task descriptions and source records used to design a robot's
capability interface. `index.yaml` maps AA1 robot IDs to source package versions;
paths in the index are relative to this directory. Source JSON files retain their
original robot IDs, versions, task IDs, scoring clauses and source references.

The library contains 15 packages: 13 nonempty packages with 260 tasks, and empty
Unitree A1 and ANYmal-C packages. Empty or unbound inputs cannot start automatic
design. G1 and Barkour have public packages but no AA1 configuration binding.
The SO-101 package binds to `menagerie_so101`, not the separate `so101` model.

From the AA1 directory, use the ordinary local orchestrator:

```python
from pathlib import Path
from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig

config = SelfAssembleConfig(
    robot_id="piper",
    mjcf_path=Path("assets/mjcf/piper/scene.xml"),
    workspace_root=Path("/tmp/my-aa1-design-run"),
    mode="local",
    prepare_capabilities=True,
)
with SelfAssemble(config) as runner:
    result = runner.run(stop_after="generate")
```

The original study reads the actual model first. Task-grounded capability design
then consumes that study and the public tasks, and driver generation implements
the resulting interface. Set `capability_design_path` instead of
`prepare_capabilities` to supply an existing design. With neither option, the
original catalog path remains in use.

The generated `capability_inputs/capability_design.json` contains the interface
and criteria. `criteria.json` is a derived view. Generation traces and resource
records stay in the run directory. Task-support links describe the proposed
design; they do not demonstrate task completion. Proposed numerical requirements
have not been physically calibrated by this stage.

TGCD starts with paths to the actual `study.json`, the bound robot package's
`catalog.json`, and optional `capability_inputs/skeleton_context.json`. It reads
these files through the existing `read_file` tool. Task-library identity and
task-local scoring references come from the catalog; a separate `sources.json`
is not a model input. Legacy task invocation schemas are not instructions for
the new capability interface. No combined authoring brief or public-input copy
is generated.

The model uses the existing `write_file` tool to write
`draft/capability_design.json`, optionally in chunks. Python validates the
current draft, returns errors for correction, and saves the main design and
derived criteria. Both study and catalog must be read before the first write;
an old artifact cannot establish success after a failed model call. The model
cannot overwrite its input files. TGCD currently exposes only `read_file` and
`write_file`; it does not generate or execute MuJoCo test cases.

Dynamic designs currently stop after study or generation. They have no associated
private executable suite and must not be judged by an old catalog suite. The
from-scratch route exposes the same preparation through `phase_study()` followed
by `phase_gen_algo()`; its full `run()` is unavailable with dynamic inputs.
