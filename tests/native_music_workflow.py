"""Opt-in LIVE music edit through an installed FilmEditor WKWebView.

Consumes normal ChatGPT-authenticated Codex allowance and uses only explicitly
supplied files. Sources are copied, then imported through the native upload IPC;
this does not prove Photos delivery or an NSOpenPanel interaction. All evidence,
copied media and renders remain in --output-dir (outside this checkout).
This test embeds at most 128 MiB of combined source bytes in a private smoke
script, then reconstructs one File at a time for the unchanged native upload
bridge. Encoded bytes are never placed in its manifest or diagnostic reports.

Example (with the configured Python 3.10+ environment):
  python -E tests/native_music_workflow.py --app ~/Applications/FilmEditor.app \
    --video /absolute/clip1.mov --video /absolute/clip2.mov \
    --video /absolute/clip3.mov --song /absolute/authorized-song.mp3 \
    --output-dir /private/tmp/filmeditor-music-run --timeout 1800

Resume reads evidence and rechecks native playback; it NEVER imports again or
submits another Codex turn:
  python -E tests/native_music_workflow.py --resume /private/tmp/filmeditor-music-run

The caller owns media authorization, aesthetic review and eventual cleanup.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tests/native_music_workflow.js'
VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi', '.mts', '.mxf'}
AUDIO_SUFFIXES = {'.wav', '.mp3', '.m4a', '.flac', '.aac', '.ogg', '.opus', '.aif', '.aiff', '.wma', '.alac'}
# Test-only embedding transport, not the editor's general import limit.
MAX_EMBEDDED_INPUT_BYTES = 128 * 1024**2


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        # The exact brief is an authoritative private input, not a diagnostic.
        json.dump(value if path.name == 'manifest.json' else clean(value), stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def clean(value):
    """Retain evidence without image/base64 payloads, credentials or reasoning."""
    if isinstance(value, dict):
        hidden = {'token', 'authorization', 'api_key', 'apikey', 'reasoning', 'reasoningcontent',
                  'accessToken', 'refreshToken', 'headers', 'url', 'currentSrc'}
        return {k: clean(v) for k, v in value.items()
                if k.lower().replace('_', '') not in {s.lower().replace('_', '') for s in hidden}
                and not (value.get('type') in ('image', 'audio') and k == 'data')}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str):
        # Codex thread/read may persist a rich MCP result as serialized JSON
        # inside a text block. Sanitize that structure before any truncation.
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list)):
            return json.dumps(clean(decoded), ensure_ascii=False, separators=(',', ':'))
        # Older retained diagnostics may already have truncated a serialized
        # image's data string, so they cannot be decoded as complete JSON.
        if re.search(r'"type"\s*:\s*"(?:image|audio)"', value):
            value = re.sub(r'("data"\s*:\s*")[A-Za-z0-9+/=\r\n]+(?="|$|\s*\[truncated\])',
                           r'\1[media data omitted]', value)
        value = re.sub(r'([?&](?:sig|token|access_token|refresh_token)=)[^&\s]+', r'\1[redacted]', value, flags=re.I)
        value = re.sub(r'data:[^\s\"\']+', '[media data omitted]', value)
        value = re.sub(r'(?i)bearer\s+[a-z0-9_.-]+', 'Bearer [redacted]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}\b', '[redacted]', value)
        return value[:20000] + (' [truncated]' if len(value) > 20000 else '')
    return value


def ipc(project: Path, route: str, timeout: float = 4):
    """Read-only API requests to the app-owned private Unix socket."""
    connection = json.loads((project / '.film/connection.json').read_text())
    request = {'operation': 'api', 'project': connection['project'],
               'token': connection['token'], 'route': route}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(connection['socket'])
        client.sendall(json.dumps(request).encode() + b'\n')
        with client.makefile('rb') as stream:
            raw = stream.readline(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Native evidence response exceeded bound')
    reply = json.loads(raw)
    if 'error' in reply:
        raise ValueError(reply['error'])
    return reply['result']


class Recorder:
    """Poll incrementally so the backend's 800-event deque cannot erase a turn."""
    def __init__(self, folder: Path, project: Path):
        self.folder, self.project = folder, project
        self.cursor, self.epoch = 0, None
        self.events = []
        existing = folder / 'events.jsonl'
        if existing.exists():
            for line in existing.read_text().splitlines():
                row = json.loads(line)
                self.events.append(row['event'])
                self.cursor, self.epoch = row['event']['id'], row['epoch']
        self.latest = {}
        self.last_details = 0.0

    def capture(self, details=False):
        snapshot = ipc(self.project, '/api/events?after=' + str(self.cursor))
        if self.epoch != snapshot['epoch']:
            # A new native process gives a new epoch but preserves event IDs.
            snapshot = ipc(self.project, '/api/events?after=0')
            self.epoch = snapshot['epoch']
        known = {event['id'] for event in self.events}
        rows = []
        for event in snapshot['events']:
            self.cursor = max(self.cursor, event['id'])
            if event['id'] in known:
                continue
            event = clean(event)
            if event['kind'] == 'activity':
                event['data'].pop('command', None)
            self.events.append(event)
            rows.append({'observed_at': time.time(), 'epoch': self.epoch, 'event': event})
        if rows:
            with (self.folder / 'events.jsonl').open('a') as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                stream.flush()
            os.chmod(self.folder / 'events.jsonl', 0o600)
        users = [e for e in self.events if e['kind'] == 'user']
        creative = [e for e in users if e['data'].get('text', '').startswith('Create and render my film. Creative brief:')]
        setups = [e for e in users if e['data'].get('text', '').startswith('Resolve only the inconclusive technical setup')]
        self.latest.update(observed_at=time.time(), busy=snapshot['busy'],
                           permission_mode=snapshot.get('permission_mode'),
                           approvals=clean(snapshot.get('approvals', [])),
                           event_count=len(self.events), creative_user_events=[e['id'] for e in creative],
                           setup_user_events=[e['id'] for e in setups],
                           phase='creative_turn' if snapshot['busy'] and creative else
                                 'automatic_import_setup' if snapshot['busy'] else 'native_ui_or_idle')
        if details or time.monotonic() - self.last_details > 8:
            self.latest['editor'] = clean(ipc(self.project, '/api/editor', timeout=10))
            self.latest['renders'] = clean(ipc(self.project, '/api/renders', timeout=10))
            self.latest['assets'] = clean(ipc(self.project, '/api/state', timeout=10).get('assets', []))
            self.last_details = time.monotonic()
        write_json(self.folder / 'checkpoint.json', self.latest)


