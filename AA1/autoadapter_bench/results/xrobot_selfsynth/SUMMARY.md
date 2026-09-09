# Cross-model self-synthesis — non-gripper morphologies (FULL)

## Phase 1: synthesis reliability (validated, non-hardcoded driver?)
| robot | opus48 | sonnet46 | haiku45 | novapro | deepseek | ministral8b | qwen32 |
|---|---|---|---|---|---|---|---|
| go2 | 1/2 | 1/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |
| skydio_x2 | 2/2 | 2/2 | 1/2 | 0/2 | 1/2 | 0/2 | 0/2 |
| h1 | 1/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 | 0/2 |

## Phase 2: task success of the synthesized driver (simple+hard, N=5, physics)
| robot | opus48 | sonnet46 | haiku45 | novapro | deepseek | ministral8b | qwen32 |
|---|---|---|---|---|---|---|---|
| go2 | 0.70 | 0.82 | — | — | — | — | — |
| skydio_x2 | 1.00 | 0.95 | 1.00 | — | 0.82 | — | — |
| h1 | 0.50 | — | — | — | — | — | — |

Phase-1 k/N excludes hardcoded drivers (skeleton import / FL-FR-RL-RR) and
Bedrock-timeout blips (those were re-run). '—' in Phase 2 = synthesis failed.
NEW dir; canonical/paper/existing leaderboard_b UNTOUCHED.