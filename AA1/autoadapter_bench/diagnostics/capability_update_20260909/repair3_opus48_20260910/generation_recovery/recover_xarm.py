import json, shutil, time
from dataclasses import asdict
from pathlib import Path
from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig
from auto_adapter.robot_catalog import REPO_ROOT, find_robot_definition
base=REPO_ROOT/'autoadapter_bench/diagnostics/capability_update_20260909'
source=base/'generated_resume1/ufactory_xarm7'
root=base/'repair3_opus48_20260910/generation_recovery'
robot='ufactory_xarm7'
ws=root/robot
if ws.exists():
    raise SystemExit('Refusing to overwrite existing generation recovery')
robotdef=find_robot_definition(robot)
config=SelfAssembleConfig(robot_id=robot,mjcf_path=REPO_ROOT/robotdef.get('capability_mjcf',robotdef['mjcf']),workspace_root=root,mode='local',model_provider='holistic',bedrock_model='eu.anthropic.claude-opus-4-8',max_iters_generate=22,max_outer_gen_val_iters=1)
runner=SelfAssemble(config)
shutil.copy2(source/'study.json',ws/'study.json')
context={'robot_id':robot,'provider':'holistic','requested_model':config.bedrock_model,'kind':'generation_recovery_from_saved_study','source_study':str(source/'study.json'),'max_generation_turns':22,'framework_repairs_used':0,'actual_generation_cost_usd':None,'started_at_unix':time.time()}
(ws/'run_context.json').write_text(json.dumps(context,indent=2))
started=time.time()
try:
    phase=runner._phase_generate()
    summary=asdict(phase)
    summary['kind']=context['kind']
    (ws/'generation_summary.json').write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps({'robot':robot,'generation_ok':phase.ok,'duration_sec':phase.duration_sec,'tokens':phase.token_usage,'error':phase.error}),flush=True)
    if phase.ok:
        validation=runner._phase_validate()
        (ws/'validation_phase.json').write_text(json.dumps(asdict(validation),indent=2,default=str))
        print(json.dumps({'robot':robot,'framework_ok':validation.ok,'detail':validation.final_text}),flush=True)
except Exception as exc:
    (ws/'recovery_error.json').write_text(json.dumps({'error':f'{type(exc).__name__}: {exc}','duration_sec':time.time()-started},indent=2))
    raise
