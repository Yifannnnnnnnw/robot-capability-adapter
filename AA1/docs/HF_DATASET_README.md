---
license: apache-2.0
task_categories:
  - robotics
tags:
  - robotics
  - mujoco
  - llm-agents
  - code-generation
  - benchmark
pretty_name: AutoAdapter-Bench (bulk artifacts)
---

# AutoAdapter-Bench — bulk artifacts

Heavy media + traces for **AutoAdapter-Bench** (EMNLP 2026 Industry Track,
*Auto-Adapter: Physics-in-the-Loop Robot Driver Synthesis for Embodied LLM Agents*).
The **code, paper, task spec, result JSONs, and curated showcase videos** live
in the GitHub repo; this dataset holds the bulk that is too large for GitHub.

## Contents
- `autoadapter_bench/results/` — result JSONs (also on GitHub) + **per-task replay videos** + 5 showcase videos.
- `artifacts/**/recordings/` — per-task MuJoCo replay videos (~792 MB) across all robots/models.
- `artifacts/**/traces/`, `**/task_traces/` — agent ReAct trace logs (`.jsonl`).
- `assets/mjcf/` — the MJCF scenes/meshes (self-contained reproduction; mostly MuJoCo Menagerie).

## Robots / morphologies (4 validated)
arm (SO-101, Piper, UR5e, Franka, KUKA) · quadruped (Go2, A1, ANYmal-C) ·
aerial (Skydio X2) · humanoid (Unitree H1).

## Headline (see GitHub `paper/results/canonical.yaml` for all numbers)
- SO-101 13-task, 7 LLMs: Auto-Adapter beats zero-shot CaP **6/7**, few-shot CaP **4/7**.
- Quadruped (Sonnet 4.5): ANYmal 68% vs CaP 28%; Go2 48% vs CaP 54%.
- Aerial: A-A and CaP both 100% (point-to-point flight is single-shot solvable).
- Humanoid: stand-balance + squat validated; dynamic walking = frontier.

## Provenance / integrity
Every number traces to `canonical.yaml`. Known caveats (disclosed in the paper):
one headline task is coverage-graded (not physics; 12-task physics subset gives the
same ranking); aerial is a tie (synthesis-breadth result); humanoid is onboarding-level.

**Code + paper:** see the linked GitHub repository (HANDOFF.md is the entry point).
