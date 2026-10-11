"""Opt-in editing check for a disposable project populated through real Photos UI.

This never imports files or proves Photos provenance by itself. Close the native
project first. Existing media stays intact; the disposable timeline is edited.
The caller owns cleanup of this workspace and must retain a separate route log.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import math
from array import array
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from film.desktop import Desktop

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--asset',action='append',required=True)
    parser.add_argument('--app',type=Path,default=Path.home()/'Applications/FilmEditor.app')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if len(set(args.asset))<2: parser.error('Use at least two distinct imported movie IDs')
    workspace=args.workspace.resolve(); project=workspace/'projects'/args.project
    if project.parent!=workspace/'projects' or not project.is_dir(): parser.error('Unknown test project')
    if list(workspace.rglob('connection.json')): parser.error('Close this workspace in FilmEditor before testing')
    report={'passed':False,'evidence':'native playback/edit/render of existing imported user movies; route provenance recorded separately'}
    process=None
    try:
        owner=Desktop(workspace)
        try:
            ws=owner.owner.library.session(args.project).workspace
            ffmpeg,ffprobe=ws.executable('ffmpeg'),ws.executable('ffprobe')
            assets={a['id']:a for a in ws.data['assets']}
            paths={ident:project/assets[ident]['path'] for ident in args.asset}
            before={ident:digest(path) for ident,path in paths.items()}
            probes={}; expected_streams={}
            for ident,path in paths.items():
                p=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(path)]))
                videos=[s for s in p['streams'] if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')]
                assert videos and float(p['format']['duration'])>2.5
                def selected(streams):
                    return next((s for s in streams if s.get('disposition',{}).get('default')),streams[0] if streams else None)
                audio=selected([s for s in p['streams'] if s.get('codec_type')=='audio'])
                expected_streams[ident]=(selected(videos)['index'],audio['index'] if audio else -1)
                probes[ident]={'duration':float(p['format']['duration']),'streams':[{k:s[k] for k in ('index','codec_type','codec_name','width','height','sample_rate','channels') if k in s} for s in p['streams']]}
            report['source_probes']=probes
        finally: owner.close()
        with tempfile.TemporaryDirectory(prefix='filmeditor-actual-controls-') as folder:
            folder=Path(folder);script=folder/'smoke.js';result=folder/'result.json'
            script.write_text((ROOT/'tests/native_actual_media_smoke.js').read_text().replace('__ACCEPTED_ASSETS__',json.dumps(args.asset)))
            process=subprocess.Popen([str(args.app/'Contents/MacOS/FilmEditor'),'--workspace',str(workspace),'--project',args.project,'--smoke-script',str(script),'--smoke-report',str(result),'--smoke-exit'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            process.wait(timeout=600)
            report['native_ui']=ui=json.loads(result.read_text())
            assert process.returncode==0 and ui['passed'],ui
            assert not ui.get('navigationErrors'),ui
        preview=project/'cache'/ui['preview'];assert preview.parent==project/'cache'
        subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-threads','1','-i',str(preview),'-f','null','-'],check=True,capture_output=True,timeout=180)
        probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-count_frames','-show_streams','-of','json',str(preview)]))
        video=next(s for s in probe['streams'] if s['codec_type']=='video')
        assert int(video['nb_read_frames'])==ui['frames'],(video,ui['frames'])
        report['decoded_frames']=int(video['nb_read_frames'])
        report['audio_present']=any(s['codec_type']=='audio' for s in probe['streams'])
        exported=project/'exports'/ui['export'];assert exported.parent==project/'exports'
        subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-threads','1','-i',str(exported),'-f','null','-'],check=True,capture_output=True,timeout=180)
        export_probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-count_frames','-show_streams','-of','json',str(exported)]))
        export_video=next(s for s in export_probe['streams'] if s['codec_type']=='video')
        assert int(export_video['nb_read_frames'])==ui['frames'] and export_video['codec_name']=='h264'
        report['export']={'decoded_frames':int(export_video['nb_read_frames']),'video_codec':export_video['codec_name'],
            'width':export_video['width'],'height':export_video['height'],
            'audio_present':any(s['codec_type']=='audio' for s in export_probe['streams'])}
        sequence=json.loads((project/'project.json').read_text())['active_project']
        tree=ET.parse(project/sequence)
        selectors={}
        for producer in tree.findall('producer')+tree.findall('chain'):
            props={p.get('name'):p.text for p in producer.findall('property')}
            for ident,path in paths.items():
                if props.get('resource')==str(path):
                    actual=(int(props.get('video_index',-1)),int(props.get('audio_index',-1)))
                    assert actual==expected_streams[ident],(ident,actual,expected_streams[ident])
                    selectors[ident]=actual
        assert set(selectors)==set(args.asset),'Missing explicit MLT stream selectors'
        report['mlt_stream_selectors']=selectors
        rate=float(ui['profile']['frame_rate_num'])/float(ui['profile']['frame_rate_den'])
        def audio_rms(path,at,stream):
            raw=subprocess.check_output([ffmpeg,'-nostdin','-v','error','-threads','1','-ss',str(at),'-i',str(path),'-t','0.35','-map',stream,'-vn','-ac','1','-ar','8000','-f','f32le','pipe:1'])
            values=array('f');values.frombytes(raw)
            if sys.byteorder!='little':values.byteswap()
            assert values,'No decoded audio samples'
            return math.sqrt(sum(float(v)*float(v) for v in values)/len(values))
        report['audio_checks']=[]
        for item in ui['clips']:
            ident=item['asset_id'];audio_index=expected_streams[ident][1]
            if audio_index<0:continue
            assert report['audio_present'],'Output lost source audio'
            source_rms=audio_rms(paths[ident],item['in_frame']/rate+.75,f'0:{audio_index}')
            rendered_rms=audio_rms(preview,item['start_frame']/rate+.75,'0:a:0')
            assert report['export']['audio_present'],'Export lost source audio'
            exported_rms=audio_rms(exported,item['start_frame']/rate+.75,'0:a:0')
            if source_rms>1e-4:assert rendered_rms>1e-5,'Rendered source audio became silent'
            if source_rms>1e-4:assert exported_rms>1e-5,'Exported source audio became silent'
            report['audio_checks'].append({'asset_id':ident,'source_rms':source_rms,'rendered_rms':rendered_rms,'exported_rms':exported_rms})
        assert all(digest(path)==before[ident] for ident,path in paths.items())
        assert not list(workspace.rglob('connection.json'))
        report.update(passed=True,imported_source_hashes_unchanged=True,backend_shutdown=True)
    finally:
        if process and process.poll() is None: process.terminate();process.wait(timeout=20)
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
