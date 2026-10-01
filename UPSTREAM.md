# Upstream relationship

This repository maintains the AutoAdapter 2.0 implementation and AA-Bench under
[robot-capability-adapter](https://github.com/Yifannnnnnnnw/robot-capability-adapter).
It derives from the open-source Auto-Adapter project:

- Repository: <https://github.com/981526092/auto-adapter>.
- Comparison baseline: `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`.
- Original distribution author attribution: Vector Robotics.
- Upstream license: Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).

The baseline identifies the imported project used for comparison. Local
AutoAdapter 2.0 changes are maintained in this repository; they must never be
pushed to the original imported repository. The original author attribution and
third-party notices are retained separately from the local modifications.

## Maintained mainline

The current mainline adds task-grounded capability design, physical validation
in fresh MuJoCo processes, a design-derived driver interface and MCP export,
a persistent export runtime, and an optional ReCAP Demo through MCP. Robot task
libraries and fixed Demo configurations provide inputs to these stages.

[`auto_adapter/`](auto_adapter/) contains the synthesis and execution code.
[`autoadapter_bench/`](autoadapter_bench/) contains the retained benchmark source
and shared robot catalog. The [README](README.md) describes the release entry
points; the [asset guide](docs/ASSETS.md) records input paths and licenses.
Historical benchmark results are not new evidence for the maintained mainline.

## Vendored components

The ReCAP task-tree controller derives from
[ReCAP-Stanford/ReCAP](https://github.com/ReCAP-Stanford/ReCAP) at revision
`2fb112ffad685c7c6f7de86d5487ecca6f566fcc`. Its MIT license, exact source record
and local integration patch are retained under
[`auto_adapter/agent/vendor/recap/`](auto_adapter/agent/vendor/recap/).

Robot models retain their own licenses and source records. See
[the asset license table](assets/THIRD_PARTY_LICENSES.md). The retained Go2
velocity policy also carries its original source and license in
[`auto_adapter/skeletons/data/`](auto_adapter/skeletons/data/).
