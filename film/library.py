"""Project catalog. Each project owns a workspace, server and Codex conversation."""
import copy
import json
import threading
import time
import uuid
from .core import Workspace, atomic, unpack

class Library:
    def __init__(self, owner):
        self.owner=owner
        self.lock=threading.RLock()
        self.file=owner.workspace.root/'.film/library.json'
        self.sessions={'legacy':owner}
        if self.file.exists(): self.data=json.loads(self.file.read_text())
        else:
            ws=owner.workspace
            existing=bool(ws.data['assets'] or ws.data['active_project'] or list((ws.root/'edits').glob('*.mlt')))
            self.data={'projects':[{'id':'legacy','name':'Existing projects','created':time.time()}] if existing else []}
            self.save()

    def save(self): atomic(self.file,json.dumps(self.data,indent=2).encode())

    def listing(self):
        with self.lock: return copy.deepcopy([p for p in self.data['projects'] if not p.get('deleted')])

    def entry(self, ident):
        return next((p for p in self.data['projects'] if p['id']==ident and not p.get('deleted')),None)

    def create(self, name, **settings):
        if not isinstance(name,str) or not name.strip() or len(name.strip())>100: raise ValueError('Enter a project name of 1–100 characters')
        automatic=settings.pop('auto_configure',True)
        if type(automatic) is not bool: raise ValueError('auto_configure must be a boolean')
        for field,minimum in (('width',16),('height',16),('fps_num',1),('fps_den',1)):
            if field in settings and (type(settings[field]) is not int or settings[field]<minimum):
                raise ValueError(f'{field} must be an integer of at least {minimum}')
        ident=uuid.uuid4().hex
        with self.lock:
            entry={'id':ident,'name':name.strip(),'created':time.time()}
            self.data['projects'].append(entry)
            try:
                server=self.session(ident)
                server.workspace.data['auto_configure']=automatic
                server.workspace.save()
                unpack(server.workspace.call('film_new_project',dict(settings,name=name)))
                self.save()
            except Exception:
                self.data['projects'].remove(entry)
                server=self.sessions.pop(ident,None)
                if server: self.close_session(server)
                raise
            return copy.deepcopy(entry)

    def session(self, ident):
        from .server import FilmServer
        with self.lock:
            if not self.entry(ident): raise ValueError('Project not found or deleted')
            if ident not in self.sessions:
                ws=Workspace(self.owner.workspace.root/'projects'/ident)
                ws.heavy_slots=self.owner.workspace.heavy_slots
                ws.render_peers=self.owner.workspace.render_peers
                ws.render_peers.append(ws)
                server=FilmServer(ws,0,library=self,project_id=ident)
                atomic(ws.root/'.film/connection.json',json.dumps({'url':server.origin,'token':server.token}).encode())
                threading.Thread(target=server.serve_forever,daemon=True).start()
                self.sessions[ident]=server
            return self.sessions[ident]

    def open(self, ident):
        server=self.session(ident)
        return {'url':server.origin+'/?project='+ident+'#'+server.token}

    def delete(self, ident):
        with self.lock:
            entry=self.entry(ident)
            if not entry: raise ValueError('Project not found or deleted')
            server=self.sessions.get(ident)
            if server:
                if server.active_operations: raise ValueError('Wait for project operations or uploads to finish before deleting it')
                if server.codex.busy: raise ValueError('Stop the active Codex turn before deleting this project')
                if any(j['status'] in ('queued','running') for j in server.workspace.jobs.values()): raise ValueError('Wait for renders to finish before deleting this project')
                if server.workspace.shotcut:
                    from .core import unpack
                    if 'list_render_jobs' in {t['name'] for t in server.workspace.engine_tools}:
                        for status in ('queued','running'):
                            jobs=unpack(server.workspace.forward('list_render_jobs',{'status':status,'limit':1}))
                            if jobs.get('jobs'): raise ValueError('Wait for exports to finish before deleting this project')
            if server:
                with server.lifecycle_lock:
                    if server.active_operations: raise ValueError('Wait for project operations or uploads to finish before deleting it')
                    if server is not self.owner: server.archived=True
            entry['deleted']=time.time(); self.save()
            # Preserve absolute paths in saved MLT files. Archive in place, hide from picker.
            if server and server is not self.owner:
                self.sessions.pop(ident)
                self.close_session(server)
            return {'deleted':ident,'retained':'Project files archived in place; camera originals are unchanged.'}

    def close_session(self,server):
        server.shutdown(); server.codex.close(); server.workspace.close(); server.server_close()
        (server.workspace.root/'.film/connection.json').unlink(missing_ok=True)
        peers=server.workspace.render_peers
        if server.workspace in peers: peers.remove(server.workspace)

    def close(self):
        for ident,server in list(self.sessions.items()):
            if server is not self.owner: self.close_session(server)
        self.sessions={'legacy':self.owner}
