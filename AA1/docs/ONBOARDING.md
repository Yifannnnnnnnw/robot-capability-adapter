# Auto-Adapter

> **In one breath:** To make any robot do something with an LLM, an engineer
> first hand-writes a "driver" — the code that turns *"move the gripper here"*
> into actual motor commands. That takes **days–weeks per robot**. We show the
> LLM can **write that driver itself**, given only the robot's blueprint file, by
> trying its code in a physics simulator and fixing it until the robot actually
> does the task. **~8 min, ~$3 per robot, fully automatic.**

**Venue:** EMNLP 2026 Industry Track · **Deadline 2026-06-16 (12 days)** ·
recruiting co-authors.

> **"MJCF / URDF" = the robot's blueprint file** — a text file listing its
> joints, links, sizes, and limits. The *only* input we need.

---

## The core idea — one picture

```
   robot blueprint              the SAME LLM that would USE the robot
   (you have this)              now WRITES the code to drive it
  ┌──────────────┐            ┌───────────────────────────────────────┐
  │  mjcf / urdf │            │   Auto-Adapter 5-phase agent loop      │
  │  ─────────── │   ───────► │                                        │
  │  joints,     │            │  STUDY → GENERATE → VALIDATE → EXPORT  │
  │  links,      │            │            ▲            │      → DEMO   │
  │  limits,     │            │            └────────────┘              │
  │  sizes       │            │      revise until physics test passes  │
  └──────────────┘            └───────────────────┬───────────────────┘
                                                   │ emits
                                                   ▼
                                          ┌──────────────────┐
                                          │    driver.py     │  move_cartesian()
                                          │ (the robot's API)│  gripper_close()
                                          └────────┬─────────┘  get_object_position()
                                                   │ now any LLM agent
                                                   ▼ can drive the robot
                                          ┌──────────────────┐
                                          │  "pick the banana"│
                                          └──────────────────┘
```

The middle box is normally **hand-written by an engineer over days–weeks**.
We replace that human with the LLM.

---

## The whole robot stack — where we sit

```
  ① TASK     "pick the banana"                    ← human
  ② AGENT    plans, picks next action             ← off-the-shelf LLM
  ③ DRIVER   move_gripper(), close_gripper() …    ← *** WE GENERATE THIS ***
             (the code an engineer hand-wrote)       (was: days–weeks)
  ④ ROBOT    motors move, gripper grips           ← simulator or real hardware
       └──► physics result flows back up to verify ──┘
```

Layers ① ② are existing agent tech; ④ is the sim/hardware. **We automate ③.**

---

## Our one technical bet — verify with PHYSICS, not text

```
  Most LLM-code systems verify with TEXT:        We verify with PHYSICS:
  ┌─────────────────────────────┐               ┌──────────────────────────────┐
  │ agent writes code           │               │ agent writes driver          │
  │      ▼                      │               │      ▼                       │
  │ read stdout / unit-test     │               │ run INSIDE the simulator →   │
  │ pass-fail / an LLM "judge"  │               │ read the ACTUAL result:      │
  │      ▼                      │               │  • where the gripper ended   │
  │ "looks right" → accept      │               │  • did the cube move?        │
  └─────────────────────────────┘               │  • did anything collide?     │
                                                 │      ▼                       │
   problem: text can lie. code                   │ judge success from reality   │
   can "look correct" and still                  └──────────────────────────────┘
   drive the arm into the table.                  the agent debugs against reality
```

This is *why* it's reliable — and the main thing that distinguishes us from
prior LLM-writes-robot-code work.

---

## A robot arm, for anyone who hasn't seen one

```
                       gripper (the "hand") ──► close_gripper() acts here
            joint ●───────┘
                  │ link
            joint ●          • "joint" = a place the arm bends
                  │ link     • "DoF"   = number of joints
            joint ●            (SO-101 has 5, Franka has 7)
                  │ link     • more joints = more flexible, harder to control
   base ──── joint ●         • IK = "I want the hand HERE" → solve the joint angles
  ════════════════════ table
```

7-joint arms (Franka) are *redundant* — many joint settings reach the same point,
so the solver must choose well. Generic solvers struggle here → **our current
ceiling, and open problem #1.**

---

## The robot zoo — what we onboard

```
  ARMS (manipulation)                      4-LEGGED (walking)
  ─────────────────────────────────       ──────────────────────────────
  SO-101        5-joint + gripper  ★deep   Go2          12-joint
  Piper         6-joint + gripper          Unitree A1   12-joint
  UR5e          6-joint                    ANYmal-C     12-joint
  Franka        7-joint + gripper  ◄ceiling
  KUKA iiwa14   7-joint
  ★deep = full task-suite eval    ◄ceiling = 7-joint control limit
```

All 8 are auto-onboarded (the LLM writes a driver that passes validation);
SO-101 carries the deep task evaluation.

---

