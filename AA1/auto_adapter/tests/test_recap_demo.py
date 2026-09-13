"""Focused checks for the AA1-to-canonical-ReCAP demo bridge."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter.agent.recap_demo import AA1CapabilityAdapter, AA1RecapModel, passed_design
from auto_adapter.robot_catalog import find_robot_definition, load_capability_design, load_capability_suite
from auto_adapter.agent.recap import ToolCall, ToolTurn, run_recap


@pytest.mark.parametrize('robot_id', ['so101', 'universal_robots_ur5e_robotiq_2f85'])
def test_only_complete_framework_capabilities_reach_real_recap(robot_id):
    robot = find_robot_definition(robot_id)
    design, suite = load_capability_design(robot), load_capability_suite(robot)
    cases = [c for c in suite['cases'] if c['capability_id'] == 'A1']
    tests = [{'case_id': c['case_id'], 'ok': True} for c in cases]
    with pytest.raises(ValueError, match='no fully Framework-passed'):
        passed_design(design, suite, {'tests': tests[:-1]})
    selected = passed_design(design, suite, {'tests': tests})
    seen = []
    def invoke(name, envelope):
        seen.append((name, envelope))
        return {'operation': {'status': 'EXECUTED'}, 'observations': {}}
    class FixtureModel:
        def generate_tool_turn(self, **kwargs):
            assert [t['function']['name'] for t in kwargs['tools']] == ['submit_plan']
            args = {'reasoning_summary': 'Execute the selected capability.',
                    'subtasks': ([] if seen else [{
                        'kind': 'capability', 'capability_name': cases[0]['method_name'],
                        'request': cases[0]['request']}])}
            return ToolTurn(None, (ToolCall(str(len(seen)), 'submit_plan', args, json.dumps(args)),))
    result = run_recap(public_task={'description': 'fixture'},
                       adapter=AA1CapabilityAdapter(selected, invoke), model=FixtureModel())
    assert result.status == 'CONTROLLER_FINISHED'
    assert seen == [(cases[0]['method_name'], {'request': cases[0]['request']})]


def test_model_bridge_preserves_tool_ids_and_observations(tmp_path):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type='tool_use', id='finish-1', name='finish', input={})], stop_reason='tool_use')
    model = object.__new__(AA1RecapModel)
    model.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    model.model, model.max_tokens, model.trace_path = 'fixture', 100, tmp_path/'trace.jsonl'
    turn = model.generate_tool_turn(stage='fixture', system_prompt='rules', tools=[], messages=[
        {'role':'user','content':'task'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'a','function':{'name':'move','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'a','content':'observed'}])
    assert captured['messages'][1]['content'][0]['id'] == 'a'
    assert captured['messages'][2]['content'][0] == {'type':'tool_result','tool_use_id':'a','content':'observed'}
    assert turn.tool_calls[0].name == 'finish'


def test_local_catalog_demo_dispatches_to_recap():
    from auto_adapter.orchestrator import SelfAssemble
    runner = object.__new__(SelfAssemble)
    runner.cfg = SimpleNamespace(mode='local')
    runner.capability_design = {'capabilities': ['fixture']}
    runner._phase_recap_demo = lambda: 'canonical recap'
    assert runner._phase_demo() == 'canonical recap'
