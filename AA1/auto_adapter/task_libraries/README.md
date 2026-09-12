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

TGCD starts with file references in its user message. It reads
`capability_inputs/authoring_brief.json` through the existing `read_file` tool;
the file contains the study, task requirements, sources and task-library
identity. `public_inputs.json` retains the fuller public records. The TGCD reader
only exposes these two inputs, and a design cannot be submitted before the
brief has been read in the current loop.

Dynamic designs currently stop after study or generation. They have no associated
private executable suite and must not be judged by an old catalog suite. The
from-scratch route exposes the same preparation through `phase_study()` followed
by `phase_gen_algo()`; its full `run()` is unavailable with dynamic inputs.
