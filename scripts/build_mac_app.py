#!/usr/bin/env python3
"""Build an AppKit FilmEditor.app with a bundled Python backend and local adapters."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def command(args, **kwargs):
    return subprocess.run([str(a) for a in args],check=True,**kwargs)

def local_environment():
    values={}
    path=ROOT/'.local/mac-env.sh'
    if path.exists():
        for line in path.read_text().splitlines():
            match=re.fullmatch(r'export ([A-Z_]+)=(.*)',line)
            if match:
                words=shlex.split(match[2])
                if len(words)==1: values[match[1]]=words[0]
    values.update({key:value for key,value in os.environ.items() if key.startswith('FILM_')})
    return values

def require(path, label):
    candidate=Path(path).expanduser()
    if not candidate.is_file(): raise ValueError(label+' is missing: '+str(candidate))
    return str(candidate.resolve())

def copy_python(prefix, target):
    """Standalone CPython is relocatable; bundled third-party site packages are unnecessary."""
    version=command([prefix/'bin/python3','-c','import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                    capture_output=True,text=True).stdout.strip()
    (target/'bin').mkdir(parents=True)
    binary=prefix/'bin'/('python'+version)
    if not binary.exists(): binary=prefix/'bin/python3'
    shutil.copy2(binary.resolve(),target/'bin'/('python'+version))
    (target/'bin/python3').symlink_to('python'+version)
    (target/'bin/python').symlink_to('python'+version)
    (target/'lib').mkdir()
    def ignored(directory,names):
        return [name for name in names if name in ('site-packages','__pycache__','test','tests','idlelib','ensurepip')
                or name.endswith(('.pyc','.pyo'))]
    shutil.copytree(prefix/'lib'/('python'+version),target/'lib'/('python'+version),ignore=ignored,symlinks=True)
    for dylib in (prefix/'lib').glob('*.dylib'): shutil.copy2(dylib,target/'lib'/dylib.name)
    env=os.environ.copy(); env.update(PYTHONHOME=str(target),PYTHONDONTWRITEBYTECODE='1')
    command([target/'bin/python3','-I','-c','import json,socket,ssl,sqlite3,subprocess,xml.etree.ElementTree;print("Bundled Python validated")'],env=env)
    return '@resources/runtime/python/bin/python3'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true',help='Install in ~/Applications after a successful build')
    parser.add_argument('--output',type=Path,default=ROOT/'build/FilmEditor.app')
    parser.add_argument('--python-prefix',type=Path,help='Relocatable standalone CPython prefix')
    parser.add_argument('--no-sign',action='store_true',help='Skip local ad-hoc signing (development only)')
    args=parser.parse_args()
    if sys.platform!='darwin': raise SystemExit('The app must be built on macOS with Xcode Command Line Tools.')
    env=local_environment()
    node=require(env.get('FILM_NODE') or shutil.which('node') or '/opt/homebrew/bin/node','Node.js')
    version=command([node,'--version'],capture_output=True,text=True).stdout.strip()
    if int(version.removeprefix('v').split('.')[0])<22: raise ValueError('Node.js 22+ is required')
    engine={
        'FILM_FFMPEG':require(env.get('FILM_FFMPEG','/Applications/Shotcut.app/Contents/MacOS/ffmpeg'),'Shotcut FFmpeg'),
        'FILM_FFPROBE':require(env.get('FILM_FFPROBE','/Applications/Shotcut.app/Contents/MacOS/ffprobe'),'Shotcut FFprobe'),
        'FILM_CODEX':require(env.get('FILM_CODEX') or shutil.which('codex') or '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex','Codex'),
    }
    require('/Applications/Shotcut.app/Contents/MacOS/melt','Shotcut MLT')
    require(ROOT/'.deps/shotcut-mcp/scripts/shotcut_mcp_server.py','Pinned Shotcut adapter (run scripts/bootstrap.py)')
    require(ROOT/'.deps/hyperframes/node_modules/.bin/hyperframes','Pinned HyperFrames (run scripts/bootstrap.py)')
    python=env.get('FILM_PYTHON') or (str(ROOT/'.venv/bin/python') if (ROOT/'.venv/bin/python').exists() else sys.executable)
    prefix=args.python_prefix or Path(command([python,'-c','import sys;print(sys.base_prefix)'],capture_output=True,text=True).stdout.strip())
    output=args.output.expanduser().resolve(); output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.native-build-',dir=output.parent))
    app=staging/'FilmEditor.app'
    resources=app/'Contents/Resources'
    executable=app/'Contents/MacOS/FilmEditor'
    resources.mkdir(parents=True); executable.parent.mkdir()
    try:
        sdk=command(['/usr/bin/xcrun','--show-sdk-path'],capture_output=True,text=True).stdout.strip()
        machine=os.uname().machine
        command(['/usr/bin/xcrun','swiftc',ROOT/'native/FilmEditor.swift','-o',executable,'-O',
                 '-sdk',sdk,'-target',machine+'-apple-macos13.0','-framework','AppKit',
                 '-framework','WebKit','-framework','UniformTypeIdentifiers'])
        iconset=staging/'FilmEditor.iconset'
        command(['/usr/bin/xcrun','swift',ROOT/'native/MakeIcon.swift',iconset])
        command(['/usr/bin/iconutil','--convert','icns','--output',resources/'FilmEditor.icns',iconset])
        source=resources/'app'; source.mkdir()
        shutil.copy2(ROOT/'app.py',source/'app.py')
        for folder in ('film','web'):
            shutil.copytree(ROOT/folder,source/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        deps=source/'.deps'; deps.mkdir()
        shutil.copytree(ROOT/'.deps/shotcut-mcp',deps/'shotcut-mcp',symlinks=True,
                        ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','.pytest_cache','tests','reports'))
        shutil.copytree(ROOT/'.deps/hyperframes',deps/'hyperframes',symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','.cache'))
        local=source/'.local'; local.mkdir()
        (local/'performance.json').write_text('{"heavy_jobs":1,"hyperframes_workers":1}\n')
        if (ROOT/'.local/chrome-sandboxed').exists():
            shutil.copy2(ROOT/'.local/chrome-sandboxed',local/'chrome-sandboxed')
            engine['PRODUCER_HEADLESS_SHELL_PATH']='@resources/app/.local/chrome-sandboxed'
        elif env.get('PRODUCER_HEADLESS_SHELL_PATH'):
            engine['PRODUCER_HEADLESS_SHELL_PATH']=require(env['PRODUCER_HEADLESS_SHELL_PATH'],'Chrome runtime')
        engine.update({
            'FILM_HYPERFRAMES':'@resources/app/.deps/hyperframes/node_modules/.bin/hyperframes',
            'FILM_SHOTCUT_MCP':'@resources/app/.deps/shotcut-mcp/scripts/shotcut_mcp_server.py',
            'PYTHONHOME':'@resources/runtime/python',
            'PRODUCER_LOW_MEMORY_MODE':'true',
            'PATH':'@resources/runtime/python/bin:'+str(Path(node).parent)+':/Applications/Shotcut.app/Contents/MacOS:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        })
        runtime=resources/'runtime/python'
        python_path=copy_python(prefix.resolve(),runtime)
        python_license=next((runtime/'lib').glob('python*/LICENSE.txt'),None)
        if python_license is None: raise ValueError('The selected Python runtime has no LICENSE.txt; use standalone CPython.')
        shutil.copy2(python_license,resources/'CPYTHON_LICENSE.txt')
        (resources/'runtime.json').write_text(json.dumps({'python':python_path,'environment':engine},indent=2)+'\n')
        info={'CFBundleName':'FilmEditor','CFBundleDisplayName':'FilmEditor','CFBundleIdentifier':'local.filmeditor.launcher',
              'CFBundleExecutable':'FilmEditor','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.2.0',
              'CFBundleVersion':'2','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,
              'CFBundleIconFile':'FilmEditor.icns',
              'NSHumanReadableCopyright':'FilmEditor local integration. Bundled adapters retain their own licenses.',
              'NSPrincipalClass':'NSApplication','LSApplicationCategoryType':'public.app-category.video'}
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        for filename in ('LICENSE','THIRD_PARTY_NOTICES.md'):
            if (ROOT/filename).exists(): shutil.copy2(ROOT/filename,resources/filename)
        if not args.no_sign: command(['/usr/bin/codesign','--force','--deep','--sign','-',app],capture_output=True,text=True)
        command(['/usr/bin/plutil','-lint',app/'Contents/Info.plist'])
        if not args.no_sign: command(['/usr/bin/codesign','--verify','--deep','--strict',app],capture_output=True,text=True)
        if output.exists(): shutil.rmtree(output)
        shutil.move(app,output)
        print('Built '+str(output))
        if args.install:
            installed=Path.home()/'Applications/FilmEditor.app'; installed.parent.mkdir(exist_ok=True)
            registry=Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
            if installed.exists():
                if registry.exists(): command([registry,'-u',installed],capture_output=True,text=True)
                backup=installed.with_name('FilmEditor.previous.app')
                if backup.exists(): shutil.rmtree(backup)
                installed.rename(backup)
                if registry.exists(): command([registry,'-u',backup],capture_output=True,text=True)
            shutil.copytree(output,installed,symlinks=True)
            if registry.exists():
                # A tested build and a retained legacy launcher can otherwise win
                # name-based resolution ahead of the newly installed desktop app.
                command([registry,'-u',output],capture_output=True,text=True)
                command([registry,'-f',installed],capture_output=True,text=True)
            print('Installed '+str(installed))
        print('This app bundles Python, the UI and adapters. Shotcut, Node, Chrome and authenticated Codex remain installed engines.')
    finally: shutil.rmtree(staging,ignore_errors=True)

if __name__=='__main__':
    try: main()
    except (OSError,ValueError,subprocess.CalledProcessError) as exc: raise SystemExit(str(exc))
