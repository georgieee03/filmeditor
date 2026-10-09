"""Apply and verify FilmEditor's exact patch over the pinned Shotcut adapter."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PATCHES=ROOT/'patches'
SHOTCUT_COMMIT='f1b78d350a9992296f0b7f11a55c04819ea25730'
MANAGED_FILES={'CHANGELOG.md','shotcut_mcp/project_document.py','tests/test_project_features.py','tests/test_transactions.py'}
DIFF=['-c','core.quotePath=false','diff','--no-ext-diff','--no-textconv','--no-color',
      '--binary','--full-index','--no-renames','--src-prefix=a/','--dst-prefix=b/','--unified=3']

def _git(bridge, *args):
    return subprocess.check_output(['git',*args],cwd=bridge,stderr=subprocess.PIPE)

def _digest(data):
    return hashlib.sha256(data).hexdigest()

def _definition(patches=PATCHES):
    manifest=json.loads((patches/'shotcut-mcp-stills.json').read_text())
    if manifest.get('version')!=1 or manifest.get('commit')!=SHOTCUT_COMMIT:
        raise ValueError('Managed Shotcut patch definition does not match the pinned revision')
    if set(manifest.get('files',{}))!=MANAGED_FILES:
        raise ValueError('Managed Shotcut patch file list differs from the supported patch')
    values={}
    for key in ('patch','legacy_patch'):
        path=patches/manifest[key]
        if path.parent!=patches: raise ValueError('Invalid managed patch path')
        values[key]=path.read_bytes()
        if _digest(values[key])!=manifest[key+'_sha256']:
            raise ValueError('Managed Shotcut patch checksum mismatch')
    return manifest,values

def inspect_bridge(bridge, *, patches=PATCHES):
    """Recognize only a clean pin, the exact preserved legacy fix, or our patch."""
    bridge=Path(bridge)
    manifest,values=_definition(patches)
    if _git(bridge,'rev-parse','HEAD').decode().strip()!=SHOTCUT_COMMIT:
        raise ValueError('Shotcut adapter HEAD differs from the pinned revision; existing files were preserved')
    if _git(bridge,'ls-files','--others','--exclude-standard','-z'):
        raise ValueError('Shotcut adapter has unrelated untracked files; existing files were preserved')
    diff=_git(bridge,*DIFF,'HEAD','--')
    candidates={b'':'clean',values['legacy_patch']:'legacy',values['patch']:'patched'}
    state=candidates.get(diff)
    if state is None:
        raise ValueError('Shotcut adapter has unmanaged edits; existing files were preserved')
    staged=_git(bridge,*DIFF,'--cached','HEAD','--')
    if staged not in candidates or state=='clean' and staged:
        raise ValueError('Shotcut adapter index has unmanaged edits; existing files were preserved')
    for name,identities in manifest['files'].items():
        if _digest((bridge/name).read_bytes())!=identities[state]:
            raise ValueError('Shotcut adapter content does not match its managed state; existing files were preserved')
    return state,manifest

def verify_bridge(bridge, *, patches=PATCHES):
    state,manifest=inspect_bridge(bridge,patches=patches)
    if state!='patched':
        raise ValueError('Shotcut adapter still patch is missing; run scripts/bootstrap.py --shotcut-only')
    return manifest

def _replace(path, data, mode):
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)

def prepare_bridge(bridge, *, patches=PATCHES):
    """Upgrade only an exact recognized state without resetting a checkout/index."""
    bridge=Path(bridge)
    state,manifest=inspect_bridge(bridge,patches=patches)
    if state=='patched': return 'already patched'
    originals={name:(bridge/name).read_bytes() for name in manifest['files']}
    modes={name:stat.S_IMODE((bridge/name).stat().st_mode) for name in originals}
    updated={}
    with tempfile.TemporaryDirectory(prefix='filmeditor-shotcut-patch-') as directory:
        stage=Path(directory)
        for name in originals:
            path=stage/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(_git(bridge,'show','HEAD:'+name))
        subprocess.run(['git','apply','--check',str(patches/manifest['patch'])],cwd=stage,check=True,capture_output=True)
        subprocess.run(['git','apply',str(patches/manifest['patch'])],cwd=stage,check=True,capture_output=True)
        for name in originals:
            updated[name]=(stage/name).read_bytes()
            if _digest(updated[name])!=manifest['files'][name]['patched']:
                raise ValueError('Managed Shotcut patch produced unexpected content; existing files were preserved')
    if inspect_bridge(bridge,patches=patches)[0]!=state:
        raise ValueError('Shotcut adapter changed during preparation; existing files were preserved')
    published=[]
    try:
        for name in originals:
            if (bridge/name).read_bytes()!=originals[name]:
                raise ValueError('Shotcut adapter changed during preparation')
            _replace(bridge/name,updated[name],modes[name]);published.append(name)
        verify_bridge(bridge,patches=patches)
    except Exception:
        for name in published:
            if (bridge/name).read_bytes()==updated[name]:
                _replace(bridge/name,originals[name],modes[name])
        raise
    return 'applied patch' if state=='clean' else 'upgraded preserved still fix'

def verify_bundle_bridge(bridge, manifest):
    """Verify copied runtime files; dependency tests are excluded from the app."""
    files={}
    for name,identities in manifest['files'].items():
        if name.startswith('tests/'): continue
        if _digest((Path(bridge)/name).read_bytes())!=identities['patched']:
            raise ValueError('Bundled Shotcut adapter does not match the verified managed patch: '+name)
        files[name]=identities['patched']
    return {'commit':manifest['commit'],'patch_sha256':manifest['patch_sha256'],'files':files}
