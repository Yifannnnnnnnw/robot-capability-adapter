# A1 overnight run — grasp-verified Self experiment

## Synthesis reliability (grasp_lift, verified pipeline)
- opus48       2/2 grasp-pass   [rr1:P  rr2:P]
- sonnet46     0/2 grasp-pass   [rr1:F  rr2:F]
- haiku45      1/2 grasp-pass   [rr1:F  rr2:P]
- novapro      0/0 grasp-pass   [rr1:-(HARDCODE!)  rr2:-]
- deepseek     1/2 grasp-pass   [rr1:F  rr2:P]
- ministral8b  0/1 grasp-pass   [rr1:F  rr2:-(HARDCODE!)]
- qwen32       0/0 grasp-pass   [rr1:-(HARDCODE!)  rr2:-(HARDCODE!)]

## A1 Self results (13-task, N=5, fixed graders)
- opus48       physics_pass=0.46153846153846156  driver=artifacts/from_scratch_xmodel/so101_opus48_rr1
- sonnet46     SYNTHESIS FAILED (no validated grasp driver)
- haiku45      physics_pass=0.46153846153846156  driver=artifacts/from_scratch_xmodel/so101_haiku45_rr2
- novapro      SYNTHESIS FAILED (no validated grasp driver)
- deepseek     physics_pass=0.32  driver=artifacts/from_scratch_xmodel/so101_deepseek_rr2
- ministral8b  SYNTHESIS FAILED (no validated grasp driver)
- qwen32       SYNTHESIS FAILED (no validated grasp driver)

NOTE: written to a NEW dir; canonical.yaml / paper / existing leaderboard_b are UNTOUCHED. Review before integrating.