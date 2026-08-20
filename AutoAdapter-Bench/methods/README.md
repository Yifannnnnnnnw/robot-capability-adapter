# Published Methods

After the paper/code audit, each admitted B2 method receives one directory:

```text
<method_id>/
  SOURCE.md       # paper, public code, pinned revision, and declared deviations
  method.json     # backbone mode, valid B_a or fixed identity, and native budgets
  adapter.py      # public observation and capability-call mapping only
```

An adapter may map names, request fields, and mechanically equivalent provider
schemas. It may not add planning, memory, recovery, scoring, or private Harness
state absent from the published method.

The initial audit pool is ReAct as a historical anchor, Inner Monologue,
Interactive Task Planning with Language Models, LLM as BT-Planner, Code as
Policies, UHBTP, and the conditional SayCan, Code-BT, HBTP, and fine-tuned BT
planner candidates defined by `../benchmark.md`.
