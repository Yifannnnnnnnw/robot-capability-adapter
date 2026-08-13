# AutoAdapter 1.0 reference provenance

Source repository: `/private/tmp/autoadapter1-audit`  
Source commit: `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`

The files below are copied from that commit without content reconstruction.

| Destination | Original path |
|---|---|
| `so101/driver.py` | `artifacts/auto_adapter_so101_v2_artifacts/driver.py` |
| `so101/study.json` | `artifacts/auto_adapter_so101_v2_artifacts/study.json` |
| `so101/validate_report.json` | `artifacts/auto_adapter_so101_v2_artifacts/validate_report.json` |
| `franka/driver.py` | `artifacts/auto_adapter_from_scratch_franka_artifacts/driver.py` (relative symlink to `driver_from_scratch.py`) |
| `franka/driver_from_scratch.py` | `artifacts/auto_adapter_from_scratch_franka_artifacts/driver_from_scratch.py` |
| `franka/study.json` | `artifacts/auto_adapter_from_scratch_franka_artifacts/study.json` |
| `franka/validate_report.json` | `artifacts/auto_adapter_from_scratch_franka_artifacts/validate_report.json` |
| `go2/driver.py` | `artifacts/from_scratch_quad_v2/go2/driver.py` |
| `go2/driver_from_scratch.py` | `artifacts/from_scratch_quad_v2/go2/driver_from_scratch.py` |
| `go2/study.json` | `artifacts/from_scratch_quad_v2/go2/study.json` |
| `go2/validate_report.json` | `artifacts/from_scratch_quad_v2/go2/validate_report.json` |

The reference workspaces intentionally omit their `mjcf.xml` links and mesh assets because the
corresponding model closures are already vendored under `demo2/legacy_assets/`. SO-101's driver
resolves `auto_adapter.skeletons` from the separately vendored `demo2/legacy_core/` at runtime.
