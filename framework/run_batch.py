"""Run the existing Experiment 1 stages by task family; never truncate a trajectory."""
import argparse
import collections
import shutil
from datetime import datetime, timezone
import json
from pathlib import Path
import experiment1 as e
import sources


def compatible_contract(old_cfg, cfg):
    """Historical processing is not new-protocol annotation. Never auto-migrate."""
    semantic_files = ['schemas.json', 'prompts/goals.md', 'prompts/alignment.md',
                      'prompts/operator.md', 'GOAL_CODEBOOK.md',
                      'ALIGNMENT_CODEBOOK.md', 'OPERATOR_CODEBOOK.md']
    a = old_cfg.get('implementation_fingerprints', {})
    b = cfg.get('implementation_fingerprints', e.implementation_fingerprints())
    return (a == b and bool(a) and all(a.get(k) for k in semantic_files)
            and e.model_input_parameters(old_cfg['model']) == e.model_input_parameters(cfg['model'])
            and all(old_cfg.get(k) == cfg.get(k) for k in ('images','codebooks','repeat_confirmations'))
            and {k:v for k,v in old_cfg['trajectory_input'].items() if k not in {'window_units','context_units'}}
                == {k:v for k,v in cfg['trajectory_input'].items() if k not in {'window_units','context_units'}})


def family_source_identity(folder, index_entry=None):
    records = e.read_lines(folder/'normalized_sources.jsonl')
    if not records:
        raise ValueError('Missing actual family sources')
    for key in ('dataset','family_id','task_id','instruction'):
        values = {tr[key] for tr in records}
        if len(values) != 1 or (index_entry and key in index_entry and values != {index_entry[key]}):
            raise ValueError('Batch index disagrees with actual family sources: '+key)
    if index_entry and 'trajectories' in index_entry and index_entry['trajectories'] != [tr['trajectory_id'] for tr in records]:
        raise ValueError('Batch index disagrees with actual Actor trajectories')
    return e.digest(records)


def prepare_batch(config, source, batch):
    batch = e.run_path(batch)
    batch.mkdir(parents=True)
    inputs = batch / 'inputs'
    inputs.mkdir()
    groups = {}
    for line_number, tr in sources.lines(source):
        if tr['split'] not in {'EXPOSED_DEVELOPMENT','MODEL_ANNOTATION_COHORT','SOURCE_ONLY_NOT_E1_SEMANTICALLY_REVIEWED'}:
            raise ValueError('Source split is not authorized for batch annotation: '+tr['split'])
        e.validate_source(tr)
        key = (tr['dataset'], tr['family_id'])
        if key not in groups:
            groups[key] = {'group': f'{len(groups):03d}', 'dataset': tr['dataset'],
                           'family_id': tr['family_id'], 'task_id': tr['task_id'],
                           'instruction': tr['instruction'], 'trajectories': [],
                           'source_lines': [], 'raw_events': 0,
                           'source_jsonl':str(Path(source).resolve()),'original_source_roles':[]}
        group = groups[key]
        if (group['task_id'], group['instruction']) != (tr['task_id'], tr['instruction']):
            raise ValueError('A task family has different instructions; inspect the source binding')
        group['trajectories'].append(tr['trajectory_id'])
        group['source_lines'].append(line_number)
        group['raw_events'] += len(tr['events'])
        group['original_source_roles'].append({'trajectory_id':tr['trajectory_id'],
            'split':tr['split'],'trial_role':tr['trial_role']})
        # Derived model cohort only; the original source-only records remain unchanged.
        tr['split'] = 'MODEL_ANNOTATION_COHORT'
        tr['trial_role'] = 'FULL_TRIAL'
        with (inputs / (group['group'] + '.jsonl')).open('a', encoding='utf-8') as f:
            f.write(json.dumps(tr, ensure_ascii=False) + '\n')
    cfg = sources.read(config)
    index = []
    for group in groups.values():
        folder = batch / group['group']
        e.prepare(cfg, folder, inputs / (group['group'] + '.jsonl'))
        traces = e.read_lines(folder / 'sources.jsonl')
        group['operation_groups'] = sum(len(t['units']) for t in traces)
        group['alignment_requests'] = sum(len(e.windows_for(0,len(t['units'])-1,cfg)) for t in traces)
        group['goal_requests'] = len(e.read_lines(folder / 'requests_goals.jsonl'))
        assert group['goal_requests'] == 1
        index.append({k: v for k, v in group.items() if k != 'instruction'})
        print('PREPARED', group['group'], group['dataset'], len(traces), flush=True)
    e.write_lines(batch / 'BATCH_INDEX.jsonl', index)
    groups_count = sum(g['operation_groups'] for g in index)
    trajectories = sum(len(g['trajectories']) for g in index)
    e.write(batch / 'BATCH_STATUS.json', {
        'status': 'MODEL_REQUESTS_PREPARED_NOT_INFERRED', 'families': len(index),
        'datasets': dict(collections.Counter(g['dataset'] for g in index)),
        'trajectories': trajectories, 'operation_groups': groups_count,
        'planned_requests': {'goals': len(index), 'alignment': sum(g['alignment_requests'] for g in index),
                             'operator': None},
        'operator_request_count': 'Computed from saved continuous Alignment after that stage completes',
        'model_calls': 0, 'human_labels': 0})


