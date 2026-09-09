# AutoAdapter-Bench

**A physics-grounded benchmark for LLM agents that *use* and *synthesize* robot
tool layers from simulator specifications.**

AutoAdapter-Bench measures two distinct, separable capabilities of an LLM on a
robot it has never been fine-tuned for:

1. **Using** a tool layer — drive a *fixed* synthesized driver (FK/IK, gripper,
   perception) to complete physics-graded manipulation and locomotion tasks.
2. **Synthesizing** a tool layer — write the driver itself from an MJCF/URDF and
   pass a structural validation suite, then operate its own driver end-to-end.

Every task is graded on **post-physics simulator state** (object/end-effector
poses after the sim settles), never on the agent's self-report. This exposes the
*over-claim* gap — agents routinely report success the physics contradicts.

---

## 1. What is evaluated

### 1.1 Robot zoo (8-robot scoreboard + 2 morphology-extension robots, 4 morphologies, 7 vendors)
| Robot | Class | DoF | Vendor | MJCF source |
|---|---|---|---|---|
| SO-101 | arm | 5+grip | LeRobot/TheRobotStudio | Menagerie |
| Piper | arm | 6+grip | AgileX | Menagerie |
| UR5e | arm | 6 | Universal Robots | Menagerie |
| Franka FR3 | arm | 7+grip | Franka Emika | Menagerie |
| KUKA iiwa14 | arm | 7 | KUKA | Menagerie |
| Unitree Go2 | quadruped | 12 | Unitree | Menagerie |
| Unitree A1 | quadruped | 12 | Unitree | Menagerie |
| ANYbotics ANYmal-C | quadruped | 12 | ANYbotics | Menagerie |
| Skydio X2 | aerial | 6 (4 rotors) | Skydio | Menagerie |
| Unitree H1 | humanoid | 19+6 | Unitree | Menagerie |

The 8-robot onboarding scoreboard (§4) covers the arms + quadrupeds. **Skydio X2**
(aerial) and **Unitree H1** (humanoid) are morphology-extension robots: the same
pipeline, only the class-specific GEN prompt + physics validator change. Skydio
validates 7/7; on a hardened (inaction-proof) 8-task aerial suite **A-A and CaP
both reach 100%** (point-to-point flight via the synthesized high-level API is
single-shot solvable — the contribution is the validated flight driver, not an
agent-loop win); H1
validates **7/7** (stable stand at torso 0.98m / upright 1.00 + squat-recover,
reproducible under the protocol) — a balance controller for a 19-DoF biped
synthesized zero-shot; dynamic walking is future work. See `canonical.yaml`
exp12/exp13.

(+ scene variants: `menagerie_so101`, `*_push` pushbench scenes, `piper/pickbench`.)

### 1.2 Task suites (`spec/tasks_{arm,quadruped,wheeled,aerial,humanoid}.yaml`)
Aerial (`tasks_aerial.yaml`, 8 tasks): takeoff/hover/point-to-point/climb/
round-trip/diagonal/box-patrol/tight-hover, physics-graded on base pose +
upright. Humanoid (`tasks_humanoid.yaml`, 8 tasks): stand-balance / squat-recover
/ hold-in-place, graded on torso height + upright (H1 reported at the
onboarding-validation level; see §1.1 note).

**Arm** (`tasks_arm.yaml`):
| Suite | #tasks | Tests |
|---|---|---|
| `simple` | 5 | reach / gripper / unreachable-detect (cross-robot) |
| `hard` | 4 | conditional pick, multi-object visit, numeric reasoning, waypoint trace |
| `contact_rich` | 5 | stack, place-in, collision-avoid, swap, stable-place |
| `contact_rich_l4` | 4 | 3-object tower, spatial-query pick, swap-chain, clear-then-reach |
| `push` | 4 | non-prehensile push-to-target / around-obstacle |

The headline **SO-101 13-task suite** = `hard` (4) + `contact_rich` (5) +
`contact_rich_l4` (4).

**Quadruped** (`tasks_quadruped.yaml`, v2):
| Suite | #tasks | Tests |
|---|---|---|
| `simple` | 5 | sit / stand / short walks |
| `hard` | 5 | target-band stop, explore-correct, walk-sit-stand, bounded drift |

