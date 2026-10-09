#!/usr/bin/env python3
"""Start/reopen or stop the user's on-demand FilmEditor launchd service."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
LABEL='local.filmeditor.editor'
DOMAIN=f'gui/{os.getuid()}'
TARGET=f'{DOMAIN}/{LABEL}'

def connection(workspace):
    try:
        c=json.loads((workspace/'.film/connection.json').read_text())
        u=urlsplit(c['url'])
        if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.path: return None
        req=urllib.request.Request(c['url']+'/api/state',headers={'Authorization':'Bearer '+c['token']})
        with urllib.request.urlopen(req,timeout=2) as response: state=json.load(response)
        if Path(state['workspace']).resolve()!=workspace: return None
        return c
    except (OSError,ValueError,KeyError): return None

def launchctl(*args):
    return subprocess.run(['/bin/launchctl',*args],capture_output=True,text=True)

def install():
    folder=Path.home()/'Library/LaunchAgents';folder.mkdir(parents=True,exist_ok=True)
    logs=ROOT/'.local';logs.mkdir(exist_ok=True);logs.chmod(0o700)
    for name in ('service.stdout.log','service.stderr.log'):
        p=logs/name;p.touch(exist_ok=True);p.chmod(0o600)
    data={'Label':LABEL,'ProgramArguments':['/bin/bash',str(ROOT/'run-mac.command'),'--no-browser'],
          'WorkingDirectory':str(ROOT),'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Interactive',
          'StandardOutPath':str(logs/'service.stdout.log'),'StandardErrorPath':str(logs/'service.stderr.log')}
    path=folder/(LABEL+'.plist')
    if path.exists() and plistlib.loads(path.read_bytes())!=data:
        raise RuntimeError('A different FilmEditor service configuration exists; preserve it and reconcile before replacing.')
    path.write_bytes(plistlib.dumps(data));path.chmod(0o600)
    if launchctl('print',TARGET).returncode:
        r=launchctl('bootstrap',DOMAIN,str(path))
        if r.returncode: raise RuntimeError('Cannot register FilmEditor service: '+r.stderr.strip())

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','stop','status']);p.add_argument('--no-browser',action='store_true');a=p.parse_args()
    workspace=Path(os.environ['FILM_WORKSPACE']).expanduser().resolve()
    if a.action=='stop':
        r=launchctl('kill','SIGINT',TARGET)
        if r.returncode and connection(workspace): raise RuntimeError('The active editor was not started by this service; stop its original terminal.')
        for _ in range(50):
            if not connection(workspace): print('FilmEditor stopped. Saved media and projects are retained.');return
            time.sleep(.1)
        raise RuntimeError('FilmEditor has not stopped yet; inspect the private service logs.')
    c=connection(workspace)
    if a.action=='status':
        print('FilmEditor is running.' if c else 'FilmEditor is stopped.');return
    if not c:
        install();r=launchctl('kickstart',TARGET)
        if r.returncode: raise RuntimeError('Cannot start FilmEditor: '+r.stderr.strip())
        for _ in range(100):
            c=connection(workspace)
            if c:break
            time.sleep(.1)
        if not c: raise RuntimeError('FilmEditor did not become ready. Inspect .local/service.stderr.log; no unrelated process was stopped.')
    if not a.no_browser: webbrowser.open(c['url']+'/#'+c['token'])
    print('FilmEditor is ready. Use stop-editor.command to stop it.')

if __name__=='__main__':
    try:main()
    except Exception as e:raise SystemExit(str(e))
