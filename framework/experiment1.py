"""One small-batch entry point: native sources -> repeats -> goals -> alignment -> operator.

Recorded actions are never executed. Inference is opt-in; the delivered trial uses
explicitly attributed Codex researcher responses, never relabelled as Qwen/Human Gold.
"""
import argparse
import ast
import base64
import collections
import hashlib
import io
import json
import os
import re
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from PIL import Image
from jsonschema import Draft202012Validator
import api_backend
import sources

HERE = Path(__file__).resolve().parent


def validate_schema(value, schema):
    """Validate the full JSON Schema; retain concrete structural error messages."""
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int, 'null': type(None)}
    expected = schema.get('type')
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(type(value) is kinds[t] for t in allowed):
            raise ValueError('Schema type mismatch: ' + str(expected))
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Schema enum mismatch')
    if isinstance(value, dict) and 'properties' in schema:
        if set(schema.get('required', [])) - set(value):
            raise ValueError('Missing required schema fields')
        if schema.get('additionalProperties') is False and set(value) - set(schema['properties']):
            raise ValueError('Unexpected schema fields')
        for key, item in value.items():
            if key in schema['properties']:
                validate_schema(item, schema['properties'][key])
    if isinstance(value, list) and 'items' in schema:
        if len(value) < schema.get('minItems', 0):
            raise ValueError('Too few schema items')
        for item in value:
            validate_schema(item, schema['items'])
    if isinstance(value, str) and len(value) < schema.get('minLength', 0):
        raise ValueError('Empty required text')
    if type(value) is int and value < schema.get('minimum', value):
        raise ValueError('Integer below schema minimum')
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda err: str(list(err.path)))
    if errors:
        error = errors[0]
        raise ValueError('JSON Schema at ' + str(list(error.path)) + ': ' + error.message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def implementation_fingerprints():
    return {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ['experiment1.py', 'sources.py', 'schemas.json',
                         'prompts/goals.md', 'prompts/alignment.md',
                         'prompts/operator.md', 'OPERATOR_CODEBOOK.md',
                         'GOAL_CODEBOOK.md', 'ALIGNMENT_CODEBOOK.md', 'api_backend.py',
                         'source_schema.json', 'run_batch.py', 'resolve_sources.py']}


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_lines(path, values):
    Path(path).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values), encoding='utf-8')


def read_lines(path):
    return [r for _, r in sources.lines(path)]


def join_units(tr, confirmations):
    events = tr['events']
    approvals = {a['first_position']: a for a in confirmations if a['trajectory_id'] == tr['trajectory_id']}
    occupied = set()
    for a in approvals.values():
        first, last = a['first_position'], a['last_position']
        if not (0 <= first < last < len(events)) or not a.get('reason') or not a.get('evidence_refs'):
            raise ValueError('Repeat confirmation needs a valid nontrivial range and evidence')
        members = events[first:last+1]
        if any(e['terminal_event'] for e in members) or len({digest(e['action']) for e in members}) != 1:
            raise ValueError('Different actions/end events cannot be merged as repeats')
        if len({e['environment'] for e in members}) != 1 or any(e['pre']['status'] != 'PRESENT' or e['post']['status'] != 'PRESENT' for e in members):
            raise ValueError('Repeat environment/native evidence missing')
        valid_refs = {str(e['raw_step']) for e in events}
        if not set(a['evidence_refs']).issubset(valid_refs):
            raise ValueError('Repeat evidence references unknown source steps')
        span = set(range(first, last+1))
        if occupied & span:
            raise ValueError('Repeat confirmations overlap')
        occupied |= span
    units, audit = [], []
    i = 0
    while i < len(events):
        approval = approvals.get(i)
        last = approval['last_position'] if approval else i
        members = events[i:last+1]
        units.append({'unit_index': len(units), 'unit_id': f"{tr['trajectory_id']}::R{len(units):04d}",
                      'members': members, 'raw_steps': [e['raw_step'] for e in members],
                      'merge_status': 'SOURCE_REVIEW_CONFIRMED_REPEAT' if approval else 'SINGLE_SOURCE_EVENT',
                      'merge_review': approval})
        if approval:
            audit.append({'positions': [i, last], 'status': 'MERGED_WITH_ALL_MEMBERS_RETAINED', 'review': approval})
        i = last+1
    for a, b in zip(events, events[1:]):
        if digest(a['action']) == digest(b['action']) and not a['terminal_event']:
            audit.append({'positions': [a['position'], b['position']],
                          'status': 'REVIEWED_MERGE' if a['position'] in occupied and b['position'] in occupied
                          else 'RETAINED_REPEAT_CANDIDATE_NEEDS_EVIDENCE',
                          'reason': 'Identical action is a candidate only; same page/pixels do not establish same semantic effect.'})
    if [e['position'] for u in units for e in u['members']] != list(range(len(events))):
        raise ValueError('Raw member loss/reorder')
    return units, audit


def validate_source(tr):
    validate_schema(tr, sources.read(HERE / 'source_schema.json'))
    if [e['position'] for e in tr['events']] != list(range(len(tr['events']))):
        raise ValueError('Normalized source events must be ordered and contiguous')
    if len({str(e['raw_step']) for e in tr['events']}) != len(tr['events']):
        raise ValueError('Normalized raw step identifiers must be unique')
    for e in tr['events']:
        for role in ('pre', 'post'):
            obs = e[role]
            if (obs['status'] == 'PRESENT') != bool(obs['path']):
                raise ValueError('Normalized observation status/path disagreement')
            if obs['path'] and not Path(obs['path']).is_file():
                raise ValueError('Normalized native observation file missing')
        content = e['action']['content']
        if 'name' in content and sources.public_call(content) != content:
            raise ValueError('Normalized tool call contains nonpublic fields')
        if set(content) - {'code','name','arguments','action_space','action_type','action','command','batch_index','batch_size','batch_last'}:
            raise ValueError('Normalized action contains unsupported metadata')


def evidence_units(tr):
    units, bindings = [], {}
    for u in tr['units']:
        members = []
        for i, e in enumerate(u['members']):
            prefix = f"U{u['unit_index']:04d}.M{i:02d}"
            ref = prefix + '.action'
            bindings[ref] = {'source_locator': e['source_locator'], 'raw_step': e['raw_step']}
            item = {'member_index': i, 'action': e['action'], 'action_ref': ref,
                    'terminal_event': e['terminal_event']}
            for role in ('pre','post'):
                obs = e[role]
                ref = prefix + '.' + role
                bindings[ref] = dict(obs, raw_step=e['raw_step'])
                item[role] = {'status': obs['status'], 'modality': obs['modality'], 'evidence_ref': ref}
            members.append(item)
        units.append({'unit_index': u['unit_index'], 'members': members})
    return units, bindings


def model_output(record):
    return {k:v for k,v in record['output'].items() if k not in {'task_id','trajectory_id'}}


