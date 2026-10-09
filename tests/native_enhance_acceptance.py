"""Packaged native enhancement UI, actual Vision/FFmpeg/MLT, synthetic media only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from film.desktop import Desktop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT/'build/FilmEditor.app')
    args = parser.parse_args()
    report = {'evidence': 'actual native UI and engines; synthetic target', 'passed': False}
    destination = ROOT/'reports/rebuild/enhance'; destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='filmeditor-native-enhance-') as folder:
        folder = Path(folder); workspace = folder/'workspace'; source = folder/'target.mp4'
        desktop = Desktop(workspace)
        try:
            entry = desktop.owner.library.create('Enhancement acceptance', width=640, height=360, fps_num=24, fps_den=1, auto_configure=False)
            ws = desktop.owner.library.session(entry['id']).workspace
            ffmpeg, ffprobe = ws.executable('ffmpeg'), ws.executable('ffprobe')
            subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','color=c=0x181818:s=640x360:r=24:d=4',
                '-f','lavfi','-i','testsrc2=s=96x96:r=24:d=4','-filter_complex',"[0:v][1:v]overlay=x='80+40*t':y=130:shortest=1",'-an',
                '-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709',str(source)],check=True,capture_output=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with source.open('rb') as stream: ws.ingest(stream,source.stat().st_size,source.name,'footage')
        finally: desktop.close()
        native_report = folder/'smoke.json'
        process = subprocess.Popen([str(args.app/'Contents/MacOS/FilmEditor'),'--workspace',str(workspace),'--project',entry['id'],
            '--smoke-script',str(ROOT/'tests/native_enhance_smoke.js'),'--smoke-report',str(native_report),'--smoke-exit'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=300)
            ui = json.loads(native_report.read_text()); report['native_ui'] = ui
            assert process.returncode == 0, process.returncode
            assert ui['passed'], ui
            assert not ui.get('navigationErrors'), ui.get('navigationErrors')
            output = workspace/'projects'/entry['id']/'cache'/ui['preview']
            subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-threads','1','-i',str(output),'-f','null','-'],check=True,capture_output=True)
            probe = json.loads(subprocess.check_output([ffprobe,'-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames,width,height','-of','json',str(output)]))['streams'][0]
            assert int(probe['nb_read_frames']) == 72, probe
            report['decoded_preview'] = probe
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
            assert not list(workspace.rglob('connection.json')), 'Owned backend left running'
            report.update(passed=True, original_unchanged=True, backend_shutdown=True)
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=20)
            (destination/'native-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    report['temporary_media_removed'] = True
    (destination/'native-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
