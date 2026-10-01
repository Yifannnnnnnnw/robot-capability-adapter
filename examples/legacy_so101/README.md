# SO-101 example for AutoAdapter-Bench

This historical compatibility example gives the existing AutoAdapter-Bench
TaskPlanner a concrete driver for the SO-101 reach task. It uses the earlier
fixed reference driver, not the current Auto Adapter Design-to-ReCAP interface.
It is not a new driver-generation result or evidence for the current thesis
experiments.

## Source and scope

`driver.py` is an unchanged copy of
[`artifacts/auto_adapter_so101_v2_artifacts/driver.py`](https://github.com/981526092/auto-adapter/blob/585eb1f1fde33f17f5f9a1e169a18dd41f97b586/artifacts/auto_adapter_so101_v2_artifacts/driver.py)
from Auto-Adapter revision `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`, under
the repository's [Apache-2.0 license](../../LICENSE). The upstream handoff
describes this as its fixed SO-101 reference driver. No independent generation
trace accompanies this example, so its model-generation provenance is not
asserted here.

The driver fills an `ArmSpec` and uses the maintained `ArmSerialDLSSkeleton`.
Its scene is `assets/mjcf/so101_mujoco.xml`, with meshes already included under
`assets/urdf/meshes/`; see the [asset notices](../../assets/THIRD_PARTY_LICENSES.md).
Its gripper uses simulator weld constraints. The example below exercises reach
and return, and makes no contact-grasping claim.

Only this reach path has been checked offline with the current loader, real
MuJoCo steps and the existing task criterion. No model-powered benchmark trial
or video-evidence check was run for this addition. Historical validation reports
and their success claims are not bundled.

## Prepare a writable workspace

From the repository root, with the package installed and its Python environment
active, copy the driver into a fresh temporary workspace. The resolved scene
link preserves MuJoCo's mesh paths:

```bash
BENCH_WORKSPACE="$(mktemp -d "${TMPDIR:-/tmp}/autoadapter-so101.XXXXXX")"
cp examples/legacy_so101/driver.py "$BENCH_WORKSPACE/driver.py"
python - "$BENCH_WORKSPACE" <<'PY'
from pathlib import Path
import sys

workspace = Path(sys.argv[1]).resolve()
scene = Path("assets/mjcf/so101_mujoco.xml").resolve(strict=True)
(workspace / "mjcf.xml").symlink_to(scene)
print(workspace)
PY
```

## Run the existing reach evaluator

The following command makes Bedrock model requests. It requires AWS credentials,
access to the selected model in `us-east-1`, and a working MuJoCo video renderer.
Replace the placeholder with a model identifier enabled for your account.

```bash
AUTO_ADAPTER_MODEL='your-enabled-bedrock-model-id'
python -m autoadapter_bench.eval \
  --robot so101 --suites simple --tasks move_x_plus_5cm \
  --driver-workspace "$BENCH_WORKSPACE" \
  --provider bedrock --model "$AUTO_ADAPTER_MODEL" \
  --region us-east-1 --n-trials 1 \
  --output "$BENCH_WORKSPACE/reach.json"
```

Results, task traces and recordings are written inside that workspace. The
evaluator checks the live execution and requires trace and video evidence for
`physics_ok`. This example has no current `validate_report.json`, so
`framework_ok` and `validated_driver_task_ok` remain false even if the reach
task passes. See the [AutoAdapter-Bench guide](../../autoadapter_bench/README.md)
for the evaluator's scope and optional baselines.
