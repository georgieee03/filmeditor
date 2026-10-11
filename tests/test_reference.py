"""Reference analysis/extraction fixtures; no real FFmpeg or native app runs."""
from array import array
from contextlib import nullcontext
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from film.core import Workspace
from film.reference import REFERENCE_TOOLS, FRAME_BYTES, WIDTH, HEIGHT, Reference, compare_audio, measure_transitions

def frame(color):return bytes(color)*(WIDTH*HEIGHT)
def rhythm(seconds=4):
    values=array('h')
    for index in range(8000*seconds):
        phase=index/8000%0.5
        values.append(round(24000*math.sin(2*math.pi*220*index/8000)*max(0,1-phase/.09)) if phase<.09 else 0)
    return values.tobytes()

def metadata(duration=4,audio=True):
    streams=[{'index':0,'codec_type':'video','duration':str(duration),'disposition':{'default':1,'attached_pic':1}},
             {'index':1,'codec_type':'video','codec_name':'h264','duration':str(duration),'disposition':{'default':1}}]
    if audio: streams += [{'index':2,'codec_type':'audio','duration':str(duration),'codec_name':'aac','disposition':{}},
                          {'index':3,'codec_type':'audio','duration':str(duration),'codec_name':'aac','disposition':{'default':1}}]
    return {'streams':streams,'format':{'duration':str(duration),'tags':{'comment':'Untrusted private container tag'}}}

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'workspace'); self.reference=Reference(self.ws)
        self.asset=self.ws.ingest(io.BytesIO(b'fixture video'),13,'reference.mp4','reference')
        self.commands=[]; self.duration=4; self.raw=frame((0,0,0))*8+frame((255,255,255))*16+frame((255,0,0))*8

    def tearDown(self):self.ws.close(); self.tmp.cleanup()

    def media(self,command,timeout):
        self.commands.append(command)
        if '-f' in command and command[command.index('-f')+1]=='rawvideo': payload=self.raw
        elif '-f' in command and command[command.index('-f')+1]=='s16le': payload=rhythm()
        elif '-show_format' in command:
            payload=json.dumps({'streams':[{'index':0,'codec_type':'audio','duration':str(self.duration),
                'codec_name':'pcm_s16le','sample_rate':'48000','channels':2}], 'format':{'duration':str(self.duration)}}).encode()
        else:
            payload=b''; path=Path(command[-1])
            if path.suffix=='.wav': path.write_bytes(b'fixture PCM'+b'\0'*(math.ceil(self.duration*48000)*4+44-11))
            else:path.write_bytes(b'fixture PNG')
        return subprocess.CompletedProcess(command,0,payload,b'')

    def invoke(self,method,info=None,**kwargs):
        with patch.object(self.ws,'probe',return_value=copy.deepcopy(info or metadata())),\
             patch.object(self.ws,'heavy',side_effect=lambda *args,**kwargs:nullcontext()),\
             patch.object(self.ws,'run_media',side_effect=self.media),patch.object(self.ws,'executable',side_effect=lambda name:'/fixture/'+name):
            return method(**kwargs)

    def test_hard_cut_candidates_use_measured_frames_and_timing_brackets(self):
        report=measure_transitions(self.raw,8,start_seconds=10)
        cuts=[item for item in report['candidates'] if item['kind']=='hard_cut_candidate']
        self.assertEqual([item['time_seconds'] for item in cuts],[11,13])
        self.assertEqual((cuts[0]['start_seconds'],cuts[0]['end_seconds']),(10.875,11.125))
        self.assertTrue(all(item['requires_visual_review'] for item in cuts))
        self.assertEqual(report['median_candidate_cut_interval_seconds'],2)

    def test_flash_is_unknown_instead_of_two_certified_edits(self):
        report=measure_transitions(frame((30,30,30))*5+frame((255,255,255))+frame((30,30,30))*5,8)
        self.assertEqual(len(report['candidates']),1)
        self.assertEqual(report['candidates'][0]['kind'],'unknown_abrupt_change_candidate')

    def test_dark_low_contrast_cut_is_detected_against_local_motion(self):
        raw=frame((8,8,8))*16+frame((22,22,22))*16+frame((40,20,20))*16
        report=measure_transitions(raw,8)
        cuts=[item for item in report['candidates'] if item['kind']=='hard_cut_candidate']
        self.assertEqual([item['time_seconds'] for item in cuts],[2,4])
        self.assertTrue(all(item['change_scale']=='adaptive low contrast' for item in cuts))

    def test_smooth_low_light_motion_is_not_a_series_of_adaptive_cuts(self):
        raw=b''.join(frame((20+index%9,20+index%9,20+index%9)) for index in range(48))
        report=measure_transitions(raw,8)
        self.assertEqual([item for item in report['candidates'] if item['kind']=='hard_cut_candidate'],[])

    def test_gradual_changes_are_candidates_and_dark_blue_is_not_black(self):
        fade=frame((0,0,0))*4+b''.join(frame((value,value,value)) for value in range(20,241,20))+frame((255,255,255))*4
        report=measure_transitions(fade,8)
        self.assertTrue(any(item['kind']=='fade_candidate' for item in report['candidates']))
        mix=frame((255,0,0))*4+b''.join(frame((255-value,0,value)) for value in range(20,241,20))+frame((0,0,255))*4
        report=measure_transitions(mix,8)
        self.assertTrue(any(item['kind']=='gradual_transition_candidate' for item in report['candidates']))
        self.assertFalse(any(item['kind']=='fade_candidate' for item in report['candidates']))

    def test_no_change_and_truncated_frames_have_honest_results(self):
        report=measure_transitions(frame((10,30,100))*10,8)
        self.assertEqual(report['candidates'],[])
        for raw in (b'',b'bad RGB',frame((1,2,3))):
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):measure_transitions(raw,8)

    def test_audio_comparison_does_not_extrapolate_or_claim_intent(self):
        candidates=[{'candidate_id':1,'kind':'hard_cut_candidate','time_seconds':10.51},
                    {'candidate_id':2,'kind':'hard_cut_candidate','time_seconds':12.01},
                    {'candidate_id':3,'kind':'hard_cut_candidate','time_seconds':15}]
        report=compare_audio(candidates,{'estimated_bpm':120,'confidence':.8,'beats_seconds':[0,.5,1,1.5,2,2.5],
            'detected_beats_seconds':[.01,.52,1.02,1.5,2.01,2.51],'duration_analyzed_seconds':3},10,8)
        self.assertEqual(len(report['cut_beat_comparisons']),2)
        self.assertAlmostEqual(report['cut_beat_comparisons'][0]['signed_distance_seconds'],-.01)
        self.assertEqual(report['candidate_cut_intervals_in_estimated_beats'][0],3)
        self.assertEqual(report['beat_source'],'observed_onsets')
        self.assertTrue(any('does not prove' in item for item in report['limitations']))

    def test_analysis_maps_default_real_video_and_audio_and_writes_review_artifacts(self):
        original=Path(self.asset['path']).read_bytes()
        value=self.invoke(self.reference.analyze,asset_id=self.asset['id'],max_seconds=4)
        self.assertEqual(value['video_stream_index'],1); self.assertEqual(value['audio_stream_index'],3)
        self.assertTrue(value['audio']['available']); self.assertFalse(value['visual_review_completed'])
        self.assertTrue(value['review_frames']); self.assertTrue(Path(value['report_path']).is_file())
        self.assertTrue(Path(value['contact_sheet_path']).read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(Path(self.asset['path']).read_bytes(),original)
        self.assertEqual(len(self.ws.data['assets']),1); self.assertFalse(value['timeline_modified'])
        video_command=next(command for command in self.commands if '-f' in command and command[command.index('-f')+1]=='rawvideo')
        audio_command=next(command for command in self.commands if '-f' in command and command[command.index('-f')+1]=='s16le')
        self.assertEqual(video_command[video_command.index('-map')+1],'0:1')
        self.assertEqual(audio_command[audio_command.index('-map')+1],'0:3')
        self.assertNotIn('Untrusted private container tag',json.dumps(value))

    def test_no_audio_is_reported_without_a_tempo_claim(self):
        value=self.invoke(self.reference.analyze,info=metadata(audio=False),asset_id=self.asset['id'],max_seconds=4)
        self.assertFalse(value['audio']['available']); self.assertIsNone(value['audio_stream_index'])
        self.assertFalse(any('s16le' in command for command in self.commands))

    def test_analysis_bounds_and_incomplete_payload_are_rejected(self):
        for kwargs in ({'max_seconds':301},{'sample_fps':13},{'sample_fps':True},{'start_seconds':float('inf')},{'max_seconds':float('nan')}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.reference.analyze(self.asset['id'],**kwargs)
        self.raw+=b'bad'
        with self.assertRaisesRegex(ValueError,'incomplete RGB'):self.invoke(self.reference.analyze,asset_id=self.asset['id'],max_seconds=4)

    def test_full_audio_extraction_registers_new_generated_audio_and_preserves_original(self):
        original=hashlib.sha256(Path(self.asset['path']).read_bytes()).hexdigest()
        value=self.invoke(self.reference.extract_audio,asset_id=self.asset['id'])
        self.assertEqual(value['audio_stream_index'],3); self.assertTrue(value['full_selected_audio'])
        self.assertEqual(value['asset']['kind'],'audio'); self.assertTrue(value['asset']['relative_path'].startswith('generated/'))
        self.assertFalse(Path(value['output_path']).name.startswith('.'))
        self.assertEqual(value['asset']['audio_extraction']['source_asset_id'],self.asset['id'])
        self.assertEqual(hashlib.sha256(Path(self.asset['path']).read_bytes()).hexdigest(),original)
        self.assertFalse(value['timeline_modified']); self.assertEqual(len(self.ws.data['assets']),2)
        command=next(command for command in self.commands if '-c:a' in command)
        self.assertEqual(command[command.index('-map')+1],'0:3')
        self.assertTrue(Path(command[-1]).name.startswith('.')); self.assertFalse(Path(command[-1]).exists())

    def test_explicit_stream_and_range_can_extract_a_bounded_part_of_long_video(self):
        self.duration=2
        value=self.invoke(self.reference.extract_audio,info=metadata(duration=1000),asset_id=self.asset['id'],
                          start_seconds=400,duration_seconds=2,audio_stream_index=2)
        self.assertEqual(value['audio_stream_index'],2); self.assertEqual(value['start_seconds'],400)
        self.assertFalse(value['full_selected_audio'])

    def test_long_full_source_without_duration_never_silently_truncates(self):
        with self.assertRaisesRegex(ValueError,'explicit duration_seconds'):
            self.invoke(self.reference.extract_audio,info=metadata(duration=1000),asset_id=self.asset['id'])
        self.assertEqual(self.commands,[]); self.assertEqual(list((self.ws.root/'generated').iterdir()),[])

    def test_invalid_audio_stream_and_ranges_do_not_publish(self):
        for kwargs in ({'audio_stream_index':1},{'audio_stream_index':True},{'duration_seconds':0},
                       {'duration_seconds':301},{'duration_seconds':float('nan')},{'start_seconds':5},
                       {'start_seconds':3,'duration_seconds':2}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.invoke(self.reference.extract_audio,asset_id=self.asset['id'],**kwargs)
        self.assertEqual(len(self.ws.data['assets']),1); self.assertEqual(list((self.ws.root/'generated').iterdir()),[])

    def test_failed_decode_or_validation_discards_staging_file(self):
        def fail(command,timeout):
            if '-c:a' in command:
                Path(command[-1]).write_bytes(b'partial'); return subprocess.CompletedProcess(command,1,b'',b'fixture decode failure')
            return self.media(command,timeout)
        with patch.object(self.ws,'probe',return_value=metadata()),patch.object(self.ws,'heavy',side_effect=lambda:nullcontext()),\
             patch.object(self.ws,'run_media',side_effect=fail),patch.object(self.ws,'executable',return_value='/fixture/ffmpeg'):
            with self.assertRaisesRegex(ValueError,'fixture decode failure'):self.reference.extract_audio(self.asset['id'])
        self.assertEqual(list((self.ws.root/'generated').iterdir()),[]); self.assertEqual(len(self.ws.data['assets']),1)
        self.duration=3
        with self.assertRaisesRegex(ValueError,'duration did not match'):self.invoke(self.reference.extract_audio,asset_id=self.asset['id'])
        self.assertEqual(list((self.ws.root/'generated').iterdir()),[])

    def test_dispatch_and_tool_namespace_are_bounded(self):
        self.assertEqual({tool['name'] for tool in REFERENCE_TOOLS},{'film_reference_analyze','film_extract_audio'})
        with patch.object(self.reference,'extract_audio',return_value={'fixture':True}) as extracted:
            self.assertEqual(self.reference.dispatch('film_extract_audio',{'asset_id':self.asset['id']}),{'fixture':True})
        extracted.assert_called_once_with(asset_id=self.asset['id'])
        for args in ([],None):
            with self.assertRaises(ValueError):self.reference.dispatch('film_extract_audio',args)

if __name__=='__main__':unittest.main()
