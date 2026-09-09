# Work-in-progress figures (NOT in the submitted paper)

Candidate figures for the next version / dissertation. Not wired into
paper/latex; not part of the EMNLP submission.

## synth_trajectory.png  (script: plot_synth_trajectory.py)

NLP-native behavioral analysis of the synthesis traces (1705 jsonl
files under artifacts/**/traces/).

(a) Per-iteration trajectory of 6 synthesis runs. Each cell = one inner
    iteration, colored read/plan vs execute-clean vs execute-error,
    with the study|generate phase boundary marked. Shows synthesis is
    an execution-grounded error-recovery loop, never one-shot.
    Top 3 rows = SO-101 same task, 3 models (fair comparison);
    bottom 3 = 3 morphologies on Sonnet 4.5.
(b) Controlled SO-101 trio (Opus/DeepSeek/Haiku): iterations, hard
    errors, output tokens.

CAVEAT (must accompany any use): the trace corpus is SUCCESSFUL runs
only (failed-synthesis runs left no traces). So (a)/(b) characterize
what successful synthesis looks like; the complementary phase-
differentiated failure modes (study/IK/grasp) come from
canonical.yaml exp11 writes_api. Do not claim "all models recover."

Regenerate: python paper/figures_wip/plot_synth_trajectory.py
(reads from /tmp output path; edit savefig path before use).
