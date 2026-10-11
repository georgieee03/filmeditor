"""Private native IPC and import ownership regressions, using the labeled engine fixture."""
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from app import api_request
from film.core import unpack
from film.desktop import Desktop

class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'library'
        self.env=patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(Path(__file__).with_name('fixture_engine.py').resolve())})
        self.env.start()
        self.desktop=Desktop(self.path)

    def tearDown(self):
        self.desktop.close(); self.env.stop(); self.tmp.cleanup()

    def project(self):
        entry=self.desktop.owner.library.create('Native fixture')
        return self.desktop.owner.library.session(entry['id'])

    def test_private_unix_socket_and_shared_mcp_state(self):
        owner=self.desktop.owner
        self.assertEqual(self.desktop.socket.socket.family,socket.AF_UNIX)
        self.assertEqual(owner.socket_path.stat().st_mode & 0o777,0o600)
        session=self.project()
        api_request(session.workspace.root,'/api/call',{'name':'film_brief','arguments':{'text':'Same native project'}})
        self.assertEqual(session.state()['brief'],'Same native project')
        self.assertEqual(api_request(session.workspace.root,'/api/state')['workspace'],str(session.workspace.root))
        self.assertTrue(session.state()['desktop'])
        self.assertTrue(owner.library.open(session.project_id)['url'].startswith('filmapp://app/'))

    def test_socket_rejects_wrong_token_and_native_file_access(self):
        owner=self.desktop.owner
        with self.assertRaises(PermissionError):
            self.desktop.dispatch({'project':'legacy','token':'wrong','route':'/api/state'})
        with self.assertRaises(PermissionError):
            self.desktop.dispatch({'project':'legacy','token':owner.token,'operation':'import','body':{'paths':['/etc/hosts']}})

    def test_media_capability_is_signed_confined_and_session_bound(self):
        session=self.project()
        item=session.workspace.ingest(io.BytesIO(b'video'),5,'fixture.mp4','footage')
        url=session.media_url(item['path'])
        self.assertEqual(session.resolve_media(url)['size'],5)
        with self.assertRaises(PermissionError): session.resolve_media(url.replace('sig=','sig=bad'))
        with self.assertRaises(PermissionError): self.desktop.owner.resolve_media(url)
        with patch('film.desktop.time.time',return_value=time.time()+3700):
            with self.assertRaises(PermissionError): session.resolve_media(url)
        with self.assertRaises(ValueError): session.media_url('.film/connection.json')
        with self.assertRaises(ValueError): session.media_url('/etc/hosts')

    def test_chunked_import_blocks_deletion_and_cleans_failed_upload(self):
        session=self.project()
        def upload(body):
            return self.desktop.dispatch({'project':session.project_id,'operation':'upload','body':body},trusted=True)
        value=upload({'action':'begin','size':5,'name':'fixture.mp4','kind':'footage'})
        self.assertEqual(session.active_operations,1)
        with self.assertRaises(ValueError): self.desktop.owner.library.delete(session.project_id)
        upload({'action':'chunk','id':value['id'],'offset':0,'data':base64.b64encode(b'video').decode()})
        asset=upload({'action':'finish','id':value['id']})
        self.assertEqual(session.active_operations,0)
        self.assertEqual(Path(asset['path']).read_bytes(),b'video')
        invalid=upload({'action':'begin','size':5,'name':'fixture.mp4','kind':'footage'})
        with self.assertRaises(ValueError): upload({'action':'chunk','id':invalid['id'],'offset':1,'data':'dmlkZW8='})
        self.assertFalse(session.uploads); self.assertEqual(session.active_operations,0)

    def test_archived_session_rejects_operations_atomically(self):
        session=self.project()
        with session.lifecycle_lock: session.archived=True
        with self.assertRaises(ValueError):
            with session.operation(): self.fail('Archived operation started')
        self.assertEqual(session.active_operations,0)

    def test_owner_lock_does_not_overwrite_active_connection(self):
        previous=(self.path/'.film/connection.json').read_bytes()
        with self.assertRaises(ValueError): Desktop(self.path)
        self.assertEqual((self.path/'.film/connection.json').read_bytes(),previous)
        self.assertEqual(self.desktop.owner.state()['workspace'],str(self.path.resolve()))

    def test_external_mcp_follows_selection_but_rejects_stale_client(self):
        first=self.project()
        second_id=self.desktop.owner.library.create('Second fixture')['id']
        second=self.desktop.owner.library.session(second_id)
        def select(ident,sequence):
            return self.desktop.dispatch({'operation':'selectProject','body':{'selected':ident,'sequence':sequence}},trusted=True)
        def call(name,args=None):
            return unpack(api_request(self.path,'/api/call',{'name':name,'arguments':args or {}}))
        with self.assertRaisesRegex(ValueError,'Open a project'): call('film_assets')
        select(first.project_id,1)
        with self.assertRaisesRegex(ValueError,'Reconnect'):
            self.desktop.dispatch({'project':'legacy','token':self.desktop.owner.token,'route':'/api/call','body':{'name':'film_assets'}})
        # Legacy in-app chat explicitly pins the root and never follows selection.
        api_request(self.path,'/api/call',{'name':'film_brief','arguments':{'text':'Legacy pinned'}},follow_selection=False)
        self.assertEqual(self.desktop.owner.state()['brief'],'Legacy pinned')
        with self.assertRaisesRegex(ValueError,'Call film_assets'): call('film_brief',{'text':'unsafe'})
        self.assertEqual(call('film_assets')['workspace'],str(first.workspace.root))
        call('film_brief',{'text':'From desktop'})
        self.assertEqual(first.state()['brief'],'From desktop')
        select(second_id,2)
        self.desktop.dispatch({'project':'legacy','token':self.desktop.owner.token,'client_id':'other-client','route':'/api/call','body':{'name':'film_assets'}})
        with self.assertRaisesRegex(ValueError,'changed'): call('film_brief',{'text':'stale'})
        self.assertEqual(second.state()['brief'],'')
        # Explicit child-workspace Codex stays pinned despite native selection.
        api_request(first.workspace.root,'/api/call',{'name':'film_brief','arguments':{'text':'Pinned'}})
        self.assertEqual(first.state()['brief'],'Pinned')
        self.assertEqual(call('film_assets')['workspace'],str(second.workspace.root))
        call('film_brief',{'text':'Second'})
        select(None,3)
        select(first.project_id,2)  # An older asynchronous navigation callback cannot restore selection.
        with self.assertRaisesRegex(ValueError,'Open a project'): call('film_assets')
        select(first.project_id,4)
        with self.assertRaisesRegex(ValueError,'changed'): call('film_brief',{'text':'stale again'})

    @unittest.skipUnless(sys.platform=='darwin' or hasattr(socket,'SO_PEERCRED'),'Kernel peer identity unavailable')
    def test_older_adapter_processes_have_independent_kernel_bindings(self):
        first=self.project()
        second=self.desktop.owner.library.create('Other')['id']
        script='''import sys,json,socket
from pathlib import Path
config=json.loads((Path(sys.argv[1])/'.film/connection.json').read_text())
for line in sys.stdin:
 body=json.loads(line)
 message={'operation':'api','route':'/api/call','project':'legacy','token':config['token'],'body':body}
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.connect(config['socket']);s.sendall(json.dumps(message).encode()+b'\\n')
  with s.makefile('rb') as f:reply=f.readline()
 print(reply.decode().strip(),flush=True)
'''
        clients=[subprocess.Popen([sys.executable,'-E','-u','-c',script,str(self.path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) for _ in range(2)]
        def call(client,name,args=None):
            client.stdin.write(json.dumps({'name':name,'arguments':args or {}})+'\n');client.stdin.flush()
            return json.loads(client.stdout.readline())
        def select(ident,sequence):
            self.desktop.dispatch({'operation':'selectProject','body':{'selected':ident,'sequence':sequence}},trusted=True)
        try:
            select(first.project_id,1)
            self.assertIn('result',call(clients[0],'film_assets'))
            select(second,2)
            self.assertIn('result',call(clients[1],'film_assets'))
            self.assertIn('changed',call(clients[0],'film_brief',{'text':'stale'})['error'])
            self.assertIn('result',call(clients[1],'film_brief',{'text':'correct'}))
            self.assertEqual(self.desktop.owner.library.session(second).workspace.data['brief'],'correct')
        finally:
            for client in clients:
                client.stdin.close();client.wait(timeout=10);client.stdout.close()

if __name__=='__main__': unittest.main()
