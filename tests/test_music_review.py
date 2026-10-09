"""Independent music boundary regressions; media render acceptance is separate."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from film.core import Workspace, unpack
from film.music import Music, _PinnedHTTPS, _https_address


class Response:
    def __init__(self, body=b'', status=200, headers=None):
        self.body=io.BytesIO(body); self.status=status; self.headers=headers or {}
        self.closed=False
    def getheader(self, name, default=None): return self.headers.get(name,default)
    def getheaders(self): return list(self.headers.items())
    def read1(self, amount): return self.body.read(amount)
    def close(self): self.closed=True


class MusicReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.ws=Workspace(Path(self.temporary.name)/'project')
        self.music=Music(self.ws)
    def tearDown(self):
        self.ws.close(); self.temporary.cleanup()

    @staticmethod
    def addresses(*values):
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in values]

    def test_mixed_public_private_dns_answers_are_rejected(self):
        for private in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','::ffff:127.0.0.1'):
            with self.subTest(private=private),patch('film.music.socket.getaddrinfo',return_value=self.addresses('8.8.8.8',private)):
                with self.assertRaisesRegex(ValueError,'Private or reserved'):
                    _https_address('https://catalogue.example/song.mp3')

    def test_unsafe_url_forms_never_reach_dns(self):
        with patch('film.music.socket.getaddrinfo') as resolve:
            for url in ('http://example.com/a','file:///tmp/audio','https://user:password@example.com/a',
                        'https://example.com:444/a','https://example.com/a#fragment'):
                with self.subTest(url=url),self.assertRaises(ValueError): _https_address(url)
            resolve.assert_not_called()

    def test_redirect_to_private_address_is_rejected_before_connect(self):
        response=Response(status=302,headers={'Location':'https://private.example/song.mp3'})
        connection=Mock(connected_socket=None); connection.getresponse.return_value=response
        def resolve(host,*args,**kwargs):
            return self.addresses('8.8.8.8' if host=='public.example' else '127.0.0.1')
        with patch('film.music.socket.getaddrinfo',side_effect=resolve), \
             patch('film.music._PinnedHTTPS',return_value=connection) as connect:
            with self.assertRaisesRegex(ValueError,'Private or reserved'):
                self.music._request('https://public.example/song.mp3',100)
        self.assertEqual(connect.call_count,1)
        self.assertTrue(response.closed); connection.close.assert_called_once()

    def test_response_limit_and_truncation_apply_to_streamed_bodies(self):
        for response,maximum,message in ((Response(b'1234567890'),9,'size limit'),
                                         (Response(b'123',headers={'Content-Length':'5'}),10,'Incomplete')):
            with self.subTest(message=message):
                connection=Mock(connected_socket=None); connection.getresponse.return_value=response
                destination=io.BytesIO()
                with patch('film.music.socket.getaddrinfo',return_value=self.addresses('8.8.8.8')), \
                     patch('film.music._PinnedHTTPS',return_value=connection):
                    with self.assertRaisesRegex(ValueError,message):
                        self.music._request('https://public.example/song.mp3',maximum,destination=destination)
                self.assertTrue(response.closed); connection.close.assert_called_once()

    def test_deadline_eof_without_content_length_is_not_partial_success(self):
        clock=[0]
        class ExpiringResponse(Response):
            def read1(self, amount):
                chunk=super().read1(amount)
                if not chunk: clock[0]=31
                return chunk
        response=ExpiringResponse(b'partial song')
        connection=Mock(connected_socket=None); connection.getresponse.return_value=response
        with patch('film.music.socket.getaddrinfo',return_value=self.addresses('8.8.8.8')), \
             patch('film.music._PinnedHTTPS',return_value=connection), \
             patch('film.music.threading.Timer'), \
             patch('film.music.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(ValueError,'timed out'):
                self.music._request('https://public.example/song.mp3',100,timeout=30)
        self.assertTrue(response.closed); connection.close.assert_called_once()

    def test_multiple_addresses_share_one_connection_deadline(self):
        clock=[0]; first=Mock(); second=Mock(); third=Mock()
        def timeout_after(seconds):
            def fail(*args):
                clock[0]+=seconds
                raise TimeoutError('Unreachable public endpoint')
            return fail
        first.connect.side_effect=timeout_after(8)
        second.connect.side_effect=timeout_after(4)
        with patch('film.music.time.monotonic',side_effect=lambda:clock[0]), \
             patch('film.music.socket.socket',side_effect=[first,second,third]) as make_socket:
            connection=_PinnedHTTPS('public.example',['8.8.8.8','8.8.4.4','1.1.1.1'],10)
            connection.deadline=11
            with self.assertRaisesRegex(TimeoutError,'timed out'): connection.connect()
        self.assertEqual(make_socket.call_count,2)
        first.settimeout.assert_called_with(10)
        second.settimeout.assert_called_with(3)
        first.close.assert_called(); second.close.assert_called()

    def test_http_connection_close_keeps_response_socket_until_body_finishes(self):
        # Exercise stdlib HTTP response ownership using a local socket pair,
        # without depending on a provider or an HTTPS test server.
        client,peer=socket.socketpair(); response=None
        connection=_PinnedHTTPS('public.example',[],1)
        connection.sock=client; connection.connected_socket=client
        try:
            connection.request('GET','/song')
            peer.recv(4096)
            peer.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 5\r\n\r\naudio')
            response=connection.getresponse()
            connection.connected_socket.settimeout(1)
            self.assertEqual(response.read1(5),b'audio')
            self.assertGreaterEqual(client.fileno(),0)
        finally:
            if response is not None: response.close()
            connection.retain_socket=False; connection.close(); peer.close()
        self.assertEqual(client.fileno(),-1)

    def test_unknown_license_version_is_not_relabelled_as_valid_cc(self):
        item={'id':'7d573956-f2f0-457a-9ed6-3b2de0280c4e','license':'by','license_version':'9.9',
              'license_url':'https://creativecommons.org/licenses/by/9.9/',
              'url':'https://public.example/song.mp3','category':'music'}
        with self.assertRaises(ValueError): Music._metadata(item)

    def test_import_refresh_rejects_a_licence_changed_since_search(self):
        ident='7d573956-f2f0-457a-9ed6-3b2de0280c4e'
        self.music.catalogue[ident]={'id':ident,'license':'by'}
        detail={'id':ident,'license':'by-nd','license_version':'4.0',
                'url':'https://public.example/song.mp3'}
        with patch.object(self.music,'_request',return_value={'data':json.dumps(detail).encode()}) as request, \
             patch.object(self.music,'_probe') as probe:
            with self.assertRaisesRegex(ValueError,'licence is not enabled'): self.music.import_track(ident)
        self.assertEqual(request.call_count,1,'A rejected licence must not trigger the audio download')
        self.assertIn('/audio/'+ident+'/',request.call_args.args[0])
        probe.assert_not_called(); self.assertEqual(self.ws.data['assets'],[])

    def test_catalogue_duration_uses_milliseconds_and_text_is_inert(self):
        item={'id':'7d573956-f2f0-457a-9ed6-3b2de0280c4e','license':'by','license_version':'3.0',
              'license_url':'https://creativecommons.org/licenses/by/3.0/',
              'url':'https://public.example/song.mp3','category':'music','duration':228000,
              'title':'<script>not executable</script>','creator_url':'javascript:alert(1)'}
        value=Music._metadata(item)
        self.assertEqual(value['duration_seconds'],228)
        self.assertEqual(value['title'],item['title'])
        self.assertEqual(value['creator_url'],'')

    def test_silence_has_no_fabricated_tempo_or_beats(self):
        result=Music.estimate_pcm(bytes(8000*2*5))
        self.assertIsNone(result['estimated_bpm'])
        self.assertEqual(result['beats_seconds'],[])
        self.assertEqual(result['confidence'],0)

    def score_fixture(self):
        source=self.ws.root/'originals/source.wav'; source.write_bytes(b'mocked audio input')
        asset=self.ws.register(source,'Local music','audio')
        path=self.ws.root/'edits/timeline.mlt'; path.write_bytes(b'mocked sequence')
        self.ws.data['active_project']='edits/timeline.mlt'
        snapshot={'path':str(path),'revision':'1'*64,'profile':{'fps':30},'notes':'Existing notes',
                  'tracks':[{'track_id':'v1','name':'V1','kind':'video','properties':{},'filters':[],
                             'items':[{'type':'clip','item_ref':'clip1','start_frame':15,'duration_frames':45,'filters':[]}]}]}
        analysis={'audio_duration_seconds':5,'estimated_bpm':None,'confidence':0,'beats_seconds':[],'partial':False}
        return asset,path,snapshot,analysis

    def test_committed_audio_survives_following_asset_registration_failure(self):
        asset,path,snapshot,analysis=self.score_fixture(); committed=[]
        def forward(name,args):
            if name=='inspect_project': return {'structuredContent':snapshot}
            self.assertEqual(name,'edit_project')
            committed.append(Path(args['operations'][1]['path']))
            return {'structuredContent':{'revision':'2'*64}}
        def render(command,timeout):
            Path(command[-1]).write_bytes(b'mocked fitted audio')
            return subprocess.CompletedProcess(command,0,b'',b'')
        with patch.object(self.music,'analyze',return_value=analysis), \
             patch.object(self.music,'_probe',return_value=({}, {}, 1.5)), \
             patch.object(self.ws,'forward',side_effect=forward), \
             patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()), \
             patch.object(self.ws,'executable',return_value='mock-ffmpeg'), \
             patch.object(self.ws,'run_media',side_effect=render), \
             patch.object(self.ws,'register',side_effect=OSError('Disk full during asset registration')):
            with self.assertRaisesRegex(OSError,'Disk full'):
                self.music.score(asset['id'],str(path),'1'*64)
        self.assertEqual(len(committed),1)
        self.assertTrue(committed[0].is_file(),'A committed MLT reference was deleted after bookkeeping failed')

    def test_committed_audio_survives_history_persistence_failure_in_forward(self):
        asset,path,snapshot,analysis=self.score_fixture(); committed=[]
        def engine(method,params,**kwargs):
            if params['name']=='inspect_project': return {'structuredContent':snapshot}
            self.assertEqual(params['name'],'edit_project')
            committed.append(Path(params['arguments']['operations'][1]['path']))
            path.write_text('Committed audio: '+str(committed[0]))
            return {'structuredContent':{'revision':'2'*64}}
        def render(command,timeout):
            Path(command[-1]).write_bytes(b'mocked fitted audio')
            return subprocess.CompletedProcess(command,0,b'',b'')
        rpc=Mock(); rpc.request.side_effect=engine
        self.ws.engine_tools=[{'name':'inspect_project'},{'name':'edit_project'}]
        self.ws.edit_observer=Mock(side_effect=OSError('Disk full while saving undo history'))
        with patch.object(self.music,'analyze',return_value=analysis), \
             patch.object(self.music,'_probe',return_value=({}, {}, 1.5)), \
             patch.object(self.ws,'connect_shotcut',return_value=rpc), \
             patch.object(self.ws,'protect'), \
             patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()), \
             patch.object(self.ws,'executable',return_value='mock-ffmpeg'), \
             patch.object(self.ws,'run_media',side_effect=render):
            result=self.music.score(asset['id'],str(path),'1'*64)
        self.assertEqual(len(committed),1)
        self.assertTrue(committed[0].is_file(),'Committed MLT media was removed after undo persistence failed')
        self.assertEqual(result['revision'],'2'*64)
        self.assertEqual(Path(result['fitted_asset']['path']),committed[0])

    def test_sequence_switch_during_preparation_discards_audio_without_edit(self):
        asset,path,snapshot,analysis=self.score_fixture(); commits=[]
        def forward(name,args):
            if name=='edit_project': commits.append(args)
            return {'structuredContent':snapshot}
        def render(command,timeout):
            Path(command[-1]).write_bytes(b'mocked fitted audio')
            self.ws.data['active_project']='edits/another.mlt'
            return subprocess.CompletedProcess(command,0,b'',b'')
        with patch.object(self.music,'analyze',return_value=analysis), \
             patch.object(self.music,'_probe',return_value=({}, {}, 1.5)), \
             patch.object(self.ws,'forward',side_effect=forward), \
             patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()), \
             patch.object(self.ws,'executable',return_value='mock-ffmpeg'), \
             patch.object(self.ws,'run_media',side_effect=render):
            with self.assertRaisesRegex(ValueError,'Active sequence changed while preparing'):
                self.music.score(asset['id'],str(path),'1'*64)
        self.assertEqual(commits,[])
        self.assertEqual(list((self.ws.root/'generated').iterdir()),[])

    def export_fixture(self):
        asset,path,_,_=self.score_fixture()
        source=self.ws.data['assets'][0]
        source['music']={'attribution':'Referenced artist, CC BY 4.0.',
                         'license_url':'https://creativecommons.org/licenses/by/4.0/',
                         'changes':'Looped and faded.'}
        self.ws.data['deleted_assets']=[self.ws.data['assets'].pop(0)]
        unused=self.ws.root/'originals/unused.wav'; unused.write_bytes(b'unused song')
        self.ws.register(unused,'Unused music','audio')
        self.ws.data['assets'][0]['music']={'attribution':'Unused artist; must not be credited.'}
        snapshot=self.ws.root/'edits/timeline.render-fixture.mlt'
        document=ET.Element('mlt'); chain=ET.SubElement(document,'chain',{'id':'music'})
        ET.SubElement(chain,'property',{'name':'warp_resource'}).text=asset['path']
        ET.ElementTree(document).write(snapshot,encoding='utf-8')
        metadata={'job_id':'fixture-job','status':'running','project_path':str(path),
                  'project_revision':'1'*64,'render_project_path':str(snapshot),
                  'output_path':str(self.ws.root/'exports/mix.mp4')}
        self.ws.engine_tools=[{'name':'start_render'}]
        rpc=Mock(); rpc.request.return_value={'structuredContent':metadata}
        return path,metadata,rpc

    def test_raw_codex_export_credits_immutable_references_and_retired_assets(self):
        path,metadata,rpc=self.export_fixture()
        # The editable source deliberately has no resource; only the captured
        # render snapshot contains the actual exported soundtrack.
        path.write_text('<mlt/>')
        with patch.object(self.ws,'connect_shotcut',return_value=rpc), \
             patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()), \
             patch('film.ingest.Ingest.ensure_render_ready',return_value={'ready':True,'warnings':[]}):
            result=unpack(self.ws.call('start_render',{'project_path':str(path),
                'expected_revision':'1'*64,'output_path':metadata['output_path']}))
        credits=Path(result['credits_path']).read_text()
        self.assertIn('Referenced artist',credits)
        self.assertIn('Looped and faded.',credits)
        self.assertNotIn('Unused artist',credits)
        self.assertIn('exports/mix.mp4.credits.txt',self.ws.state()['outputs'])
        self.assertEqual(rpc.request.call_count,1)

    def test_credit_write_failure_does_not_hide_an_already_started_render(self):
        path,metadata,rpc=self.export_fixture()
        with patch.object(self.ws,'connect_shotcut',return_value=rpc), \
             patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()), \
             patch('film.ingest.Ingest.ensure_render_ready',return_value={'ready':True,'warnings':[]}), \
             patch('film.core.atomic',side_effect=OSError('No space for credits')):
            result=unpack(self.ws.call('start_render',{'project_path':str(path),
                'expected_revision':'1'*64,'output_path':metadata['output_path']}))
        self.assertEqual(result['job_id'],'fixture-job')
        self.assertEqual(result['status'],'running')
        self.assertTrue(any('credits could not be saved' in warning for warning in result['warnings']))
        self.assertNotIn('credits_path',result)


if __name__=='__main__': unittest.main()
