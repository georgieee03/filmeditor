"""Bounded installed-FFmpeg enhancement efficacy; synthetic media only.

Run with the configured FILM_PYTHON, --output-directory outside this repository.
Retains fixtures, derivatives, logs and a technical report, including on failure.
"""
import argparse
from array import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film.core import Workspace, ROOT
from film.enhance import Enhance

WIDTH, HEIGHT, FPS, SECONDS, RATE = 320, 180, 30, 5, 48000

def run(command, input_bytes=None):
    child = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if child.returncode:
        raise AssertionError(child.stderr.decode(errors='replace')[-4000:])
    return child.stdout

def create_video(ffmpeg, path):
    randomizer = random.Random(441)
    # A static, textured world and independently moving bright target. Known
    # camera jitter moves the whole world; target movement must survive deshake.
    cells = [[35 + randomizer.randrange(190) for _ in range(WIDTH // 7 + 1)] for _ in range(HEIGHT // 7 + 1)]
    background = bytes(cells[y // 7][x // 7] for y in range(HEIGHT) for x in range(WIDTH))
    frames = bytearray()
    for index in range(FPS * SECONDS):
        dx = round(8 * math.sin(index * .85) + 3 * math.sin(index * .23))
        dy = round(5 * math.sin(index * .71))
        frame = bytearray(WIDTH * HEIGHT)
        target_x = 30 + index * (WIDTH - 60) // (FPS * SECONDS)
        for y in range(HEIGHT):
            sy = max(0, min(HEIGHT - 1, y - dy))
            for x in range(WIDTH):
                sx = max(0, min(WIDTH - 1, x - dx))
                value = background[sy * WIDTH + sx]
                if target_x <= sx < target_x + 12 and 80 <= sy < 94:
                    value = 250
                frame[y * WIDTH + x] = value
        frames.extend(frame)
    run([ffmpeg, '-nostdin', '-v', 'error', '-n', '-threads', '1', '-f', 'rawvideo', '-pixel_format', 'gray',
         '-video_size', f'{WIDTH}x{HEIGHT}', '-framerate', str(FPS), '-i', 'pipe:0', '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000:duration=5',
         '-map', '1:a:0', '-map', '0:v:0', '-c:a', 'aac', '-vf', 'setsar=1', '-c:v', 'libx264', '-preset', 'fast', '-crf', '14', '-pix_fmt', 'yuv420p', '-threads', '1',
         '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv', str(path)], bytes(frames))

def create_audio(path, impulse=False, rate=RATE):
    randomizer = random.Random(991)
    pcm = array('h')
    for index in range(rate * SECONDS):
        t = index / rate
        if impulse:
            left = .7 if index == rate * 2 else .65 if index == rate * SECONDS - 10 else 0.
            right = -.6 if index == rate * 2 else -.55 if index == rate * SECONDS - 10 else 0.
        else:
            noise = randomizer.uniform(-.025, .025)
            rumble = .035 * math.sin(2 * math.pi * 40 * t)
            left = noise + rumble + (.16 * math.sin(2 * math.pi * 880 * t) if 1 <= t < 4 else 0)
            right = noise * .8 + rumble * .7 + (.13 * math.sin(2 * math.pi * 660 * t) if 1 <= t < 4 else 0)
        pcm.extend((round(left * 32767), round(right * 32767)))
    if sys.byteorder != 'little':
        pcm.byteswap()
    with wave.open(str(path), 'wb') as f:
        f.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        f.writeframes(pcm.tobytes())

def import_asset(workspace, path, kind):
    with path.open('rb') as stream:
        return workspace.ingest(stream, path.stat().st_size, path.name, kind)

def wait(enhance, job):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        job = enhance.status(job['id'])['job']
        if job['status'] in ('completed', 'failed', 'cancelled'):
            if job['status'] != 'completed':
                raise AssertionError(job)
            return job
        time.sleep(.1)
    raise AssertionError('Enhancement did not finish within the bounded acceptance time')

def decode_gray(ffmpeg, path, metric_blur=False):
    filters = ['-vf', 'gblur=sigma=2'] if metric_blur else []
    data = run([ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1', '-i', str(path), '-map', '0:v:0', *filters, '-pix_fmt', 'gray', '-threads', '1', '-f', 'rawvideo', 'pipe:1'])
    size = WIDTH * HEIGHT
    assert len(data) % size == 0
    return [data[start:start + size] for start in range(0, len(data), size)]

def jitter(frames):
    reference = frames[0]
    sample_positions = [y * WIDTH + x for y in range(30, HEIGHT - 30, 15) for x in range(30, WIDTH - 30, 20) if not 65 <= y <= 110]
    reference_values = [reference[index] for index in sample_positions]
    offsets = []
    gaps = []
    for frame in frames[9::3]:
        best = None
        second_score = float('inf')
        for dy in range(-14, 15):
            for dx in range(-14, 15):
                delta = dy * WIDTH + dx
                score = sum(abs(frame[index + delta] - value) for index, value in zip(sample_positions, reference_values))
                if best is None or score < best[0]:
                    if best is not None:
                        second_score = best[0]
                    best = score, dx, dy
                elif score < second_score:
                    second_score = score
        offsets.append((best[1], best[2]))
        gaps.append(second_score - best[0])
    centered = math.sqrt(statistics.pvariance([p[0] for p in offsets]) + statistics.pvariance([p[1] for p in offsets]))
    step = math.sqrt(statistics.mean((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 for a, b in zip(offsets, offsets[1:])))
    return {'position_rms_pixels': centered, 'sample_step_rms_pixels': step, 'sampled_frames': len(offsets), 'offsets': offsets,
            'median_match_score_gap': statistics.median(gaps), 'search_boundary_optima': sum(abs(x) == 14 or abs(y) == 14 for x, y in offsets)}

def target_span(frames, offsets):
    locations = []
    for frame, shift in zip(frames[9::3], offsets):
        pixels = [index % WIDTH for index, value in enumerate(frame) if value >= 238]
        assert len(pixels) >= 30, 'Synthetic moving target is no longer identifiable'
        locations.append(statistics.mean(pixels) - shift[0])
    return max(locations) - min(locations)

def decode_pcm(ffmpeg, path):
    data = run([ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-map', '0:a:0', '-c:a', 'pcm_f64le', '-threads', '1', '-f', 'f64le', 'pipe:1'])
    pcm = array('d')
    pcm.frombytes(data)
    if sys.byteorder != 'little':
        pcm.byteswap()
    return pcm

def rms(pcm, start, end, channel=0):
    values = pcm[round(start * RATE) * 2 + channel:round(end * RATE) * 2:2]
    return math.sqrt(sum(value * value for value in values) / len(values))

def tone(pcm, frequency, channel):
    start, end = int(1.5 * RATE), int(3.5 * RATE)
    real = sum(pcm[index * 2 + channel] * math.cos(2 * math.pi * frequency * index / RATE) for index in range(start, end))
    imaginary = sum(pcm[index * 2 + channel] * math.sin(2 * math.pi * frequency * index / RATE) for index in range(start, end))
    return 2 * math.hypot(real, imaginary) / (end - start)

def main(directory):
    directory = directory.expanduser().resolve()
    assert directory != ROOT and ROOT not in directory.parents, 'Keep fixtures outside source checkout'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = Workspace(directory / 'workspace')
    report = {'evidence': 'installed FFmpeg, synthetic fixtures; no user-footage/long-film or perceptual listening claim', 'checks': {}}
    try:
        ffmpeg = workspace.executable('ffmpeg')
        report['ffmpeg'] = run([ffmpeg, '-version']).decode().splitlines()[0]
        video_path, audio_path, impulse_path = directory / 'jitter-audio-first.mp4', directory / 'tone-hiss-rumble.wav', directory / 'impulse.wav'
        create_video(ffmpeg, video_path)
        create_audio(audio_path)
        create_audio(impulse_path, impulse=True)
        video = import_asset(workspace, video_path, 'footage')
        audio = import_asset(workspace, audio_path, 'audio')
        impulse = import_asset(workspace, impulse_path, 'audio')
        originals = {asset['id']: (asset['sha256'], Path(asset['path']).read_bytes()) for asset in (video, audio, impulse)}
        metadata = workspace.probe(video['id'])
        assert metadata['streams'][0]['codec_type'] == 'audio' and metadata['streams'][1]['codec_type'] == 'video'
        enhance = Enhance(workspace)
        stabilized = wait(enhance, enhance.start(video['id'], 'stabilize', strength='standard'))
        before_frames = decode_gray(ffmpeg, video['path'])
        after_frames = decode_gray(ffmpeg, stabilized['asset']['path'])
        before, after = jitter(decode_gray(ffmpeg, video['path'], metric_blur=True)), jitter(decode_gray(ffmpeg, stabilized['asset']['path'], metric_blur=True))
        known = [(round(8 * math.sin(i * .85) + 3 * math.sin(i * .23)), round(5 * math.sin(i * .71))) for i in range(9, FPS * SECONDS, 3)]
        known_error = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(known, before['offsets']))
        moving_spans = {'before': target_span(before_frames, before['offsets']), 'after': target_span(after_frames, after['offsets'])}
        report['stabilization'] = {'before': before, 'after': after, 'job_id': stabilized['id'], 'method': stabilized['provenance']['method'],
                                   'measurement': 'Known static 7px-cell textured world; 2px Gaussian-prefiltered background translation registration avoids a single-pixel/subpixel resampling oracle',
                                   'source_measurement_max_error_pixels': known_error, 'target_relative_x_span_pixels': moving_spans}
        assert known_error <= 1. and before['search_boundary_optima'] == after['search_boundary_optima'] == 0
        assert before['median_match_score_gap'] > 0 and after['median_match_score_gap'] > 0
        assert moving_spans['before'] > 200 and moving_spans['after'] > 200
        assert after['position_rms_pixels'] < before['position_rms_pixels'] * .75, report['stabilization']
        assert after['sample_step_rms_pixels'] < before['sample_step_rms_pixels'] * .75, report['stabilization']
        assert len(before_frames) == len(after_frames) == FPS * SECONDS
        assert len({hashlib.sha256(frame).hexdigest() for frame in after_frames}) > len(after_frames) * .9, 'Moving target was frozen'
        assert not any(all(value <= 16 for value in frame[:WIDTH]) for frame in after_frames), 'A full-width black top strip appeared'
        report['checks']['stabilization_reduces_known_jitter_keeps_motion_and_frames'] = True
        for strength in ('gentle', 'strong'):
            preset = wait(enhance, enhance.start(video['id'], 'stabilize', strength=strength))
            assert preset['status'] == 'completed'
        report['checks']['all_stabilization_presets_use_supported_search_radius'] = True
        cleaned = wait(enhance, enhance.start(audio['id'], 'audio_cleanup', strength='standard'))
        source_pcm, cleaned_pcm = decode_pcm(ffmpeg, audio['path']), decode_pcm(ffmpeg, cleaned['asset']['path'])
        source_noise, clean_noise = rms(source_pcm, 4.25, 4.9), rms(cleaned_pcm, 4.25, 4.9)
        reduction = 20 * math.log10(source_noise / clean_noise)
        source_tones = [tone(source_pcm, 880, 0), tone(source_pcm, 660, 1)]
        clean_tones = [tone(cleaned_pcm, 880, 0), tone(cleaned_pcm, 660, 1)]
        report['audio_cleanup'] = {'noise_rms_before': source_noise, 'noise_rms_after': clean_noise, 'noise_reduction_db': reduction,
                                   'tone_amplitudes_before': source_tones, 'tone_amplitudes_after': clean_tones,
                                   'decoded_samples_before': len(source_pcm) // 2, 'decoded_samples_after': len(cleaned_pcm) // 2,
                                   'max_output_peak': max(abs(v) for v in cleaned_pcm), 'job_id': cleaned['id'], 'method': cleaned['provenance']['method']}
        assert reduction >= 3., report['audio_cleanup']
        assert all(after > before * .45 for before, after in zip(source_tones, clean_tones)), report['audio_cleanup']
        assert len(source_pcm) == len(cleaned_pcm) == RATE * SECONDS * 2, report['audio_cleanup']
        assert max(abs(value) for value in cleaned_pcm) <= .892, report['audio_cleanup']
        report['checks']['cleanup_reduces_rumble_hiss_retains_tones_channels_samples_limits_peak'] = True
        impulse_result = wait(enhance, enhance.start(impulse['id'], 'audio_cleanup', strength='gentle'))
        input_impulse, output_impulse = decode_pcm(ffmpeg, impulse['path']), decode_pcm(ffmpeg, impulse_result['asset']['path'])
        def peak_index(values):
            return max(range(len(values) // 2), key=lambda index: abs(values[index * 2]))
        drift = peak_index(output_impulse) - peak_index(input_impulse)
        tail_peak = max(abs(v) for v in output_impulse[-128::2])
        report['audio_alignment'] = {'impulse_drift_samples': drift, 'decoded_samples_before': len(input_impulse) // 2, 'decoded_samples_after': len(output_impulse) // 2,
                                     'compensated_delay_samples': impulse_result['provenance']['compensated_delay_samples'], 'tail_impulse_peak': tail_peak}
        assert abs(drift) <= 2 and len(input_impulse) == len(output_impulse), report['audio_alignment']
        assert tail_peak > .1, report['audio_alignment']
        impulse_44100_path = directory / 'impulse-44100.wav'
        create_audio(impulse_44100_path, impulse=True, rate=44100)
        impulse_44100 = import_asset(workspace, impulse_44100_path, 'audio')
        result_44100 = wait(enhance, enhance.start(impulse_44100['id'], 'audio_cleanup', strength='gentle'))
        pcm_44100 = decode_pcm(ffmpeg, result_44100['asset']['path'])
        drift_44100 = peak_index(pcm_44100) - 44100 * 2
        tail_44100 = max(abs(v) for v in pcm_44100[-128::2])
        report['audio_alignment_44100'] = {'impulse_drift_samples': drift_44100, 'decoded_samples_after': len(pcm_44100) // 2,
                                           'compensated_delay_samples': result_44100['provenance']['compensated_delay_samples'], 'tail_impulse_peak': tail_44100}
        assert abs(drift_44100) <= 2 and len(pcm_44100) == 44100 * SECONDS * 2 and tail_44100 > .1, report['audio_alignment_44100']
        report['checks']['cleanup_compensates_limiter_delay'] = True
        # Actual video cleanup: audio-first source selection plus video stream copy.
        video_clean = wait(enhance, enhance.start(video['id'], 'audio_cleanup', strength='gentle'))
        video_clean_frames = decode_gray(ffmpeg, video_clean['asset']['path'])
        assert [hashlib.sha256(f).digest() for f in video_clean_frames] == [hashlib.sha256(f).digest() for f in before_frames]
        report['checks']['video_cleanup_preserves_all_decoded_video_frames_audio_first_source'] = True
        for ident, (digest, contents) in originals.items():
            source = Path(workspace.asset(ident)['path'])
            assert source.read_bytes() == contents and hashlib.sha256(contents).hexdigest() == digest
        assert workspace.data['active_project'] is None
        assert all(j['status'] == 'completed' for j in enhance.status()['jobs'])
        assert not list((workspace.root / '.film/enhancements').rglob('derivative.*'))
        report['checks']['originals_unchanged_no_timeline_mutation_no_unfinished_media'] = True
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False, error=str(exc))
        raise
    finally:
        workspace.close()
        (directory / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    main(parser.parse_args().output_directory)
