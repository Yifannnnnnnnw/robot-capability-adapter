# Upstream relationship

This directory is our maintained research implementation. It derives from the
open-source Auto-Adapter project:

- Repository: <https://github.com/981526092/auto-adapter>
- Comparison baseline: `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`
- Upstream licence: Apache License 2.0

The comparison revision identifies the public upstream state against which the
AutoAdapter 2.0 mainline is described. This repository is maintained and
published under our own repository ownership. Changes here must never be
pushed to the original upstream repository.

## Main changes

The maintained mainline adds:

1. a task-grounded Design stage that produces capability request schemas,
   physical criteria and prepared MuJoCo scene cases;
2. validation in fresh simulation processes using measured trajectories and
   declared physical criteria instead of trusting a driver's return value;
3. a dynamic driver interface and MCP export derived from the validated
   design;
4. a persistent export runtime that shares the selected MuJoCo model and state;
5. an optional downstream Demo using the official ReCAP task-tree controller
   through the MCP client interface;
6. task libraries and fixed Demo configurations for the supported robot
   models.

The smallest publication boundary and the files that implement these changes
are listed in [`docs/PUBLIC_RELEASE.md`](docs/PUBLIC_RELEASE.md).

## Vendored ReCAP component

The ReCAP controller is derived from
<https://github.com/ReCAP-Stanford/ReCAP> at revision
`2fb112ffad685c7c6f7de86d5487ecca6f566fcc`. Its MIT licence, exact source
location and local integration patch are retained under
`auto_adapter/agent/vendor/recap/`.

## Release preparation

Before creating a public release, modified files retained from the Apache-2.0
upstream should carry a concise modification notice where required, and the
release should retain `LICENSE`, `NOTICE`, this file, and all applicable robot
asset and vendored-component licences.
