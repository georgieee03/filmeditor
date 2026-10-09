"""App-owned desktop sessions over stdio and a private Unix socket; no TCP server."""
from __future__ import annotations
import base64
import concurrent.futures
from contextlib import contextmanager
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import socketserver
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit
from .core import Workspace, atomic
from .codex import Codex
from .editor import Editor
from .library import Library

MAX_MESSAGE = 8 * 1024**2

class DesktopLibrary(Library):
    def session(self, ident):
        with self.lock:
            if not self.entry(ident): raise ValueError('Project not found or deleted')
            if ident not in self.sessions:
                ws=Workspace(self.owner.workspace.root/'projects'/ident)
                ws.heavy_slots=self.owner.workspace.heavy_slots
                ws.render_peers=self.owner.workspace.render_peers
                ws.render_peers.append(ws)
                server=DesktopSession(ws, library=self, project_id=ident)
                self.sessions[ident]=server
                server.publish_connection()
            return self.sessions[ident]

    def open(self, ident):
        self.session(ident)
        return {'url':'filmapp://app/?'+urlencode({'project':ident})}

    def close_session(self, server):
        server.close()
        peers=server.workspace.render_peers
        if server.workspace in peers: peers.remove(server.workspace)

class DesktopSession:
    def __init__(self, workspace, library=None, project_id='legacy'):
        self.workspace=workspace
        self.project_id=project_id
        self.token=secrets.token_urlsafe(32)
        self.origin='filmapp://app'
        self.lifecycle_lock=threading.RLock()
        self.active_operations=0
        self.archived=False
        self.codex=Codex(workspace)
        self.library=library or DesktopLibrary(self)
        self.editor=Editor(self)
        self.uploads={}
        self.upload_lock=threading.RLock()

    @contextmanager
    def operation(self):
        with self.lifecycle_lock:
            if self.archived or self.workspace.stopping: raise ValueError('This project has closed. Return to the project picker.')
            self.active_operations+=1
        try: yield
        finally:
            with self.lifecycle_lock: self.active_operations-=1

    def connection(self):
        owner=self.library.owner
        return {'transport':'unix','socket':str(owner.socket_path),
                'project':self.project_id,'token':self.token,'pid':os.getpid()}

    def publish_connection(self):
        atomic(self.workspace.root/'.film/connection.json',json.dumps(self.connection()).encode())

    def media_url(self, value):
        p=self.workspace.path(value)
        if not p.is_file(): raise ValueError('Media missing')
        rel=str(p.relative_to(self.workspace.root))
        if rel.startswith('.film/') or rel=='project.json': raise ValueError('Private application state is not media')
        expires=int(time.time())+3600
        sig=hmac.new(self.token.encode(),f'{rel}\n{expires}'.encode(),hashlib.sha256).hexdigest()
        return 'filmapp://app/media?'+urlencode({'project':self.project_id,'path':rel,'expires':expires,'sig':sig})

    def resolve_media(self, url):
        route=urlsplit(url)
        if route.scheme!='filmapp' or route.netloc!='app' or route.path!='/media':
            raise PermissionError('Invalid media URL')
        args=parse_qs(route.query)
        if args.get('project',[''])[0]!=self.project_id: raise PermissionError('Media project mismatch')
        rel=args.get('path',[''])[0]
        expires=int(args.get('expires',['0'])[0])
        expected=hmac.new(self.token.encode(),f'{rel}\n{expires}'.encode(),hashlib.sha256).hexdigest()
        if expires<time.time() or not hmac.compare_digest(args.get('sig',[''])[0],expected):
            raise PermissionError('Media URL expired/invalid')
        p=self.workspace.path(rel)
        if not p.is_file() or rel.startswith('.film/') or rel=='project.json':
            raise ValueError('Media missing or private')
        mime=mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
        if mime in ('text/html','image/svg+xml','text/javascript','application/javascript'):
            mime='application/octet-stream'
        return {'path':str(p),'size':p.stat().st_size,'mime':mime}

    def state(self):
        data=self.workspace.state()
        entry=self.library.entry(self.project_id)
        data['project_name']=entry['name'] if entry else 'Existing projects'
        data['library_home']='filmapp://app/'
        data['desktop']=True
        for asset in data['assets']:
            try: asset['url']=self.media_url(asset['path'])
            except ValueError: asset['missing']=True
        data['outputs']=[{'path':p,'url':self.media_url(p)} for p in data['outputs']]
        return data

    def import_paths(self, paths, kind='footage'):
        if not isinstance(paths,list) or not 1<=len(paths)<=200: raise ValueError('Select 1–200 files')
        values=[]
        for value in paths:
            p=Path(value).expanduser().resolve()
            if not p.is_file(): raise ValueError('Import file missing')
            with p.open('rb') as stream:
                values.append(self.workspace.ingest(stream,p.stat().st_size,p.name,kind))
        return values

    def upload(self, operation, body):
        with self.upload_lock:
            return self._upload(operation, body)

    def _upload(self, operation, body):
        if operation=='begin':
            if len(self.uploads)>=8: raise ValueError('Wait for current imports to finish')
            size=body['size']
            if type(size) is not int or not 0<size<=32*1024**3: raise ValueError('Invalid import size')
            from .core import MEDIA
            filename=Path(body['name']).name
            suffix=Path(filename).suffix.lower()
            kind=body.get('kind','footage')
            if suffix not in MEDIA or kind not in ('footage','reference','audio','lut'): raise ValueError('Unsupported import type')
            ident=uuid.uuid4().hex
            path=self.workspace.root/'.film'/('upload-'+ident)
            self.uploads[ident]={'stream':path.open('xb'),'path':path,'size':size,'count':0,'name':filename,'kind':kind}
            with self.lifecycle_lock: self.active_operations+=1  # Keep deletion blocked for the complete upload.
            return {'id':ident}
        item=self.uploads.get(body['id'])
        if not item: raise ValueError('Upload not found')
        try:
            if operation=='chunk':
                if body.get('offset')!=item['count']: raise ValueError('Import chunk is out of sequence')
                raw=base64.b64decode(body['data'],validate=True)
                if len(raw)>256*1024 or item['count']+len(raw)>item['size']: raise ValueError('Invalid import chunk')
                item['stream'].write(raw); item['count']+=len(raw)
                return {'received':item['count']}
            item['stream'].close()
            if operation=='finish':
                if item['count']!=item['size']: raise ValueError('Incomplete import')
                with item['path'].open('rb') as stream:
                    return self.workspace.ingest(stream,item['size'],item['name'],item['kind'])
            if operation!='cancel': raise ValueError('Unknown upload operation')
            return {'cancelled':body['id']}
        finally:
            if operation!='chunk' or sys.exc_info()[0] is not None:
                item['stream'].close(); item['path'].unlink(missing_ok=True); self.uploads.pop(body['id'],None)
                with self.lifecycle_lock: self.active_operations-=1

    def api(self, route, body=None):
        parts=urlsplit(route); args=parse_qs(parts.query); route=parts.path
        ws=self.workspace; codex=self.codex
        if body is None:
            with self.lifecycle_lock:
                if self.archived: raise ValueError('This project was deleted. Return to the project picker.')
            if route=='/api/library': return {'projects':self.library.listing()}
            if route=='/api/state': return self.state()
            if route=='/api/status': return ws.status()
            if route=='/api/tools': return {'tools':ws.tools()}
            if route=='/api/events': return codex.snapshot(int(args.get('after',['0'])[0]))
            if route=='/api/editor': return self.editor.snapshot()
            if route=='/api/renders': return {'jobs':self.editor.renders()}
            raise ValueError('Unknown desktop API route')
        if not isinstance(body,dict): raise ValueError('Expected JSON object')
        tracked=not route.startswith('/api/library/')
        with self.lifecycle_lock:
            if self.archived or self.workspace.stopping: raise ValueError('This project has closed. Return to the project picker.')
            if tracked: self.active_operations+=1
        try:
            if route=='/api/library/create': return self.library.create(**body)
            if route=='/api/library/open': return self.library.open(body['id'])
            if route=='/api/library/delete': return self.library.delete(body['id'])
            if route=='/api/assets/delete':
                if codex.busy: raise ValueError('Wait for Codex to finish before deleting media')
                return ws.delete_asset(body['id'])
            if route=='/api/call': return ws.call(body['name'],body.get('arguments',{}))
            if route=='/api/media-url': return {'url':self.media_url(body['path'])}
            if route=='/api/codex/connect': return codex.models()
            if route=='/api/codex/start': return codex.start(**body)
            if route=='/api/codex/stop': return codex.stop()
            if route=='/api/codex/reset': return codex.reset()
            if route=='/api/codex/approve': return codex.approve(**body)
            if route=='/api/codex/configure': return codex.configure(**body)
            if route=='/api/editor/edit': return self.editor.edit(**body)
            if route=='/api/editor/history': return self.editor.restore(**body)
            if route=='/api/editor/media': return self.editor.media(**body)
            if route=='/api/editor/frame': return self.editor.frame(**body)
            if route=='/api/editor/render': return self.editor.render(**body)
            raise ValueError('Unknown desktop API route')
        finally:
            if tracked:
                with self.lifecycle_lock: self.active_operations-=1

    def close(self):
        with self.lifecycle_lock: self.archived=True
        with self.upload_lock:
            for item in list(self.uploads.values()):
                item['stream'].close(); item['path'].unlink(missing_ok=True)
            with self.lifecycle_lock: self.active_operations-=len(self.uploads)
            self.uploads.clear()
        self.codex.close(); self.workspace.close()
        connection=self.workspace.root/'.film/connection.json'
        try:
            if json.loads(connection.read_text()).get('token')==self.token: connection.unlink()
        except (OSError,ValueError): pass

