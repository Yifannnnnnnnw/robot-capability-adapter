#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 IMAGE VERIFIED_RUNTIME_LOCK OUTPUT_DIRECTORY" >&2
  exit 64
fi

image="$1"
runtime_lock="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
output_directory="$(mkdir -p "$3" && cd "$3" && pwd)"

docker run --rm --network none \
  -v "$runtime_lock:/run/autoadapter/runtime-lock.json:ro" \
  -v "$output_directory:/opt/autoadapter/general_demo/readiness_output" \
  "$image" \
  python3.10 scripts/run_unitree_go2_readiness.py \
    --run-id linux-real-unitree-go2-route \
    --manifest integrations/unitree-go2/integration_manifest.json \
    --profile contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json \
    --runtime-lock /run/autoadapter/runtime-lock.json \
    --model /opt/unitree_mujoco/unitree_robots/go2/go2.xml \
    --report /opt/autoadapter/general_demo/readiness_output/readiness_report.json \
    --reference-root /opt/autoadapter