def reuse_partial_captures(folder, stage, candidates, cfg):
    """Carry exact-input responses and unknown outcomes; invalid output stays invalid."""
    capture = folder/f'captured_responses_{stage}.jsonl'
    present = {r['request_id'] for r in e.read_lines(capture)} if capture.exists() else set()
    requests = {q['request_id']:q for q in e.read_lines(folder/f'requests_{stage}.jsonl')}
    bound, origins, unknown = {}, {}, {}
    for prior in reversed(candidates):
        old_cfg = sources.read(prior/'config_snapshot.json')
        if not compatible_contract(old_cfg,cfg) or not (prior/f'requests_{stage}.jsonl').exists():
            continue
        old_requests = {q['request_id']:q for q in e.read_lines(prior/f'requests_{stage}.jsonl')}
        matching = {rid for rid,q in requests.items() if rid in old_requests
                    and e.input_identity(old_requests[rid]) == e.input_identity(q)}
        old_capture = prior/capture.name
        if old_capture.exists():
            for answer in e.read_lines(old_capture):
                rid = answer['request_id']
                if (rid in present or rid not in matching or answer['request_sha256'] != old_requests[rid]['request_sha256']
                        or answer.get('authority') not in {'OPEN_SOURCE_MODEL','CLOSED_SOURCE_MODEL','API_MODEL'}):
                    continue
                q = requests[rid]
                bound[rid] = dict(answer,request_sha256=q['request_sha256'],
                    input_identity_sha256=e.input_identity(q),reuse_provenance={
                    'original_run':str(prior),'original_request_sha256':answer['request_sha256'],
                    'original_framework_version':old_requests[rid]['framework_version'],
                    'actual_input_and_contract_identical':True,'new_model_call':False})
                origins[rid] = str(prior)
        failure_path = prior/f'failures_{stage}.json'
        if failure_path.exists():
            for failure in sources.read(failure_path):
                if failure['request_id'] in matching and failure.get('charge_or_outcome_unknown'):
                    unknown[failure['request_id']] = dict(failure,original_run=str(prior))
    if bound:
        with capture.open('a',encoding='utf-8') as stream:
            for rid in requests:
                if rid in bound:
                    stream.write(json.dumps(bound[rid],ensure_ascii=False)+'\n')
    if unknown:
        path=folder/f'failures_{stage}.json'
        existing=sources.read(path) if path.exists() else []
        known={r['request_id'] for r in existing if r.get('charge_or_outcome_unknown')}
        e.write(path,existing+[failure for rid,failure in unknown.items() if rid not in known])
    if bound or unknown:
        reused = sources.read(folder/'REUSED_STAGES.json') if (folder/'REUSED_STAGES.json').exists() else []
        reused.append({'stage':stage,'kind':'BOUND_PARTIAL_CAPTURES',
            'captured_request_origins':origins,'unknown_outcomes_preserved':sorted(unknown),
            'validated_against_actual_input_identity':True,'labels_recomputed':False,
            'invalid_responses_are_not_accepted_labels':True})
        e.write(folder/'REUSED_STAGES.json',reused)


