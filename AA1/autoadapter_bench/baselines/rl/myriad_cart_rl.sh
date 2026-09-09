#!/bin/bash -l
#$ -l h_rt=6:00:00
#$ -l mem=4G
#$ -pe smp 12
#$ -N cart_rl
#$ -wd /home/user/auto_adapter_bench
#$ -j y
#$ -o /home/user/auto_adapter_bench/cart_rl_$JOB_ID.log

set -e
echo "JOB_ID=$JOB_ID HOST=$(hostname) DATE=$(date)"
module load python3/3.11
source .venv-rl/bin/activate
export PYTHONPATH=/home/user/auto_adapter_bench:$PYTHONPATH
export MUJOCO_GL=disable
WS=auto_adapter_so101_v2_artifacts
M="python -u -m autoadapter_bench.baselines.rl.train_eval_cartesian --workspace $WS"

echo "=== PARALLEL TRAIN: 6 single-skill + 3 multitask $(date) ==="
for s in 0 1 2; do
  $M --mode train_reach     --reach-steps 300000 --seed $s > cart_reach_s$s.log 2>&1 &
  $M --mode train_pick      --pick-steps  350000 --seed $s > cart_pick_s$s.log 2>&1 &
  $M --mode train_multitask --mt-steps    600000 --seed $s > cart_mt_s$s.log 2>&1 &
done
wait
echo "=== TRAIN DONE $(date); EVAL ==="
for s in 0 1 2; do
  $M --mode crosseval     --seed $s --n-episodes 30 --output autoadapter_bench/baselines/cartesian_cross_skill_s$s.json > cart_eval_s$s.log 2>&1
  $M --mode eval_multitask --seed $s --n-episodes 30 --output autoadapter_bench/baselines/cartesian_multitask_s$s.json > cart_mt_eval_s$s.log 2>&1
  echo "seed $s eval done $(date)"
done

echo "=== AGGREGATE ==="
python - <<'PY'
import json, numpy as np
seeds=[0,1,2]
CS=[json.load(open(f"autoadapter_bench/baselines/cartesian_cross_skill_s{s}.json"))["results"] for s in seeds]
MT=[json.load(open(f"autoadapter_bench/baselines/cartesian_multitask_s{s}.json"))["results"] for s in seeds]
def ra(r,k): return np.mean([v["success_rate"] for v in r[k].values()])
rows={
 "cart_reach->reach(own)":[ra(r,"cart_reach_on_reach") for r in CS],
 "cart_pick->pick(own)":[r["cart_pick_on_pick"]["success_rate"] for r in CS],
 "cart_reach->pick(cross)":[r["cart_reach_on_pick"]["success_rate"] for r in CS],
 "cart_pick->reach(cross)":[ra(r,"cart_pick_on_reach") for r in CS],
 "multitask->reach":[r["multitask_on_reach_avg"] for r in MT],
 "multitask->pick":[r["multitask_on_pick"]["success_rate"] for r in MT],
}
agg={}
print(f"{'cell':28s} mean+/-std  per-seed")
for k,v in rows.items():
    v=np.array(v)*100; agg[k]={"mean":float(v.mean()),"std":float(v.std()),"per_seed":v.tolist()}
    print(f"{k:28s} {v.mean():5.1f}+/-{v.std():4.1f}%  {v.tolist()}")
json.dump({"seeds":seeds,"n_episodes":30,"reach_steps":300000,"pick_steps":350000,"mt_steps":600000,"cells":agg},
          open("autoadapter_bench/baselines/cartesian_rl_3seed.json","w"),indent=2)
print("Saved: autoadapter_bench/baselines/cartesian_rl_3seed.json")
PY
echo "=== CART_RL DONE $(date) ==="
