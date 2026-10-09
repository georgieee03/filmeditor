"""State and permission regressions; real-engine/browser acceptance is separate."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from film.core import Workspace
from film.codex import Codex
from film.server import FilmServer

class EditorV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ws=Workspace(Path(self.tmp.name)/'workspace')
        self.server=FilmServer(self.ws,0)
    def tearDown(self):
        self.server.codex.close();self.server.server_close();self.ws.close();self.tmp.cleanup()
    def test_full_access_default_and_review_mode_persist(self):
        c=self.server.codex
        self.assertEqual(c.policy()['approvalPolicy'],'never')
        self.assertEqual(c.policy(True)['sandboxPolicy'],{'type':'dangerFullAccess'})
        c.configure('auto-review')
        restarted=Codex(self.ws)
        self.assertEqual(restarted.policy()['approvalsReviewer'],'auto_review')
        self.assertEqual(restarted.policy()['approvalPolicy'],'on-request')
        self.assertEqual(restarted.policy()['sandbox'],'workspace-write')
    def test_permission_change_refuses_active_turn(self):
        c=self.server.codex;c.busy=True
        with self.assertRaises(ValueError):c.configure('ask')
        self.assertEqual(c.permission_mode,'full-access')
    def test_completed_chat_restores_once_and_reset_clears_it(self):
        c=self.server.codex
        c.event('user',{'text':'Hello'})
        c.event('text',{'item_id':'one','text':'Part'})
        c.event('message',{'item_id':'one','text':'Part complete'})
        other=Codex(self.ws)
        self.assertEqual([e['kind'] for e in other.snapshot()['events']],['user','message'])
        other.reset();self.assertEqual(Codex(self.ws).snapshot()['events'],[])
    def test_unknown_permission_rejected(self):
        with self.assertRaises(ValueError):self.server.codex.configure('invented')
    def test_manual_edit_requires_revision_and_operations(self):
        with self.assertRaises(ValueError):self.server.editor.edit('',[{'op':'set_notes'}],project_path='edits/test.mlt')
        with self.assertRaises(ValueError):self.server.editor.edit('x',[],project_path='edits/test.mlt')
    def test_stale_history_never_restores_backup(self):
        self.ws.data['active_project']='edits/test.mlt'
        with patch.object(self.ws,'forward') as forward:
            with self.assertRaises(ValueError):self.server.editor.restore('stale',project_path='edits/test.mlt')
            forward.assert_not_called()
    def test_render_list_excludes_other_projects(self):
        with patch.object(self.ws,'forward',return_value={'structuredContent':{'jobs':[{'job_id':'secret','project_path':'/another/project.mlt'}]}}) as forward:
            self.assertEqual(self.server.editor.renders(),[])
            self.assertEqual(forward.call_count,1)
    def test_locked_tracks_protect_all_manual_actions(self):
        from film.editor import Editor
        snapshot={'tracks':[{'track_id':'v1','name':'V1','properties':{'shotcut:lock':'1'},'filters':[],
            'items':[{'item_ref':'clip','filters':[{'filter_id':'fx'}]}]},
            {'track_id':'v2','name':'V2','properties':{},'filters':[],'items':[]}]}
        for op in [{'op':'remove_item','item_ref':'clip'}, {'op':'split_item','item_ref':'clip'},
                   {'op':'add_clip','track':'V1'}, {'op':'move_item','track':'V2','target_track':'V1'},
                   {'op':'remove_filter','filter_id':'fx'}, {'op':'remove_range','tracks':'all'}]:
            with self.subTest(op=op),self.assertRaises(ValueError):Editor.check_locks(snapshot,[op])
        Editor.check_locks(snapshot,[{'op':'update_track','track':'V1','locked':False},{'op':'remove_item','item_ref':'clip'}])

    def test_identical_sequence_bytes_do_not_authorize_another_active_path(self):
        first=self.ws.root/'edits/first.mlt'; second=self.ws.root/'edits/second.mlt'
        raw=b'<mlt><profile/></mlt>'
        first.write_bytes(raw); second.write_bytes(raw)
        self.ws.data['active_project']='edits/second.mlt'
        revision=hashlib.sha256(raw).hexdigest()
        with patch.object(self.ws,'forward') as forward:
            with self.assertRaisesRegex(ValueError,'Active sequence changed'):
                self.server.editor.edit(revision,[{'op':'set_notes','notes':'wrong sequence'}],project_path=str(first))
            forward.assert_not_called()
        self.assertEqual(second.read_bytes(),raw)

    def test_mcp_and_manual_edits_share_persisted_undo_history_once(self):
        path=str(self.ws.root/'edits/shared.mlt')
        self.ws.data['active_project']='edits/shared.mlt'
        current={'revision':'r0'}; commits=[]
        def engine(method,params,**_):
            name=params['name']; args=params['arguments']
            if name=='inspect_project':
                return {'structuredContent':{'path':path,'revision':current['revision'],'tracks':[]}}
            self.assertEqual(name,'edit_project')
            self.assertEqual(args['expected_revision'],current['revision'])
            backup='backup-'+current['revision']; commits.append(backup)
            current['revision']='r'+str(len(commits))
            return {'structuredContent':{'revision':current['revision'],'backup_path':backup}}
        rpc=Mock(); rpc.request.side_effect=engine
        self.ws.engine_tools=[{'name':'inspect_project'},{'name':'edit_project'}]
        with patch.object(self.ws,'connect_shotcut',return_value=rpc):
            self.ws.call('edit_project',{'project_path':path,'expected_revision':'r0','operations':[{'op':'set_notes','notes':'Codex edit'}]})
            snapshot=self.server.editor.edit('r1',[{'op':'set_notes','notes':'Manual edit'}],project_path=path)
        self.assertTrue(snapshot['can_undo'])
        self.assertEqual(commits,['backup-r0','backup-r1'])
        saved=json.loads(self.server.editor.history_file.read_text())[path]
        self.assertEqual(saved['undo'],commits)
        self.assertEqual(saved['revision'],'r2')

    def test_concurrent_mcp_commits_cannot_overtake_undo_recording(self):
        path=str(self.ws.root/'edits/concurrent.mlt')
        recording_first=threading.Event(); release_first=threading.Event()
        second_ready=threading.Event(); second_committed=threading.Event()
        current={'revision':'r0'}; errors=[]
        record=self.ws.edit_observer; protect=self.ws.protect
        def observer(project,expected,result):
            if expected=='r0':
                recording_first.set()
                if not release_first.wait(3): raise TimeoutError('First history record was not released')
            record(project,expected,result)
        def validate(name,args):
            protect(name,args)
            if args['expected_revision']=='r1': second_ready.set()
        def engine(method,params,**_):
            args=params['arguments']; expected=args['expected_revision']
            self.assertEqual(expected,current['revision'])
            current['revision']='r1' if expected=='r0' else 'r2'
            if expected=='r1': second_committed.set()
            return {'structuredContent':{'revision':current['revision'],'backup_path':'backup-'+expected}}
        def edit(expected):
            try:
                self.ws.forward('edit_project',{'project_path':path,'expected_revision':expected,
                                               'operations':[{'op':'set_notes','notes':expected}]})
            except Exception as exc: errors.append(exc)
        rpc=Mock(); rpc.request.side_effect=engine
        self.ws.engine_tools=[{'name':'edit_project'}]
        first=threading.Thread(target=edit,args=('r0',)); second=threading.Thread(target=edit,args=('r1',))
        with patch.object(self.ws,'connect_shotcut',return_value=rpc), \
             patch.object(self.ws,'edit_observer',side_effect=observer), \
             patch.object(self.ws,'protect',side_effect=validate):
            try:
                first.start(); self.assertTrue(recording_first.wait(2))
                second.start(); self.assertTrue(second_ready.wait(2))
                self.assertFalse(second_committed.wait(.1),'A later commit overtook the first undo record')
            finally:
                release_first.set(); first.join(3)
                if second.ident is not None: second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors,[])
        self.assertTrue(second_committed.is_set())
        saved=json.loads(self.server.editor.history_file.read_text())[path]
        self.assertEqual(saved['revision'],'r2')
        self.assertEqual(saved['undo'],['backup-r0','backup-r1'])

    def test_subagent_completion_cannot_clear_parent_turn(self):
        codex=self.server.codex
        codex.thread_id='parent'; codex.turn_id='parent-turn'; codex.busy=True
        codex.on_message({'method':'turn/started','params':{'threadId':'child','turn':{'id':'child-turn'}}})
        codex.on_message({'method':'turn/completed','params':{'threadId':'child','turn':{'id':'child-turn','status':'completed'}}})
        codex.on_message({'method':'turn/completed','params':{'threadId':'parent','turn':{'id':'old-parent-turn','status':'completed'}}})
        self.assertTrue(codex.busy)
        self.assertEqual(codex.turn_id,'parent-turn')
        self.assertFalse(any(e['kind']=='finished' for e in codex.events))
        codex.on_message({'method':'turn/completed','params':{'threadId':'parent','turn':{'id':'parent-turn','status':'completed'}}})
        self.assertFalse(codex.busy)

    def test_resolved_subagent_question_leaves_no_stale_approval(self):
        codex=self.server.codex; codex.thread_id='parent'
        codex.on_message({'id':'question-1','method':'item/tool/requestUserInput',
                          'params':{'threadId':'child','questions':[]}})
        self.assertIn('question-1',codex.requests)
        codex.on_message({'method':'serverRequest/resolved','params':{'threadId':'child','requestId':'question-1'}})
        self.assertEqual(codex.snapshot()['approvals'],[])

    def test_frame_uses_snapshot_when_original_changes_during_render(self):
        source=self.ws.root/'edits/frame.mlt'; original=b'<mlt version="before"/>'
        source.write_bytes(original); self.ws.data['active_project']='edits/frame.mlt'
        revision=hashlib.sha256(original).hexdigest(); rendered=[]
        def render(name,args):
            self.assertEqual(name,'render_preview')
            frozen=Path(args['project_path']); rendered.append(frozen)
            self.assertNotEqual(frozen,source)
            source.write_bytes(b'<mlt version="after"/>')
            self.assertEqual(frozen.read_bytes(),original)
            Path(args['output_path']).write_bytes(b'fixture image, not a decoder test')
            return {'structuredContent':{'created':True}}
        with patch.object(self.ws,'forward',side_effect=render):
            response=self.server.editor.frame(revision,7,project_path=str(source))
        self.assertEqual(response['revision'],revision)
        self.assertEqual(response['frame'],7)
        self.assertEqual(response['project_path'],str(source))
        self.assertTrue(all(not p.exists() for p in rendered))

    def test_render_recovery_paginates_past_other_workspaces(self):
        output=self.ws.root/'cache/preview.mp4'; output.write_bytes(b'fixture')
        path=str(self.ws.root/'edits/film.mlt'); visited=[]
        def forward(name,args):
            visited.append((name,args))
            if name=='list_render_jobs' and not args.get('cursor'):
                return {'structuredContent':{'jobs':[{'job_id':'other','project_path':'/another/film.mlt'}], 'next_cursor':'page-2'}}
            if name=='list_render_jobs':
                self.assertEqual(args['cursor'],'page-2')
                return {'structuredContent':{'jobs':[{'job_id':'mine','project_path':path,'status':'completed'}]}}
            self.assertEqual(args['job_id'],'mine')
            return {'structuredContent':{'job_id':'mine','status':'completed','output_path':str(output)}}
        with patch.object(self.ws,'forward',side_effect=forward):
            recovered=self.server.editor.renders()
        self.assertEqual([j['job_id'] for j in recovered],['mine'])
        self.assertIn('url',recovered[0])
        self.assertEqual(sum(name=='list_render_jobs' for name,_ in visited),2)

    def test_dead_durable_worker_does_not_block_new_media_work(self):
        rpc=Mock()
        def request(method,params,**_):
            if params['name']=='render_status':
                return {'structuredContent':{'job_id':'dead','status':'failed'}}
            status=params['arguments']['status']
            return {'structuredContent':{'jobs':[{'job_id':'dead','status':'running'}] if status=='running' else []}}
        rpc.request.side_effect=request
        self.ws.engine_tools=[{'name':'list_render_jobs'}]
        with patch.object(self.ws,'bridge_path',return_value=Mock(is_file=lambda:True)), \
             patch.object(self.ws,'connect_shotcut',return_value=rpc), \
             patch('film.core.time.sleep',side_effect=AssertionError('Stale summary caused an unnecessary wait')):
            with self.ws.heavy(): pass

    def test_closing_workspace_terminates_its_preparation_process(self):
        finished=[]
        def prepare():
            try: finished.append(self.ws.run_media([sys.executable,'-c','import time;time.sleep(30)'],timeout=40))
            except Exception as exc: finished.append(exc)
        thread=threading.Thread(target=prepare); thread.start()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with self.ws.lock: children=list(self.ws.processes.values())
            if children: break
            time.sleep(.01)
        try:
            self.assertTrue(children,'Preparation process did not start')
            self.ws.close(); thread.join(6)
            self.assertFalse(thread.is_alive(),'Workspace close left preparation running')
            self.assertTrue(all(child.poll() is not None for child in children))
            self.assertEqual(self.ws.processes,{})
        finally:
            self.ws.close(); thread.join(6)

if __name__=='__main__':unittest.main()
