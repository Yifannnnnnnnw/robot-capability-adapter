# Auto-Adapter — Co-author Handoff (EMNLP 2026 Industry Track)

**Submitted to EMNLP 2026 Industry Track (2026-06-16).** Paper compiles to
**6 main pages** (+ appendices) with **0 undefined references**. Every number
traces to `paper/results/canonical.yaml`. Repo + HF dataset stay **private** until
acceptance (double-blind); reviewers see only the anonymized 4open mirror.

---

## 1. One-paragraph pitch
Tool-using LLM agents assume a robot driver/API already exists. **Auto-Adapter**
hands the *driver-authoring* step to an LLM too: a 5-phase pipeline
(study→generate→validate→export→demo) reads an MJCF/URDF and emits a
self-contained Python driver (`move_cartesian`, `get_ee_pose`, …), verified
against **post-step physics state** (not text feedback). We release
**AutoAdapter-Bench**: validated driver artifacts, a task spec, baselines
(Code-as-Policies, PPO, Diffusion Policy, OpenVLA), and a canonical results file.

## 2. Headline results (all verified against the JSONs, 2026-06-07)
- **Onboarding scoreboard (8 robots, arms+quadrupeds):** validated drivers in
  **7.8 ± 2.4 min @ $2.98 ± $0.84** per robot.
- **SO-101 model-portability (7 LLMs, 4 vendors, 13-task suite, N=5, trial-level physics):**
  Auto-Adapter (A-A) beats zero-shot CaP on **6/7**, beats few-shot CaP on **4/7** (1 tie).
  A-A col: Sonnet4.6 **69**, Opus4.8 **63**, Haiku4.5 **62**, Nova **60**, DeepSeek **57**,
  Ministral **54**, Qwen **49** (aggregate task-level Wilcoxon p<0.01, App. I).
  *Use ≠ synthesize:* only **3/7** can write a validated grasp driver
  (Opus/Haiku/DeepSeek; **Sonnet fails grasp validation** despite being the best
  API *user*; Nova/Ministral/Qwen fail earlier). **Self** (model runs its own
  driver, grasp-verified): Opus **0.46**, Haiku **0.46**, DeepSeek **0.28**.
- **Robustness (App. I):** identical 6/7 ranking on the **12-task physics-only** subset
  (A-A 67/60/58/57/53/50/45) — headline does not depend on the one coverage-graded task.
- **Quadruped (Sonnet 4.5, 10-task):** embodiment-dependent — **ANYmal-C 68% vs CaP 28%**,
  **Go2 48% vs CaP 54%**. (Sonnet 4.6 ablation in App. I.)
- **Morphology breadth (App. J):** **Skydio X2 quadrotor** — validated 7/7; A-A & CaP
  **both 100%** on a hardened 8-task aerial suite (point-to-point flight is single-shot
  solvable → the contribution is the *validated flight controller*, not an agent-loop win).
  **Unitree H1 humanoid** — validated 7/7 stand-balance + squat-recover (reproducible);
  **dynamic walking attempted but NOT achieved → the pipeline frontier.**
- **Cross-model self-synthesis (App. J):** 7 LLMs × {go2, skydio, h1} from
  scratch. Both synthesis reliability AND task success are **morphology-ordered**
  — flight (0.82–1.00) > locomotion (0.70–0.82) > humanoid (0.50) > grasp
  (0.28–0.46); only Opus/Sonnet synthesize across >1 morphology, open/small
  models none. Story: *use ≠ write, and writing gets harder with morphology/contact.*
- **Baselines:** per-skill PPO/DP ≤70% own / ≤50% cross; OpenVLA-7B 0/50 zero-shot;
  current ceiling = IK precision on redundant 7-DoF Franka (20/70 @ 2 cm).

## 3. Repo map
```
paper/latex/                 # LaTeX source — main.tex + sections/, compile here
  main.pdf                   # 6 main pages + appendices A–J
paper/results/canonical.yaml # SINGLE SOURCE OF TRUTH (exp1..exp13). Every paper number cites this.
autoadapter_bench/
  BENCHMARK.md               # benchmark card (zoo, suites, 34 graders, baselines, layout)
  spec/                      # robot_zoo.yaml + tasks_{arm,quadruped,wheeled,aerial,humanoid}.yaml
  eval.py                    # ours (ReAct) eval harness + all graders
  eval_baseline.py           # CaP baseline runner (--baseline cap, --few-shot)
  baselines/code_as_policies/runner.py  # CaP sandbox (binds per-class primitives)
  results/
    clean/                   # SO-101 ours_/cap_*.json (13-task, N=5)
    fewshot/                 # CaP few-shot *.json
    quadruped_v2/            # go2/anymal ours_/cap_*sonnet45.json (+4.6 ablation)
    aerial/   humanoid/      # skydio + h1 results + videos/
    LEADERBOARD.json         # machine-readable leaderboard (current)
auto_adapter/
  orchestrator_from_scratch.py  # the synthesis pipeline + per-class GEN prompts & validators
  agent/task_planner.py         # ReAct tool layer (per-class tool specs) + _FrameCapture (video)
artifacts/                   # synthesized driver workspaces (driver_from_scratch.py per robot)
  auto_adapter_so101_v2_artifacts/  # the fixed SO-101 reference driver (all 7 LLMs "use" this)
  from_scratch_quad_v2/{go2,anymal} | from_scratch_aerial_v2/skydio_x2 | from_scratch_humanoid_v3/h1
scripts/run/                 # synth_*.py, eval_aerial.sh (see scripts/run/README.md; video re-render tools archived)
```

