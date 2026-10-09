"""Opt-in native playback/responsiveness acceptance. A supplied source stays local.

All test projects and generated media are temporary; reports exclude source names,
frames, metadata tags and signed URLs. Defaults to synthetic H.264.
"""
import argparse
import hashlib
import contextlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film.core import ROOT
from film.desktop import Desktop

def checksum(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT/'build/FilmEditor.app')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--keep-workspace', type=Path, help='Explicit debug destination; caller owns cleanup')
    args = parser.parse_args()
    reports = ROOT/'reports/playback-fix'; reports.mkdir(parents=True, exist_ok=True)
    report = {'source_evidence': 'user-selected local video' if args.source else 'synthetic video'}
    if args.keep_workspace: args.keep_workspace.mkdir(parents=True,exist_ok=False)
    with (contextlib.nullcontext(str(args.keep_workspace)) if args.keep_workspace else tempfile.TemporaryDirectory(prefix='filmeditor-playback-')) as temporary:
        folder = Path(temporary); workspace = folder/'workspace'
        desktop = Desktop(workspace)
        try:
            entry = desktop.owner.library.create('Playback verification', width=640, height=360, fps_num=30, fps_den=1)
            session = desktop.owner.library.session(entry['id']); ffmpeg = session.workspace.executable('ffmpeg')
            source = args.source.resolve() if args.source else folder/'synthetic.mp4'
            if not args.source:
                subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30',
                                '-t','15','-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p',str(source)],check=True)
            original_hash = checksum(source)
            movie = session.import_paths([str(source)])[0]
            still = folder/'still.jpg'
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=0x204080:size=640x360',
                            '-frames:v','1','-threads','1','-update','1',str(still)],check=True)
            pictures=[]
            for index in range(28):
                copy = folder/f'still-{index:02}.jpg'; shutil.copy2(still,copy)
                pictures.append(session.import_paths([str(copy)])[0])
            snap=session.editor.snapshot()
            snap=session.editor.edit(snap['revision'],[
                {'op':'add_clip','track':'V1','path':movie['path'],'position_frame':0,'out_frame':89},
                {'op':'add_clip','track':'V1','path':pictures[0]['path'],'position_frame':90},
            ], project_path=snap['path'])
            clips=[c for t in snap['tracks'] for c in t['items'] if c['type']!='gap']
            assert snap['duration_frames']==240, snap['duration_frames']
            assert clips[-1]['duration_frames']==150, clips[-1]
            project = entry['id']
        finally: desktop.close()
        output=folder/'native.json'
        process=subprocess.Popen([str(args.app/'Contents/MacOS/FilmEditor'),'--workspace',str(workspace),'--project',project,
                                  '--smoke-script',str(ROOT/'tests/native_playback_acceptance.js'),'--smoke-report',str(output),'--smoke-exit'],
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=180)
            report.update(json.loads(output.read_text()))
            assert report['passed'], report
            project_root=workspace/'projects'/project
            previews=sorted((project_root/'cache').glob('preview-*.mp4'),key=lambda p:p.stat().st_mtime)
            assert previews, 'No preview produced'
            subprocess.run([ffmpeg,'-v','error','-threads','1','-i',str(previews[-1]),'-f','null','-'],check=True)
            def samples(path):
                return [subprocess.check_output([ffmpeg,'-v','error','-threads','1','-ss',str(at),'-i',str(path),
                        '-frames:v','1','-vf','scale=64:64','-pix_fmt','rgb24','-f','rawvideo','-']) for at in (.5,2.5)]
            source_frames=samples(source); rendered_frames=samples(previews[-1])
            assert all(len(frame)==64*64*3 for frame in source_frames+rendered_frames)
            if source_frames[0]!=source_frames[1]: assert rendered_frames[0]!=rendered_frames[1], 'Moving source froze in render'
            if any(max(frame)>32 for frame in source_frames): assert any(max(frame)>32 for frame in rendered_frames), 'Video section rendered black'
            report['moving_source_and_render_samples']=source_frames[0]!=source_frames[1] and rendered_frames[0]!=rendered_frames[1]
            pixel=subprocess.check_output([ffmpeg,'-v','error','-threads','1','-sseof','-0.1','-i',str(previews[-1]),
                                           '-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
            assert len(pixel)==3 and pixel[2]>80 and pixel[1]>35, list(pixel)
            report.update(preview_fully_decodes=True, late_still_frame_present=True,
                          source_unchanged=checksum(source)==original_hash)
            assert report['source_unchanged']
            assert not list(workspace.rglob('connection.json')), 'Backend did not stop'
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=20)
    # Native smoke screenshots and temporary copies disappear with the workspace.
    report['temporary_media_removed']=not bool(args.keep_workspace)
    (reports/'native-playback.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
