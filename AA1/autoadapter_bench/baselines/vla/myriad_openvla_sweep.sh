#!/bin/bash -l
#$ -l h_rt=4:00:00
#$ -l mem=32G
#$ -l gpu=1
#$ -ac allow=L
#$ -N openvla_sweep
#$ -wd /home/user/auto_adapter_bench
#$ -j y
#$ -o /home/user/auto_adapter_bench/sweep_$JOB_ID.log

set -e
echo "=== SGE info ==="
echo "JOB_ID=$JOB_ID  HOST=$(hostname)  DATE=$(date)"
nvidia-smi || echo "no GPU?"

module load python3/3.11
module load cuda/12.2.2/gnu-10.2.0
source .venv-openvla/bin/activate

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DEACTIVATE_ASYNC_LOAD=1
export PYTHONPATH=/home/user/auto_adapter_bench:$PYTHONPATH
export MUJOCO_GL=egl
export PYOPENGL_PLATFORM=egl

python -c "import torch; print('torch', torch.__version__, 'cuda:', torch.cuda.is_available())"

echo "=== launch OpenVLA config-robustness sweep ==="
python autoadapter_bench/baselines/vla/eval_openvla_sweep.py \
    --workspace auto_adapter_from_scratch_so101_artifacts \
    --output autoadapter_bench/baselines/vla/openvla_sweep.json \
    --n-episodes 3 \
    --max-steps 15

echo "=== DONE sweep at $(date) ==="
