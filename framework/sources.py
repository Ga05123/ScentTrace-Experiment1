"""Native source readers. Read only; never execute recorded GUI/script actions."""
import ast
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def lines(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                yield number, json.loads(line)


def public_call(call):
    # Explicit allowlist: descriptions/CoT/conclusions are not executable arguments.
    allowed = {'action', 'coordinate', 'x', 'y', 'label', 'text', 'keys', 'key',
               'direction', 'amount', 'duration', 'url', 'button', 'clicks',
               'start_box', 'end_box', 'position', 'scroll_amount', 'answer',
               'query', 'time', 'fact', 'point', 'content', 'selector', 'href',
               'start_point', 'end_point', 'coordinate_system'}
    args = call.get('arguments', {})
    excluded = {'action_html', 'thought', 'thoughts', 'reasoning', 'description',
                'conclusion', 'reward', 'success'}
    unknown = set(args) - allowed - excluded
    if unknown:
        raise ValueError('Unmapped executable argument keys: ' + ','.join(sorted(unknown)))
    return {'name': call.get('name', args.get('action', 'unknown')),
            'arguments': {k: v for k, v in args.items() if k in allowed}}


def image(path, locator, modality='NATIVE_GUI'):
    p = Path(path) if path else None
    return {'path': str(p) if p and p.is_file() else None,
            'source_locator': locator, 'modality': modality,
            'status': 'PRESENT' if p and p.is_file() else 'MISSING'}


def event(i, raw_id, action, pre, post, locator, terminal=False, env='unknown'):
    return {'position': i, 'raw_step': raw_id, 'action': action,
            'pre': pre, 'post': post, 'source_locator': locator,
            'terminal_event': terminal, 'environment': env}


def resolve_image(root, value):
    if not value:
        return None
    p = Path(value)
    if p.is_absolute() and p.is_file():
        return p
    candidates = [root / p, root / p.name, root / 'trajectory' / p.name,
                  root / 'solver_log' / p.name, root / 'screenshots' / p.name]
    direct = {p.resolve() for p in candidates if p.is_file()}
    if len(direct) > 1:
        raise ValueError('Ambiguous native observation path in declared source root')
    if direct:
        return next(iter(direct))
    # Existing OM2W exports changed PNG encoding to JPEG while retaining native
    # observation stems. Exact unique stem only; never neighboring frame numbers.
    exported = {p.with_suffix(s) for p in candidates for s in ['.jpg', '.jpeg', '.png']
                if p.with_suffix(s).is_file()}
    if len(exported) > 1:
        raise ValueError('Ambiguous exported native observation filename')
    return next(iter(exported), None)


def public_agentnet_code(code):
    """Redact only an actual termination call's status, preserving compound actions."""
    tree = ast.parse(code)
    raw = code.encode('utf-8')
    lines = raw.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    replacements = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == 'computer'
                and call.func.attr == 'terminate'):
            continue
        if call.args or any(k.arg is None for k in call.keywords):
            raise ValueError('Unmapped positional/expanded termination arguments; preserve source for review')
        call.keywords = [k for k in call.keywords if k.arg != 'status']
        replacements.append((offsets[call.lineno-1]+call.col_offset,
                             offsets[call.end_lineno-1]+call.end_col_offset,
                             ast.unparse(call).encode('utf-8')))
    for first,last,replacement in sorted(replacements, reverse=True):
        raw = raw[:first] + replacement + raw[last:]
    return raw.decode('utf-8'), bool(replacements)


def agentnet(spec):
    found = [(n, r) for n, r in lines(spec['source']) if r.get('task_id') == spec['task_id']]
    if len(found) != 1:
        raise ValueError('AgentNet task source must be unique')
    line, row = found[0]
    steps = row['traj']
    events = []
    for i, s in enumerate(steps):
        code = s.get('value', {}).get('code')
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Missing native action code; not inferred')
        public, terminal = public_agentnet_code(code)
        pic = lambda step: resolve_image(Path(spec['image_root']), step.get('image'))
        pre = image(pic(s), {'jsonl_line': line, 'traj_offset': i, 'field': 'image'})
        post = image(pic(steps[i+1]) if i+1 < len(steps) else None,
                     {'jsonl_line': line, 'traj_offset': i+1, 'field': 'image'})
        events.append(event(i, s.get('index', i), {'code': public}, pre, post,
                            {'path': spec['source'], 'jsonl_line': line,
                             'traj_offset': i, 'field': 'value.code',
                             'termination_status_redacted': terminal}, terminal, spec.get('system', 'unknown')))
    return row['instruction'], {'path': spec['source'], 'jsonl_line': line, 'field': 'instruction'}, events


