# AutoAdapter 1.0 runtime provenance

This directory vendors the smallest Python text closure needed by the Demo2
AutoAdapter 1.0 STUDY/GENERATE ReAct path and its framework validation path.

## Fixed source

- Repository: `/private/tmp/autoadapter1-audit`
- Commit: `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`

## Copied files

The following files were copied from the corresponding upstream paths without
adapter-logic changes:

- `auto_adapter/orchestrator.py`
- `auto_adapter/agent/__init__.py`
- `auto_adapter/agent/converse_client.py`
- `auto_adapter/agent/react_loop.py`
- `auto_adapter/agent/task_planner.py`
- `auto_adapter/agent/tools.py`

The relative `auto_adapter/` layout is retained. The top-level
`auto_adapter/__init__.py` is intentionally not copied: `demo2/legacy_core`
provides `auto_adapter/skeletons` as the existing namespace portion, and a
regular top-level package initializer here would hide that portion. The
vendored runtime is therefore imported through `auto_adapter.orchestrator` or
`auto_adapter.agent.*` with `demo2/legacy_runtime` and `demo2/legacy_core` on
`PYTHONPATH`.

## Intentionally excluded

- `auto_adapter/skeletons/**`: already present under `demo2/legacy_core/`.
- MJCF and other robot assets: already present under `demo2/legacy_assets/`.
- `auto_adapter/agent/vision_tool.py`: not imported by the STUDY/GENERATE or
  framework-validation closure.
- `auto_adapter/orchestrator_from_scratch.py`: separate 1.0 path, not used by
  the requested orchestrator.
- Tests, benchmarks, documentation, and project packaging metadata.

## Runtime requirements still external

- `boto3` and its `botocore` dependency for Bedrock Converse and the Bedrock
  AgentCore Code Interpreter session; AWS credentials, network access, and
  suitable IAM/model permissions are also required.
- `anthropic` when the selected model uses `AnthropicBedrock`.
- `numpy` for the task planner and the existing skeleton runtime.
- `mujoco` for generated-driver probing and framework validation.
- `imageio` and `imageio-ffmpeg` for validation/task recordings.
- `ssh`/`scp` executables when the orchestrator runs in DGX mode.

The vendoring and compile check do not invoke a model or fabricate model
responses.
