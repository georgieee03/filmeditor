"""Private native IPC and import ownership regressions, using the labeled engine fixture."""
import base64
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from app import api_request
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
        self.assertEqual(api_request(self.path,'/api/state')['workspace'],str(self.path.resolve()))

if __name__=='__main__': unittest.main()