## Does the driver work after we write it? — yes, we close the loop

```
  blueprint ──► LLM writes driver ──► ✓ VALIDATE: do the primitives really work?
                                         (does move_gripper actually move it?)
                       │
                       ▼
            ✓ CAPABILITY: the agent USES the driver to do real tasks —
              reach, pick, stack, push, multi-step — scored by PHYSICS,
              not by what the model claims.  →  this is the 7-model leaderboard.
```

- ✅ **Validation** — generated primitives provably work.
- ✅ **Capability** — agents complete real tasks with them, across 7 LLMs × 4 tiers.
- ⚠️ **Still open** — hardest long-horizon contact tasks (even top models ~0%),
  7-joint precision, and the jump to the *physical* robot.

---

## What's done (you're not starting from zero)

| | |
|---|---|
| **Robots** | 8 auto-onboarded: SO-101, Piper, UR5e, Franka, KUKA, Go2, A1, ANYmal-C — $3 / 8 min each |
| **Models** | 7 LLMs × 4 vendors (Anthropic, Mistral, Qwen, DeepSeek, Amazon) |
| **Tasks** | 4 difficulty tiers + a cross-arm push suite |
| **Baselines** | Code-as-Policies, reinforcement learning, Diffusion Policy, a vision-language-action model |
| **Headline** | Ours > baseline on **6/7 models** (p=0.021); **open-source ≈ closed**; consistent + low-variance |
| **Real robot** | Physical SO-ARM101 wired up (Jetson + camera + teleop); sim-to-real gaps mapped |
| **Reproducible** | every number backed by a saved data file; per-run videos + traces |

We'd rather state the boundary than oversell — that's the "Still open" line above.

---

## The tasks we test (5 difficulty tiers, 22 tasks)

```
  simple  (5)  basic motion         move +5cm · trace a square · open/close gripper
  hard    (4)  reasoning + planning  conditional pick · visit all objects · numeric midpoint · draw an "L"
  contact (5)  touch & manipulate    stack lego on duck · place duck in mug · swap two objects
  hardest (4)  long-horizon contact  build a 3-block tower · two swaps in a row · clear-then-reach
  push    (4)  non-grasp pushing     push a T-block · slide cube to zone · push around obstacle · sweep 3 cubes
```

Success drops cleanly tier by tier — that's the curve the leaderboard captures.

---

## The leaderboard (SO-101, physics-scored success)

```
  model           ours   baseline      ← "ours" = Auto-Adapter agent
  ─────────────────────────────────       "baseline" = Code-as-Policies (zero-shot)
  Sonnet-4.6      69%     57%
  Opus-4.8        63%     51%
  Haiku-4.5       62%     54%
  Nova-Pro        60%     57%
  DeepSeek-v3.2   57%      9%   ◄ open-source, beats baseline by 48 pts
  Ministral-8B    54%     23%
  Qwen3-32B       49%     72%   ◄ the one case baseline wins (a finding, not a bug)
```

Ours wins on 6 of 7 models; open-source DeepSeek lands right in the Claude range.
*(13-task hard suite, N=5, trial-level physics; authoritative numbers in
`paper/results/canonical.yaml::exp11` and `HANDOFF.md`.)*

---

## What one run actually looks like (real agent trace)

Task: *"if the banana is left of the mug, pick the banana and lift 10 cm."*

```
  iter0  check positions     → get_object_position(banana), get_object_position(mug)
  iter1  reason              "banana X=0.110 < mug X=0.220 → pick the banana"
  iter2  approach above it   → move_cartesian(0.110, 0.12, 0.10)
  iter3  open + descend      → gripper_open(); move_cartesian(0.110, 0.12, 0.045)
  iter4  grasp              → gripper_close()
  iter5  verify             → is_holding()  → True
  iter6  lift 10 cm         → move_cartesian(0.110, 0.12, 0.145)   ✓ physics: success
```

The agent reasons, acts, reads the result, and self-corrects — all on a driver
it wrote itself minutes earlier.

---

## Success videos to show (open these)

`artifacts/auto_adapter_so101_v2_artifacts/recordings/ours_so101_opus48/`

```
  swap_banana_bottle_t0.mp4       two-object swap        (best demo — 1.2 MB)
  place_duck_inside_mug_t1.mp4    contact placement      (0.96 MB)
  stack_lego_on_duck_t0.mp4       stacking               (0.62 MB)
  visit_all_objects_t0.mp4        6-object tour          (0.62 MB)
  stable_place_screwdriver_t0.mp4 pick-and-place         (0.59 MB)
  draw_letter_L_t0.mp4            trajectory control     (0.31 MB)
  conditional_pick_t0.mp4         reasoning + grasp      (0.23 MB)
```

Every other model + method has its own folder under `…/recordings/<model>_<method>/`.

---

*Full paper: `paper/latex/main.pdf` · all numbers: `autoadapter_bench/results/`*