def launch(app: Path, workspace: Path, folder: Path, config: dict,
           project_id=None, recorder=None) -> dict:
    phase = config['mode']
    script = folder / (phase + '-smoke.js')
    smoke_config = dict(config)
    if phase == 'workflow':
        if sum(source['bytes'] for source in config['sources']) > MAX_EMBEDDED_INPUT_BYTES:
            raise ValueError('Native test embedding is bounded to 128 MiB of combined inputs')
        embedded = []
        for source in config['sources']:
            path = workspace / 'projects' / project_id / source['staged_path']
            if digest(path) != source['sha256']:
                raise AssertionError('Staged input changed before native upload')
            chunks = []
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(256 * 1024), b''):
                    chunks.append(base64.b64encode(chunk).decode('ascii'))
            embedded.append(dict(source, embedded_chunks=chunks))
        smoke_config['sources'] = embedded
    # WKWebView cannot fetch this app's custom scheme. A private test script
    # carries bounded source bytes; real File objects still use the unchanged
    # FilmDesktop.uploadFiles -> stdio chunk-upload implementation.
    script.write_text('const nativeMusicConfig=' + json.dumps(smoke_config, ensure_ascii=False) + ';\n' + SCRIPT.read_text())
    os.chmod(script, 0o600)
    result = folder / (phase + '-native.json')
    # Retain earlier reports when observe-only resume is repeated.
    if result.exists():
        result = folder / (phase + '-native-' + str(int(time.time())) + '.json')
    command = [str(app / 'Contents/MacOS/FilmEditor'), '--workspace', str(workspace),
               '--smoke-script', str(script), '--smoke-report', str(result), '--smoke-exit']
    if project_id:
        command += ['--project', project_id]
    timeout = config['timeout_ms'] / 1000 + 45
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    write_json(folder / 'process.json', {'pid': process.pid, 'phase': phase,
                                        'started_at': time.time(), 'deadline_seconds': timeout})
    deadline = time.monotonic() + timeout
    last_progress, last_notice = '', 0.0
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError('Native workflow exceeded bounded timeout; resume is observe-only')
            if recorder:
                try:
                    recorder.capture()
                    progress = recorder.latest['phase'] + ' (' + str(len(recorder.events)) + ' events)'
                    if progress != last_progress or time.monotonic() - last_notice > 30:
                        print(progress, flush=True)
                        last_progress, last_notice = progress, time.monotonic()
                except (OSError, ValueError, KeyError) as exc:
                    write_json(folder / 'collector-status.json', {'last_error': clean(str(exc)), 'at': time.time()})
            time.sleep(1)
        if not result.is_file():
            raise RuntimeError('Native process exited without its smoke report; retained logs are in workspace/.film')
        value = json.loads(result.read_text())
        value['report_path'] = str(result)
        value['native_process_exit_code'] = process.returncode
        return value
    finally:
        if process.poll() is None:
            process.terminate()  # Stop only this exact owned native process.
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def codex_evidence(project: Path, executable: Path | None, folder: Path) -> dict:
    """Read a persisted thread without turn/start, resume, reset or credentials."""
    thread_id = json.loads((project / 'project.json').read_text()).get('thread_id')
    report = {'method': 'thread/read', 'thread_id': thread_id, 'available': False,
              'mcp_calls': [], 'non_mcp_actions': [], 'creative_turn_ids': [], 'setup_turn_ids': []}
    if not thread_id or not executable:
        report['limitation'] = 'No persisted thread ID or configured Codex executable'
        write_json(folder / 'codex-tool-evidence.json', report)
        return report
    sys.path.insert(0, str(ROOT))
    from film.rpc import JsonlProcess
    env = os.environ.copy()
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
        env.pop(key, None)
    child = None
    try:
        child = JsonlProcess([str(executable), '-c', 'forced_login_method="chatgpt"', 'app-server'],
                             cwd=project, env=env, jsonrpc=False)
        child.request('initialize', {'clientInfo': {'name': 'filmeditor-evidence', 'version': '1'},
                                     'capabilities': {'experimentalApi': True}}, timeout=20)
        child.notify('initialized')
        thread = child.request('thread/read', {'threadId': thread_id, 'includeTurns': True}, timeout=45)['thread']
        for turn in thread.get('turns', []):
            for item in turn.get('items', []):
                if item.get('type') == 'userMessage':
                    content = item.get('content', [])
                    texts = [c.get('text', '') for c in content if isinstance(c, dict)] if isinstance(content, list) else []
                    if any('Create and render my film. Creative brief:' in text for text in texts):
                        report['creative_turn_ids'].append(turn.get('id'))
                    if any('Resolve only the inconclusive technical setup' in text for text in texts):
                        report['setup_turn_ids'].append(turn.get('id'))
                if item.get('type') in ('commandExecution', 'fileChange'):
                    report['non_mcp_actions'].append({'id': item.get('id'), 'type': item.get('type'),
                                                      'status': item.get('status'), 'turn_id': turn.get('id'),
                                                      'command': clean(item.get('command')),
                                                      'changed_paths': [change.get('path') for change in item.get('changes', [])
                                                                        if isinstance(change, dict)]})
                if item.get('type') != 'mcpToolCall':
                    continue
                record = {key: clean(item.get(key)) for key in ('id', 'server', 'tool', 'status')}
                # Only FilmEditor MCP payloads are relevant. Never retain reasoning,
                # raw command output, credentials or image/audio base64.
                if 'filmeditor' in str(item.get('server', '')).lower():
                    record.update(arguments=clean(item.get('arguments')), result=clean(item.get('result')),
                                  error=clean(item.get('error')))
                record['turn_id'] = turn.get('id')
                record['call_order'] = len(report['mcp_calls'])
                report['mcp_calls'].append(record)
        report['available'] = True
        report['turn_statuses'] = [{'id': t.get('id'), 'status': t.get('status')} for t in thread.get('turns', [])]
        calls = report['mcp_calls']
        report['failed_tools'] = []
        for index, call in enumerate(calls):
            result_error = any(result.get('isError') for result, _ in result_objects(call.get('result')))
            if call.get('status') == 'failed' or call.get('error') or result_error:
                later = any(c.get('tool') == call.get('tool') and c.get('status') == 'completed' and not c.get('error')
                            and not any(result.get('isError') for result, _ in result_objects(c.get('result')))
                            for c in calls[index + 1:])
                report['failed_tools'].append({'id': call.get('id'), 'tool': call.get('tool'), 'error': call.get('error'),
                                               'result_is_error': result_error,
                                               'classification': 'same tool completed later; inspect arguments to establish recovery' if later
                                               else 'no later completion of this tool; final artifact checks are separate'})
    except Exception as exc:
        report['limitation'] = clean(str(exc))
    finally:
        if child:
            child.close()
    write_json(folder / 'codex-tool-evidence.json', report)
    return report