def advance_family(folder, concurrency=None, candidates=None, retry_ambiguous=False):
    """Shared Goals, then independent complete Actor trajectories; no fabricated gaps."""
    cfg = sources.read(folder/'config_snapshot.json')
    if cfg.get('implementation_fingerprints') != e.implementation_fingerprints():
        raise ValueError('Code/Prompt/Schema changed; preserve historical run and use a new batch')
    failures=[]
    verified_stages=[]
    verified_images={}
    for stage in ['goals','alignment','operator']:
        if stage=='operator' and not (folder/'requests_operator.jsonl').exists():
            break
        try:
            if (folder/f'results_{stage}.jsonl').exists():
                e.validate_saved_stage(folder,stage,verified_images=verified_images)
                following={'goals':'alignment','alignment':'operator'}.get(stage)
                if ((following and not (folder/f'requests_{following}.jsonl').exists())
                    or (stage=='alignment' and not (folder/'continuous_alignment.jsonl').exists())
                    or (stage=='operator' and 'operator' not in sources.read(folder/'STATUS.json').get('completed_stages',[]))):
                    e.finish_stage(folder,stage,verified_images)
            else:
                if candidates:
                    reuse_partial_captures(folder,stage,candidates,sources.read(folder/'config_snapshot.json'))
                e.step(folder,stage,infer=True,authorize=True,concurrency=concurrency,
                       retry_ambiguous=retry_ambiguous)
            if (folder/f'results_{stage}.jsonl').exists():
                verified_stages.append(stage)
        except Exception as exc:
            details=folder/f'failures_{stage}.json'
            failures.append({'stage':stage,'error_type':type(exc).__name__,'reason':str(exc),
                             'request_failures':sources.read(details) if details.exists() else []})
            status=sources.read(folder/'STATUS.json')
            status.update(status='PARTIAL_WITH_ERRORS',failed_stage=stage,
                completed_stages=list(verified_stages),
                pending_stages=[s for s in ('goals','alignment','operator') if s not in verified_stages])
            e.write(folder/'STATUS.json',status)
            if stage=='goals' or (folder/f'results_{stage}.jsonl').exists():
                break
    return failures


def infer_batch(batch, authorize, selected=None, concurrency=None, retry_ambiguous=False):
    if not authorize:
        raise PermissionError('Start inference only after API/batch authorization with --authorize-inference')
    batch = e.run_path(batch)
    index = e.read_lines(batch / 'BATCH_INDEX.jsonl')
    if not index or len({g['group'] for g in index}) != len(index):
        raise ValueError('Missing or duplicate task-family batch index; not a completed experiment')
    unknown = set(selected or []) - {g['group'] for g in index}
    if unknown:
        raise ValueError('Unknown task-family group: ' + ', '.join(sorted(unknown)))
    stages = ['goals', 'alignment', 'operator']
    completed, failures = [], []
    for group in index:
        if selected and group['group'] not in selected:
            continue
        folder = batch / group['group']
        try:
            family_source_identity(folder,group)
            family_failures=advance_family(folder,concurrency,retry_ambiguous=retry_ambiguous)
            if family_failures:
                failures.extend(dict(r,group=group['group']) for r in family_failures)
                print('UNCOMPLETED',group['group'],flush=True)
                continue
            completed.append(group['group'])
            print('COMPLETED', group['group'], flush=True)
        except Exception as exc:
            failures.append({'group': group['group'], 'stage': 'family',
                             'error_type': type(exc).__name__, 'reason': str(exc)})
            print('UNCOMPLETED', group['group'], 'family', type(exc).__name__, flush=True)
    calls = sum(r.get('authority') in {'OPEN_SOURCE_MODEL', 'CLOSED_SOURCE_MODEL', 'API_MODEL'}
                for g in index for stage in stages
                if (batch/g['group']/f'captured_responses_{stage}.jsonl').exists()
                for r in e.read_lines(batch/g['group']/f'captured_responses_{stage}.jsonl'))
    e.write(batch / 'BATCH_EXECUTION_STATUS.json', {
        'status': 'SELECTED_MODEL_STAGES_COMPLETE' if not failures else 'PARTIAL_WITH_ERRORS',
        'selected_stages': stages, 'completed_groups': completed,
        'failures': failures, 'captured_model_responses': calls, 'human_labels': 0,
        'scientific_acceptance': 'PENDING_HUMAN_ANNOTATION'})


