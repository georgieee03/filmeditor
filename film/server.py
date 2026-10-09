"""Loopback-only web UI with authenticated APIs and signed local media URLs."""
from __future__ import annotations
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import secrets
import time
import threading
from urllib.parse import urlsplit, parse_qs, urlencode
from .core import ROOT
from .codex import Codex

class FilmServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workspace,port=4317,library=None,project_id="legacy"):
        self.lifecycle_lock=threading.RLock(); self.active_operations=0; self.archived=False
        self.workspace=workspace; self.token=secrets.token_urlsafe(32); self.codex=Codex(workspace)
        super().__init__(('127.0.0.1',port),Handler)
        self.origin=f'http://127.0.0.1:{self.server_port}'
        from .library import Library
        self.project_id=project_id
        self.library=library or Library(self)
        from .editor import Editor
        self.editor=Editor(self)

    def media_url(self,value):
        p=self.workspace.path(value)
        if not p.is_file(): raise ValueError('Media missing')
        rel=str(p.relative_to(self.workspace.root))
        if rel.startswith('.film/') or rel=='project.json': raise ValueError('Private application state is not media')
        expires=int(time.time())+3600
        sig=hmac.new(self.token.encode(),f'{rel}\n{expires}'.encode(),hashlib.sha256).hexdigest()
        return '/media?'+urlencode({'path':rel,'expires':expires,'sig':sig})

    def state(self):
        data=self.workspace.state()
        entry=self.library.entry(self.project_id)
        data['project_name']=entry['name'] if entry else 'Existing projects'
        owner=self.library.owner
        data['library_home']=owner.origin+'/#'+owner.token
        for a in data['assets']:
            try: a['url']=self.media_url(a['path'])
            except ValueError: a['missing']=True
        data['outputs']=[{'path':p,'url':self.media_url(p)} for p in data['outputs']]
        return data

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass  # Do not log private signed paths/tokens.

    def handle(self):
        # Browser media seeks close keep-alive sockets routinely.
        try: super().handle()
        except (BrokenPipeError,ConnectionResetError): pass

    def guard(self,auth=True):
        if self.server.archived: raise ValueError('This project was deleted. Return to the project picker.')
        if self.headers.get('Host')!=self.server.origin.removeprefix('http://'): raise PermissionError('Invalid Host')
        origin=self.headers.get('Origin')
        if origin and origin!=self.server.origin: raise PermissionError('Cross-origin request denied')
        if auth and not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token):
            raise PermissionError('Open the private startup URL; authentication missing')

    def send_headers(self,status,length,mime='application/json'):
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(length))
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

    def reply(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode(); self.send_headers(status,len(raw)); self.wfile.write(raw)

    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=4*1024**2: raise ValueError('Invalid JSON body size')
        value=json.loads(self.rfile.read(n))
        if not isinstance(value,dict): raise ValueError('Expected JSON object')
        return value

    def do_GET(self):
        try:
            route=urlsplit(self.path); args=parse_qs(route.query)
            if route.path in ('/','/app.js','/desktop.js','/music.js','/music.css','/advanced.js','/advanced.css','/quickstart.js','/quickstart.css','/assistant.js','/assistant.css','/style.css'):
                self.guard(False); p=ROOT/'web'/('index.html' if route.path=='/' else route.path[1:]); raw=p.read_bytes()
                self.send_headers(200,len(raw),mimetypes.guess_type(str(p))[0] or 'text/plain'); self.wfile.write(raw); return
            if route.path=='/media':
                self.guard(False); rel=args.get('path',[''])[0]; expires=int(args.get('expires',['0'])[0]); sig=args.get('sig',[''])[0]
                expected=hmac.new(self.server.token.encode(),f'{rel}\n{expires}'.encode(),hashlib.sha256).hexdigest()
                if expires<time.time() or not hmac.compare_digest(sig,expected): raise PermissionError('Media URL expired/invalid')
                p=self.server.workspace.path(rel)
                if not p.is_file(): raise ValueError('Media missing')
                mime=mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
                if mime in ('text/html','image/svg+xml','text/javascript','application/javascript'): mime='application/octet-stream'
                size=p.stat().st_size; start=0; end=size-1; code=200; range_=self.headers.get('Range')
                if range_:
                    if not range_.startswith('bytes=') or ',' in range_: raise ValueError('Unsupported range')
                    a,b=range_[6:].split('-',1)
                    if a: start=int(a); end=min(end,int(b)) if b else end
                    else: start=max(0,size-int(b))
                    if start<0 or start>end or start>=size:
                        self.send_response(416); self.send_header('Content-Range',f'bytes */{size}'); self.send_header('Content-Length','0'); self.end_headers(); return
                    code=206
                self.send_response(code); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(max(0,end-start+1)))
                self.send_header('Accept-Ranges','bytes'); self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
                if code==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                with p.open('rb') as f:
                    f.seek(start); remaining=end-start+1
                    while remaining>0:
                        chunk=f.read(min(1024**2,remaining))
                        if not chunk: break
                        self.wfile.write(chunk); remaining-=len(chunk)
                return
            self.guard()
            if route.path=='/api/library': self.reply({'projects':self.server.library.listing()})
            elif route.path=='/api/state': self.reply(self.server.state())
            elif route.path=='/api/status': self.reply(self.server.workspace.status())
            elif route.path=='/api/tools': self.reply({'tools':self.server.workspace.tools()})
            elif route.path=='/api/events': self.reply(self.server.codex.snapshot(int(args.get('after',['0'])[0])))
            elif route.path=='/api/editor': self.reply(self.server.editor.snapshot())
            elif route.path=='/api/renders': self.reply({'jobs':self.server.editor.renders()})
            else: self.reply({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError): pass
        except PermissionError as exc: self.close_connection=True; self.reply({'error':str(exc)},403)
        except Exception as exc: self.close_connection=True; self.reply({'error':str(exc)},400)

    def do_POST(self):
        tracked=not urlsplit(self.path).path.startswith('/api/library/')
        with self.server.lifecycle_lock:
            if self.server.archived:
                self.reply({'error':'Project deleted'},410); return
            if tracked: self.server.active_operations+=1
        try: self.post()
        finally:
            if tracked:
                with self.server.lifecycle_lock: self.server.active_operations-=1

    def post(self):
        try:
            self.guard(); route=urlsplit(self.path); args=parse_qs(route.query)
            if route.path=='/api/import':
                self.connection.settimeout(600)
                value=self.server.workspace.ingest(self.rfile,int(self.headers.get('Content-Length','0')),args.get('name',[''])[0],args.get('kind',['footage'])[0])
                self.reply(value); return
            body=self.body(); ws=self.server.workspace; codex=self.server.codex
            if route.path=='/api/library/create': value=self.server.library.create(**body)
            elif route.path=='/api/library/open': value=self.server.library.open(body['id'])
            elif route.path=='/api/library/delete': value=self.server.library.delete(body['id'])
            elif route.path=='/api/assets/delete':
                if codex.busy: raise ValueError('Wait for Codex to finish before deleting media')
                value=ws.delete_asset(body['id'])
            elif route.path=='/api/call': value=ws.call(body['name'],body.get('arguments',{}))
            elif route.path=='/api/media-url': value={'url':self.server.media_url(body['path'])}
            elif route.path=='/api/codex/connect': value=codex.models()
            elif route.path=='/api/codex/start': value=codex.start(**body)
            elif route.path=='/api/codex/stop': value=codex.stop()
            elif route.path=='/api/codex/reset': value=codex.reset()
            elif route.path=='/api/codex/approve': value=codex.approve(**body)
            elif route.path=='/api/codex/configure': value=codex.configure(**body)
            elif route.path=='/api/editor/edit': value=self.server.editor.edit(**body)
            elif route.path=='/api/editor/history': value=self.server.editor.restore(**body)
            elif route.path=='/api/editor/media': value=self.server.editor.media(**body)
            elif route.path=='/api/editor/render': value=self.server.editor.render(**body)
            elif route.path=='/api/editor/frame': value=self.server.editor.frame(**body)
            else: self.reply({'error':'Not found'},404); return
            self.reply(value)
        except (BrokenPipeError,ConnectionResetError): pass
        except PermissionError as exc: self.close_connection=True; self.reply({'error':str(exc)},403)
        except Exception as exc: self.close_connection=True; self.reply({'error':str(exc)},400)
