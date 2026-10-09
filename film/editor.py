"""Revision-safe manual editing and browser media preparation over the real MLT engine."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import uuid
from .core import atomic, unpack, IMAGE, MEDIA_INPUT_OPTIONS


class Editor:
    def __init__(self, server):
        self.server=server
        self.ws=server.workspace
        self.lock=threading.RLock()
        self.media_lock=threading.RLock()
        self.history_file=self.ws.root/'.film/editor-history.json'
        try: self.history=json.loads(self.history_file.read_text())
        except (OSError,ValueError): self.history={}
        self.ws.edit_observer=self.record_edit
        self.ws.edit_lock=self.lock

    def save(self):
        atomic(self.history_file,json.dumps(self.history).encode())

    def snapshot(self, path=None):
        if not self.ws.data.get('active_project'): return None
        value=unpack(self.ws.forward('inspect_project',{'path':str(path or self.ws.active())}))
        assets={a['path']:a for a in self.ws.ordered_assets()}
        for track in value.get('tracks',[]):
            for item in track['items']:
                a=assets.get(item.get('resource'))
                item['name']=a['name'] if a else item.get('caption') or item['type']
                item['asset_id']=a['id'] if a else None
        h=self.history.get(value['path'],{})
        current=h.get('revision')==value['revision']
        value['can_undo']=current and bool(h.get('undo'))
        value['can_redo']=current and bool(h.get('redo'))
        warning=getattr(self.ws,'history_warning',None)
        if warning:
            value['warnings']=[warning]
            value['can_undo']=value['can_redo']=False
        return value

    def target(self, project_path):
        path=self.ws.path(project_path)
        if path!=self.ws.active(): raise ValueError('Active sequence changed. Refresh before editing.')
        return str(path)

    def edit(self, expected_revision, operations, *, project_path):
        if not isinstance(expected_revision,str) or not expected_revision: raise ValueError('A current revision is required')
        if not isinstance(operations,list) or not 1<=len(operations)<=100: raise ValueError('Supply 1–100 edit operations')
        with self.lock:
            path=self.target(project_path)
            snap=self.snapshot(path)
            if snap['revision']!=expected_revision: raise ValueError('Timeline changed. Refresh before editing.')
            self.check_locks(snap,operations)
            result=unpack(self.ws.forward('edit_project',{'project_path':path,'expected_revision':expected_revision,'operations':operations}))
            return self.snapshot(path)

    def record_edit(self,path,expected_revision,result):
        """Both MCP/Codex and manual transactions enter the same undo history."""
        with self.lock:
            h=self.history.setdefault(str(path),{'undo':[],'redo':[]})
            if h.get('revision')!=expected_revision: h['undo']=[]
            if result.get('backup_path'): h['undo'].append(result['backup_path'])
            h.update(revision=result['revision'],redo=[])
            h['undo']=h['undo'][-15:]
            self.save()

    @staticmethod
    def check_locks(snapshot, operations):
        """MLT's shotcut:lock is UI metadata; enforce it for every manual entry point."""
        tracks={t['track_id']:t for t in snapshot['tracks']}
        names={t['name']:t['track_id'] for t in tracks.values()}
        locked={k for k,t in tracks.items() if str(t.get('properties',{}).get('shotcut:lock'))=='1'}
        refs={i.get('item_ref'):k for k,t in tracks.items() for i in t['items'] if i.get('item_ref')}
        filters={f.get('filter_id'):k for k,t in tracks.items()
                 for f in t.get('filters',[])+[f for i in t['items'] for f in i.get('filters',[])]}
        for op in operations:
            if not isinstance(op,dict): raise ValueError('Edit operations must be objects')
            targets=set()
            for key in ('track','target_track'):
                value=op.get(key)
                if isinstance(value,str): targets.add(names.get(value,value))
            if op.get('item_ref') in refs: targets.add(refs[op['item_ref']])
            if op.get('filter_id') in filters: targets.add(filters[op['filter_id']])
            if op.get('op') in ('remove_range','insert_gap'):
                selected=op.get('tracks','all')
                targets.update(tracks if selected=='all' else [names.get(v,v) for v in selected])
            unlocking=op.get('op')=='update_track' and op.get('locked') is False and set(op)<= {'op','track','locked'}
            if not unlocking and targets & locked:
                labels=', '.join(tracks[k]['name'] for k in sorted(targets & locked))
                raise ValueError('Unlock '+labels+' before editing this track.')
            if op.get('op')=='update_track' and 'locked' in op:
                if op['locked']: locked.update(targets)
                else: locked.difference_update(targets)
            if op.get('as') and targets: refs['@'+op['as']]=next(iter(targets))

    def restore(self, expected_revision, direction='undo', *, project_path):
        if direction not in ('undo','redo'): raise ValueError('Invalid history direction')
        with self.lock:
            path=self.target(project_path); h=self.history.get(path,{})
            if h.get('revision')!=expected_revision or not h.get(direction):
                raise ValueError('History changed. Refresh the timeline before undoing.')
            # The engine verifies revision and backup ownership under its file lock.
            result=unpack(self.ws.forward('restore_project_backup',{'project_path':path,
                'backup_path':h[direction][-1],'expected_revision':expected_revision}))
            h[direction].pop()
            if result.get('backup_path'): h['redo' if direction=='undo' else 'undo'].append(result['backup_path'])
            h['revision']=result['revision']; self.save()
            return self.snapshot(path)

    def media(self, asset_id, proxy=False, metadata_only=False, thumbnail_only=False):
        if any(type(flag) is not bool for flag in (proxy,metadata_only,thumbnail_only)):
            raise ValueError('Media preparation options must be booleans')
        if metadata_only and thumbnail_only or proxy and (metadata_only or thumbnail_only):
            raise ValueError('Choose one media preparation mode')
        asset=self.ws.asset(asset_id); path=self.ws.path(asset['path'])
        if path.suffix.lower() in {'.cube','.srt','.vtt'}:
            return {'asset_id':asset_id,'kind':'data','url':self.server.media_url(path),'duration':0}
        info=self.ws.probe(asset_id)
        streams=info.get('streams',[])
        videos=[s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')]
        video=next((s for s in videos if s.get('disposition',{}).get('default')),videos[0] if videos else None)
        audios=[s for s in streams if s.get('codec_type')=='audio']
        audio=next((s for s in audios if s.get('disposition',{}).get('default')),audios[0] if audios else None)
        from .ingest import Ingest
        detected=Ingest.classify(asset,info)
        kind=detected['kind']
        if kind=='unknown' or kind=='image' and not video:
            raise ValueError('No readable video, image, or audio stream was reported')
        duration=5.0 if kind=='image' else None
        if duration is None:
            for candidate in (info.get('format',{}).get('duration'),(video or {}).get('duration'),(audio or {}).get('duration')):
                try: seconds=float(candidate)
                except (TypeError,ValueError): continue
                if math.isfinite(seconds) and 0<seconds<=86400*30:
                    duration=seconds; break
        if duration is None:
            raise ValueError('Media duration is missing or invalid. Use a source with readable timing metadata.')
        stamp=hashlib.sha256(f'{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}'.encode()).hexdigest()[:16]
        thumbnail=self.ws.root/f'cache/thumb-{stamp}.jpg'
        waveform=self.ws.root/f'cache/wave-{stamp}.png'
        playable=path
        # A browser proxy is for responsive source inspection; MLT always renders originals.
        compatible=(kind=='image' and path.suffix.lower() in IMAGE and video.get('codec_name') in ('mjpeg','png','webp')
                    or (kind=='video' and video.get('codec_name')=='h264'
                    and video.get('pix_fmt') in ('yuv420p','yuvj420p') and path.suffix.lower() in ('.mp4','.m4v','.mov'))
                    or (not video and path.suffix.lower() in ('.mp3','.wav','.m4a')))
        needs_proxy=not compatible or bool(kind=='video'
            and (int(video.get('width') or 0)>1280 or int(video.get('height') or 0)>720))
        target=self.ws.root/(f'cache/source-{stamp}.jpg' if kind=='image' else f'cache/proxy-{stamp}.mp4')
        if proxy and needs_proxy and target.exists(): playable=target
        def mapping(stream, fallback):
            index=(stream or {}).get('index')
            return f'0:{index}' if type(index) is int and index>=0 else fallback
        def run(arguments, destination, timeout=180):
            temp=destination.with_name(uuid.uuid4().hex+destination.suffix)
            try:
                r=self.ws.run_media([self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*MEDIA_INPUT_OPTIONS,
                    '-i',str(path),*arguments,'-threads','1',str(temp)],timeout=timeout)
                if r.returncode or not temp.is_file(): raise ValueError(r.stderr.decode(errors='replace')[-1600:] or 'Media preparation failed')
                temp.replace(destination)
            finally: temp.unlink(missing_ok=True)
        need_thumbnail=bool(video and not thumbnail.exists() and not metadata_only)
        # Waveforms analyze the entire source. Source selection waits only for
        # playable media; import/background preparation can request the waveform.
        need_waveform=bool(audio and not waveform.exists() and not (proxy or metadata_only or thumbnail_only))
        needs=need_thumbnail or need_waveform or (proxy and needs_proxy and not target.exists())
        if needs:
            with self.media_lock, self.ws.heavy():
                if video and not thumbnail.exists():
                    run(['-map',mapping(video,'0:v:0'),'-frames:v','1','-vf','scale=320:180:force_original_aspect_ratio=decrease','-an'],thumbnail)
                if need_waveform and not waveform.exists():
                    run(['-filter_complex','aformat=channel_layouts=mono,showwavespic=s=1200x64:colors=9fdac6','-frames:v','1'],waveform)
                if proxy and needs_proxy:
                    if not target.exists():
                        args=[]
                        if video: args+=['-map',mapping(video,'0:v:0')]
                        if kind=='image':
                            args+=['-frames:v','1','-vf','scale=1280:720:force_original_aspect_ratio=decrease','-an']
                        else:
                            if audio: args+=['-map',mapping(audio,'0:a:0')]
                            if video: args+=['-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p']
                            args+=['-c:a','aac','-b:a','128k','-movflags','+faststart']
                        run(args,target,1800)
                    playable=target
        value={'asset_id':asset_id,'duration':duration,'width':(video or {}).get('width'),
               'height':(video or {}).get('height'),'codec':(video or audio or {}).get('codec_name'),
               'has_audio':bool(audio),'has_video':bool(video),'proxy':playable!=path,'needs_proxy':bool(needs_proxy),
               'kind':kind,
               'url':self.server.media_url(playable)}
        if thumbnail.exists(): value['thumbnail']=self.server.media_url(thumbnail)
        if waveform.exists(): value['waveform']=self.server.media_url(waveform)
        return value

    def frame(self, expected_revision, frame, *, project_path):
        path=self.target(project_path)
        if isinstance(frame,bool) or not isinstance(frame,int) or frame<0: raise ValueError('Invalid frame')
        raw=Path(path).read_bytes()
        revision=hashlib.sha256(raw).hexdigest()
        if revision!=expected_revision: raise ValueError('Timeline changed. Refresh before rendering.')
        ident=uuid.uuid4().hex
        snapshot=self.ws.root/f'cache/frame-source-{ident}.mlt'
        output=self.ws.root/f'cache/frame-{revision[:12]}-{frame}.png'
        try:
            if not output.exists():
                atomic(snapshot,raw)
                unpack(self.ws.forward('render_preview',{'project_path':str(snapshot),'output_path':str(output),'frame':frame}))
            return {'url':self.server.media_url(output),'revision':revision,'frame':frame,'project_path':path}
        finally: snapshot.unlink(missing_ok=True)

    def render(self, expected_revision, preview=True, preset='h264-high', *, project_path):
        path=self.target(project_path)
        snap=self.snapshot(path)
        if not snap or not any(i['type']!='gap' for t in snap['tracks'] for i in t['items']):
            raise ValueError('Add clips to the sequence before rendering.')
        if snap['revision']!=expected_revision: raise ValueError('Timeline changed. Refresh before rendering.')
        if preset not in ('h264-high','h264-web','prores','hevc'): raise ValueError('Unsupported preset')
        if preview: preset='h264-web'
        ext='mov' if preset=='prores' else 'mp4'
        folder='cache' if preview else 'exports'
        name=('preview' if preview else 'film')+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.'+ext
        props={'threads':'1','progressive':'1'}
        # Do not override consumer width/height: bundled MLT can spin indefinitely
        # when they are supplied. Render at the sequence profile; proxies are separate.
        if preview: props['preset']='ultrafast'
        if preset.startswith('h264'): props['pix_fmt']='yuv420p'
        result=unpack(self.ws.forward('start_render',{'project_path':snap['path'],
            'output_path':str(self.ws.root/folder/name),'expected_revision':expected_revision,
            'preset':preset,'consumer_properties':props,'overwrite':False}))
        return result

    def renders(self):
        summaries=[]; cursor=None; seen=set()
        # Filter after pagination: other projects must not evict this project's jobs.
        while True:
            args={'limit':100}
            if cursor: args['cursor']=cursor
            result=unpack(self.ws.forward('list_render_jobs',args))
            summaries.extend(result.get('jobs',[]))
            cursor=result.get('next_cursor')
            if not cursor or cursor in seen: break
            seen.add(cursor)
        jobs=[]; finished=0
        for summary in summaries:
            # The installed bridge's durable job store is shared by all workspaces.
            source=summary.get('source_project_path') or summary.get('project_path','')
            if not source or not Path(source).is_relative_to(self.ws.root): continue
            active=summary.get('status') in ('queued','running')
            if not active and finished>=30: continue
            job=unpack(self.ws.forward('render_status',{'job_id':summary['job_id']}))
            if job.get('status')=='completed' and Path(job.get('output_path','')).is_file():
                job['url']=self.server.media_url(job['output_path'])
            jobs.append(job)
            if not active: finished+=1
        return jobs
