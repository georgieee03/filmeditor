"""Opt-in native WKWebView acceptance, using temporary synthetic media and the real engines."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT, unpack
from film.desktop import Desktop
from app import api_request

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,default=ROOT/'build/FilmEditor.app')
    args=parser.parse_args()
    destination=ROOT/'reports/rebuild/native'; destination.mkdir(parents=True,exist_ok=True)
    report={}
    with tempfile.TemporaryDirectory(prefix='filmeditor-native-acceptance-') as temporary:
        folder=Path(temporary); workspace=folder/'workspace'
        desktop=Desktop(workspace)
        try:
            entry=desktop.owner.library.create('Native synthetic validation',width=640,height=360,fps_num=30,fps_den=1)
            session=desktop.owner.library.session(entry['id'])
            source=folder/'synthetic-native.mp4'
            subprocess.run([session.workspace.executable('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','5','-c:v','libx264','-preset','ultrafast',
                '-threads','1','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(source)],check=True)
            checksum=hashlib.sha256(source.read_bytes()).hexdigest()
            asset=session.import_paths([str(source)])[0]
            song=folder/'synthetic-native-song.wav'
            subprocess.run([session.workspace.executable('ffmpeg'),'-v','error','-f','lavfi','-i',
                'sine=frequency=220:sample_rate=48000','-t','2',str(song)],check=True)
            session.import_paths([str(song)],kind='audio')
            snap=session.editor.snapshot()
            snap=session.editor.edit(snap['revision'],[{'op':'add_clip','track':'V1','path':asset['path'],
                'position_frame':0,'caption':'Synthetic native test'}],project_path=snap['path'])
            session.editor.media(asset['id'])
            project=entry['id']
        finally: desktop.close()
        for run in ('launch','reopen'):
            output=destination/(run+'.json'); output.unlink(missing_ok=True)
            process=subprocess.Popen([str(args.app/'Contents/MacOS/FilmEditor'),'--workspace',str(workspace),'--project',project,
                '--smoke-script',str(ROOT/'tests/native_smoke.js'),'--smoke-report',str(output),'--smoke-exit'],
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            deadline=time.monotonic()+180
            ipc=None
            while time.monotonic()<deadline and process.poll() is None and not output.exists():
                connection=workspace/'.film/connection.json'
                active=workspace/'projects'/project
                if ipc is None and connection.exists() and (active/'.film/connection.json').exists():
                    value=json.loads(connection.read_text())
                    backend=int(value['pid'])
                    parent=int(subprocess.check_output(['/bin/ps','-o','ppid=','-p',str(backend)],text=True).strip())
                    if parent!=process.pid: raise AssertionError('Backend is not a child of the native application')
                    sockets=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(process.pid)+','+str(backend),'-iTCP'],capture_output=True,text=True)
                    if sockets.stdout.strip(): raise AssertionError('Native application opened TCP: '+sockets.stdout)
                    state=api_request(active,'/api/state')
                    if state['workspace']!=str(active.resolve()) or not state.get('desktop'): raise AssertionError('External MCP workspace mismatch')
                    # Exercise the actual stdio MCP entry point, not only its Python helper.
                    mcp=subprocess.run([sys.executable,str(ROOT/'app.py'),'mcp','--workspace',str(active)],
                        input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'film_brief','arguments':{}}})+'\n',
                        capture_output=True,text=True,timeout=20,check=True)
                    response=json.loads(mcp.stdout)
                    if response.get('result',{}).get('isError') or 'result' not in response: raise AssertionError('Native external MCP failed')
                    ipc={'app_owned_backend':True,'no_tcp_connections_or_listeners':True,'external_stdio_mcp_shared_workspace':True,'transport':value['transport']}
                time.sleep(.2)
            if not output.exists():
                process.terminate()
                _,error=process.communicate(timeout=15)
                raise RuntimeError('Native '+run+' did not report: '+error.decode(errors='replace')[-1500:])
            value=json.loads(output.read_text())
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.terminate(); raise RuntimeError('Native quit failed')
            report[run]=value
            report[run].update(ipc or {})
            if ipc is None: raise AssertionError('Native IPC probe did not run')
            if not value.get('passed'): raise AssertionError(json.dumps(value,indent=2))
            sockets=list(workspace.rglob('native.sock'))
            connections=list(workspace.rglob('connection.json'))
            if sockets or connections: raise AssertionError('Native quit retained socket/connection files')
            report[run]['owned_backend_stopped']=True
            log=workspace/'.film/desktop.log'
            if log.exists(): (destination/(run+'.log')).write_bytes(log.read_bytes())
        report['source_untouched']=hashlib.sha256(source.read_bytes()).hexdigest()==checksum
        report['synthetic_only']=True
        report['app']=str(args.app.resolve())
    (destination/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