def om2w(spec):
    root = Path(spec['source_root'])
    summary = read(root / 'summary.json')
    if summary['task_id'] != spec['task_id']:
        raise ValueError('OM2W task mismatch')
    instruction = summary['task']
    if not isinstance(instruction, str):
        raise ValueError('OM2W instruction is not a literal string')
    events = []
    if spec['actor_id'] == 'fara15_9b':
        source = root / 'solver_log/events.jsonl'
        rows = list(lines(source))
        actions = [(n, r) for n, r in rows if r.get('type') == 'action']
        for i, (n, r) in enumerate(actions):
            previous = [(nn, o) for nn, o in rows if nn < n and o.get('type') == 'observation' and o.get('screenshot_path')]
            # Bound post to this action and stop before the next action. No N-1 indexing.
            next_n = actions[i+1][0] if i+1 < len(actions) else float('inf')
            after = [(nn, o) for nn, o in rows if n < nn < next_n
                     and o.get('type') == 'observation' and o.get('action_id') == r.get('id')
                     and o.get('screenshot_path')]
            def obs(pair):
                return image(resolve_image(root, pair[1]['screenshot_path']) if pair else None,
                             {'path': str(source), 'event_line': pair[0] if pair else None,
                              'native_filename': pair[1]['screenshot_path'] if pair else None,
                              'export_rule': 'Exact native stem; existing JPEG export allowed, no frame offset'})
            content = r.get('content')
            if not isinstance(content, dict):
                raise ValueError('Fara executable content missing; do not parse thought')
            action = public_call({'name': r.get('action_name'), 'arguments': content.get('arguments', {})})
            terminal = r.get('action_name', '').lower() in {'answer', 'terminate', 'done'}
            events.append(event(i, r['id'], action, obs(previous[-1] if previous else None),
                                obs(after[0] if after else None),
                                {'path': str(source), 'event_line': n, 'action_id': r['id']}, terminal, 'browser'))
    else:
        source = root / 'rollout_0/action_info.json'
        actions = read(source)
        summary_actions = {int(a['step']): a for a in summary['actions']}
        seen = set()
        for i, r in enumerate(actions):
            it = int(r['it'])
            if it in seen:
                raise ValueError('Duplicate native action it')
            seen.add(it)
            action = public_call(r['info']['tool_call'])
            # These action_info images are model-input pre images, not action effects.
            if it not in summary_actions:
                raise ValueError('Native action it absent from summary image mapping')
            pre = image(resolve_image(root, summary_actions[it].get('screenshot')), {'path': str(root / 'summary.json'), 'it': it, 'role': 'native_model_input'})
            nxt = int(actions[i+1]['it']) if i+1 < len(actions) else None
            post = image(resolve_image(root, summary_actions[nxt].get('screenshot')) if nxt is not None else None,
                         {'path': str(root / 'summary.json'), 'it': nxt, 'role': 'next_native_model_input'})
            terminal = action['arguments'].get('action') in {'answer', 'terminate', 'done'}
            events.append(event(i, it, action, pre, post, {'path': str(source), 'array_offset': i, 'it': it}, terminal, 'browser'))
    return instruction, {'path': str(root / 'summary.json'), 'field': 'task'}, events


def osworld(spec):
    # Literal extraction only. Never import or execute a task file.
    tree = ast.parse(Path(spec['instruction_source']).read_text(encoding='utf-8-sig'))
    literals = [n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'instruction' for t in n.targets)
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
    if len(literals) != 1:
        raise ValueError('Dynamic OSWorld instruction needs an explicit verified runtime source')
    root = Path(spec['source']).parent
    rows = list(lines(spec['source']))
    events = []
    previous = image(root / 'step_0_initial.png', {'path': str(root / 'step_0_initial.png')})
    for i, (n, r) in enumerate(rows):
        a = r.get('action', {})
        allowed = {k: a[k] for k in ['action_space', 'action_type', 'action', 'command',
                                    'batch_index', 'batch_size', 'batch_last'] if k in a}
        post = image(resolve_image(root, r.get('screenshot_file')), {'path': spec['source'], 'jsonl_line': n, 'field': 'screenshot_file'})
        terminal = a.get('action_type') in {'DONE', 'FAIL'}
        events.append(event(i, n, allowed, previous, post,
                            {'path': spec['source'], 'jsonl_line': n, 'source_turn': r.get('step_num')}, terminal, 'desktop'))
        previous = post
    return literals[0], {'path': spec['instruction_source'], 'field': 'literal instruction'}, events


