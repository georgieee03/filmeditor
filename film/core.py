"""Shared media/project state and adapters for separately installed rendering engines."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import math
from fractions import Fraction
from contextlib import contextmanager, nullcontext
import time
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from .rpc import JsonlProcess

ROOT = Path(__file__).resolve().parents[1]
MEDIA = {'.mp4','.mov','.m4v','.mkv','.webm','.avi','.mts','.mxf','.wav','.mp3','.m4a','.flac','.aac','.ogg','.opus','.aif','.aiff','.wma','.alac','.png','.jpg','.jpeg','.webp','.heic','.heif','.cube','.srt','.vtt'}
IMAGE = {'.png','.jpg','.jpeg','.webp'}
MEDIA_INPUT_OPTIONS=['-protocol_whitelist','file,pipe','-format_whitelist',
    'mov,matroska,avi,mpegts,mxf,wav,mp3,flac,aac,ogg,aiff,asf,image2,jpeg_pipe,png_pipe,webp_pipe,heif']

def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)

def result(value, error=False):
    return {'content':[{'type':'text','text':json.dumps(value, ensure_ascii=False)}], 'isError':error}

def unpack(value):
    if value.get('isError'): raise ValueError(json.dumps(value.get('content',value))[:5000])
    if 'structuredContent' in value: return value['structuredContent']
    for block in value.get('content',[]):
        if block.get('type') == 'text':
            try: return json.loads(block['text'])
            except ValueError: pass
    return value

def schema(name, description, properties=None, required=None, read=False):
    return {'name':name,'description':description,
        'inputSchema':{'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False},
        'annotations':{'readOnlyHint':read,'destructiveHint':not read,'openWorldHint':False}}

S = {'type':'string'}
TOOLS = [
    schema('film_status','Report local executables and integration status. Not a render-readiness certificate.',read=True),
    schema('film_assets','Read ordered media and reference labels (Video 1 = first video, Reference 1 = first reference), IDs, brief and selected timeline. Never infer footage contents from filenames.',read=True),
    schema('film_brief','Read or update the creative brief.',{'text':S}),
    schema('film_new_project','Create/select an editable Shotcut timeline. Probe representative sources before choosing dimensions/FPS.',{'name':S,'width':{'type':'integer','minimum':16},'height':{'type':'integer','minimum':16},'fps_num':{'type':'integer','minimum':1},'fps_den':{'type':'integer','minimum':1}}),
    schema('film_select_project','Select an existing edits/*.mlt.',{'path':S},['path']),
    schema('film_probe','Read allowlisted technical facts for one imported asset, excluding arbitrary container tags. Use film_detect_media for a sequence recommendation.',{'asset_id':S},['asset_id'],read=True),
    schema('film_frames','Extract up to 12 inspection thumbnails. NOT color-managed grading proofs.',{'asset_id':S,'seconds':{'type':'array','items':{'type':'number','minimum':0},'minItems':1,'maxItems':12}},['asset_id','seconds']),
    schema('film_view_image','Return actual reference/frame image content to the agent, not just a file path.',{'path':S},['path'],read=True),
    schema('film_timeline','Read a saved timeline visualization. inspect_project supplies authoritative revision-scoped edit references.',read=True),
    schema('film_scene_asset','Copy an imported asset to a self-contained HyperFrames scene; returns scene-relative src.',{'name':S,'asset_id':S},['name','asset_id']),
    schema('film_scene_write','Save trusted local HyperFrames HTML. Existing scenes require their expected_revision. Uses local vendor/gsap.min.js; no remote assets.',{'name':S,'html':S,'expected_revision':S},['name','html']),
    schema('film_scene_read','Read a scene source and revision.',{'name':S},['name'],read=True),
    schema('film_scene_render','Render a scene locally. Completed output enters the common asset bin; append it separately to the timeline. Verify MOV alpha, never assume it.',{'name':S,'format':{'type':'string','enum':['mov','mp4']},'fps':{'oneOf':[{'type':'integer','minimum':1,'maximum':120},{'type':'string','pattern':'^[0-9]+/[1-9][0-9]*$'}]}},['name']),
    schema('film_job','Read motion-render state and bounded log.',{'job_id':S},['job_id'],read=True),
    schema('film_cancel_job','Cancel a running motion-render process group.',{'job_id':S},['job_id']),
]

class Workspace:
    def __init__(self, path):
        self.root = Path(path).expanduser().resolve()
        if self.root == ROOT or ROOT in self.root.parents:
            raise ValueError('Keep footage outside the source repository; choose a separate workspace')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.engine_lock = threading.RLock()
        self.shotcut = None
        self.render_peers=[self]
        self.engine_tools = []
        self.engine_error = None
        self.jobs = {}
        self.processes = {}
        self.stopping = False
        config_path = ROOT/'.local/performance.json'
        self.performance = {'heavy_jobs':1,'hyperframes_workers':1}
        if config_path.exists(): self.performance.update(json.loads(config_path.read_text()))
        for key, ceiling in [('heavy_jobs',1),('hyperframes_workers',2)]:
            value=self.performance[key]
            if type(value) is not int or not 1<=value<=ceiling: raise ValueError('Invalid performance limit: '+key)
        self.heavy_slots=threading.BoundedSemaphore(self.performance['heavy_jobs'])
        for name in ['originals','references','cache','edits','exports','scenes','generated','.film']:
            (self.root/name).mkdir(exist_ok=True, mode=0o700)
        (self.root/'.film').chmod(0o700)
        self.statefile = self.root/'project.json'
        if not self.statefile.exists():
            atomic(self.statefile, json.dumps({'version':1,'assets':[],'active_project':None,'brief':'','thread_id':None}).encode())
        self.data = json.loads(self.statefile.read_text())

    def path(self, value):
        candidate = Path(value).expanduser()
        path = (candidate if candidate.is_absolute() else self.root/candidate).resolve()
        if path != self.root and self.root not in path.parents: raise ValueError('Path outside workspace')
        return path

    def save(self): atomic(self.statefile,json.dumps(self.data,indent=2).encode())

    def asset(self, ident):
        with self.lock:
            for a in self.data['assets']:
                if a['id']==ident: return copy.deepcopy(a)
        raise ValueError('Unknown asset id')

    def register(self, path, name, kind='footage', sha256=None):
        path = self.path(path)
        a = {'id':uuid.uuid4().hex,'name':name,'path':str(path),'relative_path':str(path.relative_to(self.root)),
             'kind':kind,'bytes':path.stat().st_size,'sha256':sha256,'metadata':None}
        with self.lock:
            self.data['assets'].append(a); self.save()
        return a

    def ordered_assets(self):
        with self.lock: assets=copy.deepcopy(self.data['assets'])
        counts={}
        for a in assets:
            section='references' if a['kind'] in ('reference','lut') else 'media'
            suffix=Path(a['path']).suffix.lower()
            category='Reference' if section=='references' else 'Video' if suffix in {'.mp4','.mov','.m4v','.mkv','.webm','.avi','.mts','.mxf'} else 'Audio' if suffix in {'.wav','.mp3','.m4a','.flac','.aac','.ogg','.opus','.aif','.aiff','.wma','.alac'} else 'Image'
            counts[category]=counts.get(category,0)+1
            a.update(section=section,ordinal=counts[category],label=f'{category} {counts[category]}')
        return assets

    def delete_asset(self, ident):
        with self.lock:
            a=self.asset(ident)
            self.data.setdefault('deleted_assets',[]).append(a)
            self.data['assets']=[item for item in self.data['assets'] if item['id']!=ident]
            self.save()
        return {'deleted':ident,'retained':'Source copy retained for saved timelines and render snapshots.'}

    def ingest(self, stream, length, filename, kind):
        def check_open():
            if self.stopping: raise ValueError('Workspace is closing; import cancelled')
        with self.lock: check_open()
        suffix = Path(filename).suffix.lower()
        if suffix not in MEDIA or kind not in ('footage','reference','audio','lut'): raise ValueError('Unsupported import type')
        if not 0 < length <= 32*1024**3: raise ValueError('Import must be between 1 byte and 32 GiB')
        folder = 'references' if kind in ('reference','lut') else 'originals'
        dest = self.path(f'{folder}/{uuid.uuid4().hex}{suffix}')
        temp = dest.with_suffix(suffix+'.upload')
        digest = hashlib.sha256()
        try:
            with temp.open('xb') as f:
                remaining=length
                while remaining:
                    with self.lock: check_open()
                    b=stream.read(min(1024**2,remaining))
                    with self.lock: check_open()
                    if not b: raise ValueError('Incomplete upload')
                    f.write(b); digest.update(b); remaining-=len(b)
                f.flush(); os.fsync(f.fileno())
            with self.lock:
                check_open()
                os.replace(temp,dest); dest.chmod(0o444)
                return self.register(dest,Path(filename).name,kind,digest.hexdigest())
        finally: temp.unlink(missing_ok=True)

    def executable(self,name):
        override=os.environ.get('FILM_'+name.upper())
        if override:
            path=Path(override).expanduser().resolve()
            if not path.is_file(): raise ValueError('Invalid executable: FILM_'+name.upper())
            return str(path)
        if name=='hyperframes':
            p=ROOT/'.deps/hyperframes/node_modules/.bin/hyperframes'
            if p.is_file(): return str(p)
        found=shutil.which(name)
        if found: return found
        for directory in ['/Applications/Shotcut.app/Contents/MacOS','/Applications/Shotcut.app/Contents/Resources']:
            p=Path(directory)/name
            if p.is_file(): return str(p)
        raise ValueError(f'{name} missing; install it or set FILM_{name.upper()}')

    def bridge_path(self):
        return Path(os.environ.get('FILM_SHOTCUT_MCP',str(ROOT/'.deps/shotcut-mcp/scripts/shotcut_mcp_server.py'))).expanduser().resolve()

    def status(self):
        paths={}
        for name in ['ffmpeg','ffprobe','hyperframes','codex']:
            try: paths[name]=self.executable(name)
            except ValueError as exc: paths[name]={'unavailable':str(exc)}
        return {'workspace':str(self.root),'paths':paths,'shotcut_bridge_installed':self.bridge_path().is_file(),
            'performance':self.performance,'shotcut_error':self.engine_error,'billing':'Uses existing ChatGPT-backed Codex access; normal plan limits apply.',
            'limitations':['Native Shotcut/HyperFrames Studio GUIs are not embedded.',
                'Real engine, color and codec acceptance tests must pass on this Mac.',
                'Reference matching is approximate, not exact LUT recovery.']}

    def connect_shotcut(self):
        with self.engine_lock:
            if self.shotcut and not self.shotcut.closed and self.shotcut.process.poll() is None: return self.shotcut
            if not self.bridge_path().is_file(): raise ValueError('Shotcut MCP missing. Run python3 scripts/bootstrap.py')
            env=os.environ.copy()
            env.update(SHOTCUT_MCP_ALLOWED_ROOTS=str(self.root),SHOTCUT_MCP_REQUIRE_ABSOLUTE_PATHS='1',
                SHOTCUT_MCP_MAX_WORKERS='1',SHOTCUT_MCP_ALLOW_NETWORK_RESOURCES='0',SHOTCUT_MCP_ALLOW_UNSAFE_CONSUMER_PROPERTIES='0')
            for a,b in [('FILM_FFMPEG','SHOTCUT_FFMPEG_PATH'),('FILM_FFPROBE','SHOTCUT_FFPROBE_PATH')]:
                if a in env: env[b]=env[a]
            child=JsonlProcess([sys.executable,'-B',str(self.bridge_path())],cwd=self.root,env=env)
            try:
                child.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'filmeditor','version':'0.1.0'}})
                child.notify('notifications/initialized')
                tools=[]; cursor=None
                for _ in range(20):
                    page=child.request('tools/list',{'cursor':cursor} if cursor else {})
                    tools.extend(page.get('tools',[])); cursor=page.get('nextCursor')
                    if not cursor: break
                else: raise ValueError('Too many tool pages')
                self.engine_tools=tools; self.shotcut=child; self.engine_error=None
                return child
            except Exception:
                child.close(); raise

    def tools(self):
        from .music import MUSIC_TOOLS
        from .ingest import FILM_INGEST_TOOLS
        from .enhance import ENHANCE_TOOLS
        try: self.connect_shotcut()
        except Exception as exc: self.engine_error=str(exc)
        return copy.deepcopy(TOOLS+MUSIC_TOOLS+FILM_INGEST_TOOLS+ENHANCE_TOOLS+self.engine_tools)

    def active(self):
        with self.lock:
            if not self.data['active_project']: raise ValueError('Create/select a timeline first')
            return self.path(self.data['active_project'])

    def protect(self,name,args):
        if args.get('force'): raise ValueError('Force edits disabled; re-inspect and reconcile')
        def outputs(v):
            if isinstance(v,dict):
                for k,item in v.items():
                    if k in ('output_path','visual_output_path'):
                        parts=self.path(item).relative_to(self.root).parts
                        if not parts or parts[0] not in ('cache','edits','exports','generated'): raise ValueError('Unsafe output destination')
                    else: outputs(item)
            elif isinstance(v,list):
                for item in v: outputs(item)
        outputs(args)
        if name in ('create_project','edit_project','restore_project_backup'):
            p=self.path(args['project_path']); parts=p.relative_to(self.root).parts
            if not parts or parts[0]!='edits' or p.suffix!='.mlt': raise ValueError('Timelines must be edits/*.mlt')

    @contextmanager
    def heavy(self, cancelled=lambda:False):
        while not self.heavy_slots.acquire(timeout=.2):
            if self.stopping or cancelled(): raise ValueError('Render cancelled while queued')
        try:
            if self.stopping or cancelled(): raise ValueError('Render cancelled while queued')
            # Durable Shotcut exports may outlive the UI. Recheck them before new work.
            for peer in list(self.render_peers):
                if not peer.bridge_path().is_file(): continue
                child=peer.connect_shotcut()
                if 'list_render_jobs' in {t['name'] for t in peer.engine_tools}:
                    while True:
                        active=False
                        for status in ('running','queued','orphaned'):
                            data=unpack(child.request('tools/call',{'name':'list_render_jobs','arguments':{'status':status,'limit':100}}))
                            for summary in data.get('jobs',[]):
                                job=unpack(child.request('tools/call',{'name':'render_status','arguments':{'job_id':summary['job_id']}}))
                                if job.get('status')=='orphaned' and isinstance(job.get('renderer_pid'),int):
                                    try: os.kill(job['renderer_pid'],0)
                                    except ProcessLookupError: pass
                                    else: raise ValueError('Render '+job['job_id']+' lost its supervisor but is still running. Stop that render before starting more media work.')
                                active=active or job.get('status') in ('queued','running')
                        if not active: break
                        if self.stopping or cancelled(): raise ValueError('Render cancelled while queued')
                        time.sleep(.5)
            yield
        finally: self.heavy_slots.release()

    def forward(self,name,args):
        child=self.connect_shotcut()
        if name not in {t['name'] for t in self.engine_tools}: raise ValueError('Unknown engine tool')
        self.protect(name,args)
        # Timeline transactions validate one frame and publish atomically. A durable
        # export owns an immutable snapshot, so manual edits need not wait for it.
        heavy_names={'start_render','render_preview','render_preview_batch','render_contact_sheet','validate_project'}
        if name not in heavy_names:
            # The same RLock is used by manual editing and history observation;
            # keep commit and undo recording ordered across concurrent MCP clients.
            with getattr(self,'edit_lock',nullcontext()) if name=='edit_project' else nullcontext():
                value=child.request('tools/call',{'name':name,'arguments':args},timeout=3700)
                observer=getattr(self,'edit_observer',None)
                if name=='edit_project' and observer and not value.get('isError'):
                    try:
                        observer(args['project_path'],args['expected_revision'],unpack(value))
                        self.history_warning=None
                    except Exception as exc:
                        # The engine has committed. Never misreport this as an
                        # aborted edit or let callers delete referenced media.
                        warning='Timeline saved, but undo history could not be persisted: '+str(exc)
                        self.history_warning=warning
                        committed=unpack(value)
                        committed.setdefault('warnings',[]).append(warning)
                        value=result(committed)
                return value
        with self.heavy():
            # Hold the edit lock only after heavy work can begin, so waiting for an
            # earlier durable render does not freeze unrelated timeline editing.
            render=name in {'start_render','render_preview','render_preview_batch','render_contact_sheet'}
            with getattr(self,'edit_lock',nullcontext()) if render else nullcontext():
                readiness=None
                if render:
                    from .ingest import Ingest
                    readiness=Ingest(self).ensure_render_ready(args.get('project_path') or args.get('path'))
                value=child.request('tools/call',{'name':name,'arguments':args},timeout=3700)
                # start_render is durable/asynchronous. Later heavy calls wait via list_render_jobs.
                if not value.get('isError') and (name=='start_render' or readiness and readiness.get('warnings')):
                    metadata=unpack(value)
                    if name=='start_render': self.render_credits(metadata)
                    if readiness and readiness.get('warnings'): metadata.setdefault('warnings',[]).extend(readiness['warnings'])
                    value=result(metadata)
                return value

    def render_credits(self, metadata):
        """Attach credits to manual and Codex exports from the immutable MLT input."""
        with self.lock:
            assets=copy.deepcopy(self.data['assets']+self.data.get('deleted_assets',[]))
        music=[a for a in assets if a.get('music')]
        if not music: return
        try:
            output=self.path(metadata['output_path'])
            if output.relative_to(self.root).parts[0]!='exports': return
            snapshot=self.path(metadata['render_project_path'])
            document=ET.parse(snapshot)
            resources={p.text for p in document.iter('property') if p.get('name') in ('resource','warp_resource')}
            from .music import Music
            credits=[]
            for asset in music:
                if asset['path'] in resources:
                    credit=Music._credit(asset['music'])
                    if credit not in credits: credits.append(credit)
            if credits:
                path=output.with_name(output.name+'.credits.txt')
                atomic(path,('Music credits for '+output.name+'\nSequence revision: '+str(metadata.get('project_revision',''))+'\n\n'+'\n\n'.join(credits)).encode())
                metadata['credits_path']=str(path)
        except (OSError,ValueError,KeyError,ET.ParseError) as exc:
            metadata.setdefault('warnings',[]).append('Render started, but music credits could not be saved: '+str(exc))

    def probe(self,ident,*,timeout=60):
        a=self.asset(ident); p=self.path(a['path']); stamp=[p.stat().st_size,p.stat().st_mtime_ns]
        if a.get('probe_stamp')==stamp: return a['metadata']
        r=self.run_media([self.executable('ffprobe'),'-v','error',*MEDIA_INPUT_OPTIONS,'-show_format','-show_streams','-of','json',str(p)],timeout=timeout)
        if r.returncode: raise ValueError(r.stderr.decode(errors='replace')[-2000:])
        data=json.loads(r.stdout)
        with self.lock:
            for item in self.data['assets']:
                if item['id']==ident: item.update(metadata=data,probe_stamp=stamp)
            self.save()
        return data

    def frames(self,ident,seconds):
        if not isinstance(seconds,list) or not 1<=len(seconds)<=12: raise ValueError('Request 1–12 frame times')
        a=self.asset(ident); paths=[]
        for second in seconds:
            if isinstance(second,bool) or not isinstance(second,(int,float)) or not math.isfinite(second) or not 0<=second<=86400:
                raise ValueError('Invalid frame time')
            dest=self.path(f'cache/{ident}-{second:.3f}.png')
            if not dest.is_file():
                temp=dest.with_name(uuid.uuid4().hex+'.png')
                try:
                    with self.heavy():
                        r=self.run_media([self.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*MEDIA_INPUT_OPTIONS,'-ss',str(second),'-i',a['path'],
                            '-frames:v','1','-vf','scale=640:640:force_original_aspect_ratio=decrease','-threads','1',str(temp)],timeout=120)
                    if r.returncode or not temp.is_file(): raise ValueError(r.stderr.decode(errors='replace')[-2000:] or 'Frame beyond clip')
                    os.replace(temp,dest)
                finally: temp.unlink(missing_ok=True)
            paths.append(str(dest))
        return {'frames':paths,'warning':'Inspection thumbnails only; use color-managed rendered frames for grading approval.'}

    def view_image(self,value):
        p=self.path(value)
        if p.suffix.lower() not in IMAGE or p.stat().st_size>4*1024**2: raise ValueError('Use PNG/JPEG/WebP under 4 MiB')
        mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}[p.suffix.lower()]
        return {'content':[{'type':'text','text':str(p)},{'type':'image','mimeType':mime,'data':base64.b64encode(p.read_bytes()).decode()}]}

    def timeline(self):
        path=self.active(); raw=path.read_bytes()
        if len(raw)>128*1024**2 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('Unsafe/oversized XML')
        root=ET.fromstring(raw); profile=root.find('profile')
        fps=int(profile.get('frame_rate_num','30'))/int(profile.get('frame_rate_den','1')) if profile is not None else 30
        def frame(value):
            value=str(value or '0')
            if ':' in value:
                h,m,s=value.split(':'); return round((int(h)*3600+int(m)*60+float(s))*fps)
            return round(float(value))
        def props(e): return {p.get('name'):p.text or '' for p in e.findall('property')}
        nodes={e.get('id'):e for e in root if e.get('id')}
        def label(ref):
            e=nodes.get(ref)
            if e is None: return ref
            if e.tag=='tractor': return 'Transition'
            resource=props(e).get('resource') or props(e).get('warp_resource')
            if resource: return next((a['name'] for a in self.data['assets'] if a['path']==resource),Path(resource).name)
            return props(e).get('shotcut:caption',ref)
        tractors=root.findall('tractor')
        main=next((e for e in tractors if props(e).get('shotcut')=='1'),tractors[-1] if tractors else None)
        tracks=[]; duration=0
        if main is not None:
            for t in main.findall('track'):
                playlist=nodes.get(t.get('producer'))
                if playlist is None or playlist.tag!='playlist' or playlist.get('id')=='background' or props(playlist).get('shotcut:background')=='1': continue
                cursor=0; items=[]
                for e in playlist:
                    if e.tag=='blank': length=frame(e.get('length')); title='Gap'; kind='gap'
                    elif e.tag=='entry': length=max(0,frame(e.get('out'))-frame(e.get('in'))+1); title=label(e.get('producer')); kind='clip'
                    else: continue
                    items.append({'kind':kind,'start':cursor,'duration':length,'name':title}); cursor+=length
                duration=max(duration,cursor)
                tracks.append({'name':props(playlist).get('shotcut:name',playlist.get('id')),'items':items})
        return {'path':str(path),'revision':hashlib.sha256(raw).hexdigest(),'fps':fps,'duration_frames':duration,'tracks':tracks}

    def scene_dir(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name): raise ValueError('Invalid scene name')
        return self.path('scenes/'+name)

    def scene_asset(self,name,ident):
        a=self.asset(ident); source=self.path(a['path'])
        dest=self.path(self.scene_dir(name)/'assets'/(a['id']+source.suffix.lower()))
        dest.parent.mkdir(parents=True,exist_ok=True)
        with self.lock:
            if not dest.exists():
                temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
                try: shutil.copyfile(source,temp); os.replace(temp,dest)
                finally: temp.unlink(missing_ok=True)
        return {'src':'assets/'+dest.name,'path':str(dest)}

    def scene_write(self,name,source,expected=None):
        if not isinstance(source,str) or len(source.encode())>1024**2 or 'data-composition-id' not in source:
            raise ValueError('Provide a HyperFrames composition under 1 MiB')
        path=self.path(self.scene_dir(name)/'index.html')
        with self.lock:
            old=path.read_bytes() if path.exists() else None
            rev=hashlib.sha256(old).hexdigest() if old is not None else None
            if old is not None and expected!=rev: raise ValueError('Scene revision conflict; load before updating')
            if old is not None: atomic(self.path(path.parent/'.history'/(rev+'.html')),old)
            gsap=ROOT/'.deps/hyperframes/node_modules/gsap/dist/gsap.min.js'
            if gsap.is_file(): atomic(self.path(path.parent/'vendor/gsap.min.js'),gsap.read_bytes())
            atomic(path,source.encode())
        return {'path':str(path),'revision':hashlib.sha256(source.encode()).hexdigest()}

    def scene_render(self,name,format='mov',fps=30):
        if format not in ('mov','mp4') or isinstance(fps,bool) or not isinstance(fps,(int,str)): raise ValueError('Invalid format/FPS')
        try: rate=Fraction(fps)
        except (ValueError,ZeroDivisionError): raise ValueError('Invalid format/FPS')
        if not 1<=rate<=120: raise ValueError('Invalid format/FPS')
        fps=str(rate)
        source=self.scene_dir(name); index=self.path(source/'index.html')
        if not index.is_file(): raise ValueError('Scene not saved')
        executable=self.executable('hyperframes')
        if 'vendor/gsap.min.js' in index.read_text():
            gsap=ROOT/'.deps/hyperframes/node_modules/gsap/dist/gsap.min.js'
            if not gsap.is_file(): raise ValueError('Local GSAP missing; run scripts/bootstrap.py')
            atomic(self.path(source/'vendor/gsap.min.js'),gsap.read_bytes())
        ident=uuid.uuid4().hex; snapshot=self.path('.film/scene-renders/'+ident)
        output=self.path(f'generated/{name}-{ident[:8]}.{format}'); log=self.path('.film/'+ident+'.log')
        with self.lock:
            if self.stopping or sum(j['status'] in ('queued','running') for j in self.jobs.values())>=8: raise ValueError('Renderer stopped or queue full')
            if any(p.is_symlink() for p in source.rglob('*')): raise ValueError('Scene symlinks not permitted')
            shutil.copytree(source,snapshot,ignore=shutil.ignore_patterns('.history','node_modules'))
            self.jobs[ident]={'id':ident,'engine':'hyperframes','status':'queued','source':str(snapshot),'output':str(output),'log':str(log)}
        def run():
            try:
                with self.heavy(lambda:self.jobs[ident]['status']=='cancelled'):
                    with log.open('wb') as f:
                        with self.lock:
                            if self.stopping or self.jobs[ident]['status']=='cancelled': return
                            self.jobs[ident]['status']='running'
                        child=subprocess.Popen([executable,'render',str(snapshot),'--format',format,'--fps',str(fps),'--quality','high','--workers',str(self.performance['hyperframes_workers']),'--output',str(output)],
                            cwd=snapshot,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
                        with self.lock:
                            self.processes[ident]=child
                            cancelled=self.jobs[ident]['status']=='cancelled' or self.stopping
                        if cancelled: self.cancel(ident)
                        code=child.wait()
                    with self.lock:
                        if self.jobs[ident]['status']=='cancelled': return
                    if code or not output.is_file() or not output.stat().st_size: raise ValueError(f'Render exited {code}; inspect job log')
                    r=subprocess.run([self.executable('ffprobe'),'-v','error','-show_streams','-of','json',str(output)],capture_output=True,timeout=60)
                    info=json.loads(r.stdout) if r.returncode==0 else {}
                    if not any(s.get('codec_type')=='video' for s in info.get('streams',[])): raise ValueError('Output has no video stream')
                    decoded=subprocess.run([self.executable('ffmpeg'),'-nostdin','-v','error','-xerror','-i',str(output),'-f','null','-'],capture_output=True,timeout=600)
                    if decoded.returncode: raise ValueError('Rendered output failed full decode')
                    with self.lock:
                        if self.jobs[ident]['status']=='cancelled': return
                        asset=self.register(output,output.name,'generated')
                        self.jobs[ident].update(status='completed',asset=asset,streams=info['streams'])
            except Exception as exc:
                with self.lock:
                    if self.jobs[ident]['status']!='cancelled': self.jobs[ident].update(status='failed',error=str(exc))
            finally:
                with self.lock: self.processes.pop(ident,None)
        threading.Thread(target=run,daemon=True).start()
        return copy.deepcopy(self.jobs[ident])

    def cancel(self,ident):
        with self.lock:
            job=self.jobs[ident]
            if job['status'] in ('completed','failed'): return copy.deepcopy(job)
            job['status']='cancelled'; child=self.processes.get(ident)
        if child and child.poll() is None:
            try: os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=4)
            except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        return copy.deepcopy(job)

    def call(self,name,args):
        if not isinstance(args,dict): raise ValueError('Arguments must be an object')
        if name.startswith('film_enhance_'):
            from .enhance import Enhance
            with self.lock:
                if not hasattr(self,'enhance'): self.enhance=Enhance(self)
            value=self.enhance.dispatch(name,args)
        elif name in ('film_detect_media','film_configure_from_media'):
            from .ingest import Ingest
            with self.lock:
                if not hasattr(self,'ingest_tools'): self.ingest_tools=Ingest(self)
            value=self.ingest_tools.dispatch(name,args)
        elif name.startswith('film_music_'):
            from .music import Music
            with self.lock:
                if not hasattr(self,'music'): self.music=Music(self)
            value=self.music.dispatch(name,args)
        elif name=='film_status': value=self.status()
        elif name=='film_assets':
            with self.lock: value=copy.deepcopy(self.data)
            value.pop('deleted_assets',None)
            value['assets']=self.ordered_assets()
            for asset in value['assets']:
                asset.pop('metadata',None)
                asset.pop('probe_stamp',None)
        elif name=='film_brief':
            with self.lock:
                if 'text' in args:
                    if not isinstance(args['text'],str) or len(args['text'])>40000: raise ValueError('Brief too long')
                    self.data['brief']=args['text']; self.save()
                value={'brief':self.data['brief']}
        elif name=='film_new_project':
            title=re.sub('[^A-Za-z0-9_-]','-',str(args.get('name','reel')))[:64] or 'reel'
            p=self.path(f'edits/{title}-{uuid.uuid4().hex[:8]}.mlt')
            value=unpack(self.forward('create_project',{'project_path':str(p),'width':args.get('width',1080),'height':args.get('height',1920),
                'fps_num':args.get('fps_num',30),'fps_den':args.get('fps_den',1),'tracks':[{'kind':'video','name':'V2'},{'kind':'video','name':'V3'},{'kind':'audio','name':'A1'},{'kind':'audio','name':'A2'},{'kind':'audio','name':'A3'}]}))
            with self.lock: self.data['active_project']=str(p.relative_to(self.root)); self.save()
        elif name=='film_select_project':
            p=self.path(args['path']); parts=p.relative_to(self.root).parts
            if not p.is_file() or p.suffix!='.mlt' or not parts or parts[0]!='edits': raise ValueError('Select an existing edits/*.mlt')
            with self.lock: self.data['active_project']=str(p.relative_to(self.root)); self.save()
            value={'active_project':str(p)}
        elif name=='film_probe':
            from .ingest import Ingest
            value=Ingest.classify(self.asset(args['asset_id']),self.probe(args['asset_id']))
        elif name=='film_frames': value=self.frames(args['asset_id'],args['seconds'])
        elif name=='film_view_image': return self.view_image(args['path'])
        elif name=='film_timeline': value=self.timeline()
        elif name=='film_scene_asset': value=self.scene_asset(args['name'],args['asset_id'])
        elif name=='film_scene_write': value=self.scene_write(args['name'],args['html'],args.get('expected_revision'))
        elif name=='film_scene_read':
            p=self.path(self.scene_dir(args['name'])/'index.html'); raw=p.read_bytes()
            value={'path':str(p),'html':raw.decode(),'revision':hashlib.sha256(raw).hexdigest()}
        elif name=='film_scene_render': value=self.scene_render(**args)
        elif name=='film_cancel_job': value=self.cancel(args['job_id'])
        elif name=='film_job':
            with self.lock: value=copy.deepcopy(self.jobs[args['job_id']])
            p=self.path(value['log'])
            if p.is_file():
                with p.open('rb') as f: f.seek(max(0,p.stat().st_size-12000)); value['log_tail']=f.read().decode(errors='replace')
        else: return self.forward(name,args)
        return result(value)

    def state(self):
        with self.lock: data=copy.deepcopy(self.data); data['jobs']=copy.deepcopy(list(self.jobs.values()))
        data.pop('deleted_assets',None)
        data['assets']=self.ordered_assets()
        data.update(workspace=str(self.root),projects=[str(p.relative_to(self.root)) for p in sorted((self.root/'edits').glob('*.mlt')) if '.render-' not in p.name],
            scenes=[p.parent.name for p in (self.root/'scenes').glob('*/index.html')],
            outputs=[str(p.relative_to(self.root)) for d in ('exports','generated','cache') for p in sorted((self.root/d).glob('*'),key=lambda p:p.stat().st_mtime,reverse=True) if p.suffix.lower() in MEDIA or d=='exports' and p.name.endswith('.credits.txt')])
        return data

    def run_media(self,command,timeout):
        """Own preparation processes so quitting the app can cancel them promptly."""
        ident='media-'+uuid.uuid4().hex
        with self.lock:
            if self.stopping: raise ValueError('Workspace is closing')
            child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            self.processes[ident]=child
        try:
            try: stdout,stderr=child.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.stop_process(child); child.communicate()
                raise ValueError('Media preparation timed out')
            return subprocess.CompletedProcess(command,child.returncode,stdout,stderr)
        finally:
            with self.lock: self.processes.pop(ident,None)

    @staticmethod
    def stop_process(child):
        if child.poll() is not None: return
        try:
            os.killpg(child.pid,signal.SIGTERM)
            try: child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL); child.wait(timeout=4)
        except ProcessLookupError: pass

    def close(self):
        with self.lock: self.stopping=True; jobs=[k for k,j in self.jobs.items() if j['status'] in ('queued','running')]
        for ident in jobs: self.cancel(ident)
        with self.lock: children=list(self.processes.values())
        for child in children: self.stop_process(child)
        if self.shotcut: self.shotcut.close()
