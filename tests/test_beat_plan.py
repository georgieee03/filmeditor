"""Deterministic beat-cut planning fixtures; no media engine is launched."""
from array import array
import io
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from film.core import Workspace, result
from film.music import MUSIC_TOOLS, Music

class BeatPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.ws=Workspace(Path(self.tmp.name)/'workspace'); self.music=Music(self.ws)
        self.asset=self.ws.ingest(io.BytesIO(b'fixture song'),12,'song.wav','audio')

    def tearDown(self):self.ws.close(); self.tmp.cleanup()

    def explicit(self,**kwargs):
        args=dict(duration_seconds=4,bpm=120,beat_phase_seconds=.1,fps_num=30,fps_den=1)
        args.update(kwargs); return self.music.cut_plan(**args)

    def test_explicit_phase_and_source_offset_map_to_exact_frames(self):
        value=self.explicit(source_offset_seconds=.35,timeline_start_frame=90,beats_per_cut=2,duration_seconds=3)
        self.assertEqual(value['cut_frames'],[98,128,158])
        self.assertEqual(value['end_frame_exclusive'],180)
        self.assertEqual(value['segments'],[{'start_frame':90,'end_frame_exclusive':98,'duration_frames':8},
            {'start_frame':98,'end_frame_exclusive':128,'duration_frames':30},
            {'start_frame':128,'end_frame_exclusive':158,'duration_frames':30},
            {'start_frame':158,'end_frame_exclusive':180,'duration_frames':22}])
        self.assertEqual(value['bpm_origin'],'explicit'); self.assertEqual(value['phase_origin'],'explicit')
        self.assertFalse(value['video_recut']); self.assertFalse(value['dialogue_modified'])
        self.assertTrue(all(abs(cut['quantization_error_seconds'])<=1/60+1e-9 for cut in value['cuts']))

    def test_rational_fps_has_no_repeated_rounded_interval_drift(self):
        value=self.explicit(duration_seconds=60,bpm=123,beat_phase_seconds=.17,fps_num=30000,fps_den=1001)
        self.assertEqual((value['fps_num'],value['fps_den']),(30000,1001))
        expected=[math.floor((.17+i*60/123)*30000/1001+.5) for i in range(123)]
        self.assertEqual(value['cut_frames'],[frame for frame in expected if 0<frame<value['duration_frames']])
        self.assertTrue(all(abs(cut['quantization_error_seconds'])<=1001/60000+1e-9 for cut in value['cuts']))

    def test_start_and_end_are_segment_boundaries_not_picture_cuts(self):
        value=self.explicit(duration_seconds=2,beat_phase_seconds=0)
        self.assertEqual(value['cut_frames'],[15,30,45]); self.assertEqual(sum(s['duration_frames'] for s in value['segments']),60)

    def test_partial_opening_and_closing_beats_are_flagged_without_silent_recuts(self):
        value=self.explicit(duration_seconds=2,beat_phase_seconds=.1)
        self.assertEqual(value['cut_frames'],[3,18,33,48])
        self.assertTrue(any('opening is a partial beat' in warning for warning in value['warnings']))
        value=self.explicit(duration_seconds=1.7,beat_phase_seconds=0)
        self.assertTrue(any('partial closing beat' in warning for warning in value['warnings']))

    def test_detected_onsets_are_preferred_over_ideal_grid(self):
        analysis={'estimated_bpm':120,'beat_phase_seconds':.1,'confidence':.7,'partial':False,
                  'audio_duration_seconds':5,'duration_analyzed_seconds':5,
                  'beats_seconds':[.1,.6,1.1,1.6,2.1], 'detected_beats_seconds':[.12,.59,1.13,1.61,2.08]}
        with patch.object(self.music,'analyze',return_value=analysis):
            value=self.music.cut_plan(duration_seconds=2,asset_id=self.asset['id'],fps_num=30,fps_den=1)
        self.assertEqual(value['cut_frames'],[4,18,34,48]); self.assertEqual(value['beat_source'],'observed_onsets')
        self.assertEqual(value['beat_phase_seconds'],.12); self.assertEqual(value['phase_origin'],'observed_onset')
        self.assertEqual(value['heuristic_beat_confidence'],.7)

    def test_analyzed_plan_never_extrapolates_past_decoded_audio(self):
        analysis={'estimated_bpm':120,'beats_seconds':[0,.5,1],'beat_phase_seconds':0,'confidence':.9,
                  'audio_duration_seconds':1000,'duration_analyzed_seconds':3,'partial':True}
        with patch.object(self.music,'analyze',return_value=analysis),self.assertRaisesRegex(ValueError,'does not cover'):
            self.music.cut_plan(duration_seconds=4,asset_id=self.asset['id'],fps_num=30,fps_den=1)

    def test_user_tempo_can_use_analyzed_phase_but_reports_regular_grid(self):
        analysis={'estimated_bpm':120,'beats_seconds':[.1,.6,1.1],'beat_phase_seconds':.1,'confidence':.9,
                  'detected_beats_seconds':[.12,.59,1.13],'audio_duration_seconds':3,'duration_analyzed_seconds':3}
        with patch.object(self.music,'analyze',return_value=analysis):
            value=self.music.cut_plan(duration_seconds=2,asset_id=self.asset['id'],bpm=100,fps_num=30,fps_den=1)
        self.assertEqual(value['cut_frames'],[4,22,40,58]); self.assertEqual(value['beat_source'],'explicit_bpm_grid')
        self.assertEqual(value['phase_origin'],'observed_onset')
        self.assertTrue(any('overridden' in warning for warning in value['warnings']))

    def test_analyzer_reports_observed_phase_and_silence_without_invented_beats(self):
        rate=8000; samples=array('h')
        for index in range(rate*8):
            phase=(index/rate-.16)%0.5
            samples.append(round(24000*math.sin(2*math.pi*220*index/rate)*max(0,1-phase/.09)) if phase<.09 else 0)
        value=Music.estimate_pcm(samples.tobytes())
        self.assertAlmostEqual(value['estimated_bpm'],120,delta=2)
        self.assertAlmostEqual(value['detected_beats_seconds'][0],.16,delta=.02)
        self.assertAlmostEqual(value['beat_phase_seconds'],.16,delta=.02)
        silence=Music.estimate_pcm(b'\0\0'*rate*5)
        self.assertIsNone(silence['beat_phase_seconds']); self.assertEqual(silence['detected_beats_seconds'],[])

    def test_current_project_profile_supplies_exact_rational_frame_rate(self):
        path=self.ws.root/'edits/test.mlt'; path.write_text('<mlt/>'); self.ws.data['active_project']='edits/test.mlt'
        snapshot={'profile':{'frame_rate_num':'24000','frame_rate_den':'1001'},'revision':'a'*64}
        with patch.object(self.ws,'forward',return_value=result(snapshot)) as forward:
            value=self.music.cut_plan(duration_seconds=2,bpm=120,beat_phase_seconds=.1)
        self.assertEqual(value['fps_num'],24000); self.assertEqual(value['fps_den'],1001)
        self.assertEqual(value['project_revision'],'a'*64); forward.assert_called_once_with('inspect_project',{'path':str(path)})

    def test_invalid_parameters_and_missing_phase_are_rejected(self):
        for kwargs in ({'bpm':None},{'beat_phase_seconds':None},{'bpm':True},{'beat_phase_seconds':float('nan')},
                       {'source_offset_seconds':float('inf')},{'beats_per_cut':1.5},{'beats_per_cut':True},
                       {'timeline_start_frame':-1},{'fps_den':None},{'duration_seconds':0},
                       {'duration_seconds':.001},{'project_path':'edits/a.mlt'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.explicit(**kwargs)

    def test_dispatch_is_registered_and_plan_does_not_edit(self):
        self.assertIn('film_music_cut_plan',{tool['name'] for tool in MUSIC_TOOLS})
        with patch.object(self.ws,'forward') as forward:
            value=self.music.dispatch('film_music_cut_plan',{'duration_seconds':1,'bpm':120,'beat_phase_seconds':0,'fps_num':30,'fps_den':1})
        forward.assert_not_called(); self.assertEqual(value['cut_frames'],[15]); self.assertEqual(len(self.ws.data['assets']),1)

if __name__=='__main__':unittest.main()
