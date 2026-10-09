"""Opt-in native UI acceptance for importing and editing short synthetic video clips.

The test creates only temporary 640x360 sources and exercises the native
WKWebView import, Append, Apply Timing, Undo/Redo, effect, title, and transition
controls. It then decodes baseline and edited previews with FFmpeg. It never
opens, reads, or changes user media or projects, and it does not synthesize
timeline pointer gestures.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import math
import statistics
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film.core import ROOT
from film.desktop import Desktop


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def generate_sources(ffmpeg, folder):
    specifications = [
        ('motion-a.mp4', '0x17324d', '0xffc857', 145, 70, 0, False),
        ('motion-b-audio-first.mp4', '0x315740', '0xff7058', 190, 280, 115, True),
        ('motion-c.mp4', '0x49345d', '0x61d7df', 115, 420, 235, False),
    ]
    videos = []
    stream_orders = {}
    for name, background, grid_color, speed, offset, hue, audio_first in specifications:
        destination = folder / name
        picture_only = folder / (Path(name).stem + '-picture-only.mp4')
        # Avoid drawbox's `t` variable (thickness, not timestamp). A moving
        # testsrc2 patch over a fixed background gives deterministic motion.
        filters = (
            f"[0:v]drawgrid=width=120:height=90:thickness=3:color={grid_color}@0.45[bg];"
            f"[1:v]hue=h={hue}:s=1.35[moving];"
            f"[bg][moving]overlay=x='mod(t*{speed}+{offset},480)':y=135:shortest=1,setsar=1[v]"
        )
        subprocess.run([
            ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            f'color=c={background}:s=640x360:r=24:d=3', '-f', 'lavfi', '-i',
            'testsrc2=size=80x90:rate=24:duration=3', '-filter_complex', filters,
            '-map', '[v]', '-t', '3', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1',
            '-pix_fmt', 'yuv420p', '-r', '24', '-field_order', 'progressive',
            '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
            '-color_range', 'tv', '-movflags', '+faststart', str(picture_only)
        ], check=True, capture_output=True)
        # Input 0 is the video-only file and input 1 is the synthetic audio.
        # This intentionally reproduces the real audio-index-0/video-index-1 case.
        maps = ['-map', '1:a:0', '-map', '0:v:0'] if audio_first else ['-map', '0:v:0', '-map', '1:a:0']
        subprocess.run([
            ffmpeg, '-nostdin', '-v', 'error', '-i', str(picture_only), '-f', 'lavfi', '-i',
            'sine=frequency=880:sample_rate=48000:duration=3', *maps, '-t', '3',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '64k', '-threads', '1', '-field_order', 'progressive',
            '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
            '-color_range', 'tv', '-movflags', '+faststart', str(destination)
        ], check=True, capture_output=True)
        videos.append(destination)
        stream_orders[name] = ['audio', 'video'] if audio_first else ['video', 'audio']

    song = folder / 'synthetic-song.wav'
    subprocess.run([
        ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=220:sample_rate=48000', '-t', '3', '-ac', '1',
        '-c:a', 'pcm_s16le', str(song)
    ], check=True, capture_output=True)
    return videos, song, stream_orders


FRAME_WIDTH = 160
FRAME_HEIGHT = 90


def frame_bytes(ffmpeg, path, at):
    result = subprocess.run([
        ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1',
        '-i', str(path), '-ss', f'{at:.4f}', '-frames:v', '1',
        '-vf', f'scale={FRAME_WIDTH}:{FRAME_HEIGHT}:flags=bilinear',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'
    ], check=True, capture_output=True, timeout=45)
    expected = FRAME_WIDTH * FRAME_HEIGHT * 3
    if len(result.stdout) != expected:
        raise AssertionError(f'Expected one 160x90 RGB frame at {at:.3f}s; got {len(result.stdout)} bytes')
    return result.stdout


def pcm_bytes(ffmpeg, path, at):
    result = subprocess.run([
        ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path),
        '-ss', f'{at:.4f}', '-t', '0.18', '-map', '0:a:0', '-vn', '-ac', '1',
        '-ar', '8000', '-f', 's16le', 'pipe:1'
    ], check=True, capture_output=True, timeout=45)
    if len(result.stdout) < 800 or len(result.stdout) % 2:
        raise AssertionError(f'No usable decoded song samples at {at:.3f}s')
    return result.stdout


def mean_absolute_difference(first, second):
    if len(first) != len(second):
        raise ValueError('Frame dimensions do not match.')
    return statistics.fmean(abs(a - b) for a, b in zip(first, second))


def frame_metrics(frame):
    if len(frame) != FRAME_WIDTH * FRAME_HEIGHT * 3:
        raise ValueError('Unexpected RGB frame size.')
    red = frame[0::3]
    green = frame[1::3]
    blue = frame[2::3]
    means = tuple(statistics.fmean(channel) for channel in (red, green, blue))
    luma = statistics.fmean(0.2126 * r + 0.7152 * g + 0.0722 * b
                            for r, g, b in zip(red, green, blue))
    chroma = statistics.fmean((abs(r - (0.2126 * r + 0.7152 * g + 0.0722 * b)) +
                               abs(b - (0.2126 * r + 0.7152 * g + 0.0722 * b))) / 2
                              for r, g, b in zip(red, green, blue))
    return {'mean_rgb': means, 'mean_luma': luma, 'mean_chroma': chroma}


def roi_mean_rgb(frame, x0, y0, x1, y1):
    pixels = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            offset = (y * FRAME_WIDTH + x) * 3
            pixels.extend(frame[offset:offset + 3])
    return statistics.fmean(pixels)


def tone_amplitude(pcm, frequency, sample_rate=8000):
    values = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
    if not values:
        return 0.0
    cosine = sum(value * math.cos(2 * math.pi * frequency * index / sample_rate)
                 for index, value in enumerate(values))
    sine = sum(value * math.sin(2 * math.pi * frequency * index / sample_rate)
               for index, value in enumerate(values))
    return math.sqrt(2) * math.hypot(cosine, sine) / len(values)


def probe_streams(ffprobe, path):
    result = subprocess.run([
        ffprobe, '-v', 'error', '-show_entries',
        'stream=index,codec_type,codec_name,width,height', '-of', 'json', str(path)
    ], check=True, capture_output=True, text=True, timeout=45)
    return json.loads(result.stdout).get('streams', [])


def decoded_frame_count(ffprobe, path):
    result = subprocess.run([
        ffprobe, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)
    ], check=True, capture_output=True, text=True, timeout=60)
    streams = json.loads(result.stdout).get('streams', [])
    if not streams or not streams[0].get('nb_read_frames', '').isdigit():
        raise AssertionError(f'Could not count decoded frames in {path.name}.')
    return int(streams[0]['nb_read_frames'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'build/FilmEditor.app')
    args = parser.parse_args()
    report_dir = ROOT / 'reports' / 'rebuild' / 'native'
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / 'multiclip-acceptance.json'
    report = {
        'evidence': 'synthetic media; native WKWebView, real MLT and FFmpeg',
        'synthetic_only': True,
        'checks': {},
        'passed': False,
    }
    failure = None

    with tempfile.TemporaryDirectory(prefix='filmeditor-multiclip-') as temporary:
        root = Path(temporary)
        workspace_path = root / 'workspace'
        desktop = Desktop(workspace_path)
        process = None
        try:
            entry = desktop.owner.library.create(
                'Synthetic multi-clip acceptance', width=640, height=360,
                fps_num=24, fps_den=1, auto_configure=True,
            )
            session = desktop.owner.library.session(entry['id'])
            ffmpeg = session.workspace.executable('ffmpeg')
            ffprobe = session.workspace.executable('ffprobe')
            videos, song, expected_orders = generate_sources(ffmpeg, root)
            source_stream_orders = {}
            source_metadata = {}
            source_frame_counts = {}
            for path in videos:
                streams = probe_streams(ffprobe, path)
                order = [stream.get('codec_type') for stream in streams]
                if order != expected_orders[path.name]:
                    raise AssertionError(f'{path.name} stream order is {order}, expected {expected_orders[path.name]}.')
                video_stream = next((stream for stream in streams if stream.get('codec_type') == 'video'), None)
                if not video_stream or (video_stream.get('width'), video_stream.get('height')) != (640, 360):
                    raise AssertionError(f'{path.name} is not 640x360 video: {streams}')
                source_stream_orders[path.name] = order
                source_metadata[path.name] = streams
                count = decoded_frame_count(ffprobe, path)
                if count != 72:
                    raise AssertionError(f'{path.name} has {count} frames; expected exactly 72 at 24 fps for 3 seconds.')
                source_frame_counts[path.name] = count
            originals = videos + [song]
            source_hashes = {path.name: checksum(path) for path in originals}
            fixtures = [
                {'name': path.name, 'type': 'video/mp4', 'kind': 'footage',
                 'base64': base64.b64encode(path.read_bytes()).decode('ascii')}
                for path in videos
            ]
            fixtures.append({
                'name': song.name, 'type': 'audio/wav', 'kind': 'audio',
                'base64': base64.b64encode(song.read_bytes()).decode('ascii'),
            })
            smoke_template = (Path(__file__).with_name('native_multiclip_smoke.js')).read_text(encoding='utf-8')
            smoke_script = root / 'native_multiclip_smoke.injected.js'
            smoke_script.write_text(
                smoke_template.replace('__MULTICLIP_FIXTURES__', json.dumps(fixtures, separators=(',', ':'))),
                encoding='utf-8',
            )
            project = entry['id']
            report['source_stream_orders'] = source_stream_orders
            report['source_metadata'] = source_metadata
            report['source_frame_counts'] = source_frame_counts
            report['checks']['audio-first control contains audio index 0 and video index 1'] = (
                source_stream_orders['motion-b-audio-first.mp4'] == ['audio', 'video']
            )
            report['checks']['other synthetic clips keep video index 0'] = all(
                source_stream_orders[path.name][0] == 'video' for path in (videos[0], videos[2])
            )
        finally:
            desktop.close()

        smoke_report = root / 'smoke.json'
        app_executable = args.app.expanduser().resolve() / 'Contents' / 'MacOS' / 'FilmEditor'
        if not app_executable.is_file():
            failure = FileNotFoundError(f'FilmEditor executable not found: {app_executable}')
        else:
            log_path = root / 'native-app.log'
            with log_path.open('wb') as app_log:
                try:
                    process = subprocess.Popen([
                        str(app_executable), '--workspace', str(workspace_path), '--project', project,
                        '--smoke-script', str(smoke_script), '--smoke-report', str(smoke_report), '--smoke-exit',
                    ], stdout=subprocess.DEVNULL, stderr=app_log)
                    deadline = time.monotonic() + 300
                    while time.monotonic() < deadline and process.poll() is None and not smoke_report.exists():
                        time.sleep(0.2)
                    if not smoke_report.exists():
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=10)
                        app_log.flush()
                        log_tail = log_path.read_text(encoding='utf-8', errors='replace')[-2000:]
                        raise RuntimeError('Native multi-clip UI did not report completion: ' + log_tail)
                    process.wait(timeout=30)
                    smoke = json.loads(smoke_report.read_text(encoding='utf-8'))
                    report['native_ui'] = smoke
                    if not smoke.get('passed'):
                        raise AssertionError('Native UI acceptance failed: ' + json.dumps(smoke, indent=2))
                    if process.returncode != 0:
                        raise RuntimeError(f'Native FilmEditor exited with status {process.returncode}')
                except Exception as error:
                    failure = error
                finally:
                    if process and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=10)

        if failure is None:
            try:
                project_root = workspace_path / 'projects' / project
                cache = project_root / 'cache'
                native_ui = report['native_ui']

                def preview_path(record):
                    name = record.get('previewFile')
                    if not name or Path(name).name != name:
                        raise AssertionError(f'Native UI reported an unsafe preview filename: {name!r}')
                    path = cache / name
                    if not path.is_file():
                        raise AssertionError(f'Native UI preview does not exist: {name}')
                    return path

                baseline = preview_path(native_ui['baseline'])
                preview = preview_path(native_ui['preview'])
                for path in (baseline, preview):
                    subprocess.run([
                        ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
                        '-i', str(path), '-f', 'null', '-'
                    ], check=True, capture_output=True, timeout=120)
                streams = probe_streams(ffprobe, preview)
                video_stream = next((stream for stream in streams if stream.get('codec_type') == 'video'), None)
                audio_stream = next((stream for stream in streams if stream.get('codec_type') == 'audio'), None)
                if not video_stream or not audio_stream:
                    raise AssertionError('Rendered preview must contain both video and the imported song.')
                if video_stream.get('width') != 640 or video_stream.get('height') != 360:
                    raise AssertionError(f'Unexpected preview dimensions: {video_stream}')

                expected_baseline_frames = native_ui['baseline']['timelineFrames']
                expected_final_frames = native_ui['preview']['timelineFrames']
                baseline_count = decoded_frame_count(ffprobe, baseline)
                final_count = decoded_frame_count(ffprobe, preview)
                if baseline_count != expected_baseline_frames:
                    raise AssertionError(f'Baseline render has {baseline_count} frames; expected exactly {expected_baseline_frames}.')
                if final_count != expected_final_frames:
                    raise AssertionError(f'Final render has {final_count} frames; expected exactly {expected_final_frames}.')
                # Use the last frame's PTS; sampling half a frame later can be
                # outside the encoded video stream even when its frame count is exact.
                final_tail = frame_bytes(ffmpeg, preview, (final_count - 1) / 24)
                tail_metrics = frame_metrics(final_tail)
                if tail_metrics['mean_luma'] < 12:
                    raise AssertionError('The final rendered frame is black.')

                source_samples = []
                for path in videos:
                    before = frame_bytes(ffmpeg, path, 0.5)
                    after = frame_bytes(ffmpeg, path, 2.5)
                    metrics = (frame_metrics(before), frame_metrics(after))
                    movement = mean_absolute_difference(before, after)
                    if movement < 1.0 or min(value['mean_luma'] for value in metrics) < 12:
                        raise AssertionError(f'Synthetic source is not visibly moving: {path.name}')
                    source_samples.append({'name': path.name, 'mean_rgb': [round(value, 1) for value in metrics[1]['mean_rgb']],
                                           'mean_luma': round(metrics[1]['mean_luma'], 1),
                                           'motion_mad': round(movement, 2)})
                source_colors = [sample['mean_rgb'] for sample in source_samples]
                if any(max(abs(a - b) for a, b in zip(source_colors[left], source_colors[right])) < 8
                       for left in range(len(source_colors)) for right in range(left + 1, len(source_colors))):
                    raise AssertionError('The three source fixtures are not visually distinct enough.')

                clips = report['native_ui']['clips']
                baseline_clips = native_ui['baseline']['clips']
                if len(clips) != 3 or len(baseline_clips) != 3:
                    raise AssertionError(f'Expected three V1 clips in each render; got final={len(clips)}, baseline={len(baseline_clips)}')
                render_samples = []
                final_by_asset = {item['asset_id']: item for item in clips}
                baseline_by_asset = {item['asset_id']: item for item in baseline_clips}
                for index, item in enumerate(clips):
                    duration_frames = item['duration_frames']
                    first_offset = duration_frames // 4
                    second_offset = (duration_frames * 3) // 4
                    first = frame_bytes(ffmpeg, preview,
                                        (item['start_frame'] + first_offset + 0.5) / 24)
                    second = frame_bytes(ffmpeg, preview,
                                         (item['start_frame'] + second_offset + 0.5) / 24)
                    metrics = (frame_metrics(first), frame_metrics(second))
                    movement = mean_absolute_difference(first, second)
                    if movement < 1.0 or min(value['mean_luma'] for value in metrics) < 12:
                        raise AssertionError(f'Rendered clip {index + 1} is frozen or black.')
                    render_samples.append({'asset_id': item['asset_id'],
                                           'mean_luma': [round(value['mean_luma'], 1) for value in metrics],
                                           'mean_rgb': [[round(channel, 1) for channel in value['mean_rgb']]
                                                        for value in metrics],
                                           'motion_mad': round(movement, 2)})

                color_asset = baseline_clips[0]['asset_id']
                color_before, color_after = baseline_by_asset[color_asset], final_by_asset[color_asset]
                color_offset = color_after['duration_frames'] // 2
                color_before_frame = frame_bytes(ffmpeg, baseline,
                    (color_before['start_frame'] + color_offset + 0.5) / 24)
                color_after_frame = frame_bytes(ffmpeg, preview,
                    (color_after['start_frame'] + color_offset + 0.5) / 24)
                color_before_metrics = frame_metrics(color_before_frame)
                color_after_metrics = frame_metrics(color_after_frame)
                luma_ratio = color_after_metrics['mean_luma'] / max(1.0, color_before_metrics['mean_luma'])
                chroma_ratio = color_after_metrics['mean_chroma'] / max(1.0, color_before_metrics['mean_chroma'])
                if luma_ratio < 1.06 or chroma_ratio < 1.05:
                    raise AssertionError(f'Brightness/saturation did not produce the expected pixel change: luma={luma_ratio:.3f}, chroma={chroma_ratio:.3f}.')

                transform_asset = baseline_clips[2]['asset_id']
                transform_before, transform_after = baseline_by_asset[transform_asset], final_by_asset[transform_asset]
                transform_offset = transform_after['duration_frames'] // 2
                transform_before_frame = frame_bytes(ffmpeg, baseline,
                    (transform_before['start_frame'] + transform_offset + 0.5) / 24)
                transform_after_frame = frame_bytes(ffmpeg, preview,
                    (transform_after['start_frame'] + transform_offset + 0.5) / 24)
                # The test applies 60% scale around (40%, 40%); this fixed lower-right
                # ROI is outside the transformed picture and should become the MLT black canvas.
                roi_before = roi_mean_rgb(transform_before_frame, 145, 78, 159, 89)
                roi_after = roi_mean_rgb(transform_after_frame, 145, 78, 159, 89)
                if roi_before < 20 or roi_after > min(16, roi_before * 0.45):
                    raise AssertionError(f'Position/scale did not change the expected corner ROI: {roi_before:.1f} -> {roi_after:.1f}.')

                baseline_song = native_ui['baseline']['songClips']
                final_song = native_ui['songClips']
                if len(baseline_song) != 2 or len(final_song) != 2:
                    raise AssertionError('Expected two song segments for a same-source volume comparison.')
                base_pcm = pcm_bytes(ffmpeg, baseline, 0.6)
                final_unmodified_pcm = pcm_bytes(ffmpeg, preview, 0.6)
                final_adjusted_pcm = pcm_bytes(ffmpeg, preview, 2.1)
                base_amplitude = tone_amplitude(base_pcm, 220)
                unmodified_amplitude = tone_amplitude(final_unmodified_pcm, 220)
                adjusted_amplitude = tone_amplitude(final_adjusted_pcm, 220)
                unmodified_ratio = unmodified_amplitude / max(1.0, base_amplitude)
                volume_ratio = adjusted_amplitude / max(1.0, base_amplitude)
                if not 0.80 <= unmodified_ratio <= 1.20:
                    raise AssertionError(f'Unmodified song segment changed unexpectedly: amplitude ratio {unmodified_ratio:.3f}.')
                if not 0.38 <= volume_ratio <= 0.82:
                    raise AssertionError(f'Minus-six-decibel song effect was not audible in decoded PCM: amplitude ratio {volume_ratio:.3f}.')
                report.update(
                    checks={
                        **report.get('checks', {}),
                        'baseline and final previews fully decode': True,
                        'preview retains 640x360 video and song audio streams': True,
                        'audio-first source has audio stream 0 and video stream 1': True,
                        'all three sources are distinct moving pictures': True,
                        'each rendered clip contains meaningfully changing nonblack frames': True,
                        'final preview has the exact timeline frame count and no black tail': True,
                        'brightness and saturation change decoded luma and chroma': True,
                        'position and scale change the decoded corner ROI': True,
                        'minus-six-decibel song volume is audible in decoded PCM': True,
                    },
                    source_samples=source_samples,
                    rendered_samples=render_samples,
                    decoded_frames={'baseline': baseline_count, 'final': final_count,
                                    'expected_baseline': expected_baseline_frames,
                                    'expected_final': expected_final_frames,
                                    'final_tail_mean_luma': round(tail_metrics['mean_luma'], 1)},
                    effect_metrics={
                        'color': {'baseline_mean_luma': round(color_before_metrics['mean_luma'], 2),
                                  'final_mean_luma': round(color_after_metrics['mean_luma'], 2),
                                  'luma_ratio': round(luma_ratio, 3),
                                  'baseline_mean_chroma': round(color_before_metrics['mean_chroma'], 2),
                                  'final_mean_chroma': round(color_after_metrics['mean_chroma'], 2),
                                  'chroma_ratio': round(chroma_ratio, 3)},
                        'position_scale_roi': {'baseline_mean_rgb': round(roi_before, 2),
                                               'final_mean_rgb': round(roi_after, 2)},
                        'song_volume': {'baseline_220hz_amplitude': round(base_amplitude, 2),
                                        'final_unmodified_220hz_amplitude': round(unmodified_amplitude, 2),
                                        'final_minus6db_220hz_amplitude': round(adjusted_amplitude, 2),
                                        'unmodified_ratio': round(unmodified_ratio, 3),
                                        'minus6db_ratio': round(volume_ratio, 3)},
                    },
                    preview_streams=[{key: stream.get(key) for key in
                                      ('codec_type', 'codec_name', 'width', 'height', 'sample_rate', 'channels')}
                                     for stream in streams],
                    sources_unchanged=all(checksum(path) == source_hashes[path.name] for path in originals),
                    temporary_media_removed=True,
                )
                if not report['sources_unchanged']:
                    raise AssertionError('A synthetic source changed during the native UI test.')
                report['checks']['synthetic inputs remain untouched'] = True

                verifier = Desktop(workspace_path)
                try:
                    reopened = verifier.owner.library.session(project).editor.snapshot()
                    saved_video = next(track for track in reopened['tracks'] if track['name'] == 'V1')
                    saved_audio = next(track for track in reopened['tracks'] if track['name'] == 'A1')
                    saved_titles = next(track for track in reopened['tracks'] if track['name'] == 'Titles')
                    saved_videos = [item for item in saved_video['items'] if item['type'] == 'clip']
                    saved_songs = [item for item in saved_audio['items'] if item['type'] == 'clip']
                    saved_title_clips = [item for item in saved_titles['items'] if item['type'] == 'clip']
                    if len(saved_videos) != 3 or len(saved_songs) != 2 or not saved_title_clips:
                        raise AssertionError('Saved MLT project did not retain the full multi-clip timeline.')
                    if report['native_ui']['checks'].get('advanced UI adds an MLT cross dissolve'):
                        if not any(item['type'] == 'transition' for item in saved_video['items']):
                            raise AssertionError('Saved MLT project lost the tested dissolve.')
                    if not any(f.get('properties', {}).get('shotcut:mcpTransform') == '1'
                               for item in saved_videos for f in item.get('filters', [])):
                        raise AssertionError('Saved MLT project lost the position/scale effect.')
                    if not any(f.get('properties', {}).get('shotcut:mcpVolume') == '1'
                               for item in saved_songs for f in item.get('filters', [])):
                        raise AssertionError('Saved MLT project lost the song volume effect.')
                    if not any(f.get('properties', {}).get('film:brightness') == '1'
                               for item in saved_videos for f in item.get('filters', [])):
                        raise AssertionError('Saved MLT project lost the brightness effect.')
                    if not any(f.get('properties', {}).get('film:saturation') == '1'
                               for item in saved_videos for f in item.get('filters', [])):
                        raise AssertionError('Saved MLT project lost the saturation effect.')
                    report['checks']['multi-clip edits, title and effects persist in the saved MLT project'] = True
                    report['persisted_timeline'] = {
                        'video_clips': len(saved_videos), 'song_clips': len(saved_songs),
                        'title_clips': len(saved_title_clips),
                        'transitions': sum(item['type'] == 'transition' for item in saved_video['items']),
                    }
                finally:
                    verifier.close()

                if list(workspace_path.rglob('connection.json')) or list(workspace_path.rglob('native.sock')):
                    raise AssertionError('A FilmEditor backend connection file or socket remained after shutdown.')
                report['checks']['temporary FilmEditor backend shut down cleanly'] = True
                report['passed'] = True
            except Exception as error:
                failure = error

    if failure is not None:
        report['passed'] = False
        report['error'] = str(failure)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if failure is not None:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