def source_segments(instruction):
    """Source locators only: preserve every character, without identifying Goals."""
    boundaries = [0] + [m.end() for m in re.finditer(
        r'(?<=[.!?。！？])[ \t]+(?=[A-Z\u4e00-\u9fff])|(?:\r?\n){2,}', instruction)]
    if boundaries[-1] != len(instruction):
        boundaries.append(len(instruction))
    return [{'source_id': f'S{i+1}', 'text': instruction[a:b]}
            for i, (a,b) in enumerate(zip(boundaries, boundaries[1:])) if a < b]


def compact_code(code):
    """Display literal top-level press runs; never execute or infer loop counts."""
    try:
        parsed = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    calls = {statement.lineno: statement.value for statement in parsed.body
             if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call)
             and statement.col_offset==0 and statement.lineno==statement.end_lineno}
    parts, keys, raw = [], [], []
    def flush_keys():
        if not keys:
            return
        runs = []
        for key in keys:
            if len(key) == 1:
                if runs and 'per_character_keys' in runs[-1]:
                    runs[-1]['per_character_keys'] += key
                else:
                    runs.append({'per_character_keys': key})
            else:
                runs.append({'key': key})
        parts.append({'call': 'pyautogui.press', 'ordered_key_runs': runs})
        keys.clear()
    for line_number,line in enumerate(code.splitlines(keepends=True),1):
        key = None
        if line.startswith('pyautogui.press('):
            try:
                call = calls.get(line_number)
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == 'pyautogui' and call.func.attr == 'press'
                        and len(call.args) == 1 and not call.keywords
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)
                        and not line.encode('utf-8')[call.end_col_offset:].strip()):
                    key = call.args[0].value
            except (SyntaxError, ValueError):
                pass
        if key is not None:
            if raw:
                parts.append({'code': ''.join(raw)}); raw.clear()
            keys.append(key)
        else:
            flush_keys(); raw.append(line)
    flush_keys()
    if raw:
        parts.append({'code': ''.join(raw)})
    if not any('ordered_key_runs' in p for p in parts):
        return None
    if len(json.dumps({'code_segments': parts}, ensure_ascii=False)) >= len(json.dumps({'code': code}, ensure_ascii=False)):
        return None
    return parts


def compact_payload(payload):
    payload = json.loads(json.dumps(payload))
    for unit in payload.get('units', []):
        for member in unit['members']:
            content = member['action'].get('content', {})
            code = content.get('code')
            if isinstance(code, str):
                parts = compact_code(code)
                if parts is not None:
                    content.pop('code'); content['code_segments'] = parts
    return payload


def goal_catalog(catalog):
    fields = {'goal_id','goal_type','description','constraints','completion_condition','depends_on'}
    projected = {'goals': [{k:v for k,v in g.items() if k in fields} for g in catalog['goals']]}
    if catalog.get('issues'):
        projected['issues'] = catalog['issues']
    return projected


def model_input_parameters(model):
    return {k:v for k,v in model.items()
            if k not in {'api_key_env','inference_authorized','timeout_seconds','pricing'}}


def input_identity(request):
    """Model-visible input and native provenance; implementation metadata is separate."""
    parameters = model_input_parameters(request['model_parameters'])
    return digest({'stage':request['stage'], 'core':request['core'],
        'messages':request['messages'], 'schema':output_schema(request),
        'images':[{'sha256':request['native_image_digests'][x['path']],
                   'evidence_refs':x['evidence_refs']} for x in request['images']],
        'image_transport':request['image_transport'], 'image_geometry_contract':'NATIVE_AND_SENT_SIZE_V1',
        'model_parameters':parameters,
        'instruction_source_provenance':request.get('instruction_source_provenance'),
        'evidence_bindings':request.get('evidence_bindings',{})})


