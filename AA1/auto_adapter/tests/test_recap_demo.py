"""Focused checks for the AA1-to-canonical-ReCAP demo bridge."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter.agent.recap import AA1CapabilityAdapter, AA1RecapModel, passed_design
from auto_adapter.agent.recap import run_recap


def test_only_complete_current_design_capabilities_reach_real_recap():
    design = {'robot_configuration_id': 'fixture', 'task_snapshot_id': 'current-fixture',
              'capabilities': [{'capability_id': 'C1', 'method_name': 'advance',
                                'description': 'Advance physics.',
                                'request_schema': {'type': 'object'}}]}
    cases = [{'case_id': 'nominal', 'capability_id': 'C1', 'method_name': 'advance',
              'request': {'steps': 1}},
             {'case_id': 'boundary', 'capability_id': 'C1', 'method_name': 'advance',
              'request': {'steps': 2}}]
    suite = {'scene_cases': cases}
    tests = [{'case_id': c['case_id'], 'ok': True} for c in cases]
    with pytest.raises(ValueError, match='no fully Framework-passed'):
        passed_design(design, suite, {'tests': tests[:-1]})
    selected = passed_design(design, suite, {'tests': tests})
    seen = []
    def invoke(name, envelope):
        seen.append((name, envelope))
        return {'operation': {'status': 'EXECUTED'}, 'observations': {}}
    class FixtureModel:
        def generate_json(self, *, messages):
            assert messages and all(isinstance(message['content'], str) for message in messages)
            action = json.dumps({'capability_name': cases[0]['method_name'],
                                 'request': cases[0]['request']})
            return json.dumps({'think': 'Execute the selected capability.',
                               'subtasks': [action]})
    result = run_recap(public_task={'description': 'fixture'},
                       adapter=AA1CapabilityAdapter(selected, invoke), model=FixtureModel())
    assert result.status == 'CONTROLLER_FINISHED'
    assert seen == [(cases[0]['method_name'], {'request': cases[0]['request']})]


def test_model_bridge_preserves_official_json_history_and_records_request_response(tmp_path):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[
            SimpleNamespace(type='text', text='{"think":"Finished.",'),
            SimpleNamespace(type='text', text='"subtasks":[]}')])
    model = object.__new__(AA1RecapModel)
    model.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    model.model, model.max_tokens, model.trace_path = 'fixture', 100, tmp_path/'trace.jsonl'
    messages = [
        {'role':'system','content':'rules'},
        {'role':'user','content':'task'},
        {'role':'assistant','content':'{"think":"Move.","subtasks":["move"]}'},
        {'role':'user','content':'observed'},
        {'role':'user','content':'revise the parent plan'}]
    response = model.generate_json(messages=messages)
    assert 'rules' in captured['system']
    assert 'brief plan summary' in captured['system']
    assert captured['messages'] == [messages[1], messages[2],
                                   {'role':'user','content':'observed\n\nrevise the parent plan'}]
    assert 'tools' not in captured
    assert json.loads(response) == {'think': 'Finished.', 'subtasks': []}
    assert json.loads(model.trace_path.read_text()) == {'messages': messages, 'response': response}


def test_local_dynamic_demo_dispatches_to_recap():
    from auto_adapter.orchestrator import SelfAssemble
    runner = object.__new__(SelfAssemble)
    runner.cfg = SimpleNamespace(mode='local')
    runner.capability_design = {'capabilities': ['fixture']}
    runner._phase_recap_demo = lambda: 'canonical recap'
    assert runner._phase_demo() == 'canonical recap'
