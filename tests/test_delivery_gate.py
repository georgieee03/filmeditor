"""Verify real stream-type checks precede publication; FFprobe responses are fixtures."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from film.core import Workspace
from film.delivery import bind_workspace

class VideoDeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ws=Workspace(Path(self.tmp.name)/'project')
        bind_workspace(self.ws,Path(self.tmp.name),'p','Film')
        self.output=self.ws.root/'exports/final.mp4'
        self.output.write_bytes(b'fixture')
        self.job={'job_id':'fixture','status':'completed','output_path':str(self.output)}
    def tearDown(self):
        self.ws.close();self.tmp.cleanup()
    def response(self,streams):
        return types.SimpleNamespace(returncode=0,stdout=json.dumps({'streams':streams}).encode())
    def test_audio_only_mp4_never_publishes(self):
        with patch.object(self.ws,'executable',return_value='ffprobe'),patch.object(self.ws,'run_media',return_value=self.response([{'codec_type':'audio'}])),patch('film.delivery.publish_completed') as publish:
            self.assertIn('delivery_skipped',self.ws.publish_render(self.job))
            publish.assert_not_called()
    def test_attached_cover_is_not_a_video(self):
        streams=[{'codec_type':'video','disposition':{'attached_pic':1}},{'codec_type':'audio'}]
        with patch.object(self.ws,'executable',return_value='ffprobe'),patch.object(self.ws,'run_media',return_value=self.response(streams)),patch('film.delivery.publish_completed') as publish:
            self.assertIn('delivery_skipped',self.ws.publish_render(self.job))
            publish.assert_not_called()
    def test_actual_video_verification_persists_and_reprobes_changed_source(self):
        with patch.object(self.ws,'executable',return_value='ffprobe'),patch.object(self.ws,'run_media',return_value=self.response([{'codec_type':'video'}])) as probe,patch('film.delivery.publish_completed',side_effect=lambda ws,job:job):
            self.ws.publish_render(self.job);self.ws.publish_render(self.job)
            self.assertEqual(probe.call_count,1)
            self.output.write_bytes(b'changed fixture')
            self.ws.publish_render(self.job)
            self.assertEqual(probe.call_count,2)
    def test_known_audio_only_and_running_jobs_skip_probe(self):
        with patch.object(self.ws,'run_media') as probe:
            self.ws.publish_render({**self.job,'consumer_properties':{'vn':'1'}})
            self.ws.publish_render({**self.job,'status':'running'})
            probe.assert_not_called()