def make_request(stage, tr, cfg, payload, core=None, bindings=None):
    prompt_path = HERE / 'prompts' / f'{stage}.md'
    book_path = (HERE / cfg['codebooks'][stage]).resolve()
    if not book_path.is_relative_to(HERE):
        raise ValueError('Stage Codebook must be in this candidate directory')
    prompt = prompt_path.read_text(encoding='utf-8-sig')
    codebook = book_path.read_text(encoding='utf-8-sig')
    system = prompt + '\n\n# Complete stage Codebook\n' + codebook
    schema = sources.read(HERE / 'schemas.json')[stage]
    payload = compact_payload(payload)
    user = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    images, by_digest, native_digests = [], {}, {}
    for ref, obs in (bindings or {}).items():
        path = obs.get('path')
        if path:
            if path not in native_digests:
                native_digests[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            sha = native_digests[path]
            if sha not in by_digest:
                by_digest[sha] = {'path': path, 'source_paths': [], 'evidence_refs': []}
                images.append(by_digest[sha])
            if path not in by_digest[sha]['source_paths']:
                by_digest[sha]['source_paths'].append(path)
            by_digest[sha]['evidence_refs'].append(ref)
    name = tr['task_id'] if stage == 'goals' else tr['trajectory_id'] + ('::U%04d-U%04d' % tuple(core))
    req = {'stage': stage, 'request_id': stage + '::' + name, 'trajectory_id': tr['trajectory_id'],
           'task_id': tr['task_id'], 'core': core, 'payload': payload,
           'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
           'images': images, 'image_transport': cfg['images'], 'schema': schema,
           'framework_version': cfg['version'], 'model_parameters': cfg['model'],
           'implementation_fingerprints': cfg.get('implementation_fingerprints', {}),
           'instruction_source_provenance': tr['instruction_source'], 'evidence_bindings': bindings or {}}
    req['native_image_digests'] = native_digests
    req['contract_hashes'] = {
        'prompt_sha256': hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        'codebook_sha256': hashlib.sha256(book_path.read_bytes()).hexdigest(),
        'schema_sha256': digest(output_schema(req)),
        'implementation_sha256': digest(implementation_fingerprints()),
        'framework_version': cfg['version']}
    req['input_identity_sha256'] = input_identity(req)
    req['request_sha256'] = digest(req)
    return req


def windows_for(first, last, cfg):
    size = cfg['trajectory_input']['window_units']
    return [(start, min(start+size-1, last)) for start in range(first, last+1, size)]


def window_context(units, bindings, first, last, cfg):
    radius = cfg['trajectory_input']['context_units']
    selected = units[max(0, first-radius):min(len(units), last+radius+1)]
    refs = {m[k] if k == 'action_ref' else m[k]['evidence_ref']
            for u in selected for m in u['members'] for k in ('action_ref', 'pre', 'post')}
    return selected, {r:v for r,v in bindings.items() if r in refs}


def requests_for(stage, trajectories, cfg, prior=None):
    requests, task_instructions = [], {}
    for tr in trajectories:
        if tr['trial_role'] != 'FULL_TRIAL':
            continue
        if stage == 'goals':
            if tr['task_id'] in task_instructions:
                if task_instructions[tr['task_id']] != tr['instruction']:
                    raise ValueError('Actor versions disagree on the native task instruction')
                continue
            task_instructions[tr['task_id']] = tr['instruction']
            requests.append(make_request(stage, tr, cfg, {'instruction':tr['instruction'],
                'source_segments':source_segments(tr['instruction'])}))
            continue
        catalog = goal_catalog(model_output(prior['goals'][tr['task_id']]))
        units, bindings = evidence_units(tr)
        alignment = [] if stage == 'alignment' else continuous_ranges(prior['alignment'][tr['trajectory_id']])
        spans = [{'first':0, 'last':len(units)-1}] if stage == 'alignment' else alignment
        for span in spans:
            for first, last in windows_for(span['first'], span['last'], cfg):
                selected, local_bindings = window_context(units, bindings, first, last, cfg)
                payload = {'instruction':tr['instruction'], 'goal_catalog':catalog,
                           'target_range':{'first':first, 'last':last}, 'units':selected}
                if stage == 'operator':
                    lo, hi = selected[0]['unit_index'], selected[-1]['unit_index']
                    payload['alignment'] = [
                        {'first':max(lo,r['first']), 'last':min(hi,r['last']),
                         'goal_ids':list(r['goal_ids']), 'status':r['status']}
                        for r in alignment if r['first']<=hi and r['last']>=lo]
                requests.append(make_request(stage,tr,cfg,payload,[first,last],local_bindings))
    return requests


def validate_response(request, output):
    validate_schema(output, request['schema'])
    stage = request['stage']
    if stage == 'goals':
        ids = [g['goal_id'] for g in output['goals']]
        if ids != [f'G{i+1}' for i in range(len(ids))]:
            raise ValueError('Goal IDs must be stable sequential IDs')
        prior = set()
        for g in output['goals']:
            if not set(g['depends_on']).issubset(prior):
                raise ValueError('Forward/unknown Goal dependency')
            prior.add(g['goal_id'])
        if 'source_segments' not in request['payload']:
            raise ValueError('Current Goal contract requires native source_segments; no historical requirement mapping')
        segments = request['payload']['source_segments']
        if ''.join(s['text'] for s in segments) != request['payload']['instruction']:
            raise ValueError('Source segments do not reproduce the complete instruction')
        positions = {s['source_id']:i for i,s in enumerate(segments)}
        for g in output['goals']:
            refs = g['source_ids']
            if any(s not in positions for s in refs):
                raise ValueError('Unknown source ID in Goal '+g['goal_id'])
            if len(set(refs)) != len(refs) or refs != sorted(refs,key=positions.get):
                raise ValueError('Source IDs must be unique and in original source order')
        return
    valid_goals = {g['goal_id'] for g in request['payload']['goal_catalog']['goals']}
    first, last = request['core']
    rows = output['ranges'] if stage == 'alignment' else output['units']
    covered = []
    for r in rows:
        if stage == 'alignment' and not set(r['goal_ids']).issubset(valid_goals):
            raise ValueError('Unknown Goal reference')
        if stage == 'alignment' and len(r['goal_ids']) != len(set(r['goal_ids'])):
            raise ValueError('Duplicate Goal reference is not a separate Goal')
        if not r['reason'].strip():
            raise ValueError('Reason missing')
        if stage == 'alignment':
            if r['first'] > r['last']:
                raise ValueError(f"Reversed Alignment range {r['first']}..{r['last']}")
            covered.extend(range(r['first'], r['last']+1))
            if r['status'] == 'ALIGNED' and len(r['goal_ids']) != 1:
                raise ValueError('ALIGNED needs one Goal')
            if r['status'] in {'SUPPORT_NO_GOAL', 'OFF_GOAL'} and r['goal_ids']:
                raise ValueError(r['status'] + ' cannot contain Goal IDs')
        else:
            idx = r['unit_index']
            if not first <= idx <= last:
                raise ValueError('Operator index outside target group/range')
            fixed = next((a for a in request['payload']['alignment'] if a['first'] <= idx <= a['last']), None)
            if fixed is None:
                raise ValueError('Operator Unit lacks saved Alignment')
            if fixed['status'] == 'OFF_GOAL' and r['operator'] in {'COMMIT', 'LEAVE'}:
                raise ValueError('OFF_GOAL cannot be COMMIT or LEAVE')
            covered.append(idx)
    if covered != list(range(first, last+1)):
        expected = set(range(first,last+1))
        counts = collections.Counter(covered)
        raise ValueError('Gap/duplicate/reorder/out-of-window result: '
            +json.dumps({'target':[first,last], 'missing':sorted(expected-set(covered)),
                         'duplicate':sorted(i for i,n in counts.items() if n>1),
                         'outside':sorted(set(covered)-expected), 'returned_order':covered}))


def output_schema(request):
    schema = json.loads(json.dumps(request['schema']))
    if request['stage'] == 'goals' and 'source_segments' in request['payload']:
        schema['properties']['goals']['items']['properties']['source_ids']['items']['enum'] = [
            s['source_id'] for s in request['payload']['source_segments']]
    elif request['stage'] == 'alignment':
        array = schema['properties']['ranges']
        # Exact, non-overlapping coverage permits at most one range per target Unit.
        array['maxItems'] = request['core'][1] - request['core'][0] + 1
        item = array['items']
        properties = item['properties']
        for key in ('first', 'last'):
            properties[key]['enum'] = list(range(request['core'][0], request['core'][1]+1))
        properties['goal_ids']['items']['enum'] = [g['goal_id'] for g in request['payload']['goal_catalog']['goals']]
        variants = []
        for status in properties['status']['enum']:
            variant = json.loads(json.dumps(item))
            variant['properties']['status'] = {'const': status}
            goals = variant['properties']['goal_ids']
            if status == 'ALIGNED':
                goals['minItems'] = goals['maxItems'] = 1
            elif status in {'SUPPORT_NO_GOAL', 'OFF_GOAL'}:
                goals['maxItems'] = 0
            variants.append(variant)
        array['items'] = {'oneOf': variants}
        if request['core'][0] == request['core'][1]:
            array['maxItems'] = 1
    elif request['stage'] == 'operator':
        array = schema['properties']['units']
        item = array['items']
        array['prefixItems'] = []
        for index in range(request['core'][0], request['core'][1]+1):
            fixed = json.loads(json.dumps(item))
            fixed['properties']['unit_index']['const'] = index
            array['prefixItems'].append(fixed)
        array['minItems'] = array['maxItems'] = len(array['prefixItems'])
        array['items'] = False
    return schema


def wire_body(request, cfg):
    messages = [dict(m) for m in request['messages']]
    content = [{'type': 'text', 'text': messages[1]['content']}]
    if request['images']:
        content.append({'type':'text','text':'Actions retain their recorded coordinates. Image geometry below '
            'maps native screenshot pixels to sent-image pixels only. Preserve explicitly recorded normalized/'
            'viewport coordinate systems; do not assume an unspecified coordinate frame matches sent-image pixels.'})
    for spec in request['images']:
        for path in spec.get('source_paths', [spec['path']]):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != request['native_image_digests'][path]:
                raise ValueError('Native image changed after request preparation')
        native = Path(spec['path']).read_bytes()
        if hashlib.sha256(native).hexdigest() != request['native_image_digests'][spec['path']]:
            raise ValueError('Native image changed after request preparation')
        im = Image.open(io.BytesIO(native)).convert('RGB')
        native_size = list(im.size)
        im.thumbnail((cfg['images']['max_edge'], cfg['images']['max_edge']))
        stream = io.BytesIO()
        im.save(stream, format='JPEG', quality=cfg['images']['jpeg_quality'])
        image_url = {'url': 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode()}
        if cfg['model']['capabilities'].get('image_detail', True):
            image_url['detail'] = 'high'
        geometry = {'native_image_size':native_size, 'sent_image_size':list(im.size),
                    'native_pixel_to_sent_pixel_scale':[im.width/native_size[0],im.height/native_size[1]]}
        content += [{'type': 'text', 'text': 'Observation refs: ' + ', '.join(spec['evidence_refs'])
                     + '\nImage geometry: ' + json.dumps(geometry)},
                    {'type': 'image_url', 'image_url': image_url}]
    messages[1]['content'] = content
    return api_backend.build_body(request, cfg['model'], messages, output_schema(request))


def call_model(request, cfg, format_feedback=None):
    body = wire_body(request, cfg)
    if format_feedback:
        body['messages'][0]['content'] += '\n\nOutput-format correction only: ' + format_feedback
    started = time.monotonic()
    response_error = None
    try:
        raw, provider_request_id, rejected = api_backend.complete(body, cfg['model'],
            interval=cfg.get('execution',{}).get('request_interval_seconds',0),
            rate_limit_retries=cfg.get('execution',{}).get('rate_limit_retries',0))
    except api_backend.APIResponseError as exc:
        if not hasattr(exc, 'raw_response'):
            raise
        raw, provider_request_id, rejected = exc.raw_response, exc.provider_request_id, []
        response_error = str(exc)
    envelope = raw if isinstance(raw, dict) else {}
    text, finish_reason = '', None
    if response_error is None:
        try:
            text, finish_reason = api_backend.content_text(envelope)
        except api_backend.APIResponseError as exc:
            response_error = str(exc)
    if envelope.get('model') != cfg['model']['model_id']:
        response_error = response_error or ('Returned model does not match requested model: '
            + str(envelope.get('model')) + ' != ' + cfg['model']['model_id'])
    try:
        output = api_backend.strict_json_loads(text) if response_error is None else None
        parse_error = None
    except ValueError as exc:
        output = None
        parse_error = str(exc)
    return {'request_id': request['request_id'], 'request_sha256': request['request_sha256'],
            'input_identity_sha256':input_identity(request), 'weight_revision':cfg['model']['weight_revision'],
            'authority': cfg['model'].get('annotation_authority', 'CLOSED_SOURCE_MODEL'), 'model': envelope.get('model'),
            'output': output, 'parse_error': parse_error, 'response_error': response_error,
            'raw_api_response': raw, 'raw_output': text, 'usage': envelope.get('usage'),
            'finish_reason': finish_reason, 'provider_request_id': provider_request_id,
            'provider_response_id': envelope.get('id'), 'system_fingerprint': envelope.get('system_fingerprint'),
            'transport_attempts':len(rejected)+1,'rejected_rate_limit_attempts':rejected,
            'contract_hashes': request['contract_hashes'],
            'elapsed_seconds': round(time.monotonic()-started, 3), 'wire_body_sha256': digest(body),
            'runtime_implementation_fingerprints': implementation_fingerprints(),
            'wire_output_schema_sha256': digest(body.get('response_format', {}).get('json_schema', {}).get('schema')),
            'format_retry': 1 if format_feedback else 0, 'format_feedback': format_feedback}


def run_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE / 'runs'):
        raise ValueError('New outputs must stay inside this framework runs directory')
    return path


