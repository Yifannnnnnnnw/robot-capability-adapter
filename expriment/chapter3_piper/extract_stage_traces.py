"""Extract existing PiPER stage traces for the process figure; makes no model calls.

Run from any working directory with Python 3. Source records are read-only.
Each tile is one logged model turn, not a tool call or validation case.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'expriment/chapter3_piper/data/runs'


def read_json(path):
    return json.loads(path.read_text())


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def relative_path(path):
    return str(Path(path).relative_to(ROOT))


def stage_name(name):
    if name == 'design':
        return 'Design'
    if 'study' in name:
        return 'Study'
    if 'generate' in name:
        return 'Generate'
    if 'repair' in name:
        return 'Repair ' + name.rsplit('_', 1)[1]
    if 'validate' in name:
        return 'Validation'
    if 'export' in name:
        return 'Export'
    return name


def main():
    out = {
        'unit': 'one recorded model invocation/response turn; multiple tool calls may occur in one turn; validation suite markers consume zero model-turn width',
        'error_rule': 'explicit observation is_error=true OR structured local_exec exit_code != 0; no inference from text; physical case/task failures are separate',
        'turn_category_rule': 'error if any explicit error; else execute if any local_exec or probe_case; else write if any write_file; else read_plan (includes no-tool terminal response). Actions retained for optional subdivisions.',
        'recap_scope': 'one fixed episode per exported driver: pick_place/L1; no pooling across nine episodes',
        'limitations': [
            'ReAct duration_ms covers model invocation, excluding tool execution. Use phase duration_sec for elapsed pipeline duration.',
            'ReCAP plan_revision includes automatic plans and is not an LLM-call count.',
            'The recorded ReCAP planning turns have no direct capability-call IDs; their category is recap_plan and call outcomes are reported separately.',
            'Tool subprocess errors do not imply driver or task failure. A normal capability return does not independently establish physical task success.',
            'The disabled zero-duration mainline demo placeholder is omitted; experiment ReCAP episode is appended separately.',
        ],
        'runs': [],
    }
    for i in range(1, 6):
        run = f'run_{i:02d}'
        g = DATA / run / 'generation/piper'
        s = read_json(g / 'summary.json')
        rr = {
            'run': run,
            'robot': 'PiPER',
            'model': 'eu.anthropic.claude-opus-4-8',
            'summary_source': relative_path(g / 'summary.json'),
            'phases': [],
            'recap': None,
        }
        offset = 0
        for p in s['phases']:
            if p['name'] == 'demo':
                continue
            ph = {
                'name': p['name'],
                'stage': stage_name(p['name']),
                'ok': p['ok'],
                'duration_sec': p['duration_sec'],
                'tokens': p['token_usage'],
                'model_turn_offset': offset,
                'turns': [],
            }
            if p['trace_path']:
                f = Path(p['trace_path'])
                rows = read_jsonl(f)
                msg = read_jsonl(f.with_suffix('.messages.jsonl'))
                full = {x['iter']: x['results'] for x in msg if x.get('event') == 'tool_results'}
                ph['trace_source'] = relative_path(f)
                req = next((x for x in msg if x.get('event') == 'request'))
                ph['model'] = req['model']
                ph['provider'] = req['provider']
                for row in rows:
                    obs = full.get(row['iter'], row['observations'])
                    assert len(obs) == len(row['actions']) or not row['actions']
                    actions = []
                    for a, o in zip(row['actions'], obs):
                        content = o.get('content')
                        payload = None
                        if isinstance(content, str):
                            try:
                                payload = json.loads(content)
                            except (ValueError, TypeError):
                                pass
                        exit_code = payload.get('exit_code') if isinstance(payload, dict) else None
                        err = bool(
                            o.get('is_error') is True
                            or (
                                a['name'] == 'local_exec'
                                and isinstance(exit_code, int)
                                and exit_code != 0
                            )
                        )
                        if a['name'] in ('local_exec', 'probe_case'):
                            cat = 'execute'
                        elif a['name'] == 'write_file':
                            cat = 'write'
                        else:
                            cat = 'read_plan'
                        actions.append({
                            'name': a['name'],
                            'category': cat,
                            'is_error': o.get('is_error', False),
                            'exit_code': exit_code,
                            'error': err,
                        })
                    is_err = any((a['error'] for a in actions)) or row.get('stop_reason') == 'invoke_error'
                    cats = {a['category'] for a in actions}
                    if is_err:
                        cat = 'error'
                    elif 'execute' in cats:
                        cat = 'execute'
                    elif 'write' in cats:
                        cat = 'write'
                    else:
                        cat = 'read_plan'
                    ph['turns'].append({
                        'iter': row['iter'],
                        'category': cat,
                        'error': is_err,
                        'actions': actions,
                        'tokens': row['token_usage'],
                        'stop_reason': row['stop_reason'],
                        'duration_ms_model_only': row['duration_ms'],
                    })
                ph['n_turns'] = len(rows)
                ph['n_tool_calls'] = sum((len(r['actions']) for r in rows))
                ph['n_error_turns'] = sum((t['error'] for t in ph['turns']))
                ph['n_error_tool_calls'] = sum((a['error'] for t in ph['turns'] for a in t['actions']))
                offset += len(rows)
                ph['trace_tokens_sum'] = {k: sum((t['tokens'].get(k, 0) for t in ph['turns'])) for k in ['in', 'out']}
                assert all((ph['trace_tokens_sum'][k] == ph['tokens'][k] for k in ['in', 'out']))
            else:
                v = p.get('metadata', {}).get('validation_report', {})
                ph.update(n_turns=0, n_tool_calls=0, n_error_turns=0, n_error_tool_calls=0)
                ph['validation'] = {
                    'n_passed': v.get('n_passed'),
                    'n_total': v.get('n_total'),
                    'all_ok': v.get('all_ok'),
                    'cases': [{
                        'case_id': t['case_id'],
                        'capability_id': t['capability_id'],
                        'ok': t['ok'],
                        'error': t.get('error'),
                        'result_source': relative_path(t['paths']['result']),
                    } for t in v.get('tests', [])],
                }
            if ph['stage'] != 'Validation':
                ph.pop('validation', None)
            if ph['stage'] == 'Export' and ph['n_turns'] == 0:
                ph['not_executed'] = True
                ph['skip_reason'] = 'validation not passed'
            rr['phases'].append(ph)
        rr['generation_model_turns'] = offset
        rr['exported'] = any((p['stage'] == 'Export' and p['ok'] for p in rr['phases']))
        rr['generation_error_turns'] = sum((p['n_error_turns'] for p in rr['phases']))
        rr['generation_tool_calls'] = sum((p['n_tool_calls'] for p in rr['phases']))
        rr['generation_tokens'] = {k: sum((p['tokens'].get(k, 0) for p in rr['phases'])) for k in ['in', 'out']}
        t = DATA / run / 'episodes/pick_place/L1/task'
        if (t / 'task_report.json').exists():
            r = read_json(t / 'task_report.json')
            models = read_jsonl(t / 'model_turns.jsonl')
            messages = read_jsonl(t / 'model_messages.jsonl')
            events = read_jsonl(t / 'trace.jsonl')
            turns = []
            assert len(models) == len(messages) == r['controller_result']['planning_turns']
            for n, (m, mm) in enumerate(zip(models, messages)):
                try:
                    response = json.loads(m['response'])
                    subs = response['subtasks']
                    kind = 'completion' if not subs else 'decomposition' if len(subs) > 1 else 'singleton_plan'
                except (ValueError, TypeError, KeyError):
                    kind = 'invalid_json_or_schema'
                turns.append({
                    'iter': n,
                    'category': 'recap_plan',
                    'decision_kind': kind,
                    'tokens': {'in': mm['usage']['input_tokens'], 'out': mm['usage']['output_tokens']},
                    'error': None,
                    'capability_outcome_mapping': 'not assigned',
                })
            calls = [{
                'capability_name': e['capability_name'],
                'operation_status': e['feedback'].get('operation', {}).get('status'),
                'error': e['feedback'].get('operation', {}).get('status') != 'EXECUTED',
            } for e in events if e['event'] == 'capability_result']
            rr['recap'] = {
                'stage': 'ReCAP',
                'episode': 'pick_place/L1',
                'source': relative_path(t / 'task_report.json'),
                'model_trace_source': relative_path(t / 'model_turns.jsonl'),
                'model_messages_source': relative_path(t / 'model_messages.jsonl'),
                'event_trace_source': relative_path(t / 'trace.jsonl'),
                'model_turn_offset': offset,
                'turns': turns,
                'n_turns': len(turns),
                'n_capability_calls': r['controller_result']['capability_calls'],
                'invalid_outputs': r['controller_result']['invalid_outputs'],
                'operation_results': calls,
                'physical_task_success': r['physical_task_success'],
                'execution_ok': r['execution_ok'],
                'controller_status': r['status'],
                'tokens': {k: sum((z['tokens'][k] for z in turns)) for k in ['in', 'out']},
                'duration_sec': r['duration_sec'],
                'plan_revision_events': sum((e['event'] == 'plan_revision' for e in events)),
            }
            assert len(calls) == rr['recap']['n_capability_calls']
        out['runs'].append(rr)
    p = DATA / 'figures/exp1_stage_traces_data.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    print(p)
    for r in out['runs']:
        print(
            r['run'],
            'turns', r['generation_model_turns'],
            'errors', r['generation_error_turns'],
            'toolcalls', r['generation_tool_calls'],
            'phases', [
                (phase['stage'], phase['n_turns'], phase['n_error_turns'])
                for phase in r['phases']
            ],
            'ReCAP', {
                key: r['recap'][key]
                for key in [
                    'n_turns', 'n_capability_calls', 'tokens', 'physical_task_success'
                ]
            } if r['recap'] else None,
        )


if __name__ == '__main__':
    main()
