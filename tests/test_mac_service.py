"""Service connection checks; no LaunchAgent installation during unit tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from scripts.manage_mac import connection

class ServiceConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();(self.root/'.film').mkdir()
    def tearDown(self):self.tmp.cleanup()
    def save(self,url):
        (self.root/'.film/connection.json').write_text(json.dumps({'url':url,'token':'test'}))
    def test_never_contacts_non_loopback_connection(self):
        self.save('https://example.com:4317')
        with mock.patch('scripts.manage_mac.urllib.request.urlopen') as request:
            self.assertIsNone(connection(self.root));request.assert_not_called()
    def test_does_not_reuse_a_different_workspace_server(self):
        self.save('http://127.0.0.1:4317')
        with mock.patch('scripts.manage_mac.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value=json.dumps({'workspace':str(self.root/'other')}).encode()
            self.assertIsNone(connection(self.root))
    def test_reuses_authenticated_matching_workspace(self):
        self.save('http://127.0.0.1:4317')
        with mock.patch('scripts.manage_mac.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value=json.dumps({'workspace':str(self.root)}).encode()
            self.assertEqual(connection(self.root)['url'],'http://127.0.0.1:4317')
            self.assertEqual(request.call_args.args[0].get_header('Authorization'),'Bearer test')