def correction_feedback(request, answer, error):
    feedback = str(error)+'. '
    if request['stage'] == 'goals':
        feedback += 'Use supplied source_ids and sequential Goal IDs with valid earlier dependencies. '
    else:
        feedback += ('Label only global target indices '+str(request['core'])
            +'; context Units are not output targets. Cover every target exactly once in order, '
             'using only supplied Goal IDs. Do not automatically preserve a conflicting old assignment. ')
    feedback += 'Re-read the same original evidence. Invalid previous output: '+str(
        answer.get('raw_output') or json.dumps(answer.get('output'),ensure_ascii=False))
    return feedback


def check_answer_identity(request, answer):
    if answer.get('request_id') != request['request_id'] or answer.get('request_sha256') != request['request_sha256']:
        raise ValueError('Response bound to a different input/prompt/schema; not a format error')
    if answer.get('response_error'):
        raise api_backend.APIResponseError(answer['response_error'], ambiguous=True)
    if answer.get('authority') not in {'CODEX_RESEARCHER_TRIAL','OPEN_SOURCE_MODEL','CLOSED_SOURCE_MODEL','API_MODEL','TECHNICAL_TEST_FIXTURE'} or not answer.get('model'):
        raise ValueError('Model/researcher attribution required; no Human Gold import here')
    if answer['authority'] in {'OPEN_SOURCE_MODEL','CLOSED_SOURCE_MODEL','API_MODEL'} and answer['model'] != request['model_parameters']['model_id']:
        raise ValueError('Response model differs from requested model; not a format error')
    if answer['authority'] in {'OPEN_SOURCE_MODEL','CLOSED_SOURCE_MODEL','API_MODEL'}:
        if not isinstance(answer.get('raw_output'),str):
            raise ValueError('Raw model text required for a model response; not a format error')
        parsed = answer.get('model_output',answer.get('output'))
        try:
            raw_parsed = api_backend.strict_json_loads(answer['raw_output'])
        except ValueError:
            if parsed is not None:
                raise ValueError('Parsed output contradicts invalid raw model text; not a format error')
        else:
            if raw_parsed != parsed:
                raise ValueError('Parsed output differs from raw model text; not a format error')


def bound_output(request, output):
    bound = dict(output, task_id=request['task_id'])
    if request['stage'] != 'goals':
        bound['trajectory_id'] = request['trajectory_id']
    if request['stage'] == 'operator':
        bound['units'] = []
        for unit in output['units']:
            fixed = next(a for a in request['payload']['alignment'] if a['first'] <= unit['unit_index'] <= a['last'])
            bound['units'].append(dict(unit,goal_ids=list(fixed['goal_ids']),alignment_status=fixed['status']))
    return bound


