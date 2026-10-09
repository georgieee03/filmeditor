"""Controlled job/geometry tests. Real filter efficacy is enhance_acceptance.py."""
import copy
from contextlib import contextmanager
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film.core import Workspace
from film.enhance import Enhance, ENGINE, _crop_path, _geometry, _duration, _expression
from film.music import Music

VIDEO = {'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'duration': '2'}, 'streams': [
    {'index': 0, 'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2, 'duration': '2'},
    {'index': 1, 'codec_type': 'video', 'codec_name': 'h264', 'width': 640, 'height': 360, 'duration': '2',
     'avg_frame_rate': '30/1', 'r_frame_rate': '30/1', 'sample_aspect_ratio': '1:1', 'display_aspect_ratio': '16:9',
     'pix_fmt': 'yuv420p', 'bits_per_raw_sample': '8', 'field_order': 'progressive', 'color_transfer': 'bt709',
     'color_primaries': 'bt709', 'color_space': 'bt709', 'color_range': 'tv', 'disposition': {'default': 1}}]}

class EnhanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.temporary.name) / 'workspace')
        self.asset = self.ws.ingest(io.BytesIO(b'fixture source'), 14, 'source.mp4', 'footage')
        self.source = Path(self.asset['path'])
        self.e = Enhance(self.ws)
        self.metadata = copy.deepcopy(VIDEO)
        self.commands = []
        self.output_duration = '2'

    def tearDown(self):
        self.ws.close()
        for _ in range(100):
            if all(j['status'] not in ('queued', 'running') for j in self.ws.jobs.values()):
                break
            time.sleep(.01)
        self.temporary.cleanup()

    def fake_process(self, ident, command, **kwargs):
        self.commands.append(command)
        if 'f32le' in command:
            from array import array
            rate = int(re.search(r':s=(\d+)', command[command.index('-i') + 1]).group(1))
            impulse = array('f', [0.]) * round(rate * .1)
            impulse[round(rate * .05)] = .5
            return impulse.tobytes()
        elif '--output' in command:
            duration = float(self.metadata['format']['duration'])
            payload = {'version': 1, 'width': 640, 'height': 360, 'duration': duration,
                       'points': [{'time': 0, 'x': .25, 'y': .5}, {'time': duration, 'x': .75, 'y': .5}], 'warnings': []}
            Path(command[command.index('--output') + 1]).write_text(json.dumps(payload))
        elif '-progress' in command:
            Path(command[-1]).write_bytes(b'fixture derivative')
        elif '-show_entries' in command:
            source_video = next((s for s in self.metadata['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
            source_audio = next((s for s in self.metadata['streams'] if s.get('codec_type') == 'audio'), None)
            streams = []
            if source_video:
                width, height = source_video['width'], source_video['height']
                encoding = next((c for c in reversed(self.commands) if '-progress' in c), [])
                if '-vf' in encoding:
                    match = re.search(r'crop=w=(\d+):h=(\d+)', encoding[encoding.index('-vf') + 1])
                    if match:
                        width, height = map(int, match.groups())
                streams.append({'index': 0, 'codec_type': 'video', 'width': width, 'height': height})
            if source_audio:
                streams.append({'index': len(streams), 'codec_type': 'audio', 'channels': source_audio['channels'], 'sample_rate': source_audio['sample_rate']})
            return json.dumps({'format': {'duration': self.output_duration}, 'streams': streams}).encode()
        return b''

    def started(self, operation='stabilize', **kwargs):
        with patch.object(self.ws, 'probe', return_value=self.metadata), patch.object(self.e, '_capabilities', return_value=('fixture engine', {'deshake', 'highpass', 'afftdn', 'acompressor', 'alimiter', 'crop'})), patch.object(self.e, '_process', side_effect=self.fake_process), patch.object(self.e, '_helper', return_value=sys.executable):
            job = self.e.start(self.asset['id'], operation, **kwargs)
            return self.wait(job['id'])

    def wait(self, ident):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.e.status(ident)['job']
            if job['status'] in ('completed', 'failed', 'cancelled'):
                return job
            time.sleep(.01)
        self.fail('fixture worker did not finish')

    def test_audio_first_selection_provenance_and_original_immutable(self):
        original_bytes = self.source.read_bytes()
        original_hash = self.asset['sha256']
        job = self.started()
        self.assertEqual(job['status'], 'completed', job)
        command = next(c for c in self.commands if '-progress' in c)
        self.assertEqual([command[i + 1] for i, v in enumerate(command) if v == '-map'], ['0:1', '0:0'])
        derived = self.ws.asset(job['asset_id'])
        self.assertEqual(derived['kind'], 'generated')
        self.assertEqual(derived['enhancement']['source_sha256'], original_hash)
        self.assertTrue(derived['enhancement']['full_source'])
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(self.ws.asset(self.asset['id'])['sha256'], original_hash)
        self.assertEqual(Path(derived['path']).stat().st_mode & 0o777, 0o444)
        self.assertEqual(derived['enhancement']['video_stream_index'], 1)
        self.assertEqual(derived['enhancement']['audio_stream_index'], 0)
        self.assertIsNone(self.ws.data['active_project'])

    def test_audio_only_cleanup_preserves_channels_and_music_credits(self):
        self.metadata['streams'] = self.metadata['streams'][:1]
        music = {'title': 'Fixture', 'creator': 'Fixture artist', 'source_url': 'https://example.com/fixture',
                 'license': 'by', 'license_url': 'https://creativecommons.org/licenses/by/4.0/', 'changes': 'Trimmed and fitted'}
        self.ws.data['assets'][0]['music'] = music
        job = self.started('audio_cleanup')
        self.assertEqual(job['status'], 'completed', job)
        asset = self.ws.asset(job['asset_id'])
        self.assertEqual(Path(asset['path']).suffix, '.wav')
        self.assertEqual(asset['enhancement']['output_channels'], 2)
        self.assertEqual(asset['music']['license'], 'by')
        self.assertIn('afftdn', Music._credit(asset['music']))
        self.assertEqual(self.ws.asset(self.asset['id'])['music'], music)
        command = next(c for c in self.commands if '-progress' in c)
        self.assertIn('level=false:latency=true', command[command.index('-af') + 1])
        self.assertIn('atrim=start_sample=1200', command[command.index('-af') + 1])
        self.assertEqual(asset['enhancement']['compensated_delay_samples'], 1200)
        self.assertNotIn('-ac', command)

    def test_reframe_crop_points_are_bounded_and_helper_has_explicit_subject(self):
        job = self.started('reframe', subject='manual', center_x=.3, center_y=.7)
        self.assertEqual(job['status'], 'completed', job)
        p = job['provenance']
        self.assertEqual(p['crop_width'] * 16, p['crop_height'] * 9)
        self.assertLessEqual(len(p['crop_points']), 64)
        self.assertEqual(p['crop_points'][0]['time'], 0)
        self.assertEqual(p['crop_points'][-1]['time'], 2)
        self.assertTrue(all(0 <= point['x'] <= 640 - p['crop_width'] and 0 <= point['y'] <= 360 - p['crop_height'] for point in p['crop_points']))
        helper = next(c for c in self.commands if '--subject' in c)
        self.assertEqual(helper[helper.index('--subject') + 1], 'manual')

    def test_risk_and_unusable_inputs_are_rejected_before_queueing(self):
        variants = []
        for key, value in [('color_transfer', 'smpte2084'), ('color_transfer', 'log'), ('color_primaries', 'smpte432'),
                           ('pix_fmt', 'yuv420p10le'), ('sample_aspect_ratio', '4:3'), ('field_order', 'tt')]:
            info = copy.deepcopy(VIDEO)
            info['streams'][1][key] = value
            if key == 'pix_fmt':
                info['streams'][1]['bits_per_raw_sample'] = '10'
            variants.append(info)
        info = copy.deepcopy(VIDEO)
        info['streams'].append(dict(info['streams'][1], index=3))
        variants.append(info)
        info = copy.deepcopy(VIDEO)
        info['format']['duration'] = '301'
        variants.append(info)
        info = copy.deepcopy(VIDEO)
        info['streams'][1]['side_data_list'] = [{'side_data_type': 'Display Matrix', 'rotation': 45}]
        variants.append(info)
        info = copy.deepcopy(VIDEO)
        info['streams'][1].pop('index')
        variants.append(info)
        for info in variants:
            with self.subTest(info=info), patch.object(self.ws, 'probe', return_value=info), self.assertRaises(ValueError):
                self.e.start(self.asset['id'], 'reframe')
        self.assertEqual(self.ws.jobs, {})
        self.assertEqual(len(self.ws.data['assets']), 1)

    def test_still_and_missing_audio_are_not_video_or_cleanup(self):
        still = {'format': {'format_name': 'image2', 'duration': '.04'}, 'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'mjpeg', 'nb_frames': '1'}]}
        for metadata, operation in [(still, 'stabilize'), ({'format': VIDEO['format'], 'streams': VIDEO['streams'][1:]}, 'audio_cleanup')]:
            with patch.object(self.ws, 'probe', return_value=metadata), self.assertRaises(ValueError):
                self.e.start(self.asset['id'], operation)
        for value in (float('nan'), float('inf'), True, -.1, 1.1):
            with self.assertRaises(ValueError):
                self.e.start(self.asset['id'], 'reframe', center_x=value)

    def test_audio_rate_and_channels_fail_before_queuing(self):
        for key, value in [('sample_rate', '0'), ('sample_rate', 'N/A'), ('sample_rate', True), ('sample_rate', float('inf')),
                           ('sample_rate', '7999'), ('sample_rate', '192001'), ('channels', True), ('channels', 0), ('channels', 9)]:
            metadata = copy.deepcopy(VIDEO)
            metadata['streams'][0][key] = value
            with self.subTest(key=key, value=value), patch.object(self.ws, 'probe', return_value=metadata), self.assertRaises(ValueError):
                self.e.start(self.asset['id'], 'audio_cleanup')
        self.assertEqual(self.ws.jobs, {})

    def test_exact_300_second_source_accepts_bounded_output_padding(self):
        self.metadata['format']['duration'] = '300'
        for s in self.metadata['streams']:
            s['duration'] = '300'
        self.output_duration = '300.033'
        self.assertEqual(self.started()['status'], 'completed')

    def test_initial_history_failure_rolls_back_queue_slot(self):
        with patch.object(self.ws, 'probe', return_value=self.metadata), patch.object(self.e, '_save', side_effect=OSError('fixture disk full')):
            with self.assertRaises(OSError):
                self.e.start(self.asset['id'], 'stabilize')
        self.assertEqual(self.ws.jobs, {})
        self.assertEqual(list((self.ws.root / '.film/enhancements').glob('*')), [])

    def test_capability_inventory_accepts_current_two_and_legacy_three_columns(self):
        outputs = [b'fixture FFmpeg 9\n', b' .. deshake V->V Stabilize\n TS afftdn A->A Denoise\n TSC highpass A->A Filter\n .. crop V->V Crop\n']
        with patch.object(self.e, '_process', side_effect=outputs):
            version, filters = self.e._capabilities('fixture')
        self.assertEqual(version, 'fixture FFmpeg 9')
        self.assertEqual(filters, {'deshake', 'afftdn', 'highpass', 'crop'})

    def test_final_job_history_failure_keeps_saved_result(self):
        save = self.e._save
        def injected():
            if any(j['status'] == 'completed' for j in self.ws.jobs.values()):
                raise OSError('fixture final history failure')
            save()
        with patch.object(self.e, '_save', side_effect=injected):
            job = self.started()
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(len(self.ws.data['assets']), 2)
        asset = self.ws.asset(job['asset_id'])
        self.assertTrue(Path(asset['path']).is_file())
        self.assertIn('history', ' '.join(job['warnings']))
        persisted = json.loads(self.ws.statefile.read_text())
        self.assertEqual(persisted['assets'][-1]['id'], asset['id'])

    def test_workspace_commit_failure_leaves_no_asset_or_published_output(self):
        previous = self.ws.statefile.read_bytes()
        with patch.object(self.ws, 'save', side_effect=OSError('fixture workspace commit failure')):
            job = self.started()
        self.assertEqual(job['status'], 'failed', job)
        self.assertEqual(len(self.ws.data['assets']), 1)
        self.assertEqual(self.ws.statefile.read_bytes(), previous)
        self.assertEqual(list((self.ws.root / 'generated').glob('*')), [])
        self.assertEqual(list((self.ws.root / '.film/enhancements').rglob('derivative.*')), [])

    def test_cancel_queued_and_source_changed_after_queue(self):
        for cancel in (True, False):
            entered, released = threading.Event(), threading.Event()
            @contextmanager
            def gate(cancelled=lambda: False):
                entered.set()
                released.wait(4)
                yield
            with patch.object(self.ws, 'heavy', side_effect=gate), patch.object(self.ws, 'probe', return_value=self.metadata):
                job = self.e.start(self.asset['id'], 'stabilize')
                self.assertTrue(entered.wait(2))
                if cancel:
                    self.e.cancel(job['id'])
                else:
                    self.source.chmod(0o600)
                    self.source.write_bytes(b'externally changed fixture')
                released.set()
                result = self.wait(job['id'])
                self.assertEqual(result['status'], 'cancelled' if cancel else 'failed')
            self.assertEqual(len(self.ws.data['assets']), 1)
            self.assertEqual(list((self.ws.root / 'generated').glob('*')), [])

    def test_running_owned_process_is_cancelled_without_publication(self):
        real_process = self.e._process
        def process(ident, command, **kwargs):
            if '-progress' in command:
                real_process(ident, [sys.executable, '-c', 'import time; time.sleep(20)'], timeout=25)
            return self.fake_process(ident, command, **kwargs)
        with patch.object(self.ws, 'probe', return_value=self.metadata), patch.object(self.e, '_capabilities', return_value=('fixture', {'deshake'})), patch.object(self.e, '_process', side_effect=process):
            job = self.e.start(self.asset['id'], 'stabilize')
            deadline = time.monotonic() + 3
            while job['id'] not in self.ws.processes and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn(job['id'], self.ws.processes)
            child = self.ws.processes[job['id']]
            self.e.cancel(job['id'])
            self.assertEqual(self.wait(job['id'])['status'], 'cancelled')
            self.assertIsNotNone(child.poll())
        self.assertEqual(len(self.ws.data['assets']), 1)
        self.assertEqual(list((self.ws.root / 'generated').glob('*')), [])

    def test_reopen_retains_completed_and_marks_interrupted_jobs(self):
        completed = self.started()
        other = 'a' * 32
        self.ws.jobs[other] = dict(completed, id=other, job_id=other, status='running', stage='processing')
        directory = self.ws.root / '.film/enhancements' / other
        directory.mkdir()
        (directory / 'derivative.mp4').write_bytes(b'owned incomplete result')
        (directory / 'geometry.json').write_text('{}')
        (directory / 'process.log').write_text('retained diagnostic')
        self.e._save()
        with _workspace_scope(self.ws.root) as reopened:
            e = Enhance(reopened)
            self.assertEqual(e.status(completed['id'])['job']['status'], 'completed')
            self.assertTrue(Path(e.status(completed['id'])['job']['asset']['path']).is_file())
            self.assertEqual(e.status(other)['job']['status'], 'cancelled')
            self.assertFalse((directory / 'derivative.mp4').exists())
            self.assertFalse((directory / 'geometry.json').exists())
            self.assertEqual((directory / 'process.log').read_text(), 'retained diagnostic')
        self.ws.jobs[other]['status'] = 'cancelled'

class GeometryTests(unittest.TestCase):
    def test_geometry_rejects_mismatch_missing_end_nan_and_duplicate_times(self):
        good = {'version': 1, 'width': 640, 'height': 360, 'duration': 2., 'points': [{'time': 0., 'x': .5, 'y': .5}, {'time': 2., 'x': .5, 'y': .5}], 'warnings': []}
        variants = []
        for key, value in [('width', 360), ('duration', 2.2), ('points', []), ('version', True)]:
            p = copy.deepcopy(good)
            p[key] = value
            variants.append(p)
        for key, value in [('time', 0.), ('x', float('nan')), ('y', 1.1), ('time', 1.)]:
            p = copy.deepcopy(good)
            p['points'][-1][key] = value
            variants.append(p)
        for p in variants:
            with self.subTest(p=p), self.assertRaises(ValueError):
                _geometry(p, 640, 360, 2.)
        self.assertEqual(_geometry(good, 640, 360, 2.)[0][-1][0], 2.)

    def test_simplification_preserves_turns_and_bounds_complex_paths(self):
        points = [(i / 5, .5 + .45 * math.sin(i * .2), .5) for i in range(1501)]
        cw, ch, knots, error, warnings = _crop_path(points, 1920, 1080, '9:16', 'gentle')
        self.assertLessEqual(len(knots), 64)
        self.assertEqual((knots[0][0], knots[-1][0]), (0, 300))
        self.assertTrue(all(0 <= x <= 1920 - cw and 0 <= y <= 1080 - ch for t, x, y in knots))
        self.assertEqual(any('64 crop points' in warning for warning in warnings), error > max(2., min(cw, ch) * .01))
        self.assertIn('clip(', _expression(knots, 1, 1920 - cw))
        self.assertLessEqual(_expression(knots, 1, 1920 - cw).count('if('), 63)
        # A short isolated turn between evenly-spaced samples must be selected.
        isolated = [(i / 5, .9 if 376 <= i <= 380 else .5, .5) for i in range(1501)]
        _, _, selected, residual, _ = _crop_path(isolated, 1920, 1080, '9:16', 'gentle')
        self.assertTrue(any(74 < t < 78 for t, x, y in selected))
        self.assertLess(residual, 20)

    def test_fast_subject_stays_in_crop_and_source_edges_stay_bounded(self):
        points = [(i / 3, .05 + .9 * i / 9, .5) for i in range(10)]
        cw, ch, knots, _, warnings = _crop_path(points, 1920, 1080, '9:16', 'strong')
        for t, x, y in points:
            a, b = next((a, b) for a, b in zip(knots, knots[1:]) if a[0] <= t <= b[0])
            fraction = (t - a[0]) / (b[0] - a[0])
            offset = a[1] + fraction * (b[1] - a[1])
            self.assertLessEqual(offset, x * 1920)
            self.assertGreaterEqual(offset + cw, x * 1920)
        self.assertTrue(any('faster camera panning' in w for w in warnings))
        for aspect in ('9:16', '1:1', '4:5'):
            cw, ch, knots, _, _ = _crop_path([(0, 0, 0), (2, 1, 1)], 640, 360, aspect, 'strong')
            self.assertTrue(all(0 <= x <= 640 - cw and 0 <= y <= 360 - ch for t, x, y in knots))

    def test_duration_never_guesses_or_accepts_bool(self):
        for value in ('N/A', float('nan'), 0, True, -1, 301):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _duration({'format': {'duration': value}, 'streams': []})

@contextmanager
def _workspace_scope(path):
    workspace = Workspace(path)
    try:
        yield workspace
    finally:
        workspace.close()

if __name__ == '__main__':
    unittest.main()
