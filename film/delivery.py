"""Safely publish completed video renders to a project-owned delivery folder."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import unicodedata
import uuid


VIDEO_SUFFIXES = {
    '.3gp', '.avi', '.m2ts', '.m4v', '.mkv', '.mov', '.mp4', '.mpeg',
    '.mpg', '.mxf', '.ogv', '.ts', '.webm', '.wmv',
}
SUCCESS_STATUSES = {'completed'}
RESERVED_FOLDERS = {
    '.', '..', '.film', 'cache', 'edits', 'exports', 'generated',
    'originals', 'projects', 'references', 'scenes',
    'con', 'prn', 'aux', 'nul', *(f'com{n}' for n in range(1, 10)),
    *(f'lpt{n}' for n in range(1, 10)),
}
_THREAD_LOCK = threading.RLock()
_DIR_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
_FILE_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


class DeliveryError(ValueError):
    """A render could not be published without violating the delivery contract."""


def bind_workspace(ws, library_root, project_id, project_name):
    """Bind trusted library identity and output location to a workspace instance."""
    root = Path(library_root).expanduser().resolve(strict=True)
    try:
        info = root.lstat()
    except OSError as exc:
        raise DeliveryError('Project delivery library is unavailable') from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise DeliveryError('Project delivery library must be a real directory')
    if not isinstance(project_id, str) or not project_id or len(project_id) > 256 or any(ord(c) < 32 for c in project_id):
        raise DeliveryError('Project delivery identity is invalid')
    if not isinstance(project_name, str) or not project_name.strip() or len(project_name) > 200:
        raise DeliveryError('Project delivery name is invalid')
    ws.delivery_library_root = root
    ws.delivery_library_identity = (info.st_dev, info.st_ino)
    ws.delivery_project_id = project_id
    ws.delivery_project_name = project_name.strip()


def export_info(ws):
    """Return the owned delivery folder and verified completed video publications."""
    if getattr(ws, 'delivery_library_root', None) is None:
        return {'available': False, 'delivery_folder': None, 'videos': []}
    try:
        with _locked(ws) as (root_fd, library_meta_fd, workspace_meta_fd):
            registry, folder_record, folder_fd = _ensure_folder(ws, root_fd, library_meta_fd)
            try:
                manifest = _load_workspace_manifest(ws, workspace_meta_fd, folder_record)
                videos = []
                for job_id, item in manifest['jobs'].items():
                    if not isinstance(item, dict) or item.get('state') != 'published':
                        continue
                    if item.get('status') not in SUCCESS_STATUSES:
                        continue
                    filename = item.get('filename')
                    if not _simple_name(filename):
                        continue
                    try:
                        info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
                    except OSError:
                        continue
                    if not _matches_identity(info, item.get('delivery_identity')):
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    videos.append({
                        'job_id': job_id,
                        'status': item['status'],
                        'name': filename,
                        'delivery_path': str(Path(ws.delivery_library_root) / folder_record['folder'] / filename),
                        'size': info.st_size,
                        'published_at': item.get('published_at'),
                    })
                videos.sort(key=lambda item: (item.get('published_at') or 0, item['name']), reverse=True)
                return {
                    'available': True,
                    'delivery_folder': str(Path(ws.delivery_library_root) / folder_record['folder']),
                    'videos': videos,
                }
            finally:
                os.close(folder_fd)
    except (DeliveryError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            'available': False,
            'delivery_folder': None,
            'videos': [],
            'delivery_warning': _warning(exc),
        }


def publish_completed(ws, job_dict):
    """Copy one completed workspace export into its owned folder, preserving render status."""
    job = copy.deepcopy(job_dict) if isinstance(job_dict, dict) else {}
    if job.get('status') not in SUCCESS_STATUSES:
        return job
    try:
        project_id = _binding(ws)[1]
        job_id = job.get('job_id')
        if not isinstance(job_id, str) or not job_id or len(job_id) > 256 or any(ord(c) < 32 for c in job_id):
            raise DeliveryError('Completed render has no usable job ID')
        source_fd, source_rel, source_identity = _open_export(ws, job.get('output_path'))
        try:
            with _locked(ws) as (root_fd, library_meta_fd, workspace_meta_fd):
                _, folder_record, folder_fd = _ensure_folder(ws, root_fd, library_meta_fd)
                try:
                    manifest = _load_workspace_manifest(ws, workspace_meta_fd, folder_record)
                    jobs = manifest['jobs']
                    existing = jobs.get(job_id)
                    source_record = {'path': source_rel, **source_identity}
                    if existing is not None:
                        if not isinstance(existing, dict) or existing.get('source') != source_record:
                            raise DeliveryError('This job ID is already bound to a different export')
                        published = _recover_or_check_existing(
                            ws, root_fd, library_meta_fd, workspace_meta_fd, manifest, folder_record,
                            folder_fd, job_id, existing, job['status'],
                        )
                        job.update(
                            delivery_path=published,
                            delivery_folder=str(Path(ws.delivery_library_root) / folder_record['folder']),
                        )
                        return job

                    filename = _delivery_filename(Path(source_rel).name, job_id)
                    if _name_exists(folder_fd, filename):
                        raise DeliveryError('A file already occupies this delivery name; nothing was overwritten')
                    item = _publish_new(
                        ws, root_fd, workspace_meta_fd, folder_record, folder_fd,
                        manifest, job_id, job['status'], filename, source_fd,
                        source_rel, source_identity,
                    )
                    job.update(
                        delivery_path=str(Path(ws.delivery_library_root) / folder_record['folder'] / filename),
                        delivery_folder=str(Path(ws.delivery_library_root) / folder_record['folder']),
                    )
                    return job
                finally:
                    os.close(folder_fd)
        finally:
            os.close(source_fd)
    except (DeliveryError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        job['delivery_warning'] = _warning(exc)
        return job


def _warning(exc):
    message = str(exc).strip()
    if not message:
        return 'The completed render could not be published.'
    return 'Video delivery unavailable: ' + message[:400]


def _binding(ws):
    root = getattr(ws, 'delivery_library_root', None)
    project_id = getattr(ws, 'delivery_project_id', None)
    project_name = getattr(ws, 'delivery_project_name', None)
    identity = getattr(ws, 'delivery_library_identity', None)
    if root is None or not project_id or not project_name or not identity:
        raise DeliveryError('Project delivery is not configured')
    return Path(root), project_id, project_name, identity


def _open_checked_directory(path):
    try:
        before = os.lstat(path)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise DeliveryError('A delivery directory was replaced or is a symlink')
        fd = os.open(path, _DIR_FLAGS)
        after = os.fstat(fd)
    except OSError as exc:
        raise DeliveryError('A required delivery directory is unavailable') from exc
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise DeliveryError('A delivery directory changed while it was opened')
    return fd, after


def _open_child_directory(parent_fd, name, *, private=False):
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise DeliveryError('A delivery directory was replaced or is a symlink')
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        after = os.fstat(fd)
    except OSError as exc:
        raise DeliveryError('A required delivery directory is unavailable') from exc
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise DeliveryError('A delivery directory changed while it was opened')
    if private:
        os.fchmod(fd, 0o700)
    return fd, after


def _ensure_child_directory(parent_fd, name, *, private=False):
    try:
        os.mkdir(name, 0o700 if private else 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise DeliveryError('A required delivery directory could not be created') from exc
    return _open_child_directory(parent_fd, name, private=private)


@contextmanager
def _locked(ws):
    """Serialize library claims and project manifests across threads and processes."""
    root_path, _, _, expected_identity = _binding(ws)
    workspace_root = Path(ws.root)
    with _THREAD_LOCK:
        root_fd, root_info = _open_checked_directory(root_path)
        workspace_root_fd = None
        library_meta_fd = None
        workspace_meta_fd = None
        lock_fd = None
        try:
            if (root_info.st_dev, root_info.st_ino) != tuple(expected_identity):
                raise DeliveryError('The project library directory was replaced')
            workspace_root_fd, _ = _open_checked_directory(workspace_root)
            library_meta_fd, _ = _ensure_child_directory(root_fd, '.film', private=True)
            workspace_meta_fd, _ = _ensure_child_directory(workspace_root_fd, '.film', private=True)
            lock_fd = os.open(
                'delivery-folders.lock',
                os.O_RDWR | os.O_CREAT | _FILE_NOFOLLOW,
                0o600,
                dir_fd=library_meta_fd,
            )
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode):
                raise DeliveryError('The delivery lock file is invalid')
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield root_fd, library_meta_fd, workspace_meta_fd
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            for fd in (workspace_meta_fd, library_meta_fd, workspace_root_fd, root_fd):
                if fd is not None:
                    os.close(fd)


def _metadata_file(directory_fd, name, default=None):
    try:
        fd = os.open(name, os.O_RDONLY | _FILE_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except OSError as exc:
        raise DeliveryError('Private delivery metadata could not be opened') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise DeliveryError('Private delivery metadata is invalid')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError) as exc:
        raise DeliveryError('Private delivery metadata is unreadable') from exc
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise DeliveryError('Private delivery metadata has an invalid shape')
    return value


def _write_metadata(directory_fd, name, value):
    try:
        existing = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise DeliveryError('Private delivery metadata could not be checked') from exc
    if existing is not None and (not stat.S_ISREG(existing.st_mode) or stat.S_ISLNK(existing.st_mode)):
        raise DeliveryError('Private delivery metadata was replaced')
    temporary = '.delivery-meta-' + uuid.uuid4().hex + '.tmp'
    fd = None
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        os.close(fd)
        fd = None
        # Recheck the target immediately before the atomic replacement.
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and (not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)):
            raise DeliveryError('Private delivery metadata was replaced')
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, DeliveryError):
            raise
        raise DeliveryError('Private delivery metadata could not be saved') from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _folder_registry(meta_fd):
    value = _metadata_file(meta_fd, 'delivery-folders.json', {'version': 1, 'folders': {}})
    if value.get('version') != 1 or not isinstance(value.get('folders'), dict):
        raise DeliveryError('Library delivery ownership metadata is invalid')
    return value


def _folder_record_valid(record):
    return (
        isinstance(record, dict)
        and isinstance(record.get('project_id'), str)
        and _simple_name(record.get('folder'))
        and type(record.get('device')) is int
        and type(record.get('inode')) is int
    )


def _find_project_record(registry, project_id):
    matches = [record for record in registry['folders'].values()
               if isinstance(record, dict) and record.get('project_id') == project_id]
    if len(matches) > 1:
        raise DeliveryError('Library delivery ownership metadata has duplicate project entries')
    return matches[0] if matches else None


def _safe_component(value, *, limit=72):
    normalized = unicodedata.normalize('NFKC', str(value))
    chars = []
    for char in normalized:
        if char.isalnum() or char in ' ._-':
            chars.append(char)
        elif char.isspace():
            chars.append(' ')
        else:
            chars.append('-')
    result = re.sub(r'\s+', ' ', ''.join(chars)).strip(' .-_')
    result = re.sub(r'-{2,}', '-', result)[:limit].strip(' .-_')
    return result or 'Project'


def _project_hash(project_id):
    return hashlib.sha256(project_id.encode('utf-8', 'replace')).hexdigest()[:10]


def _simple_name(value):
    return (isinstance(value, str) and bool(value) and value not in ('.', '..')
            and '/' not in value and '\\' not in value and '\x00' not in value
            and not any(ord(char) < 32 for char in value))


def _open_owned_folder(root_fd, record):
    if not _folder_record_valid(record):
        raise DeliveryError('Project delivery ownership metadata is invalid')
    try:
        fd, info = _open_child_directory(root_fd, record['folder'], private=True)
    except DeliveryError as exc:
        raise DeliveryError('The project delivery folder is missing or was replaced') from exc
    if (info.st_dev, info.st_ino) != (record['device'], record['inode']):
        os.close(fd)
        raise DeliveryError('The project delivery folder was replaced')
    return fd


def _ensure_folder(ws, root_fd, library_meta_fd):
    registry = _folder_registry(library_meta_fd)
    _, project_id, project_name, _ = _binding(ws)
    record = _find_project_record(registry, project_id)
    if record is not None:
        folder_fd = _open_owned_folder(root_fd, record)
        return registry, record, folder_fd

    existing = {name.casefold() for name in os.listdir(root_fd)}
    reserved = _safe_component(project_name).casefold() in RESERVED_FOLDERS
    base = _safe_component(project_name)
    unique = _project_hash(project_id)
    candidate_base = f'{base}-{unique}' if reserved or base.casefold() in existing or base.casefold() in registry['folders'] else base
    for suffix in range(10000):
        candidate = candidate_base if suffix == 0 else f'{candidate_base}-{suffix + 1}'
        key = candidate.casefold()
        if key in existing or key in registry['folders']:
            continue
        try:
            os.mkdir(candidate, 0o700, dir_fd=root_fd)
        except FileExistsError:
            existing.add(key)
            continue
        except OSError as exc:
            raise DeliveryError('Project delivery folder could not be created') from exc
        folder_fd = None
        try:
            folder_fd, info = _open_child_directory(root_fd, candidate, private=True)
            record = {
                'project_id': project_id,
                'project_name': project_name,
                'folder': candidate,
                'device': info.st_dev,
                'inode': info.st_ino,
            }
            registry['folders'][key] = record
            _write_metadata(library_meta_fd, 'delivery-folders.json', registry)
            return registry, record, folder_fd
        except Exception:
            if folder_fd is not None:
                os.close(folder_fd)
            try:
                os.rmdir(candidate, dir_fd=root_fd)
            except OSError:
                pass
            raise
    raise DeliveryError('No safe project delivery folder name is available')


def _load_workspace_manifest(ws, workspace_meta_fd, folder_record):
    project_id = ws.delivery_project_id
    default = {
        'version': 1,
        'project_id': project_id,
        'delivery_folder': folder_record['folder'],
        'delivery_device': folder_record['device'],
        'delivery_inode': folder_record['inode'],
        'jobs': {},
    }
    value = _metadata_file(workspace_meta_fd, 'deliveries.json', default)
    if (value.get('version') != 1 or value.get('project_id') != project_id
            or value.get('delivery_folder') != folder_record['folder']
            or value.get('delivery_device') != folder_record['device']
            or value.get('delivery_inode') != folder_record['inode']
            or not isinstance(value.get('jobs'), dict)):
        raise DeliveryError('Project delivery metadata does not match its folder owner')
    try:
        os.stat('deliveries.json', dir_fd=workspace_meta_fd, follow_symlinks=False)
    except FileNotFoundError:
        _write_metadata(workspace_meta_fd, 'deliveries.json', default)
    return value


def _open_export(ws, output_path):
    if not isinstance(output_path, str) or not output_path or '\x00' in output_path:
        raise DeliveryError('Completed render has no usable output path')
    root = Path(ws.root)
    output = Path(output_path)
    lexical = output if output.is_absolute() else root / output
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise DeliveryError('Only videos inside this project exports folder can be delivered') from exc
    parts = relative.parts
    if (len(parts) < 2 or parts[0] != 'exports'
            or any(part in ('', '.', '..') for part in parts)
            or parts[-1].startswith('.')
            or Path(parts[-1]).suffix.lower() not in VIDEO_SUFFIXES):
        raise DeliveryError('Only regular video files inside exports can be delivered')

    root_fd = None
    current_fd = None
    try:
        root_fd, _ = _open_checked_directory(root)
        current_fd, _ = _open_child_directory(root_fd, 'exports')
        for part in parts[1:-1]:
            next_fd, _ = _open_child_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | _FILE_NOFOLLOW, dir_fd=current_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            os.close(fd)
            raise DeliveryError('The render output is not a nonempty regular video file')
        identity = _identity(info)
        return fd, relative.as_posix(), identity
    except DeliveryError:
        raise
    except OSError as exc:
        raise DeliveryError('The completed render file is missing or unsafe') from exc
    finally:
        if current_fd is not None:
            os.close(current_fd)
        if root_fd is not None:
            os.close(root_fd)


def _identity(info):
    return {
        'device': info.st_dev,
        'inode': info.st_ino,
        'size': info.st_size,
        'mtime_ns': info.st_mtime_ns,
    }


def _matches_identity(info, expected):
    if not isinstance(expected, dict):
        return False
    actual = _identity(info)
    return all(actual.get(key) == expected.get(key) for key in actual)


def _name_exists(directory_fd, name):
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DeliveryError('The delivery filename could not be checked') from exc


def _delivery_filename(source_name, job_id):
    source = Path(source_name)
    suffix = source.suffix.lower()
    stem = _safe_component(source.stem, limit=64)
    job_hash = hashlib.sha256(job_id.encode('utf-8', 'replace')).hexdigest()[:12]
    return f'{stem}-{job_hash}{suffix}'


def _staging_directory(library_meta_fd, delivery_info):
    fd, info = _ensure_child_directory(library_meta_fd, 'delivery-staging', private=True)
    if info.st_dev != delivery_info.st_dev:
        os.close(fd)
        raise DeliveryError('Private staging and delivery folders are on different filesystems')
    return fd


def _copy_to_stage(source_fd, staging_fd, stage_name, source_identity):
    stage_fd = os.open(
        stage_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
        0o600,
        dir_fd=staging_fd,
    )
    try:
        before = os.fstat(source_fd)
        if _identity(before) != source_identity:
            raise DeliveryError('The render output changed before delivery')
        total = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(stage_fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, 'short write while staging video')
                view = view[written:]
        after = os.fstat(source_fd)
        if _identity(after) != source_identity or total != source_identity['size']:
            raise DeliveryError('The render output changed during delivery')
        os.fsync(stage_fd)
        os.fchmod(stage_fd, 0o600)
        return _identity(os.fstat(stage_fd))
    except Exception:
        try:
            os.unlink(stage_name, dir_fd=staging_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(stage_fd)


def _current_folder_identity(root_fd, record):
    try:
        info = os.stat(record['folder'], dir_fd=root_fd, follow_symlinks=False)
    except OSError as exc:
        raise DeliveryError('The project delivery folder changed during publication') from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or (info.st_dev, info.st_ino) != (record['device'], record['inode'])):
        raise DeliveryError('The project delivery folder changed during publication')


def _manifest_has_pending_stage(workspace_meta_fd, job_id, filename, stage_name, stage_identity):
    """Check whether a failed atomic manifest write left this stage referenced."""
    try:
        manifest = _metadata_file(workspace_meta_fd, 'deliveries.json', {})
        jobs = manifest.get('jobs')
        saved = jobs.get(job_id) if isinstance(jobs, dict) else None
        return (
            isinstance(saved, dict)
            and saved.get('state') == 'publishing'
            and saved.get('filename') == filename
            and saved.get('stage_name') == stage_name
            and saved.get('stage_identity') == stage_identity
        )
    except (DeliveryError, OSError, ValueError, TypeError):
        return False


def _remove_private_stage(staging_fd, stage_name):
    if staging_fd is None or stage_name is None:
        return
    try:
        os.unlink(stage_name, dir_fd=staging_fd)
        os.fsync(staging_fd)
    except OSError:
        pass


def _recover_or_check_existing(ws, root_fd, library_meta_fd, workspace_meta_fd, manifest,
                               folder_record, folder_fd, job_id, item, status):
    filename = item.get('filename')
    if not _simple_name(filename):
        raise DeliveryError('Saved delivery metadata has an invalid filename')
    if item.get('state') == 'published':
        try:
            info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
        except OSError as exc:
            raise DeliveryError('The previously delivered video is missing') from exc
        if not stat.S_ISREG(info.st_mode) or not _matches_identity(info, item.get('delivery_identity')):
            raise DeliveryError('The previously delivered video was replaced')
        return str(Path(ws.delivery_library_root) / folder_record['folder'] / filename)
    if item.get('state') != 'publishing':
        raise DeliveryError('Saved delivery metadata has an unknown publication state')

    stage_name = item.get('stage_name')
    stage_identity = item.get('stage_identity')
    if not _simple_name(stage_name) or not isinstance(stage_identity, dict):
        raise DeliveryError('Saved in-progress delivery metadata is invalid')
    staging_fd, _ = _open_child_directory(library_meta_fd, 'delivery-staging', private=True)
    try:
        delivery_identity = None
        try:
            dest_info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
        except FileNotFoundError:
            dest_info = None
        if dest_info is not None:
            if (stat.S_ISREG(dest_info.st_mode)
                    and _matches_identity(dest_info, stage_identity)):
                delivery_identity = _identity(dest_info)
            else:
                raise DeliveryError('A file already occupies this delivery name; nothing was overwritten')
        else:
            try:
                stage_fd = os.open(stage_name, os.O_RDONLY | _FILE_NOFOLLOW, dir_fd=staging_fd)
            except FileNotFoundError:
                manifest['jobs'].pop(job_id, None)
                _write_metadata(workspace_meta_fd, 'deliveries.json', manifest)
                raise DeliveryError('The interrupted private video stage is missing; retry publication')
            try:
                stage_info = os.fstat(stage_fd)
                if not stat.S_ISREG(stage_info.st_mode) or not _matches_identity(stage_info, stage_identity):
                    raise DeliveryError('The interrupted private video stage was replaced')
            finally:
                os.close(stage_fd)
            _current_folder_identity(root_fd, folder_record)
            try:
                os.link(stage_name, filename, src_dir_fd=staging_fd,
                        dst_dir_fd=folder_fd, follow_symlinks=False)
            except FileExistsError:
                dest_info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
                if not stat.S_ISREG(dest_info.st_mode) or not _matches_identity(dest_info, stage_identity):
                    raise DeliveryError('A file already occupies this delivery name; nothing was overwritten')
            dest_info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
            if not stat.S_ISREG(dest_info.st_mode) or not _matches_identity(dest_info, stage_identity):
                raise DeliveryError('The completed video could not be verified after publication')
            delivery_identity = _identity(dest_info)

        os.fsync(folder_fd)
        _current_folder_identity(root_fd, folder_record)
        item.update(state='published', status=status, delivery_identity=delivery_identity,
                    published_at=item.get('published_at') or time.time())
        item.pop('stage_name', None)
        item.pop('stage_identity', None)
        _write_metadata(workspace_meta_fd, 'deliveries.json', manifest)
        try:
            os.unlink(stage_name, dir_fd=staging_fd)
        except FileNotFoundError:
            pass
        return str(Path(ws.delivery_library_root) / folder_record['folder'] / filename)
    finally:
        os.close(staging_fd)


def _publish_new(ws, root_fd, workspace_meta_fd, folder_record, folder_fd,
                 manifest, job_id, status, filename, source_fd, source_rel,
                 source_identity):
    library_meta_fd, _ = _open_child_directory(root_fd, '.film', private=True)
    staging_fd = None
    stage_name = None
    stage_identity = None
    pending_saved = False
    try:
        folder_info = os.fstat(folder_fd)
        staging_fd = _staging_directory(library_meta_fd, folder_info)
        stage_name = f"delivery-{_project_hash(ws.delivery_project_id)}-{hashlib.sha256(job_id.encode('utf-8', 'replace')).hexdigest()[:12]}-{uuid.uuid4().hex}.tmp"
        stage_identity = _copy_to_stage(source_fd, staging_fd, stage_name, source_identity)
        item = {
            'state': 'publishing',
            'status': status,
            'source': {'path': source_rel, **source_identity},
            'filename': filename,
            'stage_name': stage_name,
            'stage_identity': stage_identity,
        }
        manifest['jobs'][job_id] = item
        try:
            _write_metadata(workspace_meta_fd, 'deliveries.json', manifest)
            pending_saved = True
        except Exception:
            # Atomic replace may have completed even if its final fsync failed.
            pending_saved = _manifest_has_pending_stage(
                workspace_meta_fd, job_id, filename, stage_name, stage_identity,
            )
            raise
        _current_folder_identity(root_fd, folder_record)
        try:
            os.link(stage_name, filename, src_dir_fd=staging_fd,
                    dst_dir_fd=folder_fd, follow_symlinks=False)
        except FileExistsError as exc:
            raise DeliveryError('A file already occupies this delivery name; nothing was overwritten') from exc
        os.fsync(folder_fd)
        info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or not _matches_identity(info, stage_identity):
            try:
                os.unlink(filename, dir_fd=folder_fd)
            except OSError:
                pass
            raise DeliveryError('The completed video could not be verified after publication')
        _current_folder_identity(root_fd, folder_record)
        item.update(state='published', delivery_identity=_identity(info), published_at=time.time())
        item.pop('stage_name', None)
        item.pop('stage_identity', None)
        _write_metadata(workspace_meta_fd, 'deliveries.json', manifest)
        try:
            os.unlink(stage_name, dir_fd=staging_fd)
        except OSError:
            pass
        return item
    except Exception:
        # A durable pending entry lets retry recover the same stage and inode.
        # Before that point, the stage has no owner and must not accumulate.
        if not pending_saved:
            pending_saved = _manifest_has_pending_stage(
                workspace_meta_fd, job_id, filename, stage_name, stage_identity,
            )
        if not pending_saved:
            _remove_private_stage(staging_fd, stage_name)
        raise
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        os.close(library_meta_fd)