def validate_saved_stage(folder, stage, complete=True, verified_images=None):
    """Validate saved work against its actual sources before treating it as complete."""
    cfg = sources.read(folder/'config_snapshot.json')
    if cfg.get('implementation_fingerprints') != implementation_fingerprints():
        raise ValueError('Saved implementation differs; preserve historical run and use a new batch')
    profile=cfg['trajectory_input']
    reuse_file=folder/'REUSED_STAGES.json'
    if reuse_file.exists():
        reused=[r for r in sources.read(reuse_file) if r['stage']==stage and 'original_input_profile' in r]
        if len(reused)>1:
            raise ValueError('Conflicting original stage input profiles')
        if reused:
            original=sources.read(Path(reused[0]['original_run'])/'config_snapshot.json')['trajectory_input']
            if original!=reused[0]['original_input_profile'] or not (folder/f'results_{stage}.jsonl').exists():
                raise ValueError('Original input profile is only valid for an explicitly reused completed stage')
            profile=original
    requests = read_lines(folder/f'requests_{stage}.jsonl')
    if not requests or len({q['request_id'] for q in requests}) != len(requests):
        raise ValueError('Missing or duplicate stage requests')
    records = ([] if complete is None else
               read_lines(folder/f'{"results" if complete else "partial_results"}_{stage}.jsonl'))
    ids = [r['request_id'] for r in records]
    lookup = {q['request_id']:q for q in requests}
    if len(set(ids)) != len(ids) or not set(ids).issubset(lookup) or (complete and set(ids) != set(lookup)):
        raise ValueError('Saved stage does not cover its exact request set')
    derived = []
    for native in read_lines(folder/'normalized_sources.jsonl'):
        tr = dict(native)
        tr['units'],tr['repeat_audit'] = join_units(tr,cfg['repeat_confirmations'])
        derived.append(tr)
    saved_sources = read_lines(folder/'sources.jsonl')
    if saved_sources != derived or len({t['trajectory_id'] for t in saved_sources}) != len(saved_sources):
        raise ValueError('Saved Units/sources differ from normalized native inputs')
    traces = {t['trajectory_id']:t for t in saved_sources if t['trial_role']=='FULL_TRIAL'}
    units = {tid:evidence_units(t) for tid,t in traces.items()}
    image_checks = {} if verified_images is None else verified_images
    coverage = collections.defaultdict(list)
    goals = ({r['output']['task_id']:model_output(r) for r in read_lines(folder/'results_goals.jsonl')}
             if stage != 'goals' else {})
    alignment = collections.defaultdict(list)
    if stage == 'operator':
        af = folder/'results_alignment.jsonl'
        for record in read_lines(af if af.exists() else folder/'partial_results_alignment.jsonl'):
            alignment[record['output']['trajectory_id']].append(record)
    for q in requests:
        check = dict(q); sha = check.pop('request_sha256')
        if digest(check) != sha or q['stage'] != stage:
            raise ValueError('Saved request identity changed')
        tr = traces.get(q['trajectory_id'])
        if tr is None or q['task_id'] != tr['task_id'] or q['payload']['instruction'] != tr['instruction']:
            raise ValueError('Saved request belongs to different task/Actor sources')
        if (model_input_parameters(q['model_parameters']) != model_input_parameters(cfg['model'])
                or q['image_transport'] != cfg['images']):
            raise ValueError('Saved request differs from requested model/image configuration')
        if stage == 'goals':
            expected_bindings = {}
            if q['payload']['source_segments'] != source_segments(tr['instruction']):
                raise ValueError('Saved instruction source segments changed')
        else:
            first,last = q['core']
            if not 0 <= first <= last < len(tr['units']):
                raise ValueError('Saved target range outside native Units')
            if q['payload']['target_range'] != {'first':first,'last':last}:
                raise ValueError('Model-visible target range differs from program target range')
            coverage[q['trajectory_id']].extend(range(first,last+1))
            native_units,bindings = units[q['trajectory_id']]
            indices = [u['unit_index'] for u in q['payload']['units']]
            radius=profile['context_units']
            expected_indices=list(range(max(0,first-radius),min(len(native_units),last+radius+1)))
            if indices != expected_indices or last-first+1 > profile['window_units']:
                raise ValueError('Saved evidence differs from the actual target/context window configuration')
            selected = [native_units[u['unit_index']] for u in q['payload']['units']]
            if compact_payload({'units':selected})['units'] != q['payload']['units']:
                raise ValueError('Saved actions/observations differ from source Units')
            refs = {m['action_ref'] for u in selected for m in u['members']}
            refs |= {m[role]['evidence_ref'] for u in selected for m in u['members'] for role in ('pre','post')}
            expected_bindings = {ref:value for ref,value in bindings.items() if ref in refs}
            if q['evidence_bindings'] != expected_bindings:
                raise ValueError('Saved evidence bindings differ from native source')
            if q['payload']['goal_catalog'] != goal_catalog(goals[tr['task_id']]):
                raise ValueError('Saved downstream request refers to a different Goal catalog')
            if stage == 'operator':
                lo,hi = selected[0]['unit_index'],selected[-1]['unit_index']
                expected_alignment = [dict(first=max(lo,a['first']),last=min(hi,a['last']),
                    goal_ids=list(a['goal_ids']),status=a['status'])
                    for a in continuous_ranges(alignment[tr['trajectory_id']]) if a['first']<=hi and a['last']>=lo]
                if q['payload']['alignment'] != expected_alignment:
                    raise ValueError('Saved Operator input differs from saved Alignment')
        for path,sha in q['native_image_digests'].items():
            if path not in image_checks:
                image_checks[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if image_checks[path] != sha:
                raise ValueError('Saved native image differs from original request')
        original_cfg = dict(cfg,version=q['framework_version'],model=q['model_parameters'])
        expected_request = make_request(stage,tr,original_cfg,q['payload'],q['core'],expected_bindings)
        if expected_request != q:
            raise ValueError('Saved request omits or changes its Prompt/Codebook/Schema/native images')
    if stage == 'goals':
        if [q['task_id'] for q in requests] != list(dict.fromkeys(t['task_id'] for t in traces.values())):
            raise ValueError('Goal requests do not match actual source tasks')
    else:
        expected = set(traces) if stage == 'alignment' or (folder/'results_alignment.jsonl').exists() else set(coverage)
        if set(coverage) != expected or any(indices != list(range(len(traces[tid]['units']))) for tid,indices in coverage.items()):
            raise ValueError('Saved requests do not cover the complete native trajectory')
    for record in records:
        q = lookup[record['request_id']]
        check_answer_identity(q, record)
        validate_response(q, record['model_output'])
        if record['output'] != bound_output(q, record['model_output']):
            raise ValueError('Saved bound output differs from its actual model response')
    return records


def infer_request(request, cfg):
    """A worker performs at most one correction; the coordinator persists responses."""
    captured = []
    try:
        answer = call_model(request,cfg); captured.append(answer)
        check_answer_identity(request, answer)
        try:
            validate_response(request,answer['output'])
        except ValueError as exc:
            if (answer.get('authority') not in {'OPEN_SOURCE_MODEL', 'CLOSED_SOURCE_MODEL', 'API_MODEL'}
                    or cfg.get('execution', {}).get('format_retries', 1) == 0):
                raise
            answer = call_model(request,cfg,format_feedback=correction_feedback(request,answer,exc))
            captured.append(answer)
            check_answer_identity(request, answer)
            validate_response(request,answer['output'])
        return captured,None
    except Exception as exc:
        return captured,exc


def prepare(cfg, folder, normalized_input=None):
    api_backend.validate_config(cfg['model'])
    if cfg.get('execution', {}).get('format_retries', 1) not in {0, 1}:
        raise ValueError('format_retries must be 0 or 1')
    folder = run_path(folder)
    if folder.exists():
        raise FileExistsError('Use a fresh run directory; preserve existing results')
    cfg = dict(cfg, implementation_fingerprints=implementation_fingerprints())
    trajectories = []
    normalized = read_lines(normalized_input) if normalized_input else []
    if normalized_input and not normalized:
        raise ValueError('Normalized input must contain trajectories')
    for item in normalized if normalized_input else cfg['samples']:
        if item['split'] not in {'EXPOSED_DEVELOPMENT', 'MODEL_ANNOTATION_COHORT'} and not (
            item['split']=='SOURCE_ONLY_NOT_E1_SEMANTICALLY_REVIEWED' and item['trial_role']!='FULL_TRIAL'):
            raise ValueError('Use an explicit model annotation cohort; sealed confirmation cannot be opened here')
        tr = dict(item) if normalized_input else sources.load(item)
        tr['trial_role'] = item['trial_role']
        validate_source(tr)
        trajectories.append(tr)
    normalized = json.loads(json.dumps(trajectories))
    for tr in trajectories:
        tr['units'], tr['repeat_audit'] = join_units(tr, cfg['repeat_confirmations'])
    folder.mkdir(parents=True)
    write(folder / 'config_snapshot.json', cfg)
    write_lines(folder / 'normalized_sources.jsonl', normalized)
    write_lines(folder / 'sources.jsonl', trajectories)
    goals = requests_for('goals', trajectories, cfg)
    write_lines(folder / 'requests_goals.jsonl', goals)
    write(folder / 'STATUS.json', {'framework_version': cfg['version'],
                                 'status': 'SOURCES_READY_GOALS_PENDING' if goals else 'SOURCE_ONLY_PREPARED',
                                 'source_trajectories': len(trajectories), 'model_calls': 0, 'human_labels': 0})
    export_review(folder, trajectories)
    return folder


def step(folder, stage, response_path=None, infer=False, authorize=False, concurrency=None,
         retry_ambiguous=False):
    folder = run_path(folder)
    cfg = sources.read(folder / 'config_snapshot.json')
    if cfg.get('implementation_fingerprints') != implementation_fingerprints():
        raise ValueError('Code/Prompt/Schema changed since preparation; use a fresh versioned run')
    requests = read_lines(folder / f'requests_{stage}.jsonl')
    if not requests:
        raise ValueError('No source Units are ready for this stage; not a completed annotation')
    output_path = folder / f'results_{stage}.jsonl'
    if output_path.exists():
        raise FileExistsError('Do not overwrite a completed stage; use a new run for revisions')
    if infer and not authorize:
        raise PermissionError('Inference disabled: obtain batch/cost approval before --authorize-inference')
    if infer and cfg['model']['model_id'].startswith('UNSELECTED'):
        raise ValueError('Select model before inference')
    incoming = [] if infer else read_lines(response_path)
    ids = [r['request_id'] for r in incoming]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate imported response')
    answers = {r['request_id']: r for r in incoming}
    if not infer and set(ids) != {r['request_id'] for r in requests}:
        raise ValueError('Responses must cover exactly this stage; no historical fallback')
    workers = (cfg.get('execution', {}).get('request_concurrency', 1)
               if concurrency is None else concurrency) if infer else 1
    if type(workers) is not int or workers < 1:
        raise ValueError('Request concurrency must be a positive integer')
    records, errors, failures = {}, [], []
    capture = folder / f'captured_responses_{stage}.jsonl'
    captured = ({r['request_id']: r for r in read_lines(capture)}
                if infer and capture.exists() else {} if infer else answers)
    prior_failure_path=folder/f'failures_{stage}.json'
    ambiguous={r['request_id']:r for r in sources.read(prior_failure_path)
               if r.get('charge_or_outcome_unknown')} if prior_failure_path.exists() else {}
    for request in requests:
        check = dict(request)
        fingerprint = check.pop('request_sha256')
        if digest(check) != fingerprint:
            raise ValueError('Prepared request changed')
        if request['model_parameters'] != cfg['model'] or request['image_transport'] != cfg['images']:
            raise ValueError('Run configuration differs from prepared request')
    if stage in {'alignment','operator'}:
        validate_saved_stage(folder,'goals')
    if stage == 'operator':
        validate_saved_stage(folder,'alignment',complete=(folder/'results_alignment.jsonl').exists())
    validate_saved_stage(folder,stage,complete=None)

    def save_capture(answer):
        # Only the coordinating thread writes; workers only make HTTP requests.
        answer['request_concurrency'] = workers
        with capture.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(answer, ensure_ascii=False) + '\n')

    def accept(request, answer, fresh):
        if fresh:
            save_capture(answer)
        check_answer_identity(request, answer)
        validate_response(request, answer['output'])
        bound = bound_output(request, answer['output'])
        records[request['request_id']] = dict(answer, output=bound, model_output=answer['output'],
            status='TECHNICAL_FIXTURE_NOT_SEMANTIC' if answer['authority']=='TECHNICAL_TEST_FIXTURE'
            else 'STRUCTURALLY_VALID_NOT_HUMAN_VALIDATED')
        if stage == 'goals' and 'source_segments' in request['payload']:
            segments = {s['source_id']:s['text'] for s in request['payload']['source_segments']}
            records[request['request_id']]['source_quotes_by_goal'] = {
                g['goal_id']:[{'source_id':s,'text':segments[s]} for s in g['source_ids']]
                for g in answer['output']['goals']}
            referenced = {s for g in answer['output']['goals'] for s in g['source_ids']}
            records[request['request_id']]['unreferenced_source_ids'] = [s for s in segments if s not in referenced]

    def reject(request, exc):
        errors.append(exc)
        failures.append({'request_id':request['request_id'],'trajectory_id':request['trajectory_id'],
                         'core':request['core'],'error_type':type(exc).__name__,
                         'reason':api_backend.safe_text(exc, cfg['model']),
                         'http_status':getattr(exc, 'status', None),
                         'retry_after':getattr(exc, 'retry_after', None),
                         'charge_or_outcome_unknown':getattr(exc, 'ambiguous', False)})

    todo = collections.deque(requests)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}
        while todo or pending:
            while todo and len(pending) < workers:
                request = todo.popleft()
                if request['request_id'] in captured:
                    try:
                        check_answer_identity(request, captured[request['request_id']])
                    except Exception as exc:
                        reject(request, exc)
                        continue
                    try:
                        accept(request, captured[request['request_id']], False)
                    except ValueError as exc:
                        answer=captured[request['request_id']]
                        if request['request_id'] in ambiguous and not retry_ambiguous:
                            reject(request,api_backend.APIError(
                                'Previous correction outcome/charge unknown; inspect before --retry-ambiguous.',
                                ambiguous=True))
                            continue
                        if (infer and answer.get('authority') in {'OPEN_SOURCE_MODEL', 'CLOSED_SOURCE_MODEL', 'API_MODEL'}
                                and not answer.get('format_retry') and cfg.get('execution', {}).get('format_retries', 1)):
                            def correct(q,a,err):
                                try:
                                    fixed=call_model(q,cfg,format_feedback=correction_feedback(q,a,err))
                                    return [fixed],None
                                except Exception as e:
                                    return [],e
                            pending[pool.submit(correct,request,answer,exc)] = request
                        else:
                            reject(request,exc)
                else:
                    if infer and request['request_id'] in ambiguous and not retry_ambiguous:
                        reject(request,api_backend.APIError(
                            'Previous API outcome/charge unknown; inspect failures and provider billing '
                            'before explicitly using --retry-ambiguous. No automatic duplicate call.',
                            ambiguous=True))
                    else:
                        pending[pool.submit(infer_request, request, cfg)] = request
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                request = pending.pop(future)
                try:
                    responses,error=future.result()
                    for answer in responses:
                        save_capture(answer)
                    if error is not None:
                        raise error
                    accept(request,responses[-1],False)
                except Exception as exc:
                    reject(request,exc)
            # A failed window does not stop other windows or Actor trajectories.
    write(folder / f'failures_{stage}.json',failures)
    if errors:
        write_lines(folder / f'partial_results_{stage}.jsonl',[records[r['request_id']] for r in requests if r['request_id'] in records])
        if stage != 'goals':
            finish_stage(folder,stage)
        else:
            state=sources.read(folder/'STATUS.json')
            state.update(status='PARTIAL_WITH_ERRORS',failed_stage=stage,failed_requests=len(failures))
            write(folder/'STATUS.json',state)
        raise errors[0]
    if stage == 'operator' and not (folder/'results_alignment.jsonl').exists():
        write_lines(folder / f'partial_results_{stage}.jsonl',[records[r['request_id']] for r in requests])
        finish_stage(folder,stage)
        return
    write_lines(output_path, [records[r['request_id']] for r in requests])
    finish_stage(folder, stage)


