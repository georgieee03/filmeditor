"""Opt-in real Apple Vision tracking acceptance; synthetic targets, no user media."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, default=ROOT / 'build/FilmEditor.app/Contents/Resources/FilmVision')
    args = parser.parse_args()
    ffmpeg = '/Applications/Shotcut.app/Contents/MacOS/ffmpeg'
    report = {'evidence': 'real Apple Vision, synthetic moving target only', 'passed': False, 'cases': {}}
    def run(command):
        return subprocess.run(command, check=True, capture_output=True, timeout=90)
    with tempfile.TemporaryDirectory(prefix='filmeditor-vision-') as folder:
        folder = Path(folder)
        source = folder / 'target.mp4'
        run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x181818:s=640x360:r=24:d=4',
             '-f', 'lavfi', '-i', 'testsrc2=s=96x96:r=24:d=4', '-filter_complex',
             "[0:v][1:v]overlay=x='80+40*t':y=130:shortest=1", '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
             '-threads', '1', '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', str(source)])
        rotated = folder / 'rotated.mp4'
        run([ffmpeg, '-nostdin', '-v', 'error', '-display_rotation', '90', '-i', str(source), '-c', 'copy', str(rotated)])
        cut = folder / 'cut.mp4'
        run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(source), '-vf', "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='gte(t,2)'",
             '-an', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', str(cut)])
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, rotated, cut)}
        for name, path, subject, center in [('manual', source, 'manual', (.2, .494)),
                                           ('auto', source, 'auto', (.5, .5)),
                                           ('rotated_auto', rotated, 'auto', (.5, .5)),
                                           ('cut_hold', cut, 'manual', (.2, .494))]:
            output = folder / (name + '.json')
            started = time.monotonic()
            run([str(args.helper), '--input', str(path), '--output', str(output), '--subject', subject,
                 '--center-x', str(center[0]), '--center-y', str(center[1]), '--interval', '.333'])
            data = json.loads(output.read_text())
            assert data['version'] == 1 and len(data['points']) >= 10
            assert (data['width'], data['height']) == ((360, 640) if name == 'rotated_auto' else (640, 360))
            points = data['points']
            assert points[0]['time'] == 0 and points[-1]['time'] == 4
            assert all(a['time'] < b['time'] for a, b in zip(points, points[1:]))
            if name == 'cut_hold':
                assert any('scene change' in message.lower() or 'lost' in message.lower() for message in data['warnings'])
                tail = [p for p in points if p['time'] >= 2.3]
                assert max(p['x'] for p in tail) - min(p['x'] for p in tail) < .001
            else:
                # Independent pixel oracle: colorful target on a neutral grey background,
                # decoded by FFmpeg with display rotation applied, outside Vision.
                errors = []
                for p in points[1:-1:3]:
                    w, h = (90, 160) if name == 'rotated_auto' else (160, 90)
                    raw = run([ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-ss', str(p['time']),
                               '-frames:v', '1', '-vf', f'scale={w}:{h}', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']).stdout
                    assert len(raw) == w * h * 3
                    marked = [(i % w, i // w) for i in range(w*h) if max(raw[3*i:3*i+3]) - min(raw[3*i:3*i+3]) > 60]
                    assert marked
                    x = (min(p[0] for p in marked) + max(p[0] for p in marked)) / (2*w)
                    y = (min(p[1] for p in marked) + max(p[1] for p in marked)) / (2*h)
                    errors.append(max(abs(x-p['x']), abs(y-p['y'])))
                assert max(errors) < .08, (name, errors)
                data['maximum_target_center_error'] = round(max(errors), 5)
            report['cases'][name] = {'width': data['width'], 'height': data['height'], 'samples': len(points),
                'warnings': data['warnings'], 'maximum_target_center_error': data.get('maximum_target_center_error'),
                'wall_seconds': round(time.monotonic() - started, 2)}
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[p.name] for p in (source, rotated, cut))
        report['source_hashes_preserved'] = True
    report.update(passed=True, temporary_media_removed=True)
    report_path = ROOT / 'reports/rebuild/enhance/vision-acceptance.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
