#!/bin/bash -l
#$ -l h_rt=2:00:00
#$ -l mem=32G
#$ -l gpu=1
#$ -ac allow=L
#$ -N openvla_canon
#$ -wd /home/user/auto_adapter_bench
#$ -j y
#$ -o /home/user/auto_adapter_bench/canon_$JOB_ID.log

set -e
echo "JOB_ID=$JOB_ID HOST=$(hostname) DATE=$(date)"
module load python3/3.11
module load cuda/12.2.2/gnu-10.2.0
source .venv-openvla/bin/activate
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DEACTIVATE_ASYNC_LOAD=1
export PYTHONPATH=/home/user/auto_adapter_bench:$PYTHONPATH
export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl

echo "=== canonical OpenVLA eval, FIXED harness (duration=0.5), n=10 ==="
python autoadapter_bench/baselines/vla/eval_openvla.py \
    --workspace auto_adapter_from_scratch_so101_artifacts \
    --output autoadapter_bench/baselines/vla/openvla_so101_reach_eval_fixed.json \
    --n-episodes 10 \
    --ee-delta-scale 2.0
echo "=== DONE canon at $(date) ==="