def continue_batch(config, batch, previous_batches, authorize, concurrency=None, retry_ambiguous=False):
    """Reuse only validated work for the same actual sources and model contract."""
    if not authorize:
        raise PermissionError('Inference requires --authorize-inference')
    batch = e.run_path(batch)
    previous = [e.run_path(p) for p in previous_batches]
    cfg = sources.read(config)
    e.api_backend.validate_config(cfg['model'])
    index = e.read_lines(previous[-1]/'BATCH_INDEX.jsonl')
    if not index or len({g['group'] for g in index}) != len(index):
        raise ValueError('Missing or duplicate task-family batch index; not a completed experiment')
    available = collections.defaultdict(list)
    identities = {}
    for prior in previous:
        for entry in e.read_lines(prior/'BATCH_INDEX.jsonl'):
            folder = prior/entry['group']
            if (folder/'normalized_sources.jsonl').exists():
                identity = family_source_identity(folder,entry)
                identities[folder] = identity
                available[identity].append(folder)
    references = {g['group']:previous[-1]/g['group'] for g in index}
    if len(references) != len(index):
        raise ValueError('Duplicate batch group index')
    contract = e.digest({'implementation':e.implementation_fingerprints(),
        'model':cfg['model'],'execution':cfg.get('execution',{}),
        'images':cfg['images'],'codebooks':cfg['codebooks'],
        'repeat_confirmations':cfg['repeat_confirmations'],'trajectory_input':cfg['trajectory_input'],
        'sources':{name:identities[folder] for name,folder in references.items()}})
    batch.mkdir(exist_ok=True)
    state_path = batch/'BATCH_EXECUTION_STATUS.json'
    state = sources.read(state_path) if state_path.exists() else {
        'status':'RUNNING','version':cfg['version'],'request_contract':contract,
        'previous_batches':[str(p) for p in previous],'preserved_completed_groups':[],
        'completed_groups':[],'failures':[],'preserved_output_runs':{},
        'preserved_input_profiles':{},'human_labels':0}
    if state.get('request_contract') != contract:
        raise ValueError('Continuation configuration/sources changed; preserve checkpoint and use a new batch')
    e.write_lines(batch/'BATCH_INDEX.jsonl',index)
    def checkpoint():
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        e.write(state_path,state)
    checkpoint()
    for group in index:
        name = group['group']
        reference = references[name]
        candidates = available[identities[reference]]
        stage = 'reuse'
        try:
            complete = None
            for prior in candidates:
                if not compatible_contract(sources.read(prior/'config_snapshot.json'),cfg):
                    continue
                if not all((prior/f'results_{s}.jsonl').exists() for s in ('goals','alignment','operator')):
                    continue
                try:
                    checked_images = {}
                    for s in ('goals','alignment','operator'):
                        e.validate_saved_stage(prior,s,verified_images=checked_images)
                except (ValueError,KeyError,IndexError,OSError):
                    continue
                complete = prior
                break
            if complete is not None:
                if name not in state['preserved_completed_groups']:
                    state['preserved_completed_groups'].append(name)
                state['preserved_output_runs'][name] = str(complete)
                state['preserved_input_profiles'][name] = sources.read(complete/'config_snapshot.json')['trajectory_input']
                state['failures'] = [f for f in state['failures'] if f['group']!=name]
                print('PRESERVED',name,flush=True)
                checkpoint()
                continue
            folder = batch/name
            stage = 'prepare'
            if not folder.exists():
                e.prepare(cfg,folder,reference/'normalized_sources.jsonl')
                reused = []
                for s in ('goals','alignment'):
                    if s=='alignment' and not (folder/'results_goals.jsonl').exists():
                        continue
                    for prior in candidates:
                        if not (prior/f'results_{s}.jsonl').exists():
                            continue
                        old_cfg = sources.read(prior/'config_snapshot.json')
                        if not compatible_contract(old_cfg,cfg):
                            continue
                        try:
                            records = e.validate_saved_stage(prior,s)
                        except (ValueError,KeyError,IndexError,OSError):
                            continue
                        if s=='alignment' and [e.model_output(r) for r in e.read_lines(prior/'results_goals.jsonl')] != [e.model_output(r) for r in e.read_lines(folder/'results_goals.jsonl')]:
                            continue
                        for filename in (f'requests_{s}.jsonl',f'results_{s}.jsonl',f'captured_responses_{s}.jsonl'):
                            if (prior/filename).exists():
                                shutil.copy2(prior/filename,folder/filename)
                        reused.append({'stage':s,'original_run':str(prior),
                            'original_input_profile':old_cfg['trajectory_input'],
                            'validated_against_original_requests':True,'labels_recomputed':False})
                        e.write(folder/'REUSED_STAGES.json',reused)
                        e.finish_stage(folder,s)
                        print('REUSED',name,s,str(prior),flush=True)
                        break
            if family_source_identity(folder,group) != identities[reference]:
                raise ValueError('Continuation folder contains different actual task/Actor sources')
            if not compatible_contract(sources.read(folder/'config_snapshot.json'),cfg):
                raise ValueError('Continuation folder uses different model/implementation parameters')
            snapshot = sources.read(folder/'config_snapshot.json')
            if (snapshot['model'] != cfg['model'] or snapshot.get('execution',{}) != cfg.get('execution',{})
                    or snapshot['trajectory_input'] != cfg['trajectory_input']):
                raise ValueError('Pending run settings differ from --config; use a new batch so new settings actually apply')
            family_failures = advance_family(folder,concurrency,candidates,retry_ambiguous)
            state['failures'] = [f for f in state['failures'] if f['group']!=name]
            if family_failures:
                state['failures'].extend(dict(f,group=name) for f in family_failures)
                print('UNCOMPLETED',name,flush=True)
            else:
                if name not in state['completed_groups']:
                    state['completed_groups'].append(name)
                print('COMPLETED',name,flush=True)
        except Exception as exc:
            state['failures'] = [f for f in state['failures'] if f['group']!=name]
            state['failures'].append({'group':name,'stage':stage,'error_type':type(exc).__name__,'reason':str(exc)})
            print('UNCOMPLETED',name,stage,type(exc).__name__,str(exc),flush=True)
        checkpoint()
    state['status'] = 'PARTIAL_WITH_ERRORS' if state['failures'] else 'MODEL_STAGES_COMPLETE_SEMANTIC_ACCEPTANCE_PENDING'
    checkpoint()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--config', required=True)
    p.add_argument('--source', required=True)
    p.add_argument('--batch', required=True)
    p = sub.add_parser('infer')
    p.add_argument('--batch', required=True)
    p.add_argument('--groups', nargs='+', help='Optional complete task-family IDs from BATCH_INDEX.jsonl')
    p.add_argument('--authorize-inference', action='store_true')
    p.add_argument('--concurrency', type=int, help='Concurrent HTTP requests; defaults to the prepared config')
    p.add_argument('--retry-ambiguous', action='store_true')
    p = sub.add_parser('continue')
    p.add_argument('--config', required=True)
    p.add_argument('--batch', required=True)
    p.add_argument('--previous-batches', nargs='+', required=True, help='Newest previous batch first; original full batch last')
    p.add_argument('--concurrency', type=int)
    p.add_argument('--authorize-inference', action='store_true')
    p.add_argument('--retry-ambiguous', action='store_true')
    args = ap.parse_args()
    if args.command == 'prepare':
        prepare_batch(args.config, args.source, args.batch)
    elif args.command == 'infer':
        infer_batch(args.batch, args.authorize_inference, args.groups, args.concurrency, args.retry_ambiguous)
    else:
        continue_batch(args.config, args.batch, args.previous_batches, args.authorize_inference,
                       args.concurrency, args.retry_ambiguous)


if __name__ == '__main__':
    main()