def webchain(spec):
    """Read the existing complete MC evidence export; never ingest synthetic CoT.

    Every original action row is retained. Pre is its matched SFT observation;
    post is only the immediately following original row's pre, never next available.
    Seed coordinates remain in the released 0..1000 coordinate system.
    """
    source = Path(spec['source'])
    root = Path(spec['evidence_root'])
    record = read(source)
    if record['trace_id'] != spec['task_id'] or record['n_raw_actions'] != len(record['steps']):
        raise ValueError('WebChain trace/action identity mismatch')
    steps = record['steps']
    if [s['source_step_index'] for s in steps] != list(range(len(steps))):
        raise ValueError('WebChain original step gap/order mismatch')
    observations = []
    for s in steps:
        img = s['input_screenshot']
        if img and img['source_step_index'] != s['source_step_index']:
            raise ValueError('WebChain screenshot/action identity mismatch')
        path = root / img['image_path'] if img else None
        if path and not path.is_file():
            raise ValueError('WebChain mapped screenshot missing')
        observations.append(image(path, {'path':str(source),'source_step_index':s['source_step_index'],
            'field':'input_screenshot','timing':'PRE_ACTION','window_uid':img['window_uid'] if img else None}))
    events = []
    for i,s in enumerate(steps):
        args=json.loads(s['seed_parameters_json']) if s['seed_parameters_json'] else {}
        if any(k in args for k in ('point','start_point','end_point')):
            args['coordinate_system']='SEED_NORMALIZED_0_1000'
        if s['source_selector']:
            args['selector']=s['source_selector']
        if s['source_href']:
            args['href']=s['source_href']
        action=public_call({'name':s['native_action'],'arguments':args})
        post=observations[i+1] if i+1<len(steps) else image(None,{'timing':'NO_NEXT_ORIGINAL_ROW'})
        events.append(event(i,s['source_step_index'],action,observations[i],post,
            {'path':str(source),'source_step_index':s['source_step_index'],
             'seed_function':s['seed_function'],'parameter_status':'RELEASED_SEED_PARAMETERS' if s['seed_parameters_json'] else 'PARTIAL_ORIGINAL_METADATA',
             'coordinate_system':'SEED_NORMALIZED_0_1000','post_binding':'NEXT_ORIGINAL_ROW_PRE',
             'dom_source_url':s['dom_source_url'],'ax_source_url':s['ax_source_url']},False,'web'))
    return record['goal'],{'path':str(source),'field':'goal','cohort':'Multi-Constraint'},events


def load(spec):
    instruction, locator, events = {'AgentNet': agentnet, 'OM2W': om2w,
                                    'OSWorld': osworld, 'WebChain':webchain}[spec['dataset']](spec)
    if not events:
        raise ValueError('Empty source trajectory')
    tr = {'source_format': 'E1_NATIVE_SOURCE_V2', 'dataset': spec['dataset'], 'task_id': spec['task_id'],
            'trajectory_id': spec['trajectory_id'], 'actor_id': spec['actor_id'],
            'family_id': spec.get('family_id', spec['task_id']),
            'split': spec['split'], 'instruction': instruction,
            'instruction_source': locator, 'events': events,
            'recording_scope': 'ALL_NATIVE_RECORDED_EVENTS',
            'terminal_event_present': any(e['terminal_event'] for e in events),
            'natural_search_bout_end_certified': False}

    if spec['dataset']=='WebChain':
        tr['cohort']='Multi-Constraint'
    for ev in tr['events']:
        content = ev['action']
        is_answer = content.get('arguments', {}).get('action') == 'answer' or content.get('name', '').lower() == 'answer'
        kind = ('PUBLIC_ANSWER' if is_answer else 'CONTROL' if ev['terminal_event'] else
                'CODE' if 'code' in content or 'action' in content else
                'COMMAND' if 'command' in content else 'TOOL_CALL')
        ev['action'] = {'kind': kind, 'content': content}
    return tr
