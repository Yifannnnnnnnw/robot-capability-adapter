#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 IMAGE VERIFIED_RUNTIME_LOCK INTEGRATION_MANIFEST OUTPUT_DIRECTORY" >&2
  exit 64
fi

image="$1"
runtime_lock="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
integration_manifest="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
project_root="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ -e "$4" ]]; then
  echo "output directory already exists; use a new attempt directory" >&2
  exit 73
fi
mkdir -p "$4"
output_directory="$(cd "$4" && pwd)"
case "$output_directory" in
  "$project_root"/*) output_relative="${output_directory#"$project_root"/}" ;;
  *) echo "output directory must be inside the project root" >&2; exit 64 ;;
esac
container_output="/opt/autoadapter/$output_relative"

locked_digest="$(jq -r '.oci_image_digest // empty' "$runtime_lock")"
actual_digest="$(docker image inspect "$image" --format '{{.Id}}')"
if [[ -z "$locked_digest" || "$actual_digest" != "$locked_digest" ]]; then
  echo "image digest does not match the verified Runtime Lock" >&2
  exit 65
fi

docker run --rm --platform linux/amd64 --network none \
  -v "$runtime_lock:/run/autoadapter/runtime-lock.json:ro" \
  -v "$integration_manifest:/opt/autoadapter/general_demo/integrations/unitree-go2/integration_manifest.json:ro" \
  -v "$output_directory:$container_output" \
  "$image" \
  python3.10 scripts/run_unitree_go2_readiness.py \
    --run-id linux-real-unitree-go2-route \
    --manifest integrations/unitree-go2/integration_manifest.json \
    --profile contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json \
    --runtime-lock /run/autoadapter/runtime-lock.json \
    --model /opt/unitree_mujoco/unitree_robots/go2/scene.xml \
    --report "$container_output/readiness_report.json" \
    --reference-root /opt/autoadapter
