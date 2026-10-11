"""Create bounded, timecoded review sheets and per-shot motion measurements.

Requires Pillow and NumPy in an analysis Python (not the app runtime). Run only
after the native workflow has stopped rendering. This prepares evidence for a
human/agent visual review; it does not decide whether an edit looks good.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--movie', type=Path, required=True)
    parser.add_argument('--native-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    native = json.loads(args.native_report.read_text())
    profile = native['final_snapshot']['profile']
    fps = float(profile['frame_rate_num']) / float(profile['frame_rate_den'])
    cuts = native['music_edit']['cuts']
    duration = native['music_edit']['duration_seconds']
    expected_frames = int(native['final_snapshot']['duration_frames'])
    if not 0 < duration <= 60 or not 1 <= len(cuts) <= 60:
        parser.error('This review is bounded to 60 seconds and 60 picture segments')
    args.output.mkdir(parents=True, exist_ok=True)

    def run(extra):
        value = subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-threads', '1',
                                *extra], capture_output=True, timeout=180)
        if value.returncode:
            raise RuntimeError(value.stderr.decode(errors='replace')[-2000:])
        return value.stdout

    # Decode the entire short output once: motion requires changed pictures,
    # not just a advancing media clock or a nonblack still frame.
    raw = run(['-i', str(args.movie), '-an', '-vf', 'scale=54:96', '-pix_fmt', 'gray',
               '-frames:v', str(expected_frames+1), '-t', str(duration+1/fps),
               '-f', 'rawvideo', 'pipe:1'])
    frame_bytes = 54 * 96
    if len(raw) % frame_bytes:
        raise ValueError('Incomplete decoded frame')
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 54)
    if len(frames) != expected_frames:
        raise ValueError('Actual decoded frame count differs from the reviewed timeline')
    motion = []
    for item in cuts:
        start, length = item['start_frame'], item['duration_frames']
        first = start + min(max(1, round(.15 * fps)), max(0, length // 4))
        last = start + length - 1 - min(round(.15 * fps), max(0, length // 4))
        if not 0 <= first < last < len(frames):
            raise ValueError('Shot interval is outside actual decoded export')
        mad = float(np.abs(frames[first].astype(float) - frames[last]).mean())
        motion.append({'start_frame': start, 'sample_frames': [first, last],
                       'mean_absolute_pixel_change': mad})

    moments = {0: 'Opening', len(frames)-1: 'Last frame'}
    for index, item in enumerate(cuts):
        start, length = item['start_frame'], item['duration_frames']
        moments[start + length // 2] = f'Shot {index + 1} interior'
        if index:
            moments[start - 1] = f'Cut {index}: before'
            moments[start] = f'Cut {index}: after'
    images = []
    ordered = sorted(moments.items())
    # Select decoded frame numbers in one pass; timestamp seeking can land on
    # a neighboring frame, which is inadequate for reviewing cut boundaries.
    with tempfile.TemporaryDirectory(prefix='review-frames-', dir=args.output) as temporary:
        selected = '+'.join(f'eq(n,{frame})' for frame, _ in ordered)
        run(['-i', str(args.movie), '-an', '-vf',
             f"select='{selected}',scale=240:426:force_original_aspect_ratio=decrease",
             '-t', str(duration), '-frames:v', str(len(ordered)),
             '-fps_mode', 'vfr', '-threads', '1', str(Path(temporary)/'frame-%04d.png')])
        pictures = sorted(Path(temporary).glob('frame-*.png'))
        if len(pictures) != len(ordered):
            raise ValueError('Not every requested decoded frame was extracted')
        for path, (frame, label) in zip(pictures, ordered):
            with Image.open(path) as value:
                picture = value.convert('RGB')
            tile = Image.new('RGB', (260, 464), '#17191c')
            tile.paste(picture, ((260-picture.width)//2, 0))
            ImageDraw.Draw(tile).text((8, 430), f'{frame/fps:.3f}s | frame {frame}\n{label}', fill='white')
            images.append(tile)
    sheets = []
    for offset in range(0, len(images), 12):
        group = images[offset:offset+12]
        sheet = Image.new('RGB', (1040, 464*((len(group)+3)//4)), '#17191c')
        for index, tile in enumerate(group):
            sheet.paste(tile, ((index % 4)*260, (index//4)*464))
        path = args.output / f'visual-review-{offset//12+1}.jpg'
        sheet.save(path, quality=91)
        sheets.append(str(path.resolve()))
    with args.movie.open('rb') as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(chunk)
        checksum = hasher.hexdigest()
    result = {'movie': str(args.movie.resolve()), 'movie_sha256': checksum,
              'revision': native['final_snapshot']['revision'], 'expected_frames': expected_frames,
              'decoded_frames': len(frames), 'motion': motion, 'review_sheets': sheets,
              'minimum_frame_mean_luma': float(frames.mean(axis=(1, 2)).min()),
              'visual_acceptance': 'Requires inspection of the timecoded sheets; motion is not aesthetic quality'}
    (args.output/'visual-metrics.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
