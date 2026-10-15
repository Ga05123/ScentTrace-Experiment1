"""Real source checks and explicit technical fixtures; no model weights or inference loaded."""
import argparse
import collections
import copy
import json
import math
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from PIL import Image
import experiment1 as e
import sources


def fixture_output(payload, system):
    if 'goal_catalog' not in payload:
        return {'goals':[{'goal_id':'G1','goal_type':'MIXED','description':'Technical fixture, no semantic claim',
                          'constraints':[],'completion_condition':'Not evaluated','depends_on':[],
                          'source_ids':[s['source_id'] for s in payload['source_segments']]}], 'issues':['Technical fixture only']}
    first,last=payload['target_range']['first'],payload['target_range']['last']
    if 'alignment' not in payload:
        return {'ranges':[{'first':first,'last':last,'goal_ids':[],
                           'status':'UNRESOLVED','reason':'Technical fixture only; no semantic judgment'}]}
    return {'units':[{'unit_index':u['unit_index'],'operator':'UNRESOLVED',
                      'reason':'Technical fixture only; no semantic judgment made'}
                     for u in payload['units'] if first<=u['unit_index']<=last]}


def technical_smoke(shortest, cfg, output):
    observed = []
    malformed = [False]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages=body['messages'];payload=json.loads(messages[1]['content'][0]['text'])
            observed.append({'path':self.path,'pictures':sum(x['type']=='image_url' for x in messages[1]['content'])})
            answer=fixture_output(payload,messages[0]['content'])
            reply={'model':'TECHNICAL_FIXTURE_NOT_A_MODEL','choices':[{'message':{'content':'{' if malformed[0] else json.dumps(answer)},'finish_reason':'length' if malformed[0] else 'stop'}]}
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(reply).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    trial_cfg=copy.deepcopy(cfg)
    trial_cfg['model'].update(model_id='TECHNICAL_FIXTURE_NOT_A_MODEL',weight_revision='TEST_FIXTURE_NOT_WEIGHTS',base_url=f'http://127.0.0.1:{server.server_port}/v1')
    actual_call=e.call_model
    def local_fixture(request,config):
        answer=actual_call(request,config)
        answer['authority']='TECHNICAL_TEST_FIXTURE'
        return answer
    tests=[]
    try:
        with mock.patch.object(e,'call_model',side_effect=local_fixture):
            for dataset,tr in sorted(shortest.items()):
                tr=copy.deepcopy(tr);tr.update(split='MODEL_ANNOTATION_COHORT',trial_role='FULL_TRIAL')
                native=output/(dataset+'_complete_input.jsonl');e.write_lines(native,[tr])
                folder=output/('TECHNICAL_'+dataset)
                e.prepare(trial_cfg,folder,native)
                for stage in ('goals', 'alignment'):
                    q=e.read_lines(folder/f'requests_{stage}.jsonl')[0]
                    e.write(folder/f'WIRE_EXAMPLE_{stage}.json',e.wire_body(q,trial_cfg))
                    e.step(folder,stage,infer=True,authorize=True)
                before=(folder/'continuous_alignment.jsonl').read_bytes()
                q=e.read_lines(folder/'requests_operator.jsonl')[0]
                e.write(folder/'WIRE_EXAMPLE_operator.json',e.wire_body(q,trial_cfg))
                e.step(folder,'operator',infer=True,authorize=True)
                # Captured Operator window responses is reused after an interrupted result save.
                (folder/'results_operator.jsonl').unlink()
                start_count=len(observed)
                e.step(folder,'operator',infer=True,authorize=True)
                assert len(observed)==start_count
                assert (folder/'continuous_alignment.jsonl').read_bytes()==before
                state=sources.read(folder/'STATUS.json')
                assert state['model_calls']==0 and state['status']=='TECHNICAL_FIXTURE_COMPLETE'
                tests.append({'dataset':dataset,'native_events':len(tr['events']),
                              'three_stage_chain':'PASS','real_image_HTTP_transport':'PASS',
                              'alignment_immutable':'PASS','resume_without_duplicate_calls':'PASS'})
            # Invalid/truncated JSON must still leave its raw response on disk.
            malformed[0]=True
            tr=copy.deepcopy(next(iter(shortest.values())))
            tr.update(split='MODEL_ANNOTATION_COHORT',trial_role='FULL_TRIAL')
            native=output/'malformed_fixture_input.jsonl';e.write_lines(native,[tr])
            folder=output/'TECHNICAL_MALFORMED_OUTPUT'
            e.prepare(trial_cfg,folder,native)
            try:e.step(folder,'goals',infer=True,authorize=True)
            except ValueError:pass
            saved=e.read_lines(folder/'captured_responses_goals.jsonl')
            assert len(saved)==1 and saved[0]['raw_output']=='{' and saved[0]['parse_error']
            tests.append({'invalid_JSON_raw_response_retention':'PASS'})
    finally:
        server.shutdown();server.server_close();thread.join()
    return {'cases':tests,'fixture_HTTP_requests':len(observed),'real_model_calls':0,
            'semantic_labels':0,'authority':'TECHNICAL_TEST_FIXTURE'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',help='Optional existing normalized source; default is the existing CPU sample config')
    ap.add_argument('--config',required=True)
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    output=e.run_path(args.output);output.mkdir(parents=True)
    cfg=sources.read(args.config)
    shortest={};longest=None;stats={};families={};dimensions={};source_meta=[]
    records=sources.lines(args.source) if args.source else enumerate(
        dict(sources.load(s),trial_role=s['trial_role']) for s in cfg['samples'])
    for _,tr in records:
        e.validate_source(tr)
        group=(tr['dataset'],tr['family_id'])
        identity=(tr['task_id'],tr['instruction'])
        if group in families:assert families[group]==identity
        families[group]=identity
        ds=tr['dataset'];s=stats.setdefault(ds,{'trajectories':0,'events':0,'pre_present':0,'post_present':0,'actors':set(),'images':set(),'repeat_candidates':0,'confirmed_merges':0})
        units,audit=e.join_units(tr,cfg['repeat_confirmations'])
        assert [v['position'] for u in units for v in u['members']]==list(range(len(tr['events'])))
        s['repeat_candidates']+=sum(a['status']=='RETAINED_REPEAT_CANDIDATE_NEEDS_EVIDENCE' for a in audit)
        s['confirmed_merges']+=sum(a['status']=='MERGED_WITH_ALL_MEMBERS_RETAINED' for a in audit)
        s['trajectories']+=1;s['events']+=len(tr['events']);s['actors'].add(tr['actor_id'])
        paths={v[r]['path'] for v in tr['events'] for r in ('pre','post') if v[r]['path']}
        for role in ('pre','post'):s[role+'_present']+=sum(v[role]['status']=='PRESENT' for v in tr['events'])
        s['images']|=paths
        for path in paths:
            if path not in dimensions:
                with Image.open(path) as image:
                    image.load();dimensions[path]=image.size
        if ds not in shortest or len(tr['events'])<len(shortest[ds]['events']):shortest[ds]=tr
        if longest is None or len(paths)>len({v[r]['path'] for v in longest['events'] for r in ('pre','post') if v[r]['path']}):longest=tr
        source_meta.append({'dataset':ds,'trajectory_id':tr['trajectory_id'],'task_id':tr['task_id'],
                            'events':len(tr['events']),'images':sorted(paths)})
        if sum(v['trajectories'] for v in stats.values())%20==0:print('SOURCES_CHECKED',sum(v['trajectories'] for v in stats.values()),flush=True)
    for s in stats.values():s['actors']=sorted(s['actors']);s['images']=len(s['images'])
    weight_files=[]  # Closed API preparation has no local model-weight dependency.
    smoke=technical_smoke(shortest,cfg,output)
    long=copy.deepcopy(longest);long['units'],long['repeat_audit']=e.join_units(long,cfg['repeat_confirmations'])
    long.update(trial_role='FULL_TRIAL')
    technical_goal={'output':{'goals':[{'goal_id':'G1'}]}}
    qs=e.requests_for('alignment',[long],cfg,{'goals':{long['task_id']:technical_goal}})
    q=qs[0];units=q['payload']['units']
    body=e.wire_body(q,cfg)
    pictures=[x for x in body['messages'][1]['content'] if x['type']=='image_url']
    assert len(pictures)==len(q['images'])
    assert [i for req in qs for i in range(req['core'][0],req['core'][1]+1)]==list(range(len(long['units'])))
    long_check={'dataset':long['dataset'],'trajectory_id':long['trajectory_id'],
                'native_events':len(long['events']),'request_units':len(units),'request_images':len(pictures),
                'transport_image_base64_chars':sum(len(x['image_url']['url']) for x in pictures),
                'all_windows_cover_complete_trajectory':True,
                'scope':'Native complete source, fixed 10+2 windows; Goal is a technical placeholder'}
    del body,pictures,q,long
    e.write(output/'SOURCE_DIMENSIONS.json',dimensions)
    e.write_lines(output/'SOURCE_REQUEST_SIZES.jsonl',source_meta)
    result={'status':'CPU_SOURCE_AND_TECHNICAL_CHAIN_PASS','datasets':stats,'families':len(families),
            'decoded_unique_images':len(dimensions),'image_decode_errors':0,
            'task_instruction_consistency':'PASS','source_schema_and_raw_mapping':'PASS',
            'local_model_weights':'NOT_REQUIRED_CLOSED_API','weight_files':weight_files,
            'technical_smoke':smoke,'longest_complete_transport':long_check,
            'paid_or_real_model_calls':0,'human_labels':0,'GPU_inference':'NOT_RUN'}
    e.write(output/'PREFLIGHT_RESULTS.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
