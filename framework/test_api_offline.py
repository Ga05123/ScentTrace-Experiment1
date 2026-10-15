"""Four-state and closed-API checks. Only an explicit loopback mock is contacted."""
import base64
import copy
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from PIL import Image
from jsonschema import Draft202012Validator
import api_backend as api
import experiment1 as e
import run_batch
import sources
from preflight_cpu import fixture_output


@contextmanager
def server_fixture(reply):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            status, data, headers=reply(body, len(seen))
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            for key,value in headers.items(): self.send_header(key,value)
            self.end_headers()
            try: self.wfile.write(data if isinstance(data,bytes) else json.dumps(data).encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', seen
    finally: server.shutdown();server.server_close();thread.join()


def envelope(output):
    return {'id':'offline-fixture-response', 'model':'OFFLINE_FIXTURE_NOT_MODEL',
            'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18},
            'choices':[{'message':{'content': output if isinstance(output,str) else json.dumps(output)},
                        'finish_reason':'stop'}]}


class FourStateAPIChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tempfile.tempdir=str(e.HERE/'runs'/'tmp')
        os.environ['SCENTTRACE_API_KEY']='offline-test-placeholder'
        cls.cfg=sources.read(e.HERE/'config.json')
        cls.tr=sources.load(cls.cfg['samples'][0]);cls.tr['trial_role']='FULL_TRIAL'
        cls.tr['units'],cls.tr['repeat_audit']=e.join_units(cls.tr,cls.cfg['repeat_confirmations'])

    def config(self, endpoint=None):
        cfg=copy.deepcopy(self.cfg)
        cfg['model'].update(model_id='OFFLINE_FIXTURE_NOT_MODEL',weight_revision='OFFLINE_NO_WEIGHTS')
        if endpoint: cfg['model']['base_url']=endpoint
        return cfg

    def alignment_request(self):
        return e.requests_for('alignment',[self.tr],self.cfg,
            {'goals':{self.tr['task_id']:{'output':{'goals':[{'goal_id':'G1'},{'goal_id':'G2'}]}}}})[0]

    def test_all_three_requests_inject_full_stage_book_and_hashes(self):
        cfg=self.config()
        goals=e.requests_for('goals',[self.tr],cfg)[0]
        align=self.alignment_request()
        prior={'goals':{self.tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}},
               'alignment':{self.tr['trajectory_id']:[{'output':{'ranges':[
                   {'first':0,'last':23,'goal_ids':['G1'],'status':'ALIGNED','reason':'Fixture'}]}}]}}
        operator=e.requests_for('operator',[self.tr],cfg,prior)[0]
        for q in (goals,align,operator):
            book=e.HERE/cfg['codebooks'][q['stage']]
            prompt=e.HERE/'prompts'/f"{q['stage']}.md"
            self.assertIn(book.read_text(encoding='utf-8-sig'),q['messages'][0]['content'])
            self.assertTrue(q['messages'][0]['content'].startswith(prompt.read_text(encoding='utf-8-sig')))
            self.assertEqual(q['contract_hashes']['codebook_sha256'],hashlib.sha256(book.read_bytes()).hexdigest())
            self.assertEqual(q['contract_hashes']['schema_sha256'],e.digest(e.output_schema(q)))
            self.assertEqual(q['contract_hashes']['implementation_sha256'],e.digest(e.implementation_fingerprints()))
            wire=e.wire_body(q,cfg)['response_format']['json_schema']
            self.assertTrue(wire['strict'])
            self.assertEqual(wire['schema'],e.output_schema(q))
        self.assertEqual(set(goals['payload']),{'instruction','source_segments'})
        self.assertFalse(goals['images'])
        self.assertEqual(''.join(x['text'] for x in goals['payload']['source_segments']),self.tr['instruction'])

    def test_four_states_schema_and_one_primary_goal(self):
        q=self.alignment_request()
        states=q['schema']['properties']['ranges']['items']['properties']['status']['enum']
        self.assertEqual(states,['ALIGNED','SUPPORT_NO_GOAL','OFF_GOAL','UNRESOLVED'])
        Draft202012Validator.check_schema(q['schema'])
        Draft202012Validator.check_schema(e.output_schema(q))
        def out(state,ids):
            return {'ranges':[{'first':q['core'][0],'last':q['core'][1],
                              'goal_ids':ids,'status':state,'reason':'Structural fixture only'}]}
        for state,ids in [('ALIGNED',['G1']),('OFF_GOAL',[]),('SUPPORT_NO_GOAL',[]),
                          ('UNRESOLVED',[]),('UNRESOLVED',['G1','G2'])]:
            e.validate_response(q,out(state,ids))
            Draft202012Validator(e.output_schema(q)).validate(out(state,ids))
        for state,ids in [('ALIGNED',[]),('ALIGNED',['G1','G2']),('OFF_GOAL',['G1']),
                          ('SUPPORT_NO_GOAL',['G1']),('MULTI_GOAL',['G1','G2'])]:
            with self.assertRaises(ValueError): e.validate_response(q,out(state,ids))

    def test_legal_mixed_without_issues(self):
        q=e.requests_for('goals',[self.tr],self.cfg)[0]
        answer=fixture_output(q['payload'],q['messages'][0]['content'])
        answer['issues']=[]
        e.validate_response(q,answer)
        self.assertIn('legitimate MIXED',q['messages'][0]['content'])

    def test_off_goal_operator_guard_does_not_relabel(self):
        prior={'goals':{self.tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}},
               'alignment':{self.tr['trajectory_id']:[{'output':{'ranges':[
                   {'first':0,'last':23,'goal_ids':[],'status':'OFF_GOAL','reason':'Fixture'}]}}]}}
        q=e.requests_for('operator',[self.tr],self.cfg,prior)[0]
        out=fixture_output(q['payload'],q['messages'][0]['content'])
        for allowed in ('INSPECT','EXECUTE','SUPPORT','UNRESOLVED'):
            out['units'][0]['operator']=allowed;e.validate_response(q,out)
        for invalid in ('COMMIT','LEAVE'):
            out['units'][0]['operator']=invalid
            original=copy.deepcopy(out)
            with self.assertRaisesRegex(ValueError,'OFF_GOAL'): e.validate_response(q,out)
            self.assertEqual(out,original)

    def test_stitching_preserves_off_goal_unresolved_and_return(self):
        rows=[{'first':0,'last':1,'goal_ids':['G1'],'status':'ALIGNED','reason':'a'},
              {'first':2,'last':3,'goal_ids':[],'status':'OFF_GOAL','reason':'b'},
              {'first':4,'last':4,'goal_ids':[],'status':'OFF_GOAL','reason':'c'},
              {'first':5,'last':5,'goal_ids':['G1','G2'],'status':'UNRESOLVED','reason':'d'},
              {'first':6,'last':6,'goal_ids':['G1'],'status':'ALIGNED','reason':'e'}]
        result=e.continuous_ranges([{'output':{'ranges':rows}}])
        self.assertEqual([(r['first'],r['last']) for r in result],[(0,1),(2,4),(5,5),(6,6)])
        rows[1]['status']='MULTI_GOAL'
        with self.assertRaisesRegex(ValueError,'auto-migrated'): e.continuous_ranges([{'output':{'ranges':rows}}])

    def test_complete_schema_and_token_parameter(self):
        q=self.alignment_request();cfg=self.config()
        body=e.wire_body(q,cfg)
        self.assertNotIn('structured_outputs',body);self.assertNotIn('chat_template_kwargs',body)
        schema=body['response_format']['json_schema']['schema']
        Draft202012Validator.check_schema(schema)
        self.assertEqual(schema,e.output_schema(q))
        self.assertIn('oneOf',schema['properties']['ranges']['items'])
        cfg['model']['temperature']=None
        cfg['model']['token_parameter']='max_completion_tokens'
        body=e.wire_body(q,cfg)
        self.assertIn('max_completion_tokens',body);self.assertNotIn('max_tokens',body)
        self.assertNotIn('temperature',body)

    def test_output_downgrades_rejected_before_http(self):
        q=self.alignment_request()
        for mode in ('json_object','none'):
            cfg=self.config()
            cfg['model']['capabilities']['structured_output']=mode
            with mock.patch.object(api,'complete',side_effect=AssertionError('HTTP must not happen')):
                with self.assertRaisesRegex(ValueError,'output downgrade'):
                    e.call_model(q,cfg)

    def test_normal_operator_wire_enforces_every_target_index(self):
        cfg=self.config()
        prior={'goals':{self.tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}},
               'alignment':{self.tr['trajectory_id']:[{'output':{'ranges':[
                   {'first':0,'last':23,'goal_ids':['G1'],'status':'ALIGNED','reason':'Fixture'}]}}]}}
        q=e.requests_for('operator',[self.tr],cfg,prior)[0]
        wire=e.wire_body(q,cfg)['response_format']['json_schema']['schema']
        array=wire['properties']['units']
        targets=list(range(q['core'][0],q['core'][1]+1))
        self.assertEqual(len(targets),10)
        self.assertEqual(array['minItems'],10);self.assertEqual(array['maxItems'],10)
        self.assertEqual([x['properties']['unit_index']['const'] for x in array['prefixItems']],targets)
        self.assertIs(array['items'],False)
        valid=fixture_output(q['payload'],q['messages'][0]['content'])
        Draft202012Validator(wire).validate(valid)
        for defect in ('missing','duplicate','reorder','context'):
            bad=copy.deepcopy(valid)
            if defect=='missing':bad['units'].pop()
            elif defect=='duplicate':bad['units'][1]['unit_index']=targets[0]
            elif defect=='reorder':bad['units'][0],bad['units'][1]=bad['units'][1],bad['units'][0]
            else:bad['units'][-1]['unit_index']=targets[-1]+1
            with self.subTest(defect=defect):
                self.assertTrue(list(Draft202012Validator(wire).iter_errors(bad)))

    def test_native_images_encoded_no_silent_text_fallback(self):
        q=self.alignment_request();cfg=self.config()
        body=e.wire_body(q,cfg)
        content=body['messages'][1]['content']
        photos=[x for x in content if x['type']=='image_url']
        self.assertEqual(len(photos),len(q['images']))
        for photo in photos:
            image=Image.open(io.BytesIO(base64.b64decode(photo['image_url']['url'].split(',')[1])))
            self.assertEqual(image.format,'JPEG');self.assertLessEqual(max(image.size),1536)
        cfg['model']['capabilities']['images']=False
        with mock.patch.object(api,'complete',side_effect=AssertionError('HTTP must not happen')):
            with self.assertRaises(api.ImageUnsupportedError): e.call_model(q,cfg)
        self.assertTrue(q['images'])

    def test_missing_key_and_unsafe_config_fail_without_http(self):
        cfg=self.config();q=e.requests_for('goals',[self.tr],cfg)[0]
        with mock.patch.dict(os.environ,{},clear=True):
            with self.assertRaises(api.AuthenticationError): e.call_model(q,cfg)
        for field in ('api_key','headers'):
            bad=copy.deepcopy(cfg['model']);bad[field]='never-save'
            with self.assertRaises(ValueError):api.validate_config(bad)
        bad=copy.deepcopy(cfg['model']);bad['base_url']='https://key:secret@api.example.com/v1'
        with self.assertRaises(ValueError):api.validate_config(bad)

    def test_http_errors_are_classified_redacted_and_not_retried(self):
        cases=[(401,'bad key',api.AuthenticationError),(429,'limited',api.RateLimitError),
               (400,'context_length_exceeded',api.ContextLimitError),
               (400,'unsupported image_url',api.ImageUnsupportedError),
               (400,'unsupported response_format',api.StructuredOutputUnsupportedError),
               (500,'server error',api.APIError)]
        for status,message,kind in cases:
            with self.subTest(status=status,message=message):
                def reply(body,count):return status,{'error':message+' offline-test-placeholder'},{'Retry-After':'2'}
                with server_fixture(reply) as (url,seen):
                    cfg=self.config(url);q=e.requests_for('goals',[self.tr],cfg)[0]
                    with self.assertRaises(kind) as result:e.call_model(q,cfg)
                    self.assertEqual(len(seen),1)
                    self.assertNotIn('offline-test-placeholder',str(result.exception))
                    self.assertEqual(result.exception.status,status)
                    self.assertEqual(result.exception.retry_after,'2')

    def test_timeout_is_ambiguous_and_not_automatically_retried(self):
        def reply(body,count):time.sleep(.15);return 200,envelope('{}'),{}
        with server_fixture(reply) as (url,seen):
            cfg=self.config(url);cfg['model']['timeout_seconds']=.02
            q=e.requests_for('goals',[self.tr],cfg)[0]
            with self.assertRaises(api.APITimeout) as result:e.call_model(q,cfg)
            self.assertTrue(result.exception.ambiguous);self.assertEqual(len(seen),1)

    def test_explicit_429_retry_is_bounded_and_preserves_body(self):
        def reply(body,count):
            if count==1:return 429,{'error':'rate limit'},{'Retry-After':'0'}
            return 200,envelope('{}'),{}
        with server_fixture(reply) as (url,seen):
            cfg=self.config(url);q=e.requests_for('goals',[self.tr],cfg)[0]
            cfg['execution'].update(request_interval_seconds=0,rate_limit_retries=1)
            answer=e.call_model(q,cfg)
            self.assertEqual(len(seen),2);self.assertEqual(seen[0],seen[1])
            self.assertEqual(answer['transport_attempts'],2)
            self.assertEqual(answer['rejected_rate_limit_attempts'][0]['http_status'],429)
        with server_fixture(lambda body,count:(429,{'error':'limited'},{'Retry-After':'0'})) as (url,seen):
            cfg=self.config(url);cfg['execution']['rate_limit_retries']=1
            q=e.requests_for('goals',[self.tr],cfg)[0]
            with self.assertRaises(api.RateLimitError):e.call_model(q,cfg)
            self.assertEqual(len(seen),2)

    def test_request_interval_is_execution_only_not_semantic_identity(self):
        cfg=self.config();q=e.requests_for('goals',[self.tr],cfg)[0]
        cfg['execution'].update(request_interval_seconds=10,rate_limit_retries=2)
        revised=e.requests_for('goals',[self.tr],cfg)[0]
        self.assertEqual(e.input_identity(q),e.input_identity(revised))
        self.assertEqual(e.wire_body(q,cfg),e.wire_body(revised,cfg))

    def test_closed_response_attribution_usage_and_one_format_retry(self):
        def reply(body,count):
            payload=json.loads(body['messages'][1]['content'][0]['text'])
            answer='{' if count==1 else fixture_output(payload,body['messages'][0]['content'])
            return 200,envelope(answer),{'x-request-id':'offline-request-id'}
        with server_fixture(reply) as (url,seen):
            cfg=self.config(url);q=e.requests_for('goals',[self.tr],cfg)[0]
            answers,error=e.infer_request(q,cfg)
            self.assertIsNone(error);self.assertEqual(len(seen),2)
            self.assertEqual(answers[0]['raw_output'],'{')
            self.assertEqual(answers[1]['authority'],'CLOSED_SOURCE_MODEL')
            self.assertEqual(answers[1]['provider_request_id'],'offline-request-id')
            self.assertEqual(answers[1]['usage']['total_tokens'],18)
            self.assertEqual(answers[1]['contract_hashes'],q['contract_hashes'])
            self.assertIn('Output-format correction only',seen[1]['messages'][0]['content'])

    def test_persisted_invalid_responses_and_no_unchanged_paid_retry(self):
        with server_fixture(lambda body,count:(200,envelope('{'),{})) as (url,seen):
            cfg=self.config(url)
            with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);native=root/'native.jsonl'
                e.write_lines(native,[{k:v for k,v in self.tr.items() if k not in {'units','repeat_audit'}}])
                folder=root/'case';e.prepare(cfg,folder,native)
                for attempt in range(2):
                    with self.assertRaises(ValueError):e.step(folder,'goals',infer=True,authorize=True)
                self.assertEqual(len(seen),2)
                saved=e.read_lines(folder/'captured_responses_goals.jsonl')
                self.assertEqual([r['format_retry'] for r in saved],[0,1])
                self.assertFalse((folder/'results_goals.jsonl').exists())
                self.assertTrue(sources.read(folder/'failures_goals.json'))

    def test_codebook_change_invalidates_historical_stage_reuse(self):
        cfg=self.config();old=dict(cfg,implementation_fingerprints=e.implementation_fingerprints())
        self.assertTrue(run_batch.compatible_contract(old,cfg))
        for name in ('GOAL_CODEBOOK.md','ALIGNMENT_CODEBOOK.md','OPERATOR_CODEBOOK.md','schemas.json'):
            bad=copy.deepcopy(old);bad['implementation_fingerprints'][name]='previous-definition'
            self.assertFalse(run_batch.compatible_contract(bad,cfg))
        bad=copy.deepcopy(old);bad['implementation_fingerprints'].pop('GOAL_CODEBOOK.md')
        self.assertFalse(run_batch.compatible_contract(bad,cfg))

    def test_ambiguous_outcome_checkpoint_blocks_duplicate_until_explicit_retry(self):
        def reply(body,count):
            if count==1:return 500,{'error':'unknown server outcome'},{}
            payload=json.loads(body['messages'][1]['content'][0]['text'])
            return 200,envelope(fixture_output(payload,body['messages'][0]['content'])),{}
        with server_fixture(reply) as (url,seen):
            cfg=self.config(url)
            with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);native=root/'native.jsonl'
                e.write_lines(native,[{k:v for k,v in self.tr.items() if k not in {'units','repeat_audit'}}])
                folder=root/'case';e.prepare(cfg,folder,native)
                with self.assertRaises(api.APIError):e.step(folder,'goals',infer=True,authorize=True)
                self.assertEqual(len(seen),1)
                self.assertEqual(sources.read(folder/'STATUS.json')['status'],'PARTIAL_WITH_ERRORS')
                with self.assertRaisesRegex(api.APIError,'Previous API outcome'):
                    e.step(folder,'goals',infer=True,authorize=True)
                self.assertEqual(len(seen),1)
                e.step(folder,'goals',infer=True,authorize=True,retry_ambiguous=True)
                self.assertEqual(len(seen),2)
                self.assertTrue((folder/'results_goals.jsonl').exists())

    def test_only_reviewed_source_adapter_fixes_differ_from_baseline(self):
        baseline=e.HERE.parent/'EXPERIMENT_1_CURRENT'
        for name in ('source_schema.json','resolve_sources.py'):
            self.assertEqual((baseline/name).read_bytes(),(e.HERE/name).read_bytes())
        import ast
        def functions(path):
            text=path.read_text(encoding='utf-8-sig');lines=text.splitlines()
            return {n.name:'\n'.join(lines[n.lineno-1:n.end_lineno]) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
        a=functions(baseline/'experiment1.py');b=functions(e.HERE/'experiment1.py')
        for name in ('join_units','validate_source','evidence_units','source_segments','compact_code',
                     'compact_payload','goal_catalog','windows_for','window_context'):
            # validate_source calls the improved schema validator, its source logic itself is intact.
            self.assertEqual(a[name],b[name],name)
        a=functions(baseline/'sources.py');b=functions(e.HERE/'sources.py')
        for name in ('read','lines','public_call','image','event','om2w','osworld','webchain','load'):
            self.assertEqual(a[name],b[name],name)

    def test_vendor_adapter_boundary_is_explicit(self):
        cfg=self.config();cfg['model']['backend']='custom_provider'
        q=e.requests_for('goals',[self.tr],cfg)[0]
        with self.assertRaisesRegex(NotImplementedError,'documentation'):e.wire_body(q,cfg)

    def small_native(self,dataset='OM2W'):
        tr=copy.deepcopy(next(t for t in e.read_lines(e.HERE/'runs/inputs/qualified_smoke_four.jsonl') if t['dataset']==dataset))
        tr.update(split='MODEL_ANNOTATION_COHORT',trial_role='FULL_TRIAL')
        return tr

    def complete_family(self,root,dataset='OM2W'):
        root.mkdir(parents=True)
        cfg=self.config();tr=self.small_native(dataset)
        e.write_lines(root/'native.jsonl',[tr])
        folder=root/'000';e.prepare(cfg,folder,root/'native.jsonl')
        e.write_lines(root/'BATCH_INDEX.jsonl',[{'group':'000','dataset':tr['dataset'],
            'task_id':tr['task_id'],'family_id':tr['family_id'],'trajectories':[tr['trajectory_id']]}])
        def fixture(q,c):
            return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                'model':c['model']['model_id'],'authority':'TECHNICAL_TEST_FIXTURE',
                'output':fixture_output(q['payload'],q['messages'][0]['content'])}
        with mock.patch.object(e,'call_model',side_effect=fixture):
            for stage in ('goals','alignment','operator'):e.step(folder,stage,infer=True,authorize=True)
        return cfg,folder

    def test_completed_cache_must_validate_rows_binding_and_implementation(self):
        for defect in ('empty','binding','implementation'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg,folder=self.complete_family(root/'batch')
                if defect=='empty':(folder/'results_operator.jsonl').write_text('',encoding='utf-8')
                elif defect=='binding':
                    records=e.read_lines(folder/'results_operator.jsonl');records[0]['request_sha256']='OTHER_SOURCE'
                    e.write_lines(folder/'results_operator.jsonl',records)
                else:
                    snapshot=sources.read(folder/'config_snapshot.json');snapshot['implementation_fingerprints']['api_backend.py']='OLD_CODE'
                    e.write(folder/'config_snapshot.json',snapshot)
                with mock.patch.object(e,'call_model',side_effect=AssertionError('No duplicate paid call')):
                    run_batch.infer_batch(folder.parent,True)
                state=sources.read(folder.parent/'BATCH_EXECUTION_STATUS.json')
                self.assertEqual(state['status'],'PARTIAL_WITH_ERRORS')
                self.assertFalse(state['completed_groups'])

    def test_reuse_compares_all_model_inputs_but_not_execution_pacing(self):
        cfg=self.config();old=dict(copy.deepcopy(cfg),implementation_fingerprints=e.implementation_fingerprints())
        for key,value in [('temperature',0.8),('max_tokens',dict(cfg['model']['max_tokens'],operator=100)),
                          ('provider_parameters',{'enable_thinking':True}),('base_url','https://another-provider.example/v1')]:
            new=copy.deepcopy(cfg);new['model'][key]=value
            with self.subTest(key=key):self.assertFalse(run_batch.compatible_contract(old,new))
        new=copy.deepcopy(cfg);new['execution'].update(request_interval_seconds=60,request_concurrency=1)
        new['model']['timeout_seconds']+=1
        self.assertTrue(run_batch.compatible_contract(old,new))

    def test_continuation_uses_actual_task_identity_and_rechecks_configuration(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp)
            cfg,a=self.complete_family(root/'a','OM2W')
            _,b=self.complete_family(root/'b','AgentNet')
            e.write(root/'cfg.json',cfg)
            with mock.patch.object(e,'call_model',side_effect=AssertionError('No paid call')):
                run_batch.continue_batch(root/'cfg.json',root/'continued',[a.parent,b.parent],True)
            state=sources.read(root/'continued/BATCH_EXECUTION_STATUS.json')
            self.assertEqual(state['preserved_output_runs']['000'],str(b))
            changed=copy.deepcopy(cfg);changed['model']['temperature']=0.8;e.write(root/'new_cfg.json',changed)
            with mock.patch.object(e,'call_model',side_effect=AssertionError('No paid call')):
                with self.assertRaisesRegex(ValueError,'configuration/sources changed'):
                    run_batch.continue_batch(root/'new_cfg.json',root/'continued',[a.parent,b.parent],True)

    def test_wrong_bound_capture_never_enters_format_correction(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native()])
            folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
            q=e.read_lines(folder/'requests_goals.jsonl')[0]
            e.write_lines(folder/'captured_responses_goals.jsonl',[{'request_id':q['request_id'],
                'request_sha256':'WRONG_SOURCE','authority':'API_MODEL','model':cfg['model']['model_id'],
                'format_retry':0,'raw_output':'UNRELATED_OLD_LABELS','output':None}])
            with mock.patch.object(e,'call_model',side_effect=AssertionError('Wrong input must not be corrected')):
                with self.assertRaisesRegex(ValueError,'different input/prompt/schema'):
                    e.step(folder,'goals',infer=True,authorize=True)
            self.assertFalse((folder/'results_goals.jsonl').exists())

    def test_refusal_and_wrong_model_responses_are_saved_and_not_reissued(self):
        for defect in ('refusal','wrong_model'):
            def reply(body,count):
                payload=json.loads(body['messages'][1]['content'][0]['text'])
                raw=envelope(fixture_output(payload,body['messages'][0]['content']))
                if defect=='refusal':raw['choices'][0]['message']={'content':None,'refusal':'OFFLINE_REFUSAL'}
                else:raw['model']='OFFLINE_OTHER_MODEL'
                return 200,raw,{}
            with self.subTest(defect=defect),server_fixture(reply) as (url,seen),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg=self.config(url);e.write_lines(root/'native.jsonl',[self.small_native()])
                folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
                for _ in range(2):
                    with self.assertRaises(api.APIResponseError):e.step(folder,'goals',infer=True,authorize=True)
                self.assertEqual(len(seen),1)
                saved=e.read_lines(folder/'captured_responses_goals.jsonl')
                self.assertEqual(len(saved),1);self.assertEqual(saved[0]['usage']['total_tokens'],18)
                self.assertTrue(saved[0]['response_error']);self.assertIn('choices',saved[0]['raw_api_response'])
                self.assertFalse((folder/'results_goals.jsonl').exists())

    def test_empty_or_duplicate_family_index_is_never_complete(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();e.write(root/'config.json',self.config())
            for entries in ([],[{'group':'000'},{'group':'000'}]):
                e.write_lines(old/'BATCH_INDEX.jsonl',entries)
                with self.subTest(entries=entries),mock.patch.object(e,'call_model') as paid:
                    with self.assertRaisesRegex(ValueError,'batch index'):
                        run_batch.infer_batch(old,True)
                    with self.assertRaisesRegex(ValueError,'batch index'):
                        run_batch.continue_batch(root/'config.json',root/'next',[old],True)
                    paid.assert_not_called()

    def test_bound_failures_survive_new_batch_without_duplicate_calls(self):
        for kind in ('refusal','unknown_outcome','correction_unknown'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native()])
                old=root/'old';new=root/'new'
                e.prepare(cfg,old,root/'native.jsonl');e.prepare(cfg,new,root/'native.jsonl')
                q=e.read_lines(old/'requests_goals.jsonl')[0]
                if kind in {'refusal','correction_unknown'}:
                    e.write_lines(old/'captured_responses_goals.jsonl',[{
                        'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                        'authority':'API_MODEL','model':cfg['model']['model_id'],
                        'response_error':'Provider refused request' if kind=='refusal' else None,
                        'output':None if kind=='refusal' else {},'raw_output':'{}','format_retry':False}])
                if kind in {'unknown_outcome','correction_unknown'}:
                    e.write(old/'failures_goals.json',[{'request_id':q['request_id'],
                        'charge_or_outcome_unknown':True,'reason':'Timeout; no captured response'}])
                run_batch.reuse_partial_captures(new,'goals',[old],sources.read(new/'config_snapshot.json'))
                with mock.patch.object(e,'call_model') as paid:
                    with self.assertRaises(api.APIError):e.step(new,'goals',infer=True,authorize=True)
                    paid.assert_not_called()
                self.assertFalse((new/'results_goals.jsonl').exists())

    def test_malformed_success_envelope_is_captured_without_retry(self):
        for data in (b'not a JSON envelope',[],{'choices':[None]},
                     {'choices':[{'message':None}]},
                     {'choices':[{'message':{'content':[{'type':'unknown'}]}}]},
                     {'choices':[{},{}]}):
            with self.subTest(data=data),server_fixture(lambda *_:(200,data,{})) as (url,seen),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg=self.config(url);e.write_lines(root/'native.jsonl',[self.small_native()])
                folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
                for _ in range(2):
                    with self.assertRaises(api.APIResponseError):e.step(folder,'goals',infer=True,authorize=True)
                self.assertEqual(len(seen),1)
                saved=e.read_lines(folder/'captured_responses_goals.jsonl')
                self.assertEqual(len(saved),1);self.assertTrue(saved[0]['response_error'])
                self.assertIn('raw_api_response',saved[0])

    def test_resume_does_not_silently_ignore_changed_runtime_settings(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg,old=self.complete_family(root/'old')
            e.write(root/'config.json',cfg)
            run_batch.continue_batch(root/'config.json',root/'next',[root/'old'],True)
            for field,value in (('timeout_seconds',cfg['model']['timeout_seconds']+1),('api_key_env','DIFFERENT_KEY_ENV')):
                changed=copy.deepcopy(cfg);changed['model'][field]=value;e.write(root/'config.json',changed)
                with self.subTest(field=field),self.assertRaisesRegex(ValueError,'configuration/sources changed'):
                    run_batch.continue_batch(root/'config.json',root/'next',[root/'old'],True)
            changed=copy.deepcopy(cfg);changed['execution']['request_interval_seconds']=3;e.write(root/'config.json',changed)
            with self.assertRaisesRegex(ValueError,'configuration/sources changed'):
                run_batch.continue_batch(root/'config.json',root/'next',[root/'old'],True)

    def test_prepared_sources_images_and_prompt_are_checked_before_calls(self):
        for defect in ('source','missing_image','missing_unit','prompt','target_range','parent_output'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native()])
                folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
                q=e.read_lines(folder/'requests_goals.jsonl')[0]
                answer={'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                    'authority':'TECHNICAL_TEST_FIXTURE','model':'TECHNICAL_FIXTURE',
                    'output':fixture_output(q['payload'],q['messages'][0]['content'])}
                e.write_lines(root/'answer.jsonl',[answer]);e.step(folder,'goals',response_path=root/'answer.jsonl')
                requests=e.read_lines(folder/'requests_alignment.jsonl');q=requests[0]
                if defect=='source':
                    traces=e.read_lines(folder/'sources.jsonl');traces[0]['actor_id']='WRONG_ACTOR'
                    e.write_lines(folder/'sources.jsonl',traces)
                elif defect=='parent_output':
                    parent=e.read_lines(folder/'results_goals.jsonl')
                    parent[0]['output']['goals'][0]['description']='Changed accepted Goal without model evidence'
                    e.write_lines(folder/'results_goals.jsonl',parent)
                else:
                    if defect=='missing_image':q['images']=[];q['native_image_digests']={}
                    elif defect=='missing_unit':q['payload']['units']=q['payload']['units'][1:]
                    elif defect=='prompt':q['messages'][0]['content']='Old or unrelated prompt'
                    else:q['payload']['target_range']={'first':100,'last':200}
                    q['input_identity_sha256']=e.input_identity(q)
                    q.pop('request_sha256');q['request_sha256']=e.digest(q)
                    e.write_lines(folder/'requests_alignment.jsonl',requests)
                with mock.patch.object(e,'call_model') as paid:
                    with self.assertRaises(ValueError):e.step(folder,'alignment',infer=True,authorize=True)
                    paid.assert_not_called()

    def model_fixture(self,q,cfg):
        output=fixture_output(q['payload'],q['messages'][0]['content'])
        return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
            'model':cfg['model']['model_id'],'authority':'API_MODEL',
            'output':output,'raw_output':json.dumps(output),'audit_fixture_only':True}

    def test_pending_window_change_is_rejected_and_fresh_batch_uses_new_profile(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();cfg=self.config()
            prior=copy.deepcopy(cfg);prior['trajectory_input'].update(window_units=20,context_units=1)
            e.write_lines(root/'native.jsonl',[self.small_native('OSWorld')])
            e.prepare(prior,old/'000',root/'native.jsonl');e.write_lines(old/'BATCH_INDEX.jsonl',[{'group':'000'}])
            e.write(root/'new.json',cfg)
            with mock.patch.object(e,'call_model') as paid:
                run_batch.continue_batch(root/'new.json',old,[old],True)
                paid.assert_not_called()
            state=sources.read(old/'BATCH_EXECUTION_STATUS.json')
            self.assertEqual(state['status'],'PARTIAL_WITH_ERRORS')
            self.assertIn('Pending run settings differ',state['failures'][0]['reason'])
            with mock.patch.object(e,'call_model',side_effect=self.model_fixture):
                run_batch.continue_batch(root/'new.json',root/'fresh',[old],True)
            state=sources.read(root/'fresh/BATCH_EXECUTION_STATUS.json')
            self.assertFalse(state['failures'])
            qs=e.read_lines(root/'fresh/000/requests_alignment.jsonl')
            self.assertEqual([q['core'] for q in qs],[[0,9],[10,12]])
            self.assertEqual([u['unit_index'] for u in qs[0]['payload']['units']],list(range(12)))

    def test_capture_reuse_fills_empty_and_partial_files_without_rewriting_valid_rows(self):
        for partial in (False,True):
            with self.subTest(partial=partial),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native('OSWorld')])
                old=root/'old';new=root/'new'
                e.prepare(cfg,old,root/'native.jsonl');e.prepare(cfg,new,root/'native.jsonl')
                stage='alignment' if partial else 'goals'
                if partial:
                    with mock.patch.object(e,'call_model',side_effect=self.model_fixture):
                        e.step(old,'goals',infer=True,authorize=True);e.step(new,'goals',infer=True,authorize=True)
                qs=e.read_lines(old/f'requests_{stage}.jsonl')
                captured=[self.model_fixture(q,cfg) for q in qs]
                e.write_lines(old/f'captured_responses_{stage}.jsonl',captured)
                path=new/f'captured_responses_{stage}.jsonl';e.write_lines(path,captured[:1] if partial else [])
                before=path.read_bytes()
                run_batch.reuse_partial_captures(new,stage,[old],sources.read(new/'config_snapshot.json'))
                self.assertTrue(path.read_bytes().startswith(before))
                self.assertEqual(len(e.read_lines(path)),len(qs))
                with mock.patch.object(e,'call_model') as paid:
                    e.step(new,stage,infer=True,authorize=True);paid.assert_not_called()

    def test_damaged_utf8_is_retained_as_error_and_never_accepted_or_reissued(self):
        def reply(body,count):
            payload=json.loads(body['messages'][1]['content'][0]['text'])
            output=fixture_output(payload,body['messages'][0]['content']);output['goals'][0]['description']='中文'
            raw=envelope(json.dumps(output,ensure_ascii=False))
            return 200,json.dumps(raw,ensure_ascii=False).encode('utf-8').replace('中'.encode('utf-8'),b'\xff'),{}
        with server_fixture(reply) as (url,seen),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config(url);e.write_lines(root/'native.jsonl',[self.small_native()])
            folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
            for _ in range(2):
                with self.assertRaisesRegex(api.APIResponseError,'UTF-8'):e.step(folder,'goals',infer=True,authorize=True)
            self.assertEqual(len(seen),1)
            saved=e.read_lines(folder/'captured_responses_goals.jsonl')
            self.assertIsNone(saved[0]['output']);self.assertTrue(saved[0]['response_error'])
            self.assertIn('\ufffd',saved[0]['raw_api_response'])
            self.assertFalse((folder/'results_goals.jsonl').exists())

    def test_duplicate_json_keys_are_rejected_with_only_one_model_format_correction(self):
        def reply(body,count):
            payload=json.loads(body['messages'][1]['content'][0]['text'])
            output=fixture_output(payload,body['messages'][0]['content'])
            text='{"goals":[],"goals":'+json.dumps(output['goals'])+',"issues":[]}'
            return 200,envelope(text if count==1 else output),{}
        with server_fixture(reply) as (url,seen),tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config(url);e.write_lines(root/'native.jsonl',[self.small_native()])
            folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl');e.step(folder,'goals',infer=True,authorize=True)
            self.assertEqual(len(seen),2)
            captures=e.read_lines(folder/'captured_responses_goals.jsonl')
            self.assertIn('Duplicate JSON key',captures[0]['parse_error']);self.assertIsNone(captures[0]['output'])
            self.assertEqual(captures[1]['format_retry'],1)
            self.assertEqual(len(e.read_lines(folder/'results_goals.jsonl')),1)
        with self.assertRaisesRegex(ValueError,'Duplicate JSON key'):
            api.strict_json_loads('{"outer":{"same":1,"same":2}}')

    def test_model_cache_raw_text_mismatch_is_an_integrity_failure_not_format_retry(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native()])
            folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
            q=e.read_lines(folder/'requests_goals.jsonl')[0];good=self.model_fixture(q,cfg)
            bad=copy.deepcopy(good);bad['output']['goals'][0]['description']='Not present in raw model text'
            e.write_lines(folder/'captured_responses_goals.jsonl',[bad])
            with mock.patch.object(e,'call_model') as paid:
                with self.assertRaisesRegex(ValueError,'differs from raw model text'):
                    e.step(folder,'goals',infer=True,authorize=True)
                paid.assert_not_called()
            with self.assertRaisesRegex(ValueError,'Raw model text required'):
                e.check_answer_identity(q,{k:v for k,v in good.items() if k!='raw_output'})
            e.write_lines(folder/'captured_responses_goals.jsonl',[good])
            e.step(folder,'goals',infer=True,authorize=True)
            rows=e.read_lines(folder/'results_goals.jsonl')
            rows[0]['output']['goals'][0]['description']='Changed bound result'
            rows[0]['model_output']['goals'][0]['description']='Changed bound result'
            e.write_lines(folder/'results_goals.jsonl',rows)
            with self.assertRaisesRegex(ValueError,'differs from raw model text'):e.validate_saved_stage(folder,'goals')

    def test_batch_split_gate_rejects_sealed_and_records_original_approved_source_role(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config();e.write(root/'cfg.json',cfg)
            for split in ('SEALED_CONFIRMATION','UNKNOWN_SPLIT'):
                tr=self.small_native();tr['split']=split;e.write_lines(root/'native.jsonl',[tr])
                with self.subTest(split=split),self.assertRaisesRegex(ValueError,'Source split is not authorized'):
                    run_batch.prepare_batch(root/'cfg.json',root/'native.jsonl',root/split)
                self.assertFalse(list((root/split).glob('*/requests_goals.jsonl')))
            tr=self.small_native();tr.update(split='SOURCE_ONLY_NOT_E1_SEMANTICALLY_REVIEWED',trial_role='SOURCE_AUDIT')
            e.write_lines(root/'native.jsonl',[tr]);original=(root/'native.jsonl').read_bytes()
            run_batch.prepare_batch(root/'cfg.json',root/'native.jsonl',root/'approved')
            index=e.read_lines(root/'approved/BATCH_INDEX.jsonl')[0]
            self.assertEqual(index['original_source_roles'][0]['split'],tr['split'])
            self.assertEqual(index['original_source_roles'][0]['trial_role'],'SOURCE_AUDIT')
            self.assertEqual((root/'native.jsonl').read_bytes(),original)
            normalized=e.read_lines(root/'approved/000/normalized_sources.jsonl')[0]
            self.assertEqual(normalized['split'],'MODEL_ANNOTATION_COHORT')

    def test_saved_request_requires_exact_context_from_its_actual_profile(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);cfg=self.config();e.write_lines(root/'native.jsonl',[self.small_native('OSWorld')])
            folder=root/'case';e.prepare(cfg,folder,root/'native.jsonl')
            with mock.patch.object(e,'call_model',side_effect=self.model_fixture):e.step(folder,'goals',infer=True,authorize=True)
            e.validate_saved_stage(folder,'alignment',complete=None)
            qs=e.read_lines(folder/'requests_alignment.jsonl');q=qs[0]
            tr=e.read_lines(folder/'sources.jsonl')[0];units,bindings=e.evidence_units(tr)
            selected=units[q['core'][0]:q['core'][1]+1]
            refs={m['action_ref'] for u in selected for m in u['members']}
            refs|={m[r]['evidence_ref'] for u in selected for m in u['members'] for r in ('pre','post')}
            payload=copy.deepcopy(q['payload']);payload['units']=selected
            qs[0]=e.make_request('alignment',tr,sources.read(folder/'config_snapshot.json'),payload,q['core'],{r:v for r,v in bindings.items() if r in refs})
            e.write_lines(folder/'requests_alignment.jsonl',qs)
            with mock.patch.object(e,'call_model') as paid:
                with self.assertRaisesRegex(ValueError,'actual target/context window'):
                    e.step(folder,'alignment',infer=True,authorize=True)
                paid.assert_not_called()

    def test_agentnet_redacts_only_status_and_does_not_match_text_literals(self):
        code='pyautogui.write("中文 answer")\npyautogui.click(10, 20)\ncomputer.terminate(status="success", answer="public answer")'
        public,terminal=sources.public_agentnet_code(code)
        self.assertTrue(terminal);self.assertIn('pyautogui.click(10, 20)',public)
        self.assertIn('中文 answer',public);self.assertIn("answer='public answer'",public)
        self.assertNotIn('success',public)
        literal='pyautogui.write("computer.terminate(")'
        self.assertEqual(sources.public_agentnet_code(literal),(literal,False))

    def test_relative_images_use_declared_root_and_reject_ambiguous_matches(self):
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);native=root/'native';native.mkdir();cwd=root/'cwd';cwd.mkdir()
            Image.new('RGB',(4,4),'red').save(native/'frame.png')
            Image.new('RGB',(4,4),'blue').save(cwd/'frame.png')
            with mock.patch('os.getcwd',return_value=str(cwd)):
                self.assertEqual(sources.resolve_image(native,'frame.png'),(native/'frame.png').resolve())
            (native/'screenshots').mkdir();Image.new('RGB',(4,4),'blue').save(native/'screenshots/frame.png')
            with self.assertRaisesRegex(ValueError,'Ambiguous'):
                sources.resolve_image(native,'frame.png')

    def test_prompt_example_fields_and_resized_image_geometry_are_explicit(self):
        for stage in ('alignment','operator'):
            text=(e.HERE/'prompts'/f'{stage}.md').read_text(encoding='utf-8-sig')
            payload=json.loads(text.split('```json\n',1)[1].split('\n```',1)[0])
            for u in payload['units']:
                for member in u['members']:
                    self.assertIn('kind',member['action']);self.assertNotIn('type',member['action'])
                    self.assertEqual(member['pre']['modality'],'NATIVE_GUI')
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            p=Path(tmp)/'native.png';Image.new('RGB',(1920,1080),'red').save(p)
            q=e.make_request('alignment',self.tr,self.cfg,
                {'goal_catalog':{'goals':[{'goal_id':'G1'}]},'target_range':{'first':0,'last':0},'units':[]},
                [0,0],{'one.pre':{'path':str(p)}})
            body=e.wire_body(q,self.cfg)
            label=next(x['text'] for x in body['messages'][1]['content'] if x.get('type')=='text' and x['text'].startswith('Observation refs:'))
            geometry=json.loads(label.split('Image geometry: ',1)[1])
            self.assertEqual(geometry['native_image_size'],[1920,1080])
            self.assertEqual(geometry['sent_image_size'],[1536,864])
            self.assertEqual(geometry['native_pixel_to_sent_pixel_scale'],[0.8,0.8])


if __name__=='__main__':unittest.main(verbosity=2)
