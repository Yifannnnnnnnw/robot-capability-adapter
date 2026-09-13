# AutoAdapter 2.0

Research code for robot capability generation and validation in MuJoCo, with
the UCL MSc thesis and our maintained AA1 implementation and benchmark in one repository.

The compiled thesis rooted at [`thesis/Main.tex`](thesis/Main.tex) supplies the
research framing and experiment numbering. Integration diagnostics and retained
results do not define a new formal experiment: its design, manifest and protocol
must be explicitly confirmed. See [`AGENTS.md`](AGENTS.md) for the working
agreement.

## Start here

- **Recent AA1 integration work:** [capability diagnostic guide](AA1/autoadapter_bench/CAPABILITY_DIAGNOSTICS.md)
  and [15-robot diagnostic report, 2026-09-10](AA1/autoadapter_bench/diagnostics/capability_update_20260909/FULL_REPORT_20260910.md).
  These records distinguish generation, Framework validation and task outcomes;
  they are not formal experiment success rates.
- **Implementation:** [AA1 setup and component guide](AA1/README.md).
- **Robot illustrations:** [rendering tools and commands](tools/README.md).
- **Thesis:** [source, build and Overleaf instructions](thesis/README.md).

## Repository map

| Path | Contents and scope |
|---|---|
| [`AA1/`](AA1/README.md) | Our maintained copy of the imported Auto-Adapter code, with its benchmark, earlier paper and current capability integration work. Python distribution: `auto-adapter`. |
| [`extensions/sdk/`](extensions/sdk/README.md) | Separate real-SDK and Translation extension. Python distribution: `autoadapter2-sdk`; import namespace: `autoadapter2_sdk`. |
| [`experiment/`](experiment/README.md) | Historical experiment locations and path-compatibility helpers. No approved new experiment workspace or runner. |
| [`thesis/`](thesis/README.md) | Maintained MSc thesis source. Research framing is taken from the compiled `Main.tex`. |
| [`research_assets/`](research_assets/) | Research illustrations and presentation assets. |
| [`poster/`](poster/) | Poster source and assets. |
| [`tools/`](tools/) | Robot-scene rendering and illustration helpers. |

`AA1/autoadapter_bench/` belongs to the maintained AA1 implementation. The AA1
paper and its historical results are distinct from the MSc thesis.

## Environments and checks

Use the selected component's own environment and working-directory instructions:

- [AA1 setup](AA1/README.md) and [integration diagnostics](AA1/autoadapter_bench/CAPABILITY_DIAGNOSTICS.md).
- [SDK extension setup and route checks](extensions/sdk/README.md).

The packages have different dependencies and Python requirements. There is no
repository-wide installation or experiment command. A package check, reference
control, model diagnostic and formal experiment support different claims; use
the scope recorded with each result.

## Source and retained evidence

Keep code, configuration, compact result summaries and documentation in Git.
Raw physics traces, bulk video, local environments and credentials stay local
under the component ignore rules. Existing tracked evidence remains tracked
until its retention is reviewed separately; adding an ignore rule does not
remove it from Git or its history.

The retired root `autoadapter/` and `AutoAdapter-Bench/` trees remain available
in Git history for their previously tracked content. Their ignored local run
outputs were removed during cleanup and are not recoverable from Git.
Old Authority references and archived experiment descriptions are historical
context, not an active experiment specification. Consult the
[experiment README](experiment/README.md) for recorded archive locations.

Use `thesis/` for thesis edits and Overleaf synchronization. Component source,
robot assets and retained evidence have existing path dependencies; directory
renaming or resource deduplication requires a separate code change.

**Never push to AA1's original/upstream repository.** Local changes under
`AA1/` are developed and managed as our own code in this parent repository.
The prohibition also applies to
direct URLs, alternate remotes and retained AA1 Git metadata; see
[`AGENTS.md`](AGENTS.md).
