#!/bin/bash
# Aerial (Skydio X2) eval: Auto-Adapter (ours) vs same-API CaP on the
# 8-task aerial suite, using the synthesized v2 drone driver (Sonnet 4.5).
cd "$(dirname "$0")/../.."
PY=${PY:-python}
MODEL="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
WS="artifacts/from_scratch_aerial_v2/skydio_x2"
OUT=autoadapter_bench/results/aerial
mkdir -p "$OUT"

echo "[aerial-eval] ours skydio_x2"
$PY autoadapter_bench/eval.py --robot skydio_x2 --suites simple,hard \
    --model "$MODEL" --n-trials 5 --driver-workspace "$WS" \
    --output "$OUT/ours_skydio_sonnet45.json" > /tmp/aerial_ours.log 2>&1

echo "[aerial-eval] cap skydio_x2"
$PY autoadapter_bench/eval_baseline.py --baseline cap \
    --robot skydio_x2 --suites simple,hard \
    --model "$MODEL" --n-trials 5 --driver-workspace "$WS" \
    --output "$OUT/cap_skydio_sonnet45.json" > /tmp/aerial_cap.log 2>&1

echo "[aerial-eval] DONE"
$PY - <<'PY'
import json
for tag in ["ours","cap"]:
    try:
        d=json.load(open(f"autoadapter_bench/results/aerial/{tag}_skydio_sonnet45.json"))
        print(f"  {tag}_skydio: phys={d.get('aggregate',{}).get('physics_pass_rate')}")
    except Exception as e:
        print(f"  {tag}: {e}")
PY
