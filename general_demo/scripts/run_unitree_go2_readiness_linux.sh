#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 IMAGE VERIFIED_RUNTIME_LOCK INTEGRATION_MANIFEST OUTPUT_DIRECTORY" >&2
  exit 64
fi

image="$1"
runtime_lock="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
integration_manifest="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
output_directory="$(mkdir -p "$4" && cd "$4" && pwd)"

if [[ -e "$output_directory/readiness_report.json" ]]; then
  echo "output directory already contains readiness_report.json; use a new attempt directory" >&2
  exit 73
fi

docker run --rm --platform linux/amd64 --network none \
  -v "$runtime_lock:/run/autoadapter/runtime-lock.json:ro" \
  -v "$integration_manifest:/opt/autoadapter/general_demo/integrations/unitree-go2/integration_manifest.json:ro" \
  -v "$output_directory:/opt/autoadapter/general_demo/readiness_output" \
  "$image" \
  python3.10 scripts/run_unitree_go2_readiness.py \
    --run-id linux-real-unitree-go2-route \
    --manifest integrations/unitree-go2/integration_manifest.json \
    --profile contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json \
    --runtime-lock /run/autoadapter/runtime-lock.json \
    --model /opt/unitree_mujoco/unitree_robots/go2/scene.xml \
    --report /opt/autoadapter/general_demo/readiness_output/readiness_report.json \
    --reference-root /opt/autoadapter
