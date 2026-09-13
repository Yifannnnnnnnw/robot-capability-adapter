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
)
with SelfAssemble(config) as runner:
    result = runner.run(stop_after="generate")
```

STUDY reads the caller's actual model first. Task-grounded capability design
then consumes that study and the public tasks, and driver generation implements
the resulting interface. Set `capability_design_path` to supply an existing design, and
`scene_cases_path` for its executable cases. Without a supplied design, DESIGN
runs automatically. There is no fixed-capability catalog fallback.

The generated `design/capability_design.json` contains the interface
and criteria. `criteria.json` is a derived view. Generation traces and resource
records stay in the run directory. Task-support links describe the proposed
design; they do not demonstrate task completion. Proposed numerical requirements
have not been physically calibrated by this stage.

TGCD starts with paths to the actual `study.json`, the bound robot package's
`catalog.json`, and optional `design/skeleton_context.json`. It reads
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
cannot overwrite its input files. TGCD prepares the current design and scene cases; the shared scene runtime
builds the scenes from the actual input model.

Both standard and from-scratch `run()` use STUDY → DESIGN → GENERATE →
VALIDATE → EXPORT, with an optional configured ReCAP DEMO. `stop_after` may stop
at any of these stages. VALIDATE executes the current design's cases in fresh
MuJoCo workers and scores their declared measurements; it never loads retired
A1–A5/G1–G5 suites. A design without executable cases may stop at GENERATE, but
cannot pass VALIDATE. Missing task-library bindings or unsupported measurements
remain explicit failures.
