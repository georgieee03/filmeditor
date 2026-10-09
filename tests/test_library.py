"""Project isolation and ordered references; engine protocol fixture only."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from film.core import Workspace, unpack
from film.server import FilmServer

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ws=Workspace(Path(self.tmp.name)/'library')
        self.fixture=patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(Path(__file__).with_name('fixture_engine.py').resolve())})
        self.fixture.start()
        self.server=FilmServer(self.ws,0)
    def tearDown(self):
        self.server.library.close();self.server.server_close();self.ws.close();self.fixture.stop();self.tmp.cleanup()
    def create(self,name='Film'):
        p=self.server.library.create(name)
        return p,self.server.library.session(p['id'])
    def test_independent_assets_briefs_timelines_and_threads(self):
        a,sa=self.create('One');b,sb=self.create('Two')
        asset=sa.workspace.ingest(io.BytesIO(b'video'),5,'clip.mp4','footage')
        sa.workspace.call('film_brief',{'text':'Only One'})
        sa.workspace.data['thread_id']='private-one';sa.workspace.save()
        self.assertEqual(sb.workspace.state()['assets'],[])
        self.assertEqual(sb.workspace.state()['brief'],'')
        self.assertIsNone(sb.workspace.data['thread_id'])
        with self.assertRaises(ValueError):sb.workspace.path(asset['path'])
        self.assertNotEqual(sa.workspace.active(),sb.workspace.active())
        self.assertIs(sa.workspace.heavy_slots,sb.workspace.heavy_slots)
    def test_archive_rejects_reopen_and_retains_timeline(self):
        a,sa=self.create();path=sa.workspace.active()
        self.server.library.delete(a['id'])
        self.assertTrue(path.exists())
        self.assertEqual(self.server.library.listing(),[])
        with self.assertRaises(ValueError):self.server.library.open(a['id'])
    def test_cannot_delete_active_codex_project(self):
        a,sa=self.create();sa.codex.busy=True
        with self.assertRaises(ValueError):self.server.library.delete(a['id'])
        sa.codex.busy=False
        self.assertEqual(len(self.server.library.listing()),1)
    def test_cannot_delete_rendering_project(self):
        a,sa=self.create();sa.workspace.jobs['x']={'status':'running'}
        with self.assertRaises(ValueError):self.server.library.delete(a['id'])
        sa.workspace.jobs.clear()
    def test_invalid_and_unknown_project_ids(self):
        for ident in ('../escape','missing',''):
            with self.assertRaises(ValueError):self.server.library.open(ident)
        with self.assertRaises(ValueError):self.server.library.create(' ')
    def test_invalid_profile_does_not_allocate_project_or_engine_session(self):
        for settings in ({'fps_num':0},{'fps_den':0},{'fps_num':None},{'fps_num':True},
                         {'fps_num':29.97},{'width':0},{'height':15}):
            with self.subTest(settings=settings),patch.object(self.server.library,'session') as session:
                with self.assertRaises(ValueError):self.server.library.create('Invalid',**settings)
                session.assert_not_called()
                self.assertEqual(self.server.library.listing(),[])
                self.assertFalse((self.ws.root/'projects').exists())
    def test_persistence(self):
        a,sa=self.create('Saved name')
        from film.library import Library
        self.assertEqual(Library(self.server).listing()[0]['name'],'Saved name')
        self.assertTrue((sa.workspace.root/'.film/connection.json').exists())
    def test_ordered_labels_survive_reload_and_reindex_after_delete(self):
        a,sa=self.create();ws=sa.workspace
        for name,kind in [('one.mp4','footage'),('look.png','reference'),('sound.wav','audio'),('two.mov','footage'),('look2.png','reference')]:
            ws.ingest(io.BytesIO(b'123'),3,name,kind)
        assets=ws.ordered_assets()
        self.assertEqual([a['label'] for a in assets],['Video 1','Reference 1','Audio 1','Video 2','Reference 2'])
        source=Path(assets[0]['path']);ws.delete_asset(assets[0]['id'])
        self.assertTrue(source.exists())
        with self.assertRaises(ValueError):ws.asset(assets[0]['id'])
        reloaded=Workspace(ws.root)
        try:
            self.assertEqual([a['label'] for a in reloaded.ordered_assets()],['Reference 1','Audio 1','Video 1','Reference 2'])
            visible=unpack(reloaded.call('film_assets',{}))
            self.assertNotIn('deleted_assets',visible)
        finally:reloaded.close()

    def test_delete_waits_for_upload(self):
        a,sa=self.create();sa.active_operations=1
        with self.assertRaises(ValueError):self.server.library.delete(a['id'])
        sa.active_operations=0
    def test_codex_prompt_captures_asset_order(self):
        from unittest.mock import Mock
        a,sa=self.create();ws=sa.workspace
        first=ws.ingest(io.BytesIO(b'1'),1,'one.mp4','footage')
        second=ws.ingest(io.BytesIO(b'2'),1,'two.mp4','footage')
        rpc=Mock();rpc.request.return_value={'turn':{'id':'turn'}}
        codex=sa.codex;codex.thread_id='thread'
        with patch.object(codex,'connect',return_value=rpc),patch.object(codex,'check_account'):
            codex.start('Use the second video first')
        request=rpc.request.call_args.args[1]
        text=request['input'][0]['text']
        self.assertIn('Video 2',text);self.assertIn(second['id'],text)
        self.assertIn('Use the second video first',text)
        codex.busy=False
    def test_existing_workspace_is_preserved(self):
        self.server.library.file.unlink()
        self.ws.ingest(io.BytesIO(b'1'),1,'existing.mp4','footage')
        from film.library import Library
        library=Library(self.server)
        self.assertEqual(library.listing()[0]['id'],'legacy')
        self.assertIs(library.session('legacy').workspace,self.ws)
