#!/usr/bin/env python3
"""Publish this reviewed source package using the Mac's existing Git authentication.
No force push, credential collection, account switching, or global Git configuration changes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
REMOTE='https://github.com/georgieee03/filmeditor.git'

def run(args, capture=False):
    return subprocess.run(['git',*args],cwd=ROOT,check=True,text=True,capture_output=capture)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run',action='store_true',help='Validate package files without Git, network or remote changes')
    a=p.parse_args()
    manifest=json.loads((ROOT/'SOURCE_MANIFEST.json').read_text())
    paths=[]
    for item in manifest['files']:
        file=(ROOT/item['path']).resolve()
        if ROOT not in file.parents or not file.is_file(): raise SystemExit('Unsafe or missing package path')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:
            raise SystemExit('Source differs from the delivered package: '+item['path']+'. Review and publish manually instead.')
        paths.append(item['path'])
    paths.append('SOURCE_MANIFEST.json')
    if a.dry_run:
        print(f'Validated {len(paths)} source files. No network, commits or uploads performed.')
        return
    if (ROOT/'.git').exists():
        top=Path(run(['rev-parse','--show-toplevel'],True).stdout.strip()).resolve()
        if top!=ROOT: raise SystemExit('Unexpected Git root; publish manually')
    else: run(['init','-b','main'])
    remotes=run(['remote'],True).stdout.splitlines()
    if 'origin' in remotes:
        url=run(['remote','get-url','origin'],True).stdout.strip()
        if url not in (REMOTE,'git@github.com:georgieee03/filmeditor.git'):
            raise SystemExit('Origin is another repository. Nothing was redirected.')
    else: run(['remote','add','origin',REMOTE])
    for key in ('user.name','user.email'):
        check=subprocess.run(['git','config','--get',key],cwd=ROOT,capture_output=True,text=True)
        if check.returncode or not check.stdout.strip():
            raise SystemExit(f'Your Git {key} is missing. Set your own local Git identity before publishing; none was invented.')
    # Refuse unrelated pre-staged files, and stage only the delivered manifest.
    staged=run(['diff','--cached','--name-only'],True).stdout.splitlines()
    if set(staged)-set(paths): raise SystemExit('Unrelated staged files found; review and publish manually')
    run(['add','--',*paths])
    changes=run(['diff','--cached','--name-only'],True).stdout.strip()
    if changes: run(['commit','-m','Rebuild FilmEditor as a native Mac editor'])
    # Git refuses non-fast-forward history; never use --force or reset the remote.
    run(['push','-u','origin','HEAD:refs/heads/main'])
    print('Push completed: '+REMOTE)

if __name__=='__main__':
    try: main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'Git command failed ({exc.returncode}). Review the Git error; no force-push fallback will be attempted.')