def object_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def result_objects(value, depth=0):
    """Read nested persisted result objects, without assuming model delivery."""
    if depth > 12:
        return
    result = object_value(value)
    if not result:
        return
    yield result, depth
    if isinstance(result.get('structuredContent'), dict):
        yield from result_objects(result['structuredContent'], depth + 1)
    for block in result.get('content') or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            yield from result_objects(block.get('text'), depth + 1)


def source_review_evidence(evidence: dict, project: Path, sources: list) -> dict:
    """Link actual completed frame extraction results to actual image-view calls."""
    state = json.loads((project / 'project.json').read_text())
    names = {s['name'] for s in sources if s['kind'] == 'footage'}
    movies = {a['id']: a['name'] for a in state['assets'] if a['name'] in names}
    source_hashes = {s['name']: s.get('sha256') for s in sources if s['kind'] == 'footage'}
    movie_hashes = {a['id']: {'source_sha256': source_hashes[a['name']], 'registered_sha256': a.get('sha256'),
                             'source_hash_verified': bool(source_hashes[a['name']]) and a.get('sha256') == source_hashes[a['name']]}
                    for a in state['assets'] if a['name'] in names}
    same_thread = bool(evidence.get('thread_id')) and evidence['thread_id'] == state.get('thread_id')
    song_names = {s['name'] for s in sources if s['kind'] == 'audio'}
    songs = {a['id']: a['name'] for a in state['assets'] if a['name'] in song_names}
    active_path = str((project / state['active_project']).resolve())
    frames = {}
    viewed = {}
    picture_edits = []
    creative_ids = set(evidence.get('creative_turn_ids', []))
    setup_ids = set(evidence.get('setup_turn_ids', []))
    def stage(call):
        return 'creative' if call.get('turn_id') in creative_ids else 'technical_setup' if call.get('turn_id') in setup_ids else 'prior_same_thread'
    all_completed = [c for c in evidence.get('mcp_calls', []) if c.get('status') == 'completed' and not c.get('error')
                     and not any(result.get('isError') for result, _ in result_objects(c.get('result')))]
    completed = [c for c in all_completed if c.get('turn_id') in creative_ids]
    for call in all_completed:
        args = object_value(call.get('arguments'))
        if call.get('tool') == 'film_frames' and args.get('asset_id') in movies:
            for value, _ in result_objects(call.get('result')):
                for path in value.get('frames', []):
                    if isinstance(path, str):
                        frames.setdefault(path, {'asset_id': args['asset_id'], 'call_order': call['call_order'],
                                                 'requested_seconds': args.get('seconds'), 'extraction_turn_id': call.get('turn_id'),
                                                 'extraction_stage': stage(call)})
        if call.get('tool') == 'film_view_image' and isinstance(args.get('path'), str):
            images = [{'storage_depth': depth, 'mime_type': block.get('mimeType')}
                      for result, depth in result_objects(call.get('result')) for block in result.get('content') or []
                      if isinstance(block, dict) and block.get('type') == 'image']
            if images:
                viewed.setdefault(args['path'], []).append({'call_order': call['call_order'],
                    'turn_id': call.get('turn_id'), 'stage': stage(call), 'persisted_image_blocks': images,
                    'top_level_typed_image': any(image['storage_depth'] == 0 for image in images),
                    'model_image_delivery_verified': False})
        if call in completed and call.get('tool') == 'edit_project' and args.get('project_path') == active_path:
            operations = args.get('operations', [])
            if any(op.get('op') in ('add_clip', 'trim_item', 'move_item', 'set_item_timing', 'replace_item_media')
                   for op in operations if isinstance(op, dict)):
                picture_edits.append({'call_order': call['call_order'], 'expected_revision': args.get('expected_revision'),
                                      'operations': operations, 'result': call.get('result')})
    first_picture_edit = min((c['call_order'] for c in picture_edits), default=float('inf'))
    def eligible_views(path, info):
        # A setup view in the same persisted chat may inform shot selection.
        # The imported bytes must still match the exact manifest source hash.
        return [v for v in viewed.get(path, []) if info['call_order'] < v['call_order'] < first_picture_edit
                and (v['stage'] == 'creative' or same_thread) and movie_hashes[info['asset_id']]['source_hash_verified']]
    coverage = [{'asset_id': ident, 'name': movies[ident], **movie_hashes[ident],
                 'viewed_frame_paths': sorted(p for p, info in frames.items() if info['asset_id'] == ident and eligible_views(p, info)),
                 'frame_calls': [{'path': p, **info, 'image_view_calls': viewed.get(p, []),
                                  'eligible_selection_views': eligible_views(p, info)} for p, info in frames.items()
                                 if info['asset_id'] == ident]} for ident in movies]
    analyzed = [c for c in completed if c.get('tool') == 'film_music_analyze' and object_value(c.get('arguments')).get('asset_id') in songs]
    scored = [c for c in completed if c.get('tool') == 'film_music_score' and object_value(c.get('arguments')).get('asset_id') in songs
              and object_value(c.get('arguments')).get('project_path') == active_path]
    return {'distinct_imported_movies_with_actual_view_calls': sum(bool(c['viewed_frame_paths']) for c in coverage),
            'coverage': coverage, 'film_music_analyze_completed': bool(analyzed),
            'film_music_score_completed': bool(scored), 'requested_song_assets': songs,
            'song_score_calls': [{'arguments': c.get('arguments'), 'result': c.get('result'), 'call_order': c['call_order']} for c in scored],
            'edit_project_completed': bool(picture_edits), 'picture_edit_calls': picture_edits,
            'creative_turn_ids': sorted(creative_ids), 'setup_turn_ids': sorted(setup_ids),
            'same_persisted_thread': same_thread,
            'source_review_policy': 'Successful source images viewed before first creative picture edit; same-thread setup context may be reused when source hashes match',
            'image_delivery_limit': 'thread/read storage nesting alone does not prove model-facing image delivery; verify exact-thread response items separately',
            'non_mcp_creative_actions': [a for a in evidence.get('non_mcp_actions', []) if a['turn_id'] in creative_ids]}


