"""Regression checks for measured Mac integration defects (no real-engine claims)."""
import json, os, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock
from film.core import Workspace, unpack
from film.codex import Codex

class MacIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'media')
    def tearDown(self): self.ws.close(); self.tmp.cleanup()
    def test_new_project_leaves_default_v1_to_engine(self):
        with mock.patch.object(self.ws,'forward',return_value={'content':[{'type':'text','text':'{}'}]}) as forward:
            self.ws.call('film_new_project',{})
        tracks=forward.call_args.args[1]['tracks']
        self.assertEqual([t['name'] for t in tracks],['V2','V3','A1','A2','A3'])
    def test_shared_slot_waits_and_cancelled_waiter_never_enters(self):
        self.ws.heavy_slots.acquire(); entered=[]; errors=[]
        def work():
            try:
                with self.ws.heavy(lambda: True): entered.append(True)
            except ValueError as e:errors.append(str(e))
        t=threading.Thread(target=work);t.start();t.join(2);self.ws.heavy_slots.release()
        self.assertFalse(t.is_alive());self.assertFalse(entered);self.assertTrue(errors)
    def test_durable_shotcut_job_blocks_new_heavy_work(self):
        rpc=mock.Mock(); polls=0
        def request(method,params):
            nonlocal polls
            if params['name']=='render_status': return {'structuredContent':{'status':'running'}}
            if params['arguments']['status']=='running':
                polls+=1
                return {'structuredContent':{'jobs':[{'job_id':'job','status':'running'}] if polls==1 else []}}
            return {'structuredContent':{'jobs':[]}}
        rpc.request.side_effect=request
        self.ws.engine_tools=[{'name':'list_render_jobs'}]
        with mock.patch.object(self.ws,'bridge_path',return_value=mock.Mock(is_file=lambda:True)),mock.patch.object(self.ws,'connect_shotcut',return_value=rpc),mock.patch('film.core.time.sleep') as sleep:
            with self.ws.heavy():pass
        self.assertEqual(sleep.call_count,1)
    def test_rational_fps_and_worker_argument_reach_child(self):
        self.ws.scene_write('test','<div data-composition-id="test"></div>')
        child=mock.Mock();child.wait.return_value=2
        with mock.patch.object(self.ws,'executable',return_value='/bin/false'),mock.patch.object(self.ws,'heavy') as heavy,mock.patch('film.core.subprocess.Popen',return_value=child) as popen:
            job=self.ws.scene_render('test',fps='30000/1001')
            for _ in range(100):
                if self.ws.jobs[job['id']]['status'] in ('failed','completed'):break
                time.sleep(.01)
            args=popen.call_args.args[0]
            self.assertEqual(args[args.index('--fps')+1],'30000/1001');self.assertEqual(args[args.index('--workers')+1],'1')
    def test_bad_fps_rejected(self):
        for fps in [True,29.97,'1/0','NaN',0,241]:
            with self.assertRaises(ValueError):self.ws.scene_render('test',fps=fps)
    def test_modern_permission_profile_and_standard_speed(self):
        c=Codex(self.ws);rpc=mock.Mock()
        rpc.request.side_effect=[{'thread':{'id':'t'}},{'turn':{'id':'u'}}]
        with mock.patch.object(c,'connect',return_value=rpc),mock.patch.object(c,'check_account'):
            c.start('test')
        for call in rpc.request.call_args_list:
            self.assertEqual(call.args[1]['approvalPolicy'],'never');self.assertNotIn('permissions',call.args[1])
        self.assertEqual(rpc.request.call_args_list[0].args[1]['sandbox'],'danger-full-access')
        self.assertEqual(rpc.request.call_args.args[1]['sandboxPolicy'],{'type':'dangerFullAccess'})
        self.assertEqual(rpc.request.call_args.args[1]['serviceTierForTurn'],'default')

if __name__=='__main__':unittest.main()
