"""Small meaningful source/dependency/transport checks. No external model calls."""
import copy
import os
import json
import threading
import tempfile
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import experiment1 as e
import sources


class FrameworkChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['SCENTTRACE_API_KEY'] = 'offline-test-placeholder'
        tempfile.tempdir = str(e.HERE / 'runs' / 'tmp')
        cls.cfg = sources.read(e.HERE / 'config.json')
        cls.traces = [sources.load(s) for s in cls.cfg['samples']]
        for tr in cls.traces:
            tr['trial_role'] = 'FULL_TRIAL'
            tr['units'], tr['repeat_audit'] = e.join_units(tr, cls.cfg['repeat_confirmations'])

    def test_agentnet_includes_terminal_and_no_outcome_metadata(self):
        t = self.traces[0]
        self.assertEqual(len(t['events']), 24)
        self.assertEqual(t['events'][-1]['raw_step'], 23)
        self.assertEqual(t['events'][-1]['action']['content'], {'code': 'computer.terminate()'})
        self.assertTrue(t['events'][-1]['terminal_event'])
        self.assertNotIn('task_completed', t)

    def test_om2w_answer_and_correct_next_pre(self):
        t = self.traces[1]
        self.assertEqual(len(t['events']), 38)
        self.assertEqual(t['events'][-1]['raw_step'], 38)
        self.assertEqual(t['events'][-1]['action']['content']['arguments']['action'], 'answer')
        for a, b in zip(t['events'], t['events'][1:]):
            self.assertEqual(a['post']['path'], b['pre']['path'])
        self.assertEqual(t['events'][-1]['post']['status'], 'MISSING')

    def test_fara_native_ids_not_offset(self):
        t = self.traces[2]
        self.assertEqual(len(t['events']), 20)
        self.assertTrue(t['events'][0]['pre']['path'].endswith('screenshot_1_pre.jpg'))
        for ev in t['events']:
            self.assertEqual(ev['pre']['status'], 'PRESENT')
            self.assertEqual(ev['post']['status'], 'PRESENT')
            self.assertLess(ev['pre']['source_locator']['event_line'], ev['source_locator']['event_line'])
            self.assertGreater(ev['post']['source_locator']['event_line'], ev['source_locator']['event_line'])
        self.assertNotIn('thoughts', json.dumps([v['action'] for v in t['events']]))
        search = next(v for v in t['events'] if v['action']['content'].get('name') == 'web_search')
        self.assertIn('Calgary', search['action']['content']['arguments']['query'])
        wait = next(v for v in t['events'] if v['action']['content'].get('name') == 'wait')
        self.assertEqual(wait['action']['content']['arguments']['time'], 3)
        fact = next(v for v in t['events'] if v['action']['content'].get('name') == 'pause_and_memorize_fact')
        self.assertIn('24 km/h', fact['action']['content']['arguments']['fact'])
        with self.assertRaisesRegex(ValueError, 'Unmapped'):
            sources.public_call({'arguments': {'unknown_executable_parameter': 1}})

    def test_osworld_does_not_split_script_or_hide_end(self):
        t = self.traces[3]
        self.assertEqual(len(t['events']), 40)
        self.assertEqual(t['events'][0]['pre']['status'], 'MISSING')
        self.assertEqual(t['events'][-1]['action']['content'], {'action_type': 'DONE'})
        self.assertNotIn('raw_response', json.dumps([v['action'] for v in t['events']]))
        self.assertEqual(len(t['units']), len(t['events']))

    def test_normalized_source_contract_and_removed_dataset(self):
        self.assertEqual({t['dataset'] for t in self.traces}, {'AgentNet','OM2W','OSWorld','WebChain'})
        for tr in self.traces:
            native={k:v for k,v in tr.items() if k not in {'units','repeat_audit'}}
            e.validate_source(native)
        bad=copy.deepcopy(native)
        bad['events'][0]['pre']['path']='invented.png'
        with self.assertRaises(ValueError):
            e.validate_source(bad)
        bad=copy.deepcopy(native)
        bad['events'][1]['position']=0
        with self.assertRaisesRegex(ValueError,'ordered'):
            e.validate_source(bad)

    def test_repeat_preserves_members_and_toggling_clicks(self):
        t = self.traces[1]
        self.assertEqual(len(t['units']), 37)
        self.assertEqual(t['units'][3]['raw_steps'], [4, 5])
        self.assertEqual([v['raw_step'] for u in t['units'] for v in u['members']], list(range(1, 39)))
        # Source 30->31 opens the sort menu; 31->32 closes it. Same click args
        # cannot authorize a repeat merge without independent effect review.
        self.assertTrue(any(u['raw_steps'] == [30] for u in t['units']))
        self.assertTrue(any(u['raw_steps'] == [31] for u in t['units']))
        bad = copy.deepcopy(self.cfg['repeat_confirmations'])
        bad[0]['last_position'] = 5
        with self.assertRaises(ValueError):
            e.join_units(t, bad)

    def test_goals_input_is_task_only(self):
        q = e.requests_for('goals', [self.traces[0]], self.cfg)[0]
        self.assertEqual(set(q['payload']), {'instruction','source_segments'})
        self.assertEqual(''.join(s['text'] for s in q['payload']['source_segments']),q['payload']['instruction'])
        self.assertEqual(q['images'], [])
        self.assertNotIn('action-pre', (e.HERE / 'prompts/goals.md').read_text(encoding='utf-8').lower())

    def test_same_task_actors_share_one_catalog_request(self):
        first = copy.deepcopy(self.traces[1])
        second = copy.deepcopy(first)
        second['actor_id'] = 'OTHER_ACTOR'
        second['trajectory_id'] = 'OTHER_ACTOR::same_task'
        requests = e.requests_for('goals', [first, second], self.cfg)
        self.assertEqual(len(requests), 1)
        self.assertNotIn(first['actor_id'], json.dumps(requests[0]['payload']))
        second['instruction'] += ' DIFFERENT TASK BINDING'
        with self.assertRaisesRegex(ValueError, 'disagree'):
            e.requests_for('goals', [first, second], self.cfg)

    def test_continuous_ranges_preserve_assignments_and_gaps(self):
        def record(first, last, goal):
            return {'output': {'ranges': [{'first': first, 'last': last,
                'goal_ids': [goal], 'status': 'ALIGNED', 'reason': 'source'}]}}
        merged = e.continuous_ranges([record(0, 3, 'G1'), record(4, 7, 'G1'), record(8, 9, 'G2')])
        self.assertEqual([(r['first'], r['last']) for r in merged], [(0, 7), (8, 9)])
        gap = e.continuous_ranges([record(0, 3, 'G1'), record(5, 7, 'G1')])
        self.assertEqual(len(gap), 2)

    def test_goal_quotes_and_dependencies_rejected_if_invented(self):
        q = e.requests_for('goals', [self.traces[0]], self.cfg)[0]
        out = sources.read(e.HERE / 'schemas.json')['goals']
        self.assertFalse(out['additionalProperties'])
        response = {'goals': [{'goal_id': 'G1', 'goal_type': 'ACT', 'description': 'a',
                    'constraints': [], 'completion_condition':'a', 'depends_on': [], 'source_ids': ['S999']}], 'issues': []}
        with self.assertRaisesRegex(ValueError, 'Unknown source'):
            e.validate_response(q, response)

    def test_alignment_coverage_and_goal_binding(self):
        tr = self.traces[0]
        goals = {'goals': [{'goal_id': 'G1'},{'goal_id':'G2'}]}
        prior = {'goals': {tr['task_id']: {'output': goals}}}
        q = e.requests_for('alignment', [tr], self.cfg, prior)[0]
        reference = q['payload']['units'][0]['members'][0]['action_ref']
        answer = {'ranges': [{'first': 0, 'last': q['core'][1], 'goal_ids': ['G1'], 'status': 'ALIGNED',
                              'reason': 'source'}]}
        e.validate_response(q, answer)
        answer['ranges'][0]['last'] = 2
        with self.assertRaisesRegex(ValueError, 'Gap'):
            e.validate_response(q, answer)
        answer['ranges'][0]['last'] = q['core'][1]
        answer['ranges'][0]['goal_ids'] = ['G999']
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            e.validate_response(q, answer)
        answer['ranges'][0].update(goal_ids=['G1', 'G1'], status='UNRESOLVED')
        with self.assertRaisesRegex(ValueError, 'Duplicate Goal'):
            e.validate_response(q, answer)

    def test_real_multimodal_request_encodes_native_images(self):
        tr = self.traces[0]
        prior = {'goals': {tr['task_id']: {'output': {'goals': [{'goal_id': 'G1'},{'goal_id':'G2'}]}}}}
        q = e.requests_for('alignment', [tr], self.cfg, prior)[0]
        body = e.wire_body(q, self.cfg)
        content = body['messages'][1]['content']
        pictures = [v for v in content if v['type'] == 'image_url']
        self.assertEqual(len(pictures), len(q['images']))
        self.assertTrue(all(v['image_url']['url'].startswith('data:image/jpeg;base64,') for v in pictures))

    def test_http_transport_against_explicit_local_fixture(self):
        observed = []
        class Fixture(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.path, payload))
                body = json.dumps({'model': 'TEST_FIXTURE_NOT_A_MODEL', 'choices': [
                    {'message': {'content': '{"fixture":true}'}, 'finish_reason': 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Fixture)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            cfg = copy.deepcopy(self.cfg)
            cfg['model']['base_url'] = f'http://127.0.0.1:{server.server_port}/v1'
            cfg['model']['model_id'] = 'TEST_FIXTURE_NOT_A_MODEL'
            q = e.requests_for('goals', [self.traces[0]], cfg)[0]
            r = e.call_model(q, cfg)
            self.assertEqual(r['model'], 'TEST_FIXTURE_NOT_A_MODEL')
            self.assertEqual(observed[0][0], '/v1/chat/completions')
            self.assertIn('response_format', observed[0][1])
            self.assertNotIn('structured_outputs', observed[0][1])
            self.assertNotIn('chat_template_kwargs', observed[0][1])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_reject_unapproved_inference_and_wrong_authority(self):
        # Gate runs before any request reaches a network endpoint.
        parent = e.HERE / 'runs'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            p = Path(folder)/'case'
            tr={k:v for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
            e.write_lines(Path(folder)/'native.jsonl',[tr])
            e.prepare(self.cfg,p,Path(folder)/'native.jsonl')
            q=e.read_lines(p/'requests_goals.jsonl')[0]
            with mock.patch.object(e, 'call_model', side_effect=AssertionError('Network attempted')):
                with self.assertRaises(PermissionError):
                    e.step(p, 'goals', infer=True, authorize=False)
            wrong = {'request_id': q['request_id'], 'request_sha256': q['request_sha256'],
                     'authority': 'HUMAN_GOLD', 'model': 'not allowed', 'output': {}}
            e.write_lines(p / 'wrong.jsonl', [wrong])
            with self.assertRaisesRegex(ValueError, 'attribution'):
                e.step(p, 'goals', p / 'wrong.jsonl')

    def test_window_input_preserves_units_and_program_bindings(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=20,context_units=2)
        tr=self.traces[0]
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'},{'goal_id':'G2'}]}}}}
        qs=e.requests_for('alignment',[tr],cfg,prior)
        self.assertEqual([q['core'] for q in qs],[[0,19],[20,23]])
        self.assertEqual([u['unit_index'] for u in qs[0]['payload']['units']],list(range(22)))
        self.assertEqual([u['unit_index'] for u in qs[1]['payload']['units']],list(range(18,24)))
        self.assertEqual([len(q['images']) for q in qs],[23,6])
        self.assertEqual([i for q in qs for i in range(q['core'][0],q['core'][1]+1)],list(range(24)))
        self.assertNotIn('U0000.M00.pre',qs[1]['evidence_bindings'])
        for q in qs:
            user=q['messages'][1]['content']
            for hidden in ['task_id','trajectory_id','source_locator','request_sha256','D:\\ScentTrace']:
                self.assertNotIn(hidden,user)
            self.assertEqual(q['payload']['goal_catalog'],prior['goals'][tr['task_id']]['output'])

    def test_single_goal_has_its_own_alignment_stage(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=20,context_units=2)
        tr=self.traces[0]
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}},'alignment':{}}
        requests=e.requests_for('alignment',[tr],cfg,prior)
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]['core'],[0,19])
        with self.assertRaises(KeyError):
            e.requests_for('operator',[tr],cfg,prior)

    def test_short_context_keeps_neighbors_without_labeling_them(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=1,context_units=1)
        tr=self.traces[0]
        catalog={'goals':[{'goal_id':'G1'},{'goal_id':'G2'}]}
        prior={'goals':{tr['task_id']:{'output':catalog}}}
        qs=e.requests_for('alignment',[tr],cfg,prior)
        self.assertEqual([q['core'] for q in qs],[[i,i] for i in range(24)])
        self.assertEqual([u['unit_index'] for u in qs[8]['payload']['units']],[7,8,9])
        self.assertEqual([u['unit_index'] for u in qs[0]['payload']['units']],[0,1])
        self.assertEqual([u['unit_index'] for u in qs[-1]['payload']['units']],[22,23])
        schema=e.output_schema(qs[8])['properties']['ranges']
        self.assertEqual(schema['maxItems'],1)
        variants=schema['items']['oneOf']
        self.assertTrue(all(v['properties']['first']['enum']==[8] for v in variants))
        unresolved=next(v for v in variants if v['properties']['status']['const']=='UNRESOLVED')
        self.assertNotIn('maxItems',unresolved['properties']['goal_ids'])
        self.assertEqual(unresolved['properties']['goal_ids']['items']['enum'],['G1','G2'])

    def test_twenty_targets_with_five_neighbors_use_only_target_outputs(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=20,context_units=5)
        tr=self.traces[1]
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'},{'goal_id':'G2'}]}}}}
        qs=e.requests_for('alignment',[tr],cfg,prior)
        self.assertEqual([q['core'] for q in qs],[[0,19],[20,36]])
        self.assertEqual([u['unit_index'] for u in qs[0]['payload']['units']],list(range(25)))
        self.assertEqual([u['unit_index'] for u in qs[1]['payload']['units']],list(range(15,37)))
        self.assertEqual([i for q in qs for i in range(q['core'][0],q['core'][1]+1)],list(range(37)))
        q=qs[1]
        dynamic=e.output_schema(q)['properties']['ranges']['items']['oneOf']
        self.assertTrue(all(v['properties']['first']['enum']==list(range(20,37)) for v in dynamic))
        prior['alignment']={tr['trajectory_id']:[{'output':{'ranges':[
            {'first':0,'last':36,'goal_ids':['G1'],'status':'ALIGNED','reason':'Technical fixture only'}]}}]}
        oq=e.requests_for('operator',[tr],cfg,prior)[1]
        array=e.output_schema(oq)['properties']['units']
        self.assertEqual([v['properties']['unit_index']['const'] for v in array['prefixItems']],list(range(20,37)))
        self.assertEqual(array['minItems'],17)
        self.assertEqual(array['maxItems'],17)

    def test_ten_targets_with_two_neighbors_preserve_full_coverage(self):
        tr=self.traces[1]
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}}}
        qs=e.requests_for('alignment',[tr],self.cfg,prior)
        self.assertEqual([q['core'] for q in qs],[[0,9],[10,19],[20,29],[30,36]])
        self.assertEqual([u['unit_index'] for u in qs[1]['payload']['units']],list(range(8,22)))
        self.assertEqual([i for q in qs for i in range(q['core'][0],q['core'][1]+1)],list(range(37)))
        prior['alignment']={tr['trajectory_id']:[{'output':{'ranges':[
            {'first':0,'last':36,'goal_ids':['G1'],'status':'ALIGNED','reason':'Fixture only'}]}}]}
        oq=e.requests_for('operator',[tr],self.cfg,prior)[1]
        array=e.output_schema(oq)['properties']['units']
        self.assertEqual([v['properties']['unit_index']['const'] for v in array['prefixItems']],list(range(10,20)))
        self.assertEqual(array['maxItems'],10)

    def test_concurrent_real_http_has_one_writer_ordered_results_and_resume(self):
        from preflight_cpu import fixture_output
        lock=threading.Lock(); barrier=threading.Barrier(4)
        activity={'active':0,'peak':0}; completed=[]; observed=[]
        class Fixture(BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                payload=json.loads(body['messages'][1]['content'][0]['text'])
                window=payload.get('target_range')
                stage='operator' if 'alignment' in payload else 'alignment' if window else 'goals'
                with lock:
                    observed.append((stage,window))
                    if window:
                        activity['active']+=1
                        activity['peak']=max(activity['peak'],activity['active'])
                if window:
                    barrier.wait(timeout=20)
                    time.sleep((30-window['first'])*0.004)
                result=fixture_output(payload,body['messages'][0]['content'])
                reply={'model':'TECHNICAL_FIXTURE_NOT_A_MODEL','choices':[
                    {'message':{'content':json.dumps(result)},'finish_reason':'stop'}]}
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
                self.wfile.write(json.dumps(reply).encode())
                if window:
                    with lock:
                        activity['active']-=1
                        completed.append((stage,window['first']))
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        cfg=copy.deepcopy(self.cfg)
        cfg['model'].update(model_id='TECHNICAL_FIXTURE_NOT_A_MODEL',weight_revision='TEST_FIXTURE',
                            base_url=f'http://127.0.0.1:{server.server_port}/v1')
        tr={k:copy.deepcopy(v) for k,v in self.traces[1].items() if k not in {'units','repeat_audit'}}
        actual_call=e.call_model; actual_open=Path.open; writer_ids=[]; main_id=threading.get_ident()
        def fixture_call(request,config):
            answer=actual_call(request,config); answer['authority']='TECHNICAL_TEST_FIXTURE'; return answer
        def tracked_open(path,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            if path.name.startswith('captured_responses_') and mode=='a': writer_ids.append(threading.get_ident())
            return actual_open(path,*args,**kwargs)
        try:
            with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
                root=Path(tmp); native=root/'native.jsonl'; e.write_lines(native,[tr])
                folder=root/'trial'; e.prepare(cfg,folder,native)
                with mock.patch.object(e,'call_model',side_effect=fixture_call),mock.patch.object(Path,'open',tracked_open):
                    for stage in ['goals','alignment','operator']: e.step(folder,stage,infer=True,authorize=True)
                    saved=(folder/'continuous_alignment.jsonl').read_bytes()
                    before=len(observed)
                    (folder/'results_operator.jsonl').unlink()
                    e.step(folder,'operator',infer=True,authorize=True)
                self.assertEqual(len(observed),before)
                self.assertEqual(activity['peak'],4)
                self.assertEqual(set(writer_ids),{main_id})
                self.assertEqual(len(observed),9)
                self.assertEqual((folder/'continuous_alignment.jsonl').read_bytes(),saved)
                rows=e.read_lines(folder/'results_operator.jsonl')
                self.assertEqual([u['unit_index'] for r in rows for u in r['output']['units']],list(range(37)))
                self.assertEqual(sources.read(folder/'STATUS.json')['status'],'TECHNICAL_FIXTURE_COMPLETE')
                self.assertNotEqual([i for s,i in completed if s=='alignment'],[0,10,20,30])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_concurrent_failure_drains_captures_and_resumes_valid_requests(self):
        from preflight_cpu import fixture_output
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=1,context_units=1)
        cfg['model'].update(model_id='TECHNICAL_FIXTURE_NOT_A_MODEL',weight_revision='TEST_FIXTURE')
        tr={k:copy.deepcopy(v) for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
        barrier=threading.Barrier(4); initial_calls=[]; resumed_calls=[]
        def fixture(request,config):
            return {'request_id':request['request_id'],'request_sha256':request['request_sha256'],
                    'authority':'TECHNICAL_TEST_FIXTURE','model':'TECHNICAL_FIXTURE_NOT_A_MODEL',
                    'output':fixture_output(request['payload'],request['messages'][0]['content'])}
        def failing(request,config):
            initial_calls.append(request['request_id']); barrier.wait(timeout=5)
            if request['core'][0]==0: raise RuntimeError('Injected request transport failure')
            time.sleep(0.08); return fixture(request,config)
        def resume(request,config):
            resumed_calls.append(request['request_id']); return fixture(request,config)
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp); native=root/'native.jsonl'; e.write_lines(native,[tr])
            folder=root/'trial'; e.prepare(cfg,folder,native)
            with mock.patch.object(e,'call_model',side_effect=fixture): e.step(folder,'goals',infer=True,authorize=True)
            with mock.patch.object(e,'call_model',side_effect=failing):
                with self.assertRaisesRegex(RuntimeError,'Injected request transport failure'):
                    e.step(folder,'alignment',infer=True,authorize=True)
            self.assertEqual(len(initial_calls),24)
            captures=e.read_lines(folder/'captured_responses_alignment.jsonl')
            self.assertEqual(len(captures),23)
            self.assertFalse((folder/'results_alignment.jsonl').exists())
            cached_ids={r['request_id'] for r in captures}
            with mock.patch.object(e,'call_model',side_effect=resume): e.step(folder,'alignment',infer=True,authorize=True)
            self.assertFalse(cached_ids & set(resumed_calls))
            self.assertEqual(len(resumed_calls),1)
            rows=e.read_lines(folder/'results_alignment.jsonl')
            self.assertEqual([r['output']['ranges'][0]['first'] for r in rows],list(range(24)))

    def test_operator_cannot_return_goal_edits(self):
        tr=self.traces[0]
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'}]}}},
               'alignment':{tr['trajectory_id']:[{'output':{'ranges':[{'first':0,'last':23,'goal_ids':['G1'],'status':'ALIGNED'}]}}]}}
        q=e.requests_for('operator',[tr],self.cfg,prior)[0]
        out={'units':[{'unit_index':u['unit_index'],'operator':'UNRESOLVED',
             'reason':'Technical fixture; not semantically evaluated'}
             for u in q['payload']['units'] if q['core'][0]<=u['unit_index']<=q['core'][1]]}
        e.validate_response(q,out)
        out['units'][0]['goal_ids']=['G999']
        with self.assertRaisesRegex(ValueError,'Unexpected'):
            e.validate_response(q,out)

    def test_operator_goal_return_and_cross_boundary_context(self):
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=20,context_units=2)
        tr=self.traces[0]
        ranges=[{'first':0,'last':5,'goal_ids':['G1'],'status':'ALIGNED','reason':'a'},
                {'first':6,'last':10,'goal_ids':['G2'],'status':'ALIGNED','reason':'b'},
                {'first':11,'last':23,'goal_ids':['G1'],'status':'ALIGNED','reason':'a return'}]
        original=copy.deepcopy(ranges)
        prior={'goals':{tr['task_id']:{'output':{'goals':[{'goal_id':'G1'},{'goal_id':'G2'}]}}},
               'alignment':{tr['trajectory_id']:[{'output':{'ranges':ranges}}]}}
        qs=e.requests_for('operator',[tr],cfg,prior)
        self.assertEqual([q['core'] for q in qs],[[0,5],[6,10],[11,23]])
        self.assertEqual([u['unit_index'] for u in qs[1]['payload']['units']],list(range(4,13)))
        self.assertEqual([r['goal_ids'] for r in qs[1]['payload']['alignment']],[['G1'],['G2'],['G1']])
        self.assertEqual(ranges,original)
        self.assertEqual([i for q in qs for i in range(q['core'][0],q['core'][1]+1)],list(range(24)))

    def test_prompt_examples_match_current_input_and_output(self):
        import re
        for stage in ['goals','alignment','operator']:
            prompt=(e.HERE/'prompts'/f'{stage}.md').read_text(encoding='utf-8')
            example=prompt.split('# Example',1)[1]
            inp,out=[json.loads(x) for x in re.findall(r'```json\s*(.*?)\s*```',example,re.S)]
            q={'stage':stage,'schema':sources.read(e.HERE/'schemas.json')[stage],
               'payload':inp,'core':None if stage=='goals' else [inp['target_range']['first'],inp['target_range']['last']]}
            e.validate_response(q,out)
            self.assertEqual(set(inp),{'instruction','source_segments'} if stage=='goals' else
                             {'instruction','goal_catalog','target_range','units'} | ({'alignment'} if stage=='operator' else set()))

    def test_webchain_all_60_retain_original_rows_and_pre_timing(self):
        records=e.read_lines(self.cfg['cpu_reference_sources']['webchain_60_native_rows'])
        self.assertEqual(len(records),60)
        self.assertEqual(sum(len(t['events']) for t in records),768)
        self.assertEqual(sum(v['pre']['status']=='PRESENT' for t in records for v in t['events']),667)
        for tr in records:
            e.validate_source(tr)
            self.assertEqual(tr['cohort'],'Multi-Constraint')
            self.assertFalse(tr['terminal_event_present'])
            self.assertEqual(tr['events'][-1]['post']['status'],'MISSING')
            for a,b in zip(tr['events'],tr['events'][1:]):
                self.assertEqual(a['post'],b['pre'])
            payload=json.dumps([v['action'] for v in tr['events']])
            for forbidden in ['thought','annotation_reason','image_dhash','branch_switch_gold','synthetic']:
                self.assertNotIn(forbidden,payload)

    def test_batch_resumes_stage_handoffs_without_repeating_model_calls(self):
        import run_batch
        from preflight_cpu import fixture_output
        cfg=copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=1,context_units=1)
        cfg['model'].update(model_id='TECHNICAL_FIXTURE_NOT_A_MODEL', weight_revision='TEST_FIXTURE')
        tr=copy.deepcopy(self.traces[0])
        tr={k:v for k,v in tr.items() if k not in {'units','repeat_audit'}}
        calls=[]
        def fixture(q,config):
            calls.append(q['request_id'])
            return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                    'authority':'TECHNICAL_TEST_FIXTURE','model':'TECHNICAL_FIXTURE_NOT_A_MODEL',
                    'output':fixture_output(q['payload'],q['messages'][0]['content'])}
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            batch=Path(tmp);native=batch/'native.jsonl';e.write_lines(native,[tr])
            folder=batch/'000';e.prepare(cfg,folder,native)
            e.write_lines(batch/'BATCH_INDEX.jsonl',[{'group':'000'}])
            remaining={'requests_alignment.jsonl','requests_operator.jsonl'}
            original=e.write_lines
            def interrupt(path,rows):
                if Path(path).name in remaining:
                    remaining.remove(Path(path).name)
                    raise RuntimeError('Injected interruption after valid stage-result save')
                return original(path,rows)
            with mock.patch.object(e,'call_model',side_effect=fixture), mock.patch.object(e,'write_lines',side_effect=interrupt):
                with self.assertRaises(RuntimeError):
                    e.step(folder,'goals',infer=True,authorize=True)
                run_batch.infer_batch(batch,True,['000'])
            self.assertTrue((folder/'results_alignment.jsonl').exists())
            self.assertFalse((folder/'requests_operator.jsonl').exists())
            before=(folder/'continuous_alignment.jsonl').read_bytes()
            real_write=e.write
            def interrupt_status(path,value):
                if Path(path)==folder/'STATUS.json' and (folder/'results_operator.jsonl').exists():
                    raise RuntimeError('Injected interruption before final status save')
                return real_write(path,value)
            with mock.patch.object(e,'call_model',side_effect=fixture), mock.patch.object(e,'write',side_effect=interrupt_status):
                run_batch.infer_batch(batch,True,['000'])
            paid_calls=len(calls)
            with mock.patch.object(e,'call_model',side_effect=AssertionError('Completed request repeated')):
                run_batch.infer_batch(batch,True,['000'])
                with self.assertRaisesRegex(ValueError,'Unknown task-family'):
                    run_batch.infer_batch(batch,True,['999'])
            self.assertEqual(paid_calls,49)
            self.assertEqual(len(set(calls)),len(calls))
            self.assertEqual((folder/'continuous_alignment.jsonl').read_bytes(),before)
            self.assertEqual(sources.read(folder/'STATUS.json')['status'],'TECHNICAL_FIXTURE_COMPLETE')
            self.assertEqual(sources.read(batch/'BATCH_EXECUTION_STATUS.json')['completed_groups'],['000'])

    def test_continuation_keeps_original_alignment_bindings_and_only_calls_missing_operator(self):
        import run_batch
        from preflight_cpu import fixture_output
        cfg = copy.deepcopy(self.cfg)
        cfg['trajectory_input'].update(window_units=20, context_units=1)
        cfg['model'].update(model_id='TEST_HISTORICAL_MODEL_SIMULATION', weight_revision='TEST_FIXTURE')
        calls = []
        def fixture(q, config):
            calls.append(q['stage'])
            output=fixture_output(q['payload'], q['messages'][0]['content'])
            return {'request_id':q['request_id'], 'request_sha256':q['request_sha256'],
                    'authority':'OPEN_SOURCE_MODEL', 'model':config['model']['model_id'],
                    'weight_revision':config['model']['weight_revision'],
                    'output':output,'raw_output':json.dumps(output)}
        with tempfile.TemporaryDirectory(dir=e.HERE / 'runs') as tmp:
            root = Path(tmp)
            old = root / 'old'
            old.mkdir()
            native = {k:v for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
            e.write_lines(root / 'native.jsonl', [native])
            e.prepare(cfg, old / '000', root / 'native.jsonl')
            e.write_lines(old / 'BATCH_INDEX.jsonl', [{'group':'000'}])
            with mock.patch.object(e, 'call_model', side_effect=fixture):
                e.step(old / '000', 'goals', infer=True, authorize=True)
                e.step(old / '000', 'alignment', infer=True, authorize=True)
            cfg['trajectory_input'].update(window_units=10, context_units=2)
            e.write(root / 'new_config.json', cfg)
            calls.clear()
            with mock.patch.object(e, 'call_model', side_effect=fixture):
                run_batch.continue_batch(root / 'new_config.json', root / 'new', [old], True, 4)
            self.assertEqual(calls, ['operator'] * 3)
            self.assertEqual((old / '000/requests_alignment.jsonl').read_bytes(),
                             (root / 'new/000/requests_alignment.jsonl').read_bytes())
            reused = e.sources.read(root / 'new/000/REUSED_STAGES.json')
            self.assertEqual([r['stage'] for r in reused], ['goals','alignment'])
            self.assertTrue(all(not r['labels_recomputed'] for r in reused))
            with mock.patch.object(e, 'call_model', side_effect=AssertionError('Duplicate inference')):
                run_batch.continue_batch(root / 'new_config.json', root / 'new', [old], True, 4)

    def test_alignment_truncated_response_has_one_retry_and_logical_range_limit(self):
        from preflight_cpu import fixture_output
        cfg = copy.deepcopy(self.cfg)
        cfg['model'].update(model_id='TEST_FORMAT_FIXTURE', weight_revision='TEST_FIXTURE')
        calls = []
        def fixture(q, config, format_feedback=None):
            calls.append((q['stage'],q['core'],bool(format_feedback)))
            truncated = q['stage']=='alignment' and q['core'][0]==0 and not format_feedback
            output=None if truncated else fixture_output(q['payload'],q['messages'][0]['content'])
            return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                    'authority':'OPEN_SOURCE_MODEL','model':config['model']['model_id'],
                    'output':output,'raw_output':'{' if truncated else json.dumps(output),
                    'parse_error':'Truncated JSON' if truncated else None,
                    'finish_reason':'length' if truncated else 'stop',
                    'format_retry':1 if format_feedback else 0}
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            folder=Path(tmp)/'case'
            native={k:v for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
            e.write_lines(Path(tmp)/'native.jsonl',[native])
            e.prepare(cfg,folder,Path(tmp)/'native.jsonl')
            with mock.patch.object(e,'call_model',side_effect=fixture):
                e.step(folder,'goals',infer=True,authorize=True)
                qs=e.read_lines(folder/'requests_alignment.jsonl')
                self.assertEqual([e.output_schema(q)['properties']['ranges']['maxItems'] for q in qs],[10,10,4])
                e.step(folder,'alignment',infer=True,authorize=True)
            self.assertEqual(sum(stage=='alignment' and corrected for stage,core,corrected in calls),1)
            records=e.read_lines(folder/'captured_responses_alignment.jsonl')
            self.assertEqual(sum(r['output'] is None for r in records),1)
            self.assertEqual(len(e.read_lines(folder/'results_alignment.jsonl')),3)

    def test_continuation_reuses_bound_capture_but_never_accepts_or_repeats_invalid_output(self):
        import run_batch
        from preflight_cpu import fixture_output
        cfg=copy.deepcopy(self.cfg)
        cfg['model'].update(model_id='TEST_CAPTURE_REUSE_FIXTURE',weight_revision='TEST_FIXTURE')
        calls=[]
        def fixture(q,config):
            calls.append(q['request_id'])
            output=fixture_output(q['payload'],q['messages'][0]['content'])
            return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                    'authority':'OPEN_SOURCE_MODEL','model':config['model']['model_id'],
                    'output':output,'raw_output':json.dumps(output)}
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);old=root/'old';old.mkdir()
            native={k:v for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
            e.write_lines(root/'native.jsonl',[native]);e.prepare(cfg,old/'000',root/'native.jsonl')
            e.write_lines(old/'BATCH_INDEX.jsonl',[{'group':'000'}])
            with mock.patch.object(e,'call_model',side_effect=fixture):e.step(old/'000','goals',infer=True,authorize=True)
            qs=e.read_lines(old/'000/requests_alignment.jsonl')
            good=fixture(qs[0],cfg);bad=dict(fixture(qs[1],cfg),output=None,raw_output='{',format_retry=1)
            e.write_lines(old/'000/captured_responses_alignment.jsonl',[good,bad])
            before=(old/'000/captured_responses_alignment.jsonl').read_bytes()
            calls.clear();e.write(root/'cfg.json',cfg)
            with mock.patch.object(e,'call_model',side_effect=fixture):
                run_batch.continue_batch(root/'cfg.json',root/'new',[old],True,4)
            self.assertNotIn(qs[0]['request_id'],calls)
            self.assertNotIn(qs[1]['request_id'],calls)
            self.assertEqual(calls,[qs[2]['request_id']])
            self.assertFalse((root/'new/000/results_alignment.jsonl').exists())
            failures=e.sources.read(root/'new/000/failures_alignment.json')
            self.assertEqual([f['request_id'] for f in failures],[qs[1]['request_id']])
            self.assertEqual(before,(old/'000/captured_responses_alignment.jsonl').read_bytes())
            reused=e.sources.read(root/'new/000/REUSED_STAGES.json')
            self.assertTrue(any(r.get('kind')=='BOUND_PARTIAL_CAPTURES' for r in reused))

    def test_simplified_goals_retain_native_quotes_and_dependencies(self):
        from preflight_cpu import fixture_output
        q=e.requests_for('goals',[self.traces[0]],self.cfg)[0]
        answer=fixture_output(q['payload'],q['messages'][0]['content'])
        e.validate_response(q,answer)
        self.assertNotIn('requirements',answer)
        self.assertNotIn('requirement_ids',answer['goals'][0])
        answer['goals'][0]['depends_on']=['G1']
        with self.assertRaisesRegex(ValueError,'dependency'):e.validate_response(q,answer)
        instruction='Set URI type "udp". Preserve cursor position.\n\nDo not delete files.'
        segments=e.source_segments(instruction)
        self.assertEqual(''.join(s['text'] for s in segments),instruction)
        self.assertIn('"udp"',segments[0]['text'])

    def test_compact_press_keys_are_ordered_and_preserve_nonpress_code(self):
        keys=list('cat /tmp/a')+['enter','tab']+list('XYZ')
        code='import pyautogui\n'+''.join('pyautogui.press('+repr(k)+')\n' for k in keys[:12])
        code+='pyautogui.click(1, 2)\n'+''.join('pyautogui.press('+repr(k)+')\n' for k in keys[12:])
        code+='if ready:\n    pyautogui.press("enter")\n'
        parts=e.compact_code(code)
        recovered=[]
        for part in parts:
            for run in part.get('ordered_key_runs',[]):
                recovered.extend(run['per_character_keys'] if 'per_character_keys' in run else [run['key']])
        self.assertEqual(recovered,keys)
        raw=''.join(p.get('code','') for p in parts)
        self.assertIn('pyautogui.click(1, 2)',raw)
        self.assertIn('    pyautogui.press("enter")',raw)
        self.assertIsNone(e.compact_code('for k in keys:\n    pyautogui.press(k)\n'))
        self.assertIsNone(e.compact_code('text = """\n'+''.join('pyautogui.press("x")\n' for _ in range(10))+'"""\n'))

    def test_input_identity_separates_metadata_from_prompt_and_model(self):
        q=e.requests_for('goals',[self.traces[0]],self.cfg)[0]
        revised=copy.deepcopy(q)
        revised['framework_version']='METADATA_ONLY'
        revised['implementation_fingerprints']={'run_batch.py':'DIFFERENT_LOGGING_CODE'}
        self.assertEqual(e.input_identity(q),e.input_identity(revised))
        revised['messages'][0]['content']+=' changed semantic prompt'
        self.assertNotEqual(e.input_identity(q),e.input_identity(revised))
        revised=copy.deepcopy(q);revised['model_parameters']['weight_revision']='DIFFERENT_WEIGHTS'
        self.assertNotEqual(e.input_identity(q),e.input_identity(revised))

    def test_identical_image_bytes_dedup_references_and_original_paths(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.png';b=Path(tmp)/'b.png'
            Image.new('RGB',(8,8),'red').save(a);b.write_bytes(a.read_bytes())
            bindings={'one.pre':{'path':str(a)},'two.post':{'path':str(b)}}
            q=e.make_request('alignment',self.traces[0],self.cfg,
                {'goal_catalog':{'goals':[{'goal_id':'G1'}]},'target_range':{'first':0,'last':0},'units':[]},[0,0],bindings)
            self.assertEqual(len(q['images']),1)
            self.assertEqual(q['images'][0]['evidence_refs'],['one.pre','two.post'])
            self.assertEqual(len(q['images'][0]['source_paths']),2)
            Image.new('RGB',(8,8),'blue').save(b)
            with self.assertRaisesRegex(ValueError,'changed'):e.wire_body(q,self.cfg)

    def test_failed_actor_does_not_block_other_actor_operator(self):
        import run_batch
        from preflight_cpu import fixture_output
        cfg=copy.deepcopy(self.cfg)
        cfg['model'].update(model_id='TEST_ISOLATION_FIXTURE',weight_revision='TEST_FIXTURE')
        first={k:copy.deepcopy(v) for k,v in self.traces[0].items() if k not in {'units','repeat_audit'}}
        second=copy.deepcopy(first);second['trajectory_id']='SECOND_ACTOR_same_task';second['actor_id']='SECOND_ACTOR'
        calls=[]
        def fixture(q,config,format_feedback=None):
            calls.append((q['stage'],q['trajectory_id'],q['core'],bool(format_feedback)))
            out=fixture_output(q['payload'],q['messages'][0]['content'])
            if q['stage']=='alignment' and q['trajectory_id']==first['trajectory_id'] and q['core'][0]==0:
                out['ranges'].append(copy.deepcopy(out['ranges'][0]))
            return {'request_id':q['request_id'],'request_sha256':q['request_sha256'],
                    'authority':'OPEN_SOURCE_MODEL','model':config['model']['model_id'],
                    'format_retry':int(bool(format_feedback)),'output':out,'raw_output':json.dumps(out)}
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            root=Path(tmp);native=root/'native.jsonl';e.write_lines(native,[first,second])
            folder=root/'trial';e.prepare(cfg,folder,native)
            with mock.patch.object(e,'call_model',side_effect=fixture):
                failures=run_batch.advance_family(folder,4)
            self.assertEqual(len(failures),1)
            self.assertEqual(failures[0]['stage'],'alignment')
            self.assertIn('duplicate',failures[0]['reason'])
            self.assertFalse((folder/'results_alignment.jsonl').exists())
            readiness=sources.read(folder/'alignment_trajectory_status.json')
            self.assertEqual(readiness['ready'],[second['trajectory_id']])
            self.assertEqual(readiness['blocked'],[first['trajectory_id']])
            operators=e.read_lines(folder/'partial_results_operator.jsonl')
            self.assertEqual({r['output']['trajectory_id'] for r in operators},{second['trajectory_id']})
            self.assertEqual(sum(len(r['output']['units']) for r in operators),24)
            self.assertEqual(sum(s=='alignment' and retry for s,tid,core,retry in calls),1)
            self.assertEqual(sources.read(folder/'STATUS.json')['status'],'PARTIAL_WITH_ERRORS')

    def test_standard_prompt_examples_match_current_contracts(self):
        import re
        fence=chr(96)*3
        for stage in ['goals','alignment','operator']:
            prompt=(e.HERE/'prompts'/f'{stage}.md').read_text(encoding='utf-8')
            payload,out=[json.loads(s) for s in re.findall(re.escape(fence)+r'json\n(.*?)'+re.escape(fence),prompt,re.S)]
            q={'stage':stage,'schema':sources.read(e.HERE/'schemas.json')[stage],'payload':payload}
            if stage!='goals':q['core']=[payload['target_range']['first'],payload['target_range']['last']]
            e.validate_response(q,out)
            if stage!='goals':
                self.assertNotIn('requirements',payload['goal_catalog'])
                self.assertTrue(all('requirement_ids' not in g and 'source_ids' not in g for g in payload['goal_catalog']['goals']))

    def test_packaged_source_resolver_changes_only_image_paths(self):
        from resolve_sources import resolve_record
        original={'instruction':'Keep "udp".', 'events':[{'pre':{'path':'native_images/a.png','status':'PRESENT'},
                    'post':{'path':None,'status':'MISSING'},'action':{'content':{'code':'not executed'}}}]}
        with tempfile.TemporaryDirectory() as tmp:
            resolved=resolve_record(original,tmp)
            self.assertEqual(resolved['events'][0]['pre']['path'],str((Path(tmp)/'native_images/a.png').resolve()))
            self.assertEqual(resolved['instruction'],original['instruction'])
            self.assertEqual(resolved['events'][0]['action'],original['events'][0]['action'])
            self.assertIsNone(resolved['events'][0]['post']['path'])
            self.assertEqual(original['events'][0]['pre']['path'],'native_images/a.png')

    def test_packaged_source_resolver_preserves_absolute_paths(self):
        from resolve_sources import resolve_record
        with tempfile.TemporaryDirectory() as tmp:
            path=str((Path(tmp)/'a.png').resolve())
            original={'events':[{'pre':{'path':path},'post':{'path':path}}]}
            resolved=resolve_record(original,Path(tmp)/'different_package')
            self.assertEqual(resolved,original)

    def test_invalid_source_resolution_does_not_publish_partial_input(self):
        import resolve_sources
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp)/'package';package.mkdir()
            e.write_lines(package/'normalized_qualified_relative.jsonl',[{'events':[]}])
            output=Path(tmp)/'inputs/native.jsonl'
            with mock.patch('sys.argv',['resolve_sources.py','--source-package',str(package),'--output',str(output)]):
                with self.assertRaises(ValueError):resolve_sources.main()
            self.assertFalse(output.exists())

    def test_review_export_preserves_actual_model_and_authority(self):
        tr=self.traces[0]
        with tempfile.TemporaryDirectory(dir=e.HERE/'runs') as tmp:
            folder=Path(tmp)
            for authority in ['OPEN_SOURCE_MODEL','CODEX_RESEARCHER_TRIAL','TECHNICAL_TEST_FIXTURE']:
                model=authority+'_ATTRIBUTION_TEST'
                goal={'authority':authority,'model':model,'output':{'task_id':tr['task_id'],'goals':[]}}
                op={'authority':authority,'model':model,'output':{'trajectory_id':tr['trajectory_id'],
                    'units':[{'unit_index':0,'operator':'UNRESOLVED','reason':'Attribution fixture only'}]}}
                e.export_review(folder,[tr],{'goals':{tr['task_id']:goal},'alignment':{}},[op])
                overview=(folder/'REVIEW_INDEX.md').read_text(encoding='utf-8')
                recording=(folder/e.review_file_name(tr)).read_text(encoding='utf-8')
                self.assertIn(authority,overview)
                self.assertIn(model,recording)
                self.assertNotIn('Codex 试跑结果',recording)
                self.assertIn('未经人工',recording)


if __name__ == '__main__':
    unittest.main(verbosity=2)
