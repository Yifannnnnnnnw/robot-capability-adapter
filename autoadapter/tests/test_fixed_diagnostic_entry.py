"""Focused runner boundary checks; model doubles are confined to this test."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fixed_diagnostic_entry', ROOT / 'scripts/run_fixed_family_diagnostic.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize('environment_ok', [True, False])
def test_basic_environment_controls_model_start_without_full_reference(monkeypatch, tmp_path, environment_ok):
    robot = 'franka_panda'
    output = tmp_path / 'run'
    monkeypatch.setattr(sys, 'argv', ['runner', '--robots', robot, '--output', str(output)])
    monkeypatch.setattr(runner, 'load_indexed_robot_package', lambda *a, **kw: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(runner, '_load_fixed_inputs', lambda *a, **kw: ({robot: {'design': {}, 'suite': {}}}, None))
    def reference(*a, **kw):
        pytest.fail('full reference must not gate normal synthesis')
    monkeypatch.setattr(runner, '_run_reference_positive_control', reference)
    def environment(*a, **kw):
        if not environment_ok:
            raise RuntimeError('no physical control response')
        return {'passed': True}
    monkeypatch.setattr(runner, 'check_environment', environment)
    calls = []
    def client(*a, **kw):
        calls.append('client')
        return SimpleNamespace(calls=[])
    monkeypatch.setattr(runner, 'model_client', client)
    def experiment(*a, **kw):
        assert kw['skip_reference_calibration'] is True
        calls.append('model')
        return {'final_capability_validation_passed': False, 'success': False, 'cells': []}
    monkeypatch.setattr(runner, 'run_experiment', experiment)
    runner.main()
    result = json.loads((output / 'diagnostic_summary.json').read_text())['robots'][robot]
    assert result['reference_executed'] is False
    assert result['reference_passed'] is None
    assert result['model_started'] is environment_ok
    assert calls == (['client', 'model'] if environment_ok else [])
    if not environment_ok:
        assert result['failure_stage'] == 'environment'


def test_holistic_config_requires_explicit_route_before_loading_credentials():
    config = runner.ExperimentConfig.from_path(ROOT / 'configs/diagnostics/fixed-family-v1-holistic-v32.json')
    with pytest.raises(ValueError, match='requires --holistic'):
        runner.model_client(config)


def test_reused_study_is_historical_and_makes_no_new_calls(tmp_path):
    """Recorded probe fixture tests bookkeeping, not new physics or model success."""
    from dataclasses import replace
    from autoadapter2.pipeline import _run_study_phase

    source = tmp_path / 'old-cell'
    package = SimpleNamespace(robot_configuration_id='franka_panda', package_version='1.0.0', morphology={})
    design = {'test': 'fixed-design'}
    output = {'robot_configuration_id': 'franka_panda', 'package_version': '1.0.0',
              'condition': 'skeleton-assisted', 'findings': ['fixture'],
              'implementation_plan': ['fixture'], 'probe_requests': [{'probe_id': 'p', 'script': 'fixture'}]}
    probe = {'exit_code': 0, 'physics_steps': 2, 'timed_out': False, 'spawn_error': None}
    runner.write(source / 'files/study.json', output)
    runner.write(source / 'design/capability_design.json', design)
    record = {'evidence': {'completed': True, 'model_call_count': 13}, 'probe_results': [probe]}
    runner.write(source / 'study_evidence.json', record)
    reused = runner.load_reused_study(source, package, design, 'skeleton-assisted')
    hooks = replace(runner.PipelineHooks(), study_runner=lambda *a, **kw: reused)
    client = SimpleNamespace(calls=[])
    config = runner.ExperimentConfig.from_path(ROOT / 'configs/diagnostics/fixed-family-v1-holistic-opus5-franka-one-repair.json')
    events = []
    _, probes = _run_study_phase(
        package=package, robot='franka_panda', condition='skeleton-assisted',
        config=config, client=client, experience=(), workspace=tmp_path / 'new-cell',
        hooks=hooks, stage_log=events, design=design,
    )
    assert client.calls == []
    assert events[0]['reused'] is True
    assert events[0]['attempted'] is False and events[0]['completed'] is False
    assert events[0]['model_call_count'] == 0
    assert events[0]['physics_executed_in_this_run'] is False
    assert probes[0]['reused'] is True
    assert json.loads((tmp_path / 'new-cell/files/study.json').read_text()) == output
    with pytest.raises(ValueError, match='capability design'):
        runner.load_reused_study(source, package, {'different': True}, 'skeleton-assisted')
    record['evidence']['completed'] = False
    runner.write(source / 'study_evidence.json', record)
    with pytest.raises(ValueError, match='did not complete'):
        runner.load_reused_study(source, package, design, 'skeleton-assisted')
