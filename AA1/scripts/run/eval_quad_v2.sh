#!/bin/bash
# v2 quadruped eval: go2 + anymal on the 10-task v2 suite, using the
# strict-validated v2 Sonnet-synthesized drivers (robot-diversity axis =
# fixed Sonnet). ours (eval.py) + cap baseline. a1 is a documented limit
# (Sonnet's walk synthesis failed) and is NOT run.
cd "$(dirname "$0")/../.."
PY=${PY:-python}
MODEL="us.anthropic.claude-sonnet-4-6"
OUT=autoadapter_bench/results/quadruped_v2
mkdir -p "$OUT"
MAXJOBS=2

# robot_zoo_id : v2_workspace
CELLS=(
  "go2:artifacts/from_scratch_quad_v2/go2"
  "anymal_c:artifacts/from_scratch_quad_v2/anymal"
)
for spec in "${CELLS[@]}"; do
  rid="${spec%%:*}"; ws="${spec#*:}"
  while [ "$(jobs -rp | wc -l)" -ge "$MAXJOBS" ]; do sleep 5; done
  short=$(echo "$rid" | sed 's/_c$//')
  echo "[quadv2-eval] ours $rid"
  $PY autoadapter_bench/eval.py --robot "$rid" --suites simple,hard \
      --model "$MODEL" --n-trials 5 --driver-workspace "$ws" \
      --output "$OUT/ours_${short}_sonnet46.json" \
      > "/tmp/quadv2eval_ours_${short}.log" 2>&1 &
  sleep 3
done
wait
echo "[quadv2-eval] ALL DONE"