class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True

class UnixHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3700)
        raw=self.rfile.readline(MAX_MESSAGE+1)
        if len(raw)>MAX_MESSAGE: return
        try:
            message=json.loads(raw)
            value=self.server.desktop.dispatch(message, trusted=False)
            reply={'result':value}
        except Exception as exc: reply={'error':str(exc)}
        try: self.wfile.write(json.dumps(reply,ensure_ascii=False).encode()+b'\n')
        except (BrokenPipeError,ConnectionResetError): pass

class Desktop:
    def __init__(self, path):
        import fcntl
        self.owner=DesktopSession(Workspace(path))
        self.lock_file=(self.owner.workspace.root/'.film/server.lock').open('a')
        try: fcntl.flock(self.lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.owner.close(); self.lock_file.close()
            raise ValueError('This workspace already has a FilmEditor process. Quit the other editor first.')
        self.owner.socket_path=self.owner.workspace.root/'.film/native.sock'
        if len(os.fsencode(self.owner.socket_path))>=104:
            # macOS sockaddr_un paths are bounded. This private directory is user-scoped.
            directory=Path('/tmp')/f'filmeditor-{os.getuid()}'
            directory.mkdir(mode=0o700,exist_ok=True)
            if directory.stat().st_uid!=os.getuid() or directory.is_symlink(): raise ValueError('Unsafe socket directory')
            directory.chmod(0o700)
            digest=hashlib.sha256(str(path).encode()).hexdigest()[:24]
            self.owner.socket_path=directory/(digest+'.sock')
        self.owner.socket_path.unlink(missing_ok=True)
        self.socket=UnixServer(str(self.owner.socket_path),UnixHandler)
        self.socket.desktop=self
        self.owner.socket_path.chmod(0o600)
        self.owner.publish_connection()
        threading.Thread(target=self.socket.serve_forever,daemon=True).start()

    def dispatch(self, message, trusted=False):
        project=message.get('project') or 'legacy'
        session=self.owner if project=='legacy' else self.owner.library.session(project)
        if not trusted and not hmac.compare_digest(str(message.get('token','')),session.token):
            raise PermissionError('Desktop authentication missing')
        operation=message.get('operation','api')
        if operation=='api':
            if message['route'].startswith('/api/library'): return session.api(message['route'],message.get('body'))
            with session.operation(): return session.api(message['route'],message.get('body'))
        if not trusted: raise PermissionError('Native operation requires app transport')
        body=message.get('body') or {}
        if operation=='media':
            args=parse_qs(urlsplit(body['url']).query)
            target=args.get('project',['legacy'])[0]
            source=self.owner if target=='legacy' else self.owner.library.session(target)
            return source.resolve_media(body['url'])
        if operation=='import':
            with session.operation(): return session.import_paths(**body)
        if operation=='upload':
            with session.operation(): return session.upload(body['action'],body)
        if operation=='cancelUploads':
            with session.upload_lock:
                for ident in list(session.uploads): session.upload('cancel',{'id':ident})
            return {'cancelled':True}
        if operation=='workspace': return {'path':str(session.workspace.root)}
        raise ValueError('Unknown native operation')

    def close(self):
        self.socket.shutdown(); self.socket.server_close()
        self.owner.library.close(); self.owner.close()
        self.owner.socket_path.unlink(missing_ok=True)
        self.lock_file.close()

def run(path):
    """A newline JSON request/response pipe. Stdin EOF quits the owned backend."""
    desktop=Desktop(path)
    output_lock=threading.Lock()
    stopped=threading.Event()
    def write(value):
        with output_lock:
            print(json.dumps(value,ensure_ascii=False),flush=True)
    def request(message):
        try: write({'id':message['id'],'result':desktop.dispatch(message,trusted=True)})
        except Exception as exc: write({'id':message.get('id'),'error':str(exc)})
    def stop(*_):
        stopped.set()
        # Wake the pipe on termination; the app normally closes stdin gracefully.
        try: os.close(sys.stdin.fileno())
        except OSError: pass
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    executor=concurrent.futures.ThreadPoolExecutor(max_workers=8,thread_name_prefix='desktop')
    write({'event':'ready','workspace':str(desktop.owner.workspace.root),'transport':'unix','pid':os.getpid()})
    try:
        while not stopped.is_set():
            raw=sys.stdin.buffer.readline(MAX_MESSAGE+1)
            if not raw: break
            if len(raw)>MAX_MESSAGE: raise ValueError('Desktop request too large')
            message=json.loads(raw)
            if message.get('operation')=='shutdown': break
            executor.submit(request,message)
    except OSError:
        if not stopped.is_set(): raise
    finally:
        desktop.close()
        executor.shutdown(wait=True,cancel_futures=True)