def finish_stage(folder, stage, verified_images=None):
    """Build downstream files from saved valid results, without another model call."""
    cfg = sources.read(folder / 'config_snapshot.json')
    if cfg.get('implementation_fingerprints') != implementation_fingerprints():
        raise ValueError('Code/Prompt/Schema changed since preparation; use a fresh versioned run')
    result_file = folder / f'results_{stage}.jsonl'
    image_checks = {} if verified_images is None else verified_images
    records = validate_saved_stage(folder, stage, complete=result_file.exists(),verified_images=image_checks)
    trajectories = read_lines(folder / 'sources.jsonl')
    prior = {'goals': {r['output']['task_id']: r for r in read_lines(folder / 'results_goals.jsonl')}}
    if stage in {'alignment','operator'}:
        prior['alignment'] = collections.defaultdict(list)
        alignment_file = folder/'results_alignment.jsonl'
        for r in read_lines(alignment_file if alignment_file.exists() else folder/'partial_results_alignment.jsonl'):
            prior['alignment'][r['output']['trajectory_id']].append(r)
    if stage == 'goals':
        write_lines(folder / 'requests_alignment.jsonl', requests_for('alignment',trajectories,cfg,prior))
        write_lines(folder / 'alignment_routes.jsonl',[
            {'task_id':t['task_id'],'trajectory_id':t['trajectory_id'],'method':'FIXED_WINDOW_ALIGNMENT',
             'goal_count':len(prior['goals'][t['task_id']]['output']['goals'])}
            for t in trajectories if t['trial_role']=='FULL_TRIAL'])
    elif stage == 'alignment':
        accepted = {r['request_id'] for rs in prior['alignment'].values() for r in rs}
        expected = collections.defaultdict(set)
        for q in read_lines(folder/'requests_alignment.jsonl'):
            expected[q['trajectory_id']].add(q['request_id'])
        ready = {tid for tid,ids in expected.items() if ids.issubset(accepted)}
        prior['alignment'] = {tid:rs for tid,rs in prior['alignment'].items() if tid in ready}
        write_lines(folder / 'continuous_alignment.jsonl',[
            {'trajectory_id':tid,'method':'FIXED_WINDOW_ALIGNMENT','ranges':continuous_ranges(rs)}
            for tid,rs in prior['alignment'].items()])
        write_lines(folder / 'requests_operator.jsonl',requests_for('operator',[
            t for t in trajectories if t['trajectory_id'] in ready],cfg,prior))
        write(folder/'alignment_trajectory_status.json',{
            'ready':sorted(ready),'blocked':sorted(set(expected)-ready),
            'accepted_windows':len(accepted),'required_windows':sum(map(len,expected.values()))})
    else:
        # Alignment is completed before Operator annotation; never write it here.
        if stage == 'operator':
            export_review(folder,trajectories,prior,records)
    stages=('goals','alignment','operator')
    calls=sum(r['authority'] in {'OPEN_SOURCE_MODEL', 'CLOSED_SOURCE_MODEL', 'API_MODEL'}
              and not r.get('reuse_provenance',{}).get('new_model_call') is False for s in stages
              if (folder/f'captured_responses_{s}.jsonl').exists()
              for r in read_lines(folder/f'captured_responses_{s}.jsonl'))
    completed=[]
    for s in stages:
        if (folder/f'results_{s}.jsonl').exists():
            validate_saved_stage(folder,s,verified_images=image_checks)
            completed.append(s)
    fixture=any(r['authority']=='TECHNICAL_TEST_FIXTURE' for s in completed
                for r in read_lines(folder/f'results_{s}.jsonl'))
    partial = any((folder/f'partial_results_{s}.jsonl').exists()
                  and not (folder/f'results_{s}.jsonl').exists() for s in stages)
    write(folder/'STATUS.json',{'framework_version':cfg['version'],
        'status':('TECHNICAL_FIXTURE_COMPLETE' if fixture else
                  'MODEL_ANNOTATION_COMPLETE' if calls else 'RECORDED_SMALL_TRIAL_COMPLETE')
                  if len(completed)==len(stages) else 'PARTIAL_WITH_ERRORS' if partial else 'STAGES_PENDING',
        'completed_stages':completed,'pending_stages':[s for s in stages if s not in completed],
        'source_trajectories':len(trajectories),'full_trial_trajectories':sum(t['trial_role']=='FULL_TRIAL' for t in trajectories),
        'raw_events':sum(len(t['events']) for t in trajectories if t['trial_role']=='FULL_TRIAL'),
        'repeat_groups':sum(len(t['units']) for t in trajectories if t['trial_role']=='FULL_TRIAL'),
        'model_calls':calls,'human_labels':0,
        'real_api_semantics_verified':False,
        'scientific_acceptance':'TECHNICAL_TEST_ONLY' if fixture else 'PENDING_HUMAN_ANNOTATION'})