## 3b. Data hosting split (GitHub + HuggingFace)
The repo is ~1.4 GB raw; for GitHub we ship a **lean** tree (~482 MB, no file >50 MB,
no secrets — verified) via `.gitignore`: code, paper, spec, **all result JSONs**,
synthesized driver `.py`, and the **5 curated showcase videos**. The **bulk** —
~792 MB per-task replay videos + agent traces (`.jsonl`) + MJCF assets (~306 MB) —
goes to a **HuggingFace dataset**:
```bash
huggingface-cli login                     # write token
# Double-blind: keep the dataset PRIVATE during review (reviewers can't see it);
# flip to public + add the real link only AFTER acceptance.
python scripts/upload_hf.py --repo-id <you>/autoadapter-bench --private
```
Dataset card: `docs/HF_DATASET_README.md`. After acceptance: make the dataset public,
then replace the `<you>/autoadapter-bench` placeholder with the real URL in
README + this file + `BENCHMARK.md`. (For a leaner GitHub repo, `assets/mjcf/`
can also be excluded — it's mostly public MuJoCo Menagerie — but then the GitHub
version needs the HF dataset to run.)

## 4. Reproduce (examples)
```bash
PY=python  # a venv with mujoco + anthropic/boto3
# SO-101 ours (one model):
$PY autoadapter_bench/eval.py --robot so101 --suites hard,contact_rich,contact_rich_l4 \
    --model us.anthropic.claude-sonnet-4-6 --n-trials 5 \
    --driver-workspace artifacts/auto_adapter_so101_v2_artifacts --output /tmp/ours.json
# CaP baseline (same driver):
$PY autoadapter_bench/eval_baseline.py --baseline cap --robot so101 --suites hard,... \
    --driver-workspace artifacts/auto_adapter_so101_v2_artifacts --output /tmp/cap.json
# Aerial: bash scripts/run/eval_aerial.sh   |   Video re-render tools: archive/_superseded/session_2026-06-07/scripts/rerender_*_videos.py
```

## 5. Status — submitted; what remains is post-submission
**DONE & verified (submission shipped):** paper 6pp/0-undef, ACL-format checked,
de-anonymized link removal verified; numbers consistent
(paper=canonical=JSON=leaderboard); 4 validated morphologies
(arm/quad/aerial/humanoid); integrity (log-heuristic grader disclosed + 12-task
robustness); aerial fairness bug fixed (CaP given flight API) + gameability hardened
(inaction→0/8); video render bug root-fixed + 5 showcase videos; 621 broken videos
cleaned; multi-round reviewer-lens passes on the aerial/humanoid framing.

**OPEN (genuine future work, not blockers — only if a co-author wants to push):**
- **Real-robot deployment** — sim-only today; SO-ARM101-on-Jetson bridge exists but
  no sim-to-real claim (see Limitations). The natural next paper.
- **Humanoid dynamic walking** — accepted as the pipeline frontier; static
  stand+squat validated, gait not (yet).
- **Camera-ready / rebuttal** — pending review outcome; flip repo + HF dataset to
  public and swap in real links only **after acceptance**.

## 6. Honest caveats to keep in the paper (don't let a reviewer "discover" these)
- **Aerial is a tie (100/100), not a win** — framed as synthesis-breadth + the insight that a
  high-level synthesized API makes point-to-point flight single-shot-solvable.
- **Humanoid = onboarding-level only** (stand+squat); no downstream eval (replay stand/squat is
  inaction-gameable); **walking attempted & failed = frontier**.
- One headline task (`visit_all_objects`) is coverage-graded, not physics — disclosed in §3;
  12-task physics subset gives the same ranking (App. I).
- Wheeled (Stretch) synthesis failed; **not claimed** as a validated morphology.

## 7. Recent major changes (so the churn isn't confusing)
numpy-bool serialization bug fixed (inflated ours ~8pp) → headline 77→69; log-heuristic grader
disclosed; quad unified to Sonnet 4.5 (4.6→ablation); aerial CaP fairness bug fixed → 100/100;
humanoid added (v3 validated); video pipeline root-fixed; LEADERBOARD.json refreshed.