### 1.3 Graders (34 types in `eval.py`; 23 used — 20 physics-state + 3 behavioral)
Twenty of the 23 used graders read **MuJoCo state after physics settles**; three
are behavioral and labeled as such (`llm_reports_failure` for `unreachable_detect`,
`tool_executed_without_crash` for `gripper_cycle`, `ee_visited_n_positions` for
`visit_all_objects` — the last now verifies physical EE reach, with commanded-target
coverage as a fallback). Highlights of the physics-state graders:
- Manipulation: `object_lifted_by`, `object_above_object`, `objects_swapped`,
  `object_pose_match_relative`, `object_close_to_object_xy`, `objects_in_tower`,
  `push_relative_obstacle_safe`, …
- Trajectory: `ee_waypoint_trace` (verifies ordered waypoints **are visited**,
  not just the endpoint), `ee_visited_n_positions`, `phase_trajectory`.
- Locomotion: `body_height_ratio`, `forward_progress` (with target bands /
  Y-drift bounds), per-phase snapshots.

**Grader rigor.** Proxy graders that can be gamed by inaction or by reporting
home (e.g. an old `ee_pose_returned_home` "draw an L") were audited and either
replaced with state-verifying graders (`ee_waypoint_trace`) or removed. See
[§6 Integrity](#6-integrity--known-limits).

---

## 2. Metrics & protocol

- **Primary metric:** *trial-level physics pass rate* — fraction of trials in
  which the post-physics state satisfies the grader. `N=5` trials/task.
- **Agreement / over-claim:** per-trial match between LLM self-report and
  physics; `over_claim = phys-fail ∧ llm-ok`. Discriminates methods that always
  claim success.
- **Synthesis:** pass/fail of the structural validation suite (build, FK/IK
  round-trip, gripper, object perception) at `k/3` reruns.
- **Cost:** Bedrock token cost (USD) per 13-task configuration; wall-clock.

**Two evaluation axes (kept deliberately separate):**
- **Model portability** — SO-101 × 7 LLMs (each drives the *same* reference
  driver, and separately *writes its own*).
- **Robot diversity** — fixed Sonnet-4.6 × multiple robots.

---

## 3. Baselines

| Baseline | Family | Notes |
|---|---|---|
| **Auto-Adapter (A-A)** | LLM ReAct tool-use | multi-turn, observes physics, recovers |
| **CaP** (Code-as-Policies) | LLM single-shot code | zero-shot; same API |
| **CaP_fs** | LLM single-shot code | few-shot exemplars (extra human effort/API) |
| **PPO** | RL from scratch | per-skill + multi-task |
| **Diffusion Policy** | imitation learning | per-skill + multi-task |
| **OpenVLA-7B** | vision-language-action | zero-shot |

`eval.py` runs A-A; `eval_baseline.py --baseline {ours,cap} [--few-shot]` runs
CaP/CaP_fs; `baselines/` holds the PPO/DP/OpenVLA harnesses.

---

## 4. Leaderboard (headline results)

### 4.1 Using vs. synthesizing — SO-101 13-task, N=5, trial-level physics %
| Model | A-A | CaP | CaP_fs | Synth | Self |
|---|---|---|---|---|---|
| Sonnet 4.6 | **69** | 57 | 54 | ✓ | 46 |
| Opus 4.8 | **63** | 51 | 42 | ✓ | 52 |
| Haiku 4.5 | **62** | 54 | 62 | ✓ | 38 |
| Nova Pro | **60** | 57 | 46 | ✗(study) | — |
| DeepSeek V3.2 | 57 | 9 | **60** | ✓ | 28 |
| Ministral 8B | **54** | 23 | 43 | ✗(study) | — |
| Qwen3 32B | 49 | **72** | 57 | ✗(validate) | — |

- **Using:** A-A tops zero-shot CaP on **6/7** models and few-shot CaP on **4/7**
  (every strong model by 14–21 pts). Few-shot exemplars only rescue weak models
  that cannot use the API zero-shot (DeepSeek 9→60) and hurt strong ones.
- **Synthesizing:** only **4/7** LLMs write a validated driver — *using ≠
  synthesizing*. Qwen, the strongest CaP user (72%), cannot synthesize at all.

### 4.2 Robot diversity (fixed Sonnet 4.6)
- Arms: SO-101 / Piper / UR5e full suites; Franka reach drill-down (redundant
  7-DoF IK ceiling, 20/70); end-to-end Piper Pick 30/30.
- Quadruped v2 (ours vs CaP): ANYmal-C 62%/50%, Go2 44%/54% — embodiment-
  dependent; both solve sit/stand/short-walk, hard band-stops limited by the
  open-loop walk step.

All numbers are reproduced in `paper/results/canonical.yaml` (`exp11` =
corrected leaderboard) and in the per-cell JSONs under `results/`.

---

## 5. Artifact layout & reproduction

```
autoadapter_bench/
  spec/         robot_zoo.yaml, tasks_arm.yaml, tasks_quadruped.yaml
  eval.py       Auto-Adapter (ReAct) evaluator  [--driver-workspace, --suites]
  eval_baseline.py   CaP / CaP_fs evaluator     [--baseline cap --few-shot]
  baselines/    code_as_policies/, dp/, vla/, PPO + eval scripts
  results/
    clean/      ours/cap × 7 models × SO-101 13-task   (corrected)
    fewshot/    cap_fs × 7 models
    leaderboard_b/  diag_* (Self, own-driver) + synthesis_scoreboard.json
    quadruped_v2/   ours/cap × {go2,anymal}
    _prefix_bugfix_backup/   pre-repair originals (provenance)
artifacts/
  auto_adapter_so101_v2_artifacts/      reference (hand-validated) SO-101 driver
  auto_adapter_from_scratch_<robot>_artifacts/   per-robot synthesized drivers
  from_scratch_xmodel/so101_<model>/    cross-model SO-101 drivers (Leaderboard B)
  from_scratch_quad_v2/{go2,a1,anymal}/ quadruped v2 drivers
```

**Reproduce a cell** (example: Auto-Adapter, SO-101 13-task, Sonnet 4.6):
```bash
python autoadapter_bench/eval.py --robot so101 \
  --suites hard,contact_rich,contact_rich_l4 \
  --model us.anthropic.claude-sonnet-4-6 --n-trials 5 \
  --output results/clean/ours_so101_sonnet46.json
# CaP zero-shot / few-shot:
python autoadapter_bench/eval_baseline.py --baseline cap --robot so101 \
  --suites hard,contact_rich,contact_rich_l4 [--few-shot] \
  --model <id> --n-trials 5 --output results/<...>.json
```

**Environment:** MuJoCo 3.3.x; AWS Bedrock (AnthropicBedrock for Claude,
Converse for others). Exact model identifiers and per-token pricing are in
`run_multi_model.py` and `paper/results/canonical.yaml`. LLM rollouts are
deterministic given identical prompt+task (temperature 0); replay-in-sim is the
success metric, so single rollouts are reproducible.

---

## 6. Integrity & known limits

The benchmark was hardened through an adversarial audit (documented in
`canonical.yaml::exp11.bugfixes_applied`):
1. **Aggregation:** a numpy-bool serialized as the string `"False"` was
   truthy-counted as a pass; fixed (bool-cast at storage + robust aggregator)
   and all legacy result files re-aggregated (0 anomalies across 140 files).
2. **Grader soundness:** the `draw_letter_L` task was unreachable on SO-101 and
   graded only on return-home (hollow passes); replaced with a feasible
   down-4cm + forward-4cm L verified by `ee_waypoint_trace`.

**Honest ceilings (documented, not hidden):**
- Redundant 7-DoF arm IK (Franka) leaves 2–4 cm Cartesian error in some
  directions — an IK-template limit, not an LLM limit.
- Quadruped hard band-stop tasks are bounded by the synthesized open-loop walk
  step (~0.15 m).
- A1 walk synthesis failed under the strict locomotion validator (documented).
- 3/7 LLMs cannot synthesize a driver at all.

---

## 7. Citation
If you use AutoAdapter-Bench, please cite the Auto-Adapter paper (see
`paper/latex/main.pdf`). The benchmark's single source of truth for all reported
numbers is `paper/results/canonical.yaml`.
