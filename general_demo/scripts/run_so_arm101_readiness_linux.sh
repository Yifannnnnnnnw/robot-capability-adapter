#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 IMAGE VERIFIED_RUNTIME_LOCK INTEGRATION_MANIFEST READINESS_PROFILE OUTPUT_DIRECTORY" >&2
  exit 64
fi

image="$1"
runtime_lock="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
integration_manifest="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
readiness_profile="$(cd "$(dirname "$4")" && pwd)/$(basename "$4")"
output_parent="$(cd "$(dirname "$5")" && pwd)"
output_directory="$output_parent/$(basename "$5")"

for input in "$runtime_lock" "$integration_manifest" "$readiness_profile"; do
  if [[ ! -f "$input" || -L "$input" ]]; then
    echo "input is not a regular file: $input" >&2
    exit 66
  fi
done
if [[ -e "$output_directory" ]]; then
  echo "output directory already exists; use a new unique attempt directory: $output_directory" >&2
  exit 73
fi

attempt="$(basename "$output_directory")"
if ! image_id="$(docker image inspect "$image" --format '{{.Id}}')"; then
  echo "could not inspect the Docker image: $image" >&2
  exit 65
fi
if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "could not resolve a verified local Docker image ID for $image" >&2
  exit 65
fi

mkdir "$output_directory"
cp "$runtime_lock" "$output_directory/runtime-lock.json"
cp "$integration_manifest" "$output_directory/integration_manifest.json"
cp "$readiness_profile" "$output_directory/readiness_profile.json"

set +e
docker run --rm --platform linux/amd64 --network none \
  --env AUTOADAPTER_IMAGE_DIGEST="$image_id" \
  --env AUTOADAPTER_GRIPPER_DIRECTION="${AUTOADAPTER_GRIPPER_DIRECTION:-tick-increases-qpos}" \
  --volume "$output_directory:/opt/autoadapter/run_artifacts/$attempt" \
  "$image" \
  python3.12 scripts/run_so_arm101_readiness.py \
    --run-id "linux-real-so101-$attempt" \
    --manifest "/opt/autoadapter/run_artifacts/$attempt/integration_manifest.json" \
    --profile "/opt/autoadapter/run_artifacts/$attempt/readiness_profile.json" \
    --runtime-lock "/opt/autoadapter/run_artifacts/$attempt/runtime-lock.json" \
    --model /opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml \
    --gripper-direction "${AUTOADAPTER_GRIPPER_DIRECTION:-tick-increases-qpos}" \
    --report "/opt/autoadapter/run_artifacts/$attempt/readiness_report.json" \
    --reference-root /opt/autoadapter
status=$?
set -e
exit "$status"