def mlt_evidence(project: Path) -> dict:
    state = json.loads((project / 'project.json').read_text())
    path = (project / state['active_project']).resolve()
    if not path.is_relative_to(project) or path.suffix != '.mlt':
        raise ValueError('Active MLT escaped disposable project')
    tree = ET.parse(path)
    resources = []
    for node in tree.findall('producer') + tree.findall('chain'):
        props = {p.get('name'): p.text for p in node.findall('property')}
        if props.get('resource'):
            resources.append({'id': node.get('id'), 'resource': props['resource'],
                              'audio_index': props.get('audio_index'), 'video_index': props.get('video_index')})
    markers = []
    for container in tree.findall('.//properties[@name="shotcut:markers"]'):
        for marker in container.findall('properties'):
            markers.append({p.get('name'): p.text for p in marker.findall('property')})
    return {'path': str(path), 'sha256': digest(path), 'resources': resources,
            'markers': markers, 'playlists': [{'id': p.get('id'),
             'entries': [dict(e.attrib) for e in p if e.tag in ('entry', 'blank')]} for p in tree.findall('playlist')]}


def runtime_paths(app: Path) -> dict:
    runtime = app / 'Contents/Resources/runtime.json'
    if not runtime.is_file():
        return {}
    value = json.loads(runtime.read_text()).get('environment', {})
    return {key: Path(path.replace('@resources/', str(app / 'Contents/Resources') + '/'))
            for key, path in value.items() if key in ('FILM_CODEX', 'FILM_FFMPEG', 'FILM_FFPROBE')}


