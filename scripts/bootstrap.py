#!/usr/bin/env python3
"""Install pinned integration dependencies, never system apps or Codex credentials."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
try:
    from .managed_shotcut import SHOTCUT_COMMIT,prepare_bridge
except ImportError:
    from managed_shotcut import SHOTCUT_COMMIT,prepare_bridge
ROOT=Path(__file__).resolve().parents[1]
HYPERFRAMES='0.8.100'
GSAP='3.12.5'

def run(args,cwd=None):
    print('+ '+' '.join(map(str,args)),flush=True)
    subprocess.run(args,cwd=cwd,check=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shotcut-only',action='store_true')
    opts=parser.parse_args()
    if sys.version_info<(3,10): raise SystemExit('Python 3.10+ required')
    if not shutil.which('git'): raise SystemExit('Git required; install Apple command-line tools first')
    deps=ROOT/'.deps'; deps.mkdir(exist_ok=True)
    bridge=deps/'shotcut-mcp'
    if not bridge.exists():
        run(['git','init',str(bridge)])
        run(['git','remote','add','origin','https://github.com/matrodrigs/shotcut-mcp.git'],bridge)
        run(['git','fetch','--depth=1','origin',SHOTCUT_COMMIT],bridge)
        run(['git','checkout','--detach','FETCH_HEAD'],bridge)
    print('Shotcut MCP at pinned revision: '+prepare_bridge(bridge))
    if not opts.shotcut_only:
        if not shutil.which('node') or not shutil.which('npm'): raise SystemExit('Node.js 22+ and npm required')
        version=subprocess.check_output(['node','--version'],text=True).strip()
        if int(version.lstrip('v').split('.')[0])<22: raise SystemExit('Node.js 22+ required')
        folder=deps/'hyperframes'; folder.mkdir(exist_ok=True)
        package={'name':'filmeditor-local-engines','private':True,'version':'0.1.0','dependencies':{'hyperframes':HYPERFRAMES,'gsap':GSAP}}
        manifest=folder/'package.json'
        if manifest.exists() and json.loads(manifest.read_text())!=package: raise SystemExit('Existing dependency manifest differs; reconcile manually')
        manifest.write_text(json.dumps(package,indent=2)+'\n')
        run(['npm','ci' if (folder/'package-lock.json').exists() else 'install','--no-audit','--no-fund'],folder)
    print('\nDependencies prepared. Install current Shotcut, ffmpeg/ffprobe, and Codex separately if missing.')
    print('Build with your selected Python: scripts/build_mac_app.py --install, then open ~/Applications/FilmEditor.app. See docs/ACCEPTANCE.md.')

if __name__=='__main__':
    try: main()
    except subprocess.CalledProcessError as exc: raise SystemExit(f'Installation command failed ({exc.returncode}); engine readiness not established')
    except (OSError,ValueError) as exc: raise SystemExit(str(exc))
