#!/usr/bin/env python3
"""Collect a local Mac build inventory without installing software or reading credentials.

Writes only the requested JSON report. Does not collect serial numbers, hardware UUIDs,
usernames, account email, environment contents, Codex tokens, or footage metadata.
Executable paths can contain a home-directory name; keep the report private.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def output(args: list[str], timeout: int = 15) -> str | None:
    """Run one bounded, read-only diagnostic without a shell."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, check=False)
        return p.stdout.strip()[:8192] if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def sysctl(key: str) -> str | None:
    return output(['/usr/sbin/sysctl', '-n', key])


def as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def locate(name: str, candidates: list[Path] | None = None) -> str | None:
    installed = shutil.which(name)
    if installed:
        return str(Path(installed).resolve())
    for p in candidates or []:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
    return None


def tool(path: str | None, args: list[str]) -> dict:
    if not path:
        return {'found': False}
    version = output([path, *args])
    return {'found': True, 'path': path,
            'version': version.splitlines()[0] if version else None,
            'binary_info': output(['/usr/bin/file', '-b', path])}


def storage(path: Path) -> dict:
    path = path.expanduser().resolve()
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        usage = shutil.disk_usage(parent)
        return {'path': str(path), 'existing_parent': str(parent),
                'free_bytes': usage.free, 'total_bytes': usage.total}
    except OSError as exc:
        return {'path': str(path), 'error': type(exc).__name__}


def collect(workspace: Path) -> dict:
    if sys.platform != 'darwin':
        raise RuntimeError('Run this inventory on the target Mac, not a Linux/cloud container.')
    home = Path.home()
    bundles = [Path('/Applications/Codex.app'), home / 'Applications/Codex.app']
    codex_candidates = [b / 'Contents/Resources/codex' for b in bundles]
    paths = {
        'python': sys.executable,
        'node': locate('node'),
        'npm': locate('npm'),
        'git': locate('git'),
        'ffmpeg': locate('ffmpeg'),
        'ffprobe': locate('ffprobe'),
        'codex': locate('codex', codex_candidates),
        'brew': locate('brew', [Path('/opt/homebrew/bin/brew'), Path('/usr/local/bin/brew')]),
    }
    tools = {name: tool(path, ['--version'] if name != 'ffmpeg' and name != 'ffprobe'
                        else ['-version']) for name, path in paths.items()}
    if paths['node']:
        tools['node']['process_arch'] = output([paths['node'], '-p', 'process.arch'])
    if paths['brew']:
        tools['brew']['prefix'] = output([paths['brew'], '--prefix'])
    shotcut_bundles = [Path('/Applications/Shotcut.app'), home / 'Applications/Shotcut.app']
    for bundle in shotcut_bundles:
        if bundle.is_dir():
            import plistlib
            try:
                with (bundle / 'Contents/Info.plist').open('rb') as f:
                    data = plistlib.load(f)
                tools['shotcut'] = {'found': True, 'path': str(bundle),
                    'version': data.get('CFBundleShortVersionString'),
                    'minimum_macos': data.get('LSMinimumSystemVersion')}
            except (OSError, ValueError):
                tools['shotcut'] = {'found': True, 'path': str(bundle), 'version': None}
            break
    tools.setdefault('shotcut', {'found': False})
    memory = as_int(sysctl('hw.memsize'))
    translated = sysctl('sysctl.proc_translated')
    return {
        'schema_version': 1,
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'inventory_only_not_acceptance',
        'hardware': {'model_identifier': sysctl('hw.model'),
            'chip_or_cpu': sysctl('machdep.cpu.brand_string'),
            'process_architecture': platform.machine(),
            'arm64_capable': sysctl('hw.optional.arm64'),
            'rosetta_translated': None if translated is None else translated == '1',
            'memory_bytes': memory,
            'memory_gib': round(memory / 1024**3, 2) if memory else None,
            'logical_cpus': as_int(sysctl('hw.logicalcpu')),
            'physical_cpus': as_int(sysctl('hw.physicalcpu'))},
        'os': {'product_version': output(['/usr/bin/sw_vers', '-productVersion']),
               'build_version': output(['/usr/bin/sw_vers', '-buildVersion'])},
        'storage': {'source': storage(ROOT), 'workspace': storage(workspace)},
        'tools': tools,
        'xcode_command_line_tools': output(['/usr/bin/xcode-select', '-p']),
        'notes': ['No model inference, login, installs, or media scans performed.',
                  'Missing optional diagnostics are null, not guessed.',
                  'GPU/filter/codec usability requires actual rendering tests.',
                  'Do not publish this machine-local report.'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.home() / 'Movies/FilmEditor')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/mac-audit.json')
    args = parser.parse_args()
    try:
        data = collect(args.workspace)
        target = args.output.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.mac-audit-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, indent=2)
                stream.write('\n')
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        print(f'Mac inventory saved to {target}. This is not a rendering or authentication test.')
        return 0
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