def continuous_ranges(records):
    """Join identical adjacent assignments, without filling any source/window gap."""
    result = []
    for record in records:
        for r in record['output']['ranges']:
            if r['status'] not in {'ALIGNED', 'SUPPORT_NO_GOAL', 'OFF_GOAL', 'UNRESOLVED'}:
                raise ValueError('Historical Alignment state cannot be auto-migrated: ' + r['status'])
            previous = result[-1] if result else None
            if previous and previous['last']+1 == r['first'] and previous['goal_ids'] == r['goal_ids'] and previous['status'] == r['status']:
                previous['last'] = r['last']
                previous['source_ranges'].append(dict(r))
                previous['reason'] = ' | '.join(dict.fromkeys(x['reason'] for x in previous['source_ranges']))
            else:
                result.append(dict(r, source_ranges=[dict(r)]))
    return result


def export_review(folder, trajectories, prior=None, operators=None):
    goal_records = prior['goals'] if prior else {}
    by_op = {(r['output']['trajectory_id'], u['unit_index']): u for r in (operators or []) for u in r['output']['units']}
    contributors = sorted({(r.get('authority', 'UNSPECIFIED'), r.get('model', 'UNSPECIFIED'))
                           for r in [*goal_records.values(), *(operators or [])]})
    attribution = ('标注来源：' + '；'.join(f'{a} / {m}' for a, m in contributors) + '。未经人工语义验收。'
                   if contributors else '来源已准备；语义模型标注尚未执行。')
    overview = ['# 统一框架：真实小样本审阅', '',
                attribution + '原始记录完整覆盖不等于自然终止搜索 bout 认证。', '']
    for tr in trajectories:
        name = review_file_name(tr)
        overview += [f"- [{tr['dataset']} / {tr['task_id']}]({name})：{len(tr['events'])} 原始事件 → {len(tr['units'])} 操作分组；{tr['trial_role']}。"]
        md = [f"# {tr['dataset']}：{tr['task_id']}", '', tr['instruction'], '',
              f"Actor：{tr['actor_id']}。来源：{tr['instruction_source']}。", '',
              attribution, '',
              '## 先检查 Local Goal、连续 Alignment 与合并', '']
        if tr['task_id'] in goal_records:
            md += ['```json', json.dumps(goal_records[tr['task_id']]['output'], ensure_ascii=False, indent=2), '```']
        if prior and tr['trajectory_id'] in prior.get('alignment', {}):
            md += ['连续范围（操作分组编号，原始成员见下方）：']
            for a in continuous_ranges(prior['alignment'][tr['trajectory_id']]):
                md += [f"- {a['first']}–{a['last']}：{a['status']} / {a['goal_ids']}；{a['reason']}"]
        md += ['', '合并只减少展示重复；全部原始成员、中间观察和终止事件均保留。',
               '```json', json.dumps(tr['repeat_audit'], ensure_ascii=False, indent=2), '```', '',
               '## 完整原始顺序与独立 Operator 选择', '',
               '先核实上述基础，再逐组选择：A INSPECT / B COMMIT / C LEAVE / D EXECUTE / E SUPPORT / F UNRESOLVED。特殊情况才说明。模型试跑答案默认折叠；人工首次判断应先独立保存。']
        for u in tr['units']:
            md += ['', f"### R{u['unit_index']:04d}：原始步骤 {u['raw_steps']}", '']
            for e in u['members']:
                md += [f"原始步骤 {e['raw_step']}；终止事件：{e['terminal_event']}。", '']
                for role in ['pre', 'post']:
                    pic = e[role]['path']
                    md += [f"![{role}](<{Path(pic).as_posix()}>)" if pic else f'{role}：原生观察缺失。']
                    if role == 'pre':
                        md += ['```json', json.dumps(e['action'], ensure_ascii=False, indent=2), '```']
            md += ['', '你的选择：□ A　□ B　□ C　□ D　□ E　□ F；特殊情况：________', '']
            op = by_op.get((tr['trajectory_id'], u['unit_index']))
            if op:
                md += ['<details><summary>完成独立判断后展开：模型／研究者标注结果（未经人工验收）</summary>', '',
                       '```json', json.dumps(op, ensure_ascii=False, indent=2), '```', '</details>']
        (folder / name).write_text('\n'.join(md) + '\n', encoding='utf-8')
    (folder / 'REVIEW_INDEX.md').write_text('\n'.join(overview) + '\n', encoding='utf-8')


