#!/usr/bin/env python3
"""Register the running FilmEditor with an existing Codex desktop/CLI client."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',default=str(Path.home()/'Movies/FilmEditor'))
    a=p.parse_args(); codex=os.environ.get('FILM_CODEX') or shutil.which('codex')
    if codex and not os.access(codex,os.X_OK): raise SystemExit('FILM_CODEX is not executable')
    if not codex: raise SystemExit('Install the Codex CLI and sign in with codex login first')
    old=subprocess.run([codex,'mcp','get','filmeditor'],capture_output=True,text=True)
    if old.returncode==0:
        print(old.stdout); raise SystemExit('Entry already exists; check its workspace. Nothing was replaced.')
    subprocess.run([codex,'mcp','add','filmeditor','--',sys.executable,str(ROOT/'app.py'),'mcp','--workspace',str(Path(a.workspace).expanduser().resolve())],check=True)
    print('Registered. Start FilmEditor, then reconnect MCP in your local Codex client.')
if __name__=='__main__': main()
