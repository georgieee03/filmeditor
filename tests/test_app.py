"""Offline regression tests. Engine protocol fixtures are NOT real engine validation."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from film.core import Workspace, ROOT, atomic, unpack
from film.codex import Codex
from film.server import FilmServer
from film.rpc import JsonlProcess, RpcError

FIXTURE=Path(__file__).with_name('fixture_engine.py').resolve()
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
SCENE='<div data-composition-id="test" data-width="1080" data-height="1920"></div>'
def wait_until(fn,seconds=8):
    end=time.time()+seconds
    while time.time()<end:
        if fn(): return
        time.sleep(.03)
    raise AssertionError('Condition did not complete')

class WorkspaceTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'workspace')
    def tearDown(self): self.ws.close(); self.tmp.cleanup()
    def import_image(self): return self.ws.ingest(io.BytesIO(PNG),len(PNG),'reference.png','reference')
    def test_reject_source_repo_workspace(self):
        with self.assertRaises(ValueError): Workspace(ROOT/'workspace')
    def test_import_preserves_hash_and_readonly(self):
        a=self.import_image(); self.assertEqual(hashlib.sha256(PNG).hexdigest(),a['sha256']); self.assertEqual(Path(a['path']).read_bytes(),PNG); self.assertEqual(Path(a['path']).stat().st_mode & 0o222,0)
    def test_partial_upload_removed(self):
        with self.assertRaises(ValueError): self.ws.ingest(io.BytesIO(b'x'),5,'clip.mp4','footage')
        self.assertEqual(list((self.ws.root/'originals').iterdir()),[])
    def test_upload_extension_rejected(self):
        with self.assertRaises(ValueError): self.ws.ingest(io.BytesIO(b'x'),1,'script.html','reference')
    def test_upload_limit(self):
        with self.assertRaises(ValueError): self.ws.ingest(io.BytesIO(b''),33*1024**3,'clip.mp4','footage')
    def test_path_escape(self):
        with self.assertRaises(ValueError): self.ws.path('../outside')
    def test_symlink_escape(self):
        (self.ws.root/'cache/escape').symlink_to(self.tmp.name,target_is_directory=True)
        with self.assertRaises(ValueError): self.ws.path('cache/escape/private')
    def test_private_state(self):
        self.assertEqual((self.ws.root/'.film').stat().st_mode & 0o077,0); self.assertEqual(self.ws.statefile.stat().st_mode & 0o077,0)
    def test_scene_revision_conflict(self):
        first=self.ws.scene_write('intro',SCENE)
        with self.assertRaises(ValueError): self.ws.scene_write('intro',SCENE+'new','stale')
        self.ws.scene_write('intro',SCENE+'new',first['revision']); self.assertEqual((self.ws.root/'scenes/intro/.history'/(first['revision']+'.html')).read_text(),SCENE)
    def test_scene_name_escape(self):
        with self.assertRaises(ValueError): self.ws.scene_dir('../../secret')
    def test_scene_asset_is_copy(self):
        a=self.import_image(); r=self.ws.scene_asset('intro',a['id']); self.assertEqual(Path(r['path']).read_bytes(),PNG); self.assertNotEqual(Path(r['path']),Path(a['path'])); self.assertTrue(r['src'].startswith('assets/'))
    def test_image_tool_actual_bytes(self):
        a=self.import_image(); r=self.ws.view_image(a['path']); self.assertEqual(base64.b64decode(r['content'][1]['data']),PNG)
    def test_output_originals_denied(self):
        with self.assertRaises(ValueError): self.ws.protect('render_preview',{'output_path':str(self.ws.root/'originals/x.png')})
    def test_nested_output_originals_denied(self):
        with self.assertRaises(ValueError): self.ws.protect('render_preview_batch',{'requests':[{'output_path':str(self.ws.root/'references/x.png')}]})
    def test_force_denied(self):
        with self.assertRaises(ValueError): self.ws.protect('edit_project',{'force':True})
    def test_project_output_scope(self):
        with self.assertRaises(ValueError): self.ws.protect('create_project',{'project_path':str(self.ws.root/'originals/x.mlt')})
    def test_invalid_frame_times(self):
        a=self.import_image()
        for seconds in [[float('nan')],[-1],[True],[],list(range(13))]:
            with self.assertRaises(ValueError): self.ws.frames(a['id'],seconds)
    def test_unknown_asset(self):
        with self.assertRaises(ValueError): self.ws.asset('unknown')
    def test_state_persistence(self):
        unpack(self.ws.call('film_brief',{'text':'warm restrained look'})); other=Workspace(self.ws.root); self.assertEqual(other.data['brief'],'warm restrained look'); other.close()
    def test_tools_without_dependencies(self):
        with mock.patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(self.ws.root/'absent.py')}): self.assertIn('film_scene_asset',{x['name'] for x in self.ws.tools()})
    def test_protocol_fixture_roundtrip(self):
        with mock.patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(FIXTURE)}):
            unpack(self.ws.call('film_new_project',{'name':'test'})); p=self.ws.active(); snap=unpack(self.ws.forward('inspect_project',{'path':str(p)})); a=self.import_image()
            unpack(self.ws.forward('edit_project',{'project_path':str(p),'expected_revision':snap['revision'],'operations':[{'op':'add_clip','track':'V1','path':a['path']}]}))
            t=self.ws.timeline(); self.assertEqual(t['duration_frames'],30); self.assertEqual(t['tracks'][0]['items'][0]['name'],'reference.png')
    def test_xml_entities_rejected(self):
        p=self.ws.root/'edits/bad.mlt'; p.write_text('<!DOCTYPE mlt []><mlt/>'); self.ws.data['active_project']='edits/bad.mlt'
        with self.assertRaises(ValueError): self.ws.timeline()

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class MediaTests(unittest.TestCase):
    setUp=WorkspaceTests.setUp
    tearDown=WorkspaceTests.tearDown
    def test_real_ffmpeg_probe_and_cached_frames(self):
        source=Path(self.tmp.name)/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=96x160:rate=30','-t','0.6','-c:v','libx264','-threads','1',str(source)],check=True)
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        with source.open('rb') as f: a=self.ws.ingest(f,source.stat().st_size,'iphone-test.mp4','footage')
        info=self.ws.probe(a['id']); self.assertEqual(info['streams'][0]['width'],96)
        r=self.ws.frames(a['id'],[0,.2]); p=Path(r['frames'][0]); stamp=p.stat().st_mtime_ns
        self.ws.frames(a['id'],[0]); self.assertEqual(stamp,p.stat().st_mtime_ns); self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
    def test_render_fixture_asset_roundtrip(self):
        self.ws.scene_write('intro',SCENE)
        with mock.patch.dict(os.environ,{'FILM_HYPERFRAMES':str(FIXTURE)}):
            job=self.ws.scene_render('intro','mov',30); wait_until(lambda:self.ws.jobs[job['id']]['status'] not in ('queued','running'))
        j=self.ws.jobs[job['id']]; self.assertEqual(j['status'],'completed',j); self.assertEqual(j['asset']['kind'],'generated'); self.assertTrue(Path(j['output']).is_file())
    def test_render_failure_not_asset(self):
        self.ws.scene_write('intro',SCENE)
        with mock.patch.dict(os.environ,{'FILM_HYPERFRAMES':str(FIXTURE),'FILM_FIXTURE_RENDER_FAIL':'1'}):
            job=self.ws.scene_render('intro'); wait_until(lambda:self.ws.jobs[job['id']]['status'] not in ('queued','running'))
        self.assertEqual(self.ws.jobs[job['id']]['status'],'failed'); self.assertEqual(self.ws.data['assets'],[])
    def test_cancel_render(self):
        self.ws.scene_write('intro',SCENE)
        with mock.patch.dict(os.environ,{'FILM_HYPERFRAMES':str(FIXTURE),'FILM_FIXTURE_RENDER_SLOW':'1'}):
            job=self.ws.scene_render('intro'); wait_until(lambda:job['id'] in self.ws.processes); self.ws.cancel(job['id']); wait_until(lambda:job['id'] not in self.ws.processes)
        self.assertEqual(self.ws.jobs[job['id']]['status'],'cancelled'); self.assertEqual(self.ws.data['assets'],[])

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'workspace'); self.server=FilmServer(self.ws,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.codex.close(); self.ws.close(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()
    def get(self,path='/',auth=False,headers=None):
        h={'Authorization':'Bearer '+self.server.token} if auth else {}; h.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(self.server.origin+path,headers=h),timeout=5)
    def test_static_ui(self):
        with self.get() as r: self.assertIn(b'FilmEditor',r.read()); self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
    def test_api_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as c: self.get('/api/state')
        self.assertEqual(c.exception.code,403)
    def test_api_token_works(self):
        with self.get('/api/state',True) as r: self.assertEqual(json.load(r)['assets'],[])
    def test_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError): self.get('/api/state',True,{'Origin':'https://evil.example'})
    def test_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError): self.get('/',headers={'Host':'evil.example'})
    def test_media_range(self):
        a=self.ws.ingest(io.BytesIO(PNG),len(PNG),'ref.png','reference'); url=self.server.media_url(a['path'])
        with self.get(url,headers={'Range':'bytes=1-4'}) as r: self.assertEqual(r.status,206); self.assertEqual(r.read(),PNG[1:5])
    def test_unsigned_media_rejected(self):
        with self.assertRaises(urllib.error.HTTPError): self.get('/media?path=project.json')
    def test_tampered_media_rejected(self):
        a=self.ws.ingest(io.BytesIO(PNG),len(PNG),'ref.png','reference'); url=self.server.media_url(a['path'])
        with self.assertRaises(urllib.error.HTTPError): self.get(url+'x')
    def test_html_never_served_active(self):
        self.ws.scene_write('intro',SCENE)
        with self.get(self.server.media_url('scenes/intro/index.html')) as r:
            self.assertEqual(r.headers['Content-Type'],'application/octet-stream'); self.assertIn('sandbox',r.headers['Content-Security-Policy'])
    def test_mcp_stdio_connects_to_same_state(self):
        atomic(self.ws.root/'.film/connection.json',json.dumps({'url':self.server.origin,'token':self.server.token}).encode())
        child=JsonlProcess([sys.executable,str(ROOT/'app.py'),'mcp','--workspace',str(self.ws.root)])
        try:
            r=child.request('initialize',{}); self.assertEqual(r['serverInfo']['name'],'filmeditor'); child.notify('notifications/initialized')
            r=child.request('tools/call',{'name':'film_brief','arguments':{'text':'Shared state'}})
            self.assertEqual(unpack(r)['brief'],'Shared state'); self.assertEqual(self.ws.data['brief'],'Shared state')
        finally: child.close()

class CodexTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'workspace'); self.codex=Codex(self.ws)
    def tearDown(self): self.codex.close(); self.ws.close(); self.tmp.cleanup()
    def test_chatgpt_gate_and_model_fixture(self):
        with mock.patch.dict(os.environ,{'FILM_CODEX':str(FIXTURE)}): r=self.codex.models()
        self.assertEqual(r['account']['type'],'chatgpt'); self.assertEqual(r['models'][0]['id'],'fixture-model')
    def test_api_key_auth_refused(self):
        with mock.patch.dict(os.environ,{'FILM_CODEX':str(FIXTURE),'FILM_FIXTURE_ACCOUNT':'apiKey'}):
            with self.assertRaises(ValueError): self.codex.models()
        self.assertIsNone(self.codex.rpc)
    def test_fixture_turn_and_reasoning_not_exposed(self):
        with mock.patch.dict(os.environ,{'FILM_CODEX':str(FIXTURE)}): self.codex.start('Test editing'); wait_until(lambda:not self.codex.busy)
        events=json.dumps(self.codex.snapshot()); self.assertIn('Fixture edit response',events); self.assertNotIn('DO NOT DISPLAY',events)
    def test_local_image_attachment(self):
        a=self.ws.ingest(io.BytesIO(PNG),len(PNG),'ref.png','reference')
        with mock.patch.dict(os.environ,{'FILM_CODEX':str(FIXTURE)}): self.codex.start('Use reference',[a['id']]); wait_until(lambda:not self.codex.busy)
        self.assertEqual(self.ws.data['thread_id'],'fixture-thread')
    def test_approval_roundtrip(self):
        self.codex.rpc=mock.Mock(); self.codex.on_message({'id':7,'method':'item/commandExecution/requestApproval','params':{'command':'echo test'}})
        self.codex.approve('7',False); self.codex.rpc.send.assert_called_with({'id':7,'result':{'decision':'decline'}}); self.assertEqual(self.codex.requests,{})
    def test_expired_approval(self):
        with self.assertRaises(ValueError): self.codex.approve('missing',True)
    def test_permission_escalation_denied(self):
        self.codex.rpc=mock.Mock(); self.codex.on_message({'id':8,'method':'item/permissions/requestApproval','params':{}})
        self.codex.rpc.send.assert_called_once_with({'id':8,'result':{'permissions':{},'scope':'turn'}})
    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError): self.codex.start(' ')

class StaticTests(unittest.TestCase):
    def test_browser_ids_exist(self):
        html=(ROOT/'web/index.html').read_text(); js=(ROOT/'web/app.js').read_text(); ids=re.findall(r'id="([^" ]+)"',html)
        self.assertEqual(len(ids),len(set(ids))); self.assertEqual(set(re.findall(r"\$\('([^']+)'\)",js))-set(ids),set())
    def test_installer_uses_published_hyperframes_name(self):
        text=(ROOT/'scripts/bootstrap.py').read_text(); self.assertIn("'hyperframes':HYPERFRAMES",text); self.assertNotIn("'@hyperframes/cli':",text)

class RpcTests(unittest.TestCase):
    def test_close_reaps_child_when_group_signal_races_exit(self):
        child=JsonlProcess([sys.executable,'-c','import time;time.sleep(.05)'])
        try:
            with mock.patch('film.rpc.os.killpg',side_effect=PermissionError('group exiting')):
                child.close()
            self.assertEqual(child.process.poll(),0)
        finally: child.close()
    def test_close_does_not_hide_permission_failure_for_live_child(self):
        child=JsonlProcess([sys.executable,'-c','import time;time.sleep(5)'])
        try:
            with mock.patch('film.rpc.os.killpg',side_effect=PermissionError('real failure')):
                with self.assertRaises(PermissionError): child.close()
            self.assertIsNone(child.process.poll())
        finally: child.close()
    def test_disconnect_reports_error(self):
        child=JsonlProcess([sys.executable,str(FIXTURE)])
        try:
            with self.assertRaises(RpcError): child.request('crash',{},timeout=3)
        finally: child.close()
    def test_timeout_not_success(self):
        child=JsonlProcess([sys.executable,str(FIXTURE)])
        try:
            with self.assertRaises(RpcError): child.request('sleep',{},timeout=.02)
        finally: child.close()

if __name__=='__main__': unittest.main()