def verify_outputs(native: dict, project: Path, runtime: dict, timeout: int) -> list:
    results = []
    snapshot = native['final_snapshot']
    profile = snapshot['profile']
    expected_frames = int(snapshot['duration_frames'])
    rate = Fraction(int(profile['frame_rate_num']), int(profile['frame_rate_den']))
    for label in ('preview', 'export'):
        job = native.get(label)
        if not job:
            raise AssertionError('AI did not complete ' + label)
        path = Path(job['output_path']).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            raise AssertionError('Completed output missing or escaped project: ' + label)
        result = {'kind': label, 'job_id': job['job_id'], 'path': str(path), 'sha256': digest(path),
                  'bytes': path.stat().st_size, 'project_revision': job.get('project_revision')}
        ffmpeg, ffprobe = runtime.get('FILM_FFMPEG'), runtime.get('FILM_FFPROBE')
        if not ffmpeg or not ffprobe:
            raise AssertionError('Installed runtime does not declare FFmpeg and FFprobe')
        decoded = subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
                                  '-i', str(path), '-f', 'null', '-'], capture_output=True, timeout=timeout)
        result['full_decode_ok'] = decoded.returncode == 0
        result['decode_error'] = clean(decoded.stderr.decode(errors='replace')[-4000:])
        probe = json.loads(subprocess.check_output([str(ffprobe), '-v', 'error', '-count_frames',
                                                   '-show_streams', '-show_format', '-of', 'json', str(path)], timeout=timeout))
        result['streams'] = [{key: s[key] for key in ('index', 'codec_type', 'codec_name', 'width', 'height',
                                                     'sample_rate', 'channels', 'nb_read_frames', 'duration',
                                                     'avg_frame_rate', 'r_frame_rate') if key in s}
                             for s in probe.get('streams', [])]
        result['duration_seconds'] = float(probe.get('format', {}).get('duration', 0))
        if not result['full_decode_ok'] or not any(s.get('codec_type') == 'audio' for s in result['streams']):
            raise AssertionError('Decode or audio-stream verification failed for ' + label)
        if label == 'export' and not any(s.get('codec_type') == 'video' and s.get('codec_name') == 'h264' for s in result['streams']):
            raise AssertionError('Final export is not H.264 video')
        video = next(s for s in result['streams'] if s.get('codec_type') == 'video')
        if label == 'export' or job.get('project_revision') == snapshot['revision']:
            if int(video.get('nb_read_frames', -1)) != expected_frames:
                raise AssertionError(label + ' frame count does not cover the complete saved sequence')
            if (int(video['width']), int(video['height'])) != (int(profile['width']), int(profile['height'])):
                raise AssertionError(label + ' dimensions differ from the saved sequence')
            if Fraction(video.get('avg_frame_rate', '0/1')) != rate:
                raise AssertionError(label + ' frame rate differs from the saved sequence')
            if abs(float(video.get('duration', 0)) - expected_frames / float(rate)) > 1 / float(rate):
                raise AssertionError(label + ' duration differs from the complete saved sequence')
            result['exact_sequence_frames_dimensions_and_rate'] = True
        elif not 24 - 1 / float(rate) <= float(video.get('duration', 0)) <= 30 + 1 / float(rate):
            raise AssertionError('Separate earlier preview is not a complete bounded first cut')
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app', type=Path, default=Path.home() / 'Applications/FilmEditor.app')
    parser.add_argument('--video', type=Path, action='append', default=[])
    parser.add_argument('--song', type=Path)
    parser.add_argument('--output-dir', type=Path, help='New retained evidence directory, outside the source checkout')
    parser.add_argument('--resume', type=Path, help='Observe-only continuation of a retained run; never starts a model turn')
    parser.add_argument('--model', help='Optional exact ID from the signed-in account model list')
    parser.add_argument('--effort', help='Optional effort supported by the selected account model')
    parser.add_argument('--codex', type=Path, help='Optional installed Codex CLI override for read-only thread evidence')
    parser.add_argument('--brief-file', type=Path, help='Optional creative brief; the harness appends its unique final export path')
    parser.add_argument('--timeout', type=int, default=1800, help='Bounded native workflow seconds (60–3600); default allows a 15-minute model turn')
    parser.add_argument('--decode-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600 or not 10 <= args.decode_timeout <= 600:
        parser.error('Timeouts are outside their bounds')
    if args.resume and (args.output_dir or args.video or args.song or args.brief_file or args.model or args.effort):
        parser.error('--resume is observe-only; do not supply new inputs, models or briefs')
    folder = (args.resume or args.output_dir)
    if not folder:
        parser.error('Specify --output-dir for a new run or --resume for retained evidence')
    folder = folder.expanduser().resolve()
    if folder == ROOT or folder.is_relative_to(ROOT):
        parser.error('Keep media and evidence outside the source checkout')
    if not args.resume and folder.exists() and any(folder.iterdir()):
        parser.error('New runs require an empty output directory; use --resume for existing evidence')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (folder / 'harness.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('A runner already owns this evidence directory; follow checkpoint.json')
    report = {'passed': False, 'evidence': 'installed native WKWebView, actual provided media, live authenticated Codex',
              'photos_provenance': False, 'retained_debug': True, 'output_dir': str(folder),
            'musical_alignment_and_aesthetic_review': 'Independent review still required; beat markers are estimates',
            'full_workflow_passed': False, 'full_workflow_status': 'pending independent musical and visual review',
            'acceptance_scope': 'native execution, source inspection, AI authoring and exact output validation'}
    manifest_path = folder / 'manifest.json'
    recorder, native = None, None
    try:
        if args.resume:
            stamp = str(time.time_ns())
            for name in ('acceptance.json', 'codex-tool-evidence.json', 'saved-mlt-evidence.json', 'checkpoint.json'):
                previous = folder / name
                if previous.exists():
                    write_json(folder / (previous.stem + '-before-resume-' + stamp + previous.suffix), json.loads(previous.read_text()))
            manifest = json.loads(manifest_path.read_text())
            app = Path(manifest['app'])
            report['resume_of_phase'] = manifest.get('phase')
            if not manifest.get('project_id'):
                raise ValueError('No project was created; resume cannot create or submit a turn')
        else:
            app = args.app.expanduser().resolve()
            videos = [p.expanduser().resolve() for p in args.video]
            song = args.song.expanduser().resolve() if args.song else None
            if len(set(videos)) < 3 or not song:
                raise ValueError('Supply at least three distinct --video paths and one authorized --song')
            if any(p.suffix.lower() not in VIDEO_SUFFIXES for p in videos) or song.suffix.lower() not in AUDIO_SUFFIXES:
                raise ValueError('Explicit movie and song paths must use supported media suffixes')
            sources = []
            for index, path in enumerate([*videos, song]):
                if not path.is_file() or not 0 < path.stat().st_size <= MAX_EMBEDDED_INPUT_BYTES:
                    raise ValueError('Each supplied source must be readable; this test embedding supports up to 128 MiB combined')
                sources.append({'source_path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size,
                                'name': f'{index + 1:02d}-' + path.name,
                                'kind': 'footage' if index < len(videos) else 'audio',
                                'staged_path': f'references/native-music-input-{index + 1:02d}' + path.suffix.lower()})
            if sum(source['bytes'] for source in sources) > MAX_EMBEDDED_INPUT_BYTES:
                raise ValueError('This test embedding supports up to 128 MiB combined; this is not the product import limit')
            if len({s['sha256'] for s in sources[:-1]}) < 3:
                raise ValueError('Use three distinct movie contents, not duplicate source copies')
            ident = uuid.uuid4().hex
            final_relative = 'exports/native-music-final-' + ident[:8] + '.mp4'
            brief = args.brief_file.read_text() if args.brief_file else (
                'Create a 24–30 second portrait Sedona travel reel led by my imported song. '
                'The requested editorial output is 720×1280, square pixels, at 30 fps; this specifies the output and does not assert an unknown original SAR. '
                'Inspect representative moments in the supplied movies and analyze the song before choosing your edit. '
                'Use at least three distinct supplied movies and at least five picture cuts. Choose the excerpts and order yourself; '
                'land most cuts around estimated beats or clear musical phrase changes. Lower or mute camera sound so the song leads. '
                'Titles are optional. Render a separate H.264 video preview, inspect representative rendered frames, '
                'and correct any framing, black frames, timing or audio problems before the final export. '
                'Use only the provided media and FilmEditor tools, preserve originals, and complete this bounded first cut.')
            brief += '\nSave the completed final H.264 file at ' + final_relative + '; use a different path for its video preview.'
            if not 1 <= len(brief) <= 36000:
                raise ValueError('Creative brief must contain 1–36,000 characters')
            manifest = {'run_id': ident, 'app': str(app), 'created_at': time.time(), 'phase': 'prepared',
                        'sources': sources, 'brief': brief, 'final_relative': final_relative,
                        'model': args.model, 'effort': args.effort,
                        'source_script_sha256': digest(SCRIPT), 'timeout_seconds': args.timeout}
            write_json(manifest_path, manifest)
        binary = app / 'Contents/MacOS/FilmEditor'
        if not binary.is_file():
            raise ValueError('Installed FilmEditor executable not found')
        report['app_binary_sha256'] = digest(binary)
        plist = app / 'Contents/Info.plist'
        if plist.is_file():
            with plist.open('rb') as stream:
                info = plistlib.load(stream)
            report['app_version'] = {key: info.get(key) for key in ('CFBundleShortVersionString', 'CFBundleVersion')}
        workspace = folder / 'workspace'
        if list(workspace.rglob('connection.json')):
            raise ValueError('This disposable workspace is already open; follow its existing checkpoint and do not launch a duplicate app')
        if not args.resume:
            created = launch(app, workspace, folder, {'mode': 'create', 'run_id': manifest['run_id'],
                             'project_name': 'Live music workflow ' + manifest['run_id'][:8], 'timeout_ms': 90000})
            report['native_create'] = created
            if not created.get('passed') or created.get('navigationErrors'):
                raise AssertionError('Installed native project creation failed')
            manifest['project_id'] = created['project_id']
            manifest['phase'] = 'created'
            write_json(manifest_path, manifest)
            project = workspace / 'projects' / manifest['project_id']
            for source in manifest['sources']:
                destination = project / source['staged_path']
                shutil.copyfile(source['source_path'], destination)
                os.chmod(destination, 0o600)
                if digest(destination) != source['sha256']:
                    raise AssertionError('Staged source hash mismatch')
            # Once armed, this directory can only be resumed in observe mode.
            # Even a crash before the button click cannot accidentally spend another turn.
            manifest['phase'] = 'creative_submission_armed'
            write_json(manifest_path, manifest)
        project = workspace / 'projects' / manifest['project_id']
        recorder = Recorder(folder, project)
        native = launch(app, workspace, folder,
                        {'mode': 'observe' if args.resume else 'workflow', 'run_id': manifest['run_id'],
                         'sources': manifest['sources'], 'brief': manifest['brief'],
                         'final_relative': manifest['final_relative'], 'model': manifest.get('model'),
                         'effort': manifest.get('effort'), 'timeout_ms': args.timeout * 1000},
                        project_id=manifest['project_id'], recorder=recorder)
        report['native_ui'] = native
        report['project_path'] = str(project)
        report['events'] = {'path': str(folder / 'events.jsonl'), 'count': len(recorder.events),
                            'setup_user_events': recorder.latest.get('setup_user_events'),
                            'creative_user_events': recorder.latest.get('creative_user_events')}
        runtime = runtime_paths(app)
        codex = args.codex.expanduser().resolve() if args.codex else runtime.get('FILM_CODEX')
        report['codex_tool_evidence'] = codex_evidence(project, codex, folder)
        report['source_review'] = source_review_evidence(report['codex_tool_evidence'], project, manifest['sources'])
        report['saved_mlt'] = mlt_evidence(project)
        write_json(folder / 'saved-mlt-evidence.json', report['saved_mlt'])
        if not native.get('passed') or native.get('navigationErrors') or native.get('native_process_exit_code') != 0:
            raise AssertionError('Native music workflow failed; inspect the retained native report and incremental events')
        report['native_structural_passed'] = True
        if len(recorder.latest.get('creative_user_events', [])) != 1:
            raise AssertionError('Expected exactly one creative submission; setup turns are recorded separately')
        if not report['codex_tool_evidence'].get('available'):
            raise AssertionError('Actual Codex thread evidence unavailable; native structure alone does not prove the full creative workflow')
        if report['source_review']['distinct_imported_movies_with_actual_view_calls'] < 3:
            raise AssertionError('Actual source image-view evidence does not cover three distinct imported movies')
        if not all(report['source_review'][key] for key in ('film_music_analyze_completed', 'film_music_score_completed', 'edit_project_completed')):
            raise AssertionError('Actual tool evidence does not prove music analysis, score placement and revision-safe authoring')
        report['non_mcp_actions_require_review'] = bool(report['source_review']['non_mcp_creative_actions'])
        if report['saved_mlt']['sha256'] != native['final_revision']:
            raise AssertionError('Final saved MLT differs from the revision played/exported by the native app')
        report['outputs'] = verify_outputs(native, project, runtime, args.decode_timeout)
        preserved = []
        state = json.loads((project / 'project.json').read_text())
        for source in manifest['sources']:
            source_path = Path(source['source_path'])
            unchanged = digest(source_path) == source['sha256']
            copied = [a for a in state['assets'] if a.get('name') == source['name']]
            imported = [a for a in copied if digest(project / a['path']) == source['sha256']]
            preserved.append({'source_path': source['source_path'], 'sha256': source['sha256'],
                              'original_unchanged': unchanged, 'imported_asset_ids': [a['id'] for a in imported]})
            if not unchanged or len(imported) != 1:
                raise AssertionError('Original or native-imported media verification failed')
        report['source_hashes'] = preserved
        report['backend_shutdown'] = not bool(list(workspace.rglob('connection.json')))
        if not report['backend_shutdown']:
            raise AssertionError('Owned native backend did not shut down')
        report.update(passed=True, native_execution_passed=True, ai_authorship_verified=True,
                      one_creative_turn=True, ai_authored_timeline=True,
                      imported_sources_unchanged=True, exact_completed_export_revision=True)
        manifest['phase'] = 'complete'
        write_json(manifest_path, manifest)
    except Exception as exc:
        report.update(passed=False, full_workflow_passed=False, ai_authored_timeline=False)
        report['error'] = clean(str(exc))
        if recorder:
            report['latest_checkpoint'] = recorder.latest
            report['retained_event_count'] = len(recorder.events)
        # Preserve partial actual thread/tool and MLT evidence even on failure.
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('project_id'):
                project = folder / 'workspace/projects' / manifest['project_id']
                try:
                    runtime = runtime_paths(Path(manifest['app']))
                    codex = args.codex.expanduser().resolve() if args.codex else runtime.get('FILM_CODEX')
                    report['codex_tool_evidence'] = codex_evidence(project, codex, folder)
                    report['saved_mlt'] = mlt_evidence(project)
                except Exception as partial:
                    report['partial_evidence_error'] = clean(str(partial))
    finally:
        write_json(folder / 'acceptance.json', report)
        lock.close()
    print(json.dumps({'passed': report['passed'], 'report': str(folder / 'acceptance.json'),
                      'workspace': str(folder / 'workspace'), 'error': report.get('error')}, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