def review_file_name(tr):
    # Long native WebChain IDs exceed Windows MAX_PATH in nested candidate runs.
    # Only the presentation filename is shortened; native IDs/mappings are intact.
    return 'REVIEW_' + tr['dataset'] + '_' + digest(tr['trajectory_id'])[:12] + '.md'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest='command', required=True)
    prep = subs.add_parser('prepare')
    prep.add_argument('--config', default=str(HERE / 'config.json'))
    prep.add_argument('--run-dir', required=True)
    prep.add_argument('--normalized-input', help='Validated E1_NATIVE_SOURCE_V2 JSONL')
    st = subs.add_parser('step')
    st.add_argument('--run-dir', required=True)
    st.add_argument('--stage', required=True, choices=['goals', 'alignment', 'operator'])
    choice = st.add_mutually_exclusive_group(required=True)
    choice.add_argument('--responses')
    choice.add_argument('--infer', action='store_true')
    st.add_argument('--authorize-inference', action='store_true')
    st.add_argument('--retry-ambiguous', action='store_true',
                    help='Explicitly retry a previously unknown API outcome after billing inspection')
    args = ap.parse_args()
    if args.command == 'prepare':
        folder = prepare(sources.read(args.config), args.run_dir, args.normalized_input)
    else:
        folder = run_path(args.run_dir)
        step(folder, args.stage, args.responses, args.infer, args.authorize_inference,
             retry_ambiguous=args.retry_ambiguous)
    print(json.dumps(sources.read(folder / 'STATUS.json'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
