"""Music security/edit regressions; --real exercises synthetic audio with actual MLT."""
from array import array
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import wave
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import Workspace,ROOT,result,unpack
from film.music import Music,_https_address

TRACK={'id':'7d573956-f2f0-457a-9ed6-3b2de0280c4e','title':'Test song','creator':'Test creator',
       'license':'by','license_version':'3.0','license_url':'https://creativecommons.org/licenses/by/3.0/',
       'foreign_landing_url':'https://example.org/song','url':'https://cdn.example.org/song.mp3',
       'source':'jamendo','provider':'jamendo','category':'music','duration':2000,'mature':False}

def rhythm(seconds=8,rate=8000):
    values=array('h')
    for index in range(round(seconds*rate)):
        phase=index/rate%0.5
        values.append(round(24000*math.sin(2*math.pi*220*index/rate)*max(0,1-phase/.09)) if phase<.09 else 0)
    return values.tobytes()

class MusicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ws=Workspace(Path(self.tmp.name)/'workspace');self.music=Music(self.ws)
    def tearDown(self): self.ws.close();self.tmp.cleanup()

    def fake_request(self,items):
        return {'data':json.dumps({'results':items}).encode(),'rate_limits':{'x-ratelimit-limit-anon_burst':'20/min'}}

    def test_default_licenses_duration_and_preview_provenance(self):
        rejected=[dict(TRACK,license=license_) for license_ in ('by-nc','by-nd','by-sa','unknown')]
        with patch.object(self.music,'_request',return_value=self.fake_request([TRACK,*rejected])):
            values=self.music.search('test music',12)['tracks']
        self.assertEqual(len(values),1);self.assertEqual(values[0]['duration_seconds'],2)
        self.assertIn('CC BY 3.0',values[0]['attribution'])
        self.assertEqual(Music._metadata(dict(TRACK,source='freesound'))['preview_only'],True)
        with self.assertRaises(ValueError): Music._metadata(dict(TRACK,license_version=None))
        with self.assertRaises(ValueError): Music._metadata(dict(TRACK,url='http://cdn.example/song.mp3'))

    def test_search_validation_and_persisted_catalogue(self):
        for args in [('',12),('x',0),('x',13),('x',True),('x'*201,1)]:
            with self.subTest(args=args),self.assertRaises(ValueError):self.music.search(*args)
        with patch.object(self.music,'_request',return_value=self.fake_request([TRACK])):
            self.music.search('instrumental',1)
        self.assertIn(TRACK['id'],Music(self.ws).catalogue)
        with self.assertRaises(ValueError):self.music.import_track('not-a-result')
        with self.assertRaises(ValueError):self.music.import_track('00000000-0000-0000-0000-000000000000')

    def test_https_private_address_and_credentials_denied(self):
        for url in ['file:///etc/passwd','http://example.org/audio','https://user:pass@example.org/audio','https://example.org:444/audio','https://example.org/audio#secret']:
            with self.subTest(url=url),self.assertRaises(ValueError):_https_address(url)
        for address in ('127.0.0.1','10.0.0.2','169.254.169.254','::1','fc00::1','::ffff:127.0.0.1'):
            with patch('film.music.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.subTest(address=address),self.assertRaises(ValueError):_https_address('https://private.example/audio')

    def test_redirect_private_destination_and_byte_limit_denied(self):
        class Response:
            status=302
            def getheader(self,name,default=None):return {'Location':'https://private.example/audio'}.get(name,default)
            def close(self):pass
        connection=Mock(connected_socket=None);connection.getresponse.return_value=Response()
        def dns(host,*args,**kwargs):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.2' if host=='private.example' else '8.8.8.8',443))]
        with patch('film.music.socket.getaddrinfo',side_effect=dns),patch('film.music._PinnedHTTPS',return_value=connection) as factory:
            with self.assertRaises(ValueError):self.music._request('https://cdn.example/song',1024)
            self.assertEqual(factory.call_count,1)
        response=Mock(status=200);response.getheader.side_effect=lambda name,default=None:default
        response.read1.return_value=b'12345678901';response.getheaders.return_value=[]
        connection.getresponse.return_value=response
        with patch('film.music.socket.getaddrinfo',side_effect=dns),patch('film.music._PinnedHTTPS',return_value=connection):
            with self.assertRaises(ValueError):self.music._request('https://cdn.example/song',10)

    def test_failed_audio_import_does_not_register_or_retain_download(self):
        self.music.catalogue[TRACK['id']]=Music._metadata(TRACK)
        def request(url,max_bytes,**kwargs):
            if kwargs.get('destination') is not None:
                kwargs['destination'].write(b'<html>not audio</html>')
                return {'bytes':22,'url':url,'mime':'text/html'}
            return {'data':json.dumps(TRACK).encode(),'rate_limits':{}}
        with patch.object(self.music,'_request',side_effect=request),patch.object(self.music,'_probe',side_effect=ValueError('Unreadable audio')):
            with self.assertRaises(ValueError):self.music.import_track(TRACK['id'])
        self.assertEqual(self.ws.data['assets'],[])
        self.assertEqual(list((self.music.directory/'sources').iterdir()),[])

    def test_estimated_beat_grid_and_silence_are_honest(self):
        value=Music.estimate_pcm(rhythm())
        self.assertAlmostEqual(value['estimated_bpm'],120,delta=2)
        self.assertGreater(value['confidence'],.7)
        self.assertGreater(len(value['beats_seconds']),10)
        silent=Music.estimate_pcm(b'\0\0'*8000*5)
        self.assertIsNone(silent['estimated_bpm']);self.assertEqual(silent['beats_seconds'],[])

    def prepare_score(self):
        path=self.ws.root/'edits/test.mlt';path.write_text('<mlt/>')
        self.ws.data['active_project']='edits/test.mlt';self.ws.save()
        asset=self.ws.ingest(io.BytesIO(b'fake sound'),10,'music.wav','audio')
        snapshot={'path':str(path),'revision':'a'*64,'profile':{'fps':30},'notes':'Keep these notes',
            'tracks':[{'track_id':'V1','name':'V1','kind':'video','properties':{},'filters':[],
                'items':[{'type':'clip','item_ref':'visual','start_frame':30,'duration_frames':150}]},
                {'track_id':'A1','name':'A1','kind':'audio','properties':{},'filters':[],
                 'items':[{'type':'clip','item_ref':'dialogue','start_frame':0,'duration_frames':300}]}]}
        calls=[]
        def forward(name,args):
            calls.append((name,copy.deepcopy(args)))
            return result(copy.deepcopy(snapshot)) if name=='inspect_project' else result({'revision':'b'*64})
        def media(command,timeout):
            Path(command[-1]).write_bytes(b'fitted wav')
            return subprocess.CompletedProcess(command,0,b'',b'')
        analysis={'audio_duration_seconds':2,'estimated_bpm':120,'confidence':.9,'beats_seconds':[0,.5,1,1.5],'partial':False}
        return path,asset,snapshot,calls,forward,media,analysis

    def test_score_one_transaction_global_fades_video_span_dialogue_preserved(self):
        path,asset,snapshot,calls,forward,media,analysis=self.prepare_score()
        with patch.object(self.ws,'forward',side_effect=forward),patch.object(self.ws,'run_media',side_effect=media),\
             patch.object(self.ws,'executable',return_value='/bin/true'),patch.object(self.music,'analyze',return_value=analysis),\
             patch.object(self.music,'_probe',return_value=({}, {},5)):
            value=self.music.score(asset['id'],str(path),'a'*64,source_offset_seconds=.5,fade_in_seconds=30,fade_out_seconds=30)
        edits=[args for name,args in calls if name=='edit_project']
        self.assertEqual(len(edits),1)
        ops=edits[0]['operations'];self.assertEqual(ops[0],{'op':'add_track','kind':'audio','name':'Music'})
        self.assertEqual(ops[1]['position_frame'],30);self.assertEqual(ops[1]['out_frame'],149)
        self.assertAlmostEqual(value['fade_in_seconds']+value['fade_out_seconds'],5)
        self.assertEqual(value['end_frame_exclusive'],180)
        self.assertFalse(value['video_recut']);self.assertFalse(value['dialogue_modified'])
        self.assertTrue(ops[2]['notes'].startswith('Keep these notes'))
        self.assertTrue(all(op['op'] in ('add_track','add_clip','set_notes','add_marker') for op in ops))
        self.assertTrue(all(30<=frame<180 for frame in value['beat_markers_frames']))

    def test_score_rechecks_active_identity_after_audio_work(self):
        path,asset,snapshot,calls,forward,media,analysis=self.prepare_score()
        def change(command,timeout):
            value=media(command,timeout)
            other=self.ws.root/'edits/other.mlt';other.write_text('<mlt/>')
            self.ws.data['active_project']='edits/other.mlt'
            return value
        with patch.object(self.ws,'forward',side_effect=forward),patch.object(self.ws,'run_media',side_effect=change),\
             patch.object(self.ws,'executable',return_value='/bin/true'),patch.object(self.music,'analyze',return_value=analysis),\
             patch.object(self.music,'_probe',return_value=({}, {},5)):
            with self.assertRaises(ValueError):self.music.score(asset['id'],str(path),'a'*64)
        self.assertFalse(any(name=='edit_project' for name,args in calls))
        self.assertFalse(list((self.ws.root/'generated').iterdir()))

    def test_music_numbers_reject_nan_infinity_and_boolean(self):
        path,asset,*_=self.prepare_score()
        for field,value in [('gain_db',float('nan')),('source_offset_seconds',float('inf')),('fade_in_seconds',True),('loop',1)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.music.score(asset['id'],str(path),'a'*64,**{field:value})

    def test_import_queue_is_shared_and_cancelled_on_workspace_shutdown(self):
        self.assertIs(Music(self.ws).download_slots,self.music.download_slots)
        self.music.download_slots.acquire()
        self.ws.stopping=True
        try:
            with self.assertRaisesRegex(ValueError,'cancelled while queued'):self.music.import_track(TRACK['id'])
        finally:self.music.download_slots.release()

    def test_nonlooping_offset_requires_one_complete_frame(self):
        path,asset,snapshot,calls,forward,media,analysis=self.prepare_score()
        with patch.object(self.ws,'forward',side_effect=forward),patch.object(self.music,'analyze',return_value=analysis),\
             patch.object(self.ws,'run_media') as render:
            with self.assertRaisesRegex(ValueError,'at least one sequence frame'):
                self.music.score(asset['id'],str(path),'a'*64,source_offset_seconds=1.99995,loop=False)
        render.assert_not_called();self.assertFalse(any(name=='edit_project' for name,args in calls))

    def test_score_rechecks_revision_after_preparation(self):
        path,asset,snapshot,calls,forward,media,analysis=self.prepare_score();prepared=False
        def change(command,timeout):
            nonlocal prepared
            prepared=True;return media(command,timeout)
        def current(name,args):
            if name=='inspect_project' and prepared:
                changed=copy.deepcopy(snapshot);changed['revision']='c'*64
                return result(changed)
            return forward(name,args)
        with patch.object(self.ws,'forward',side_effect=current),patch.object(self.ws,'run_media',side_effect=change),\
             patch.object(self.ws,'executable',return_value='/bin/true'),patch.object(self.music,'analyze',return_value=analysis),\
             patch.object(self.music,'_probe',return_value=({}, {},5)):
            with self.assertRaisesRegex(ValueError,'Timeline changed while preparing'):
                self.music.score(asset['id'],str(path),'a'*64)
        self.assertFalse(any(name=='edit_project' for name,args in calls))
        self.assertFalse(list((self.ws.root/'generated').iterdir()))


def real_acceptance():
    """Actual generated pulse song + video/dialogue, atomic MLT edit and decoded export."""
    from film.server import FilmServer
    report={'synthetic_only':True}
    destination=ROOT/'reports/rebuild/music';destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='filmeditor-music-acceptance-') as temporary:
        ws=Workspace(Path(temporary)/'workspace');server=FilmServer(ws,0);music=Music(ws)
        try:
            unpack(ws.call('film_new_project',{'name':'Synthetic soundtrack validation','width':640,'height':360,'fps_num':30,'fps_den':1}))
            video=Path(temporary)/'video.mp4'
            subprocess.run([ws.executable('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=640x360:r=30',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','8','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
            song=Path(temporary)/'synthetic-rhythm.wav'
            with wave.open(str(song),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000);audio.writeframes(rhythm(4))
            source_hash=hashlib.sha256(song.read_bytes()).hexdigest()
            with video.open('rb') as stream:v=ws.ingest(stream,video.stat().st_size,video.name,'footage')
            with song.open('rb') as stream:a=ws.ingest(stream,song.stat().st_size,song.name,'audio')
            snap=server.editor.snapshot()
            snap=server.editor.edit(snap['revision'],[{'op':'add_clip','track':'V1','path':v['path'],'position_frame':30}],project_path=snap['path'])
            snap=server.editor.edit(snap['revision'],[{'op':'set_notes','notes':'Preserve these original sequence notes.'}],project_path=snap['path'])
            before=copy.deepcopy(snap['tracks']);history=len(server.editor.history[str(ws.active())]['undo'])
            analysis=music.analyze(a['id']);report['analysis']={key:analysis[key] for key in ('estimated_bpm','confidence','method','estimated')}
            value=music.score(a['id'],snap['path'],snap['revision'],source_offset_seconds=.5,gain_db=-12,fade_in_seconds=.4,fade_out_seconds=.8)
            after=server.editor.snapshot()
            assert after.get('notes','').startswith('Preserve these original sequence notes.'),'Existing sequence notes were lost'
            report['existing_sequence_notes_preserved']=True
            assert len(server.editor.history[str(ws.active())]['undo'])==history+1,'Not one atomic shared-history edit'
            old_ids={track['track_id'] for track in before}
            previous=[track for track in after['tracks'] if track['track_id'] in old_ids]
            def content(tracks):
                # item_ref is deliberately scoped to the MLT revision and changes
                # after every transaction, even when the clip itself is preserved.
                values=copy.deepcopy(tracks)
                for track in values:
                    for item in track['items']:item.pop('item_ref',None)
                return values
            assert content(previous)==content(before),'Existing picture/dialogue changed'
            music_track=next(track for track in after['tracks'] if track['name']==value['track'])
            clips=[item for item in music_track['items'] if item['type']=='clip']
            assert len(clips)==1 and clips[0]['start_frame']==30 and clips[0]['duration_frames']==240,'Music span not exact'
            fitted=Path(value['fitted_asset']['path'])
            with wave.open(str(fitted),'rb') as audio:
                assert audio.getframerate()==48000 and audio.getnframes()==384000,'Audio samples not exact'
                samples=array('h');samples.frombytes(audio.readframes(audio.getnframes()))
            def rms(start,end):
                block=samples[round(start*48000)*2:round(end*48000)*2]
                return math.sqrt(sum(sample*sample for sample in block)/max(1,len(block)))/32768
            assert rms(0,.025)<rms(.02,.06),'Music fade-in not audible'
            assert rms(7.7,7.76)<rms(7.52,7.58),'Music fade-out not audible'
            report.update(real_audio_fit=True,one_shared_history_transaction=True,existing_picture_dialogue_preserved=True,
                exact_video_span=True,global_fade_envelope=True,source_untouched=hashlib.sha256(song.read_bytes()).hexdigest()==source_hash,
                estimated_markers=len(value['beat_markers_frames']),duration_seconds=value['duration_seconds'])
            job=server.editor.render(after['revision'],preview=True,project_path=after['path'])
            for _ in range(150):
                status=unpack(ws.forward('render_status',{'job_id':job['job_id']}))
                if status['status'] not in ('queued','running'):break
                time.sleep(.2)
            assert status['status']=='completed',status
            decoded=subprocess.run([ws.executable('ffmpeg'),'-v','error','-i',status['output_path'],'-f','null','-'],capture_output=True)
            assert decoded.returncode==0,decoded.stderr
            report['real_mlt_mix_export_decoded']=True
            mixed=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-ss','3','-i',status['output_path'],
                '-t','2','-map','0:a:0','-ac','1','-ar','8000','-f','s16le','pipe:1'],timeout=30)
            assert mixed.returncode==0,mixed.stderr
            mixed_samples=array('h');mixed_samples.frombytes(mixed.stdout)
            if sys.byteorder!='little':mixed_samples.byteswap()
            assert len(mixed_samples)==16000,'Export interval not exact'
            def amplitude(frequency):
                sine=sum(value*math.sin(2*math.pi*frequency*index/8000) for index,value in enumerate(mixed_samples))
                cosine=sum(value*math.cos(2*math.pi*frequency*index/8000) for index,value in enumerate(mixed_samples))
                return 2*math.hypot(sine,cosine)/len(mixed_samples)/32768
            song_level=amplitude(220);dialogue_level=amplitude(440)
            assert song_level>.001 and dialogue_level>.01,{'song':song_level,'dialogue':dialogue_level}
            report['export_music_and_dialogue_actually_mixed']={'music_220Hz_amplitude':song_level,'dialogue_440Hz_amplitude':dialogue_level}
            server.editor.restore(after['revision'],'undo',project_path=after['path'])
            assert server.editor.snapshot()['tracks']==before,'Undo did not restore original tracks'
            report['one_step_undo']=True
            (destination/'synthetic-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        finally:server.library.close();server.codex.close();ws.close();server.server_close()
    print(json.dumps(report,indent=2))


def live_acceptance():
    """Anonymous real catalogue/download; preserve only metadata evidence in the repo."""
    report={'live_public_catalogue':True,'credentials_used':False}
    with tempfile.TemporaryDirectory(prefix='filmeditor-music-catalogue-') as temporary:
        ws=Workspace(Path(temporary)/'workspace'); music=Music(ws)
        try:
            found=music.search('ambient',3)
            assert found['tracks'],'No supported public music was found'
            track=next((item for item in found['tracks'] if item['source']=='jamendo'),found['tracks'][0])
            imported=music.import_track(track['id']); asset=imported['asset']
            source=Path(asset['music']['original_download']);original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
            with ws.heavy():
                decoded=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',
                    '-protocol_whitelist','file,pipe','-i',asset['path'],'-map','0:a:0','-vn','-f','null','-'],timeout=120)
            assert decoded.returncode==0,decoded.stderr.decode(errors='replace')
            assert original_hash==hashlib.sha256(source.read_bytes()).hexdigest()
            assert asset['music']['license'] in ('by','cc0','pdm') and asset['music']['attribution']
            assert Path(imported['credit_path']).is_file()
            report.update(track_id=track['id'],title=track['title'],creator=track['creator'],source=track['source'],
                license=asset['music']['license'],license_version=asset['music']['license_version'],
                license_url=asset['music']['license_url'],source_url=asset['music']['source_url'],
                duration_seconds=asset['music']['duration_seconds'],download_bytes=asset['music']['download_bytes'],
                preview_only=asset['music']['preview_only'],actual_full_audio_decode=True,
                retained_readonly_original=True,attribution_preserved=True,rate_limits=found['rate_limits'],
                no_song_in_repository=True,rights_evidence='Provider metadata; not independent copyright clearance')
        finally:ws.close()
    destination=ROOT/'reports/rebuild/music';destination.mkdir(parents=True,exist_ok=True)
    (destination/'live-openverse.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    if '--real' in sys.argv:
        real_acceptance()
    elif '--live' in sys.argv:
        live_acceptance()
    else:unittest.main()
