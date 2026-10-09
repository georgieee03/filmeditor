"""Opt-in installed-app home form acceptance. No media, model calls or user data."""
import argparse,json,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,default=Path.home()/'Applications/FilmEditor.app')
    args=parser.parse_args();destination=ROOT/'reports/rebuild/project-creation'
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='filmeditor-project-creation-') as temporary:
        folder=Path(temporary);workspace=folder/'workspace'
        def launch(script,report,project=None):
            report.unlink(missing_ok=True)
            command=[str(args.app/'Contents/MacOS/FilmEditor'),'--workspace',str(workspace),
                     '--smoke-script',str(script),'--smoke-report',str(report),'--smoke-exit']
            if project:command+=['--project',project]
            process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                process.wait(timeout=120)
                value=json.loads(report.read_text());assert value['passed'],value
                assert not value['navigationErrors'],value['navigationErrors']
                return value
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=20)
        result=launch(ROOT/'tests/native_project_creation.js',destination/'create.json')
        for index,project in enumerate(result['projects']):
            state=json.loads((workspace/'projects'/project['id']/'project.json').read_text())
            assert state['auto_configure']==(project['format']=='auto')
            script=folder/'reopen.js'
            script.write_text('''(async()=>{const report={};try{
              const end=Date.now()+30000;
              while(!timeline?.revision&&Date.now()<end)await new Promise(r=>setTimeout(r,100));
              if(!timeline?.revision)throw new Error('Timeline did not open');
              const profile=timeline.profile;
              if(Number(profile.frame_rate_num)!=='''+str(project['fps'])+'''||Number(profile.frame_rate_den)!==1)throw new Error('Wrong frame rate');
              if(Number(profile.width)!=='''+str(1080 if project['format']=='vertical' else 1920)+'''||Number(profile.height)!=='''+str(1920 if project['format']=='vertical' else 1080)+''')throw new Error('Wrong dimensions');
              if(timeline.tracks.length!==6||state.assets.length)throw new Error('Not a blank six-track project');
              report.passed=true;report.profile=profile;
            }catch(e){report.passed=false;report.error=e.message;}
            window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});})();''')
            project['reopen']=launch(script,destination/f'reopen-{index}.json',project['id'])
        assert not list(workspace.rglob('connection.json')),'Owned backend did not stop'
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(args.app)],check=True,capture_output=True)
    result.update(signature_valid=True,temporary_projects_removed=True,no_media_created=True)
    (destination/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
