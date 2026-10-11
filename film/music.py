"""Public catalogue discovery and revision-safe local soundtrack fitting.

Openverse metadata is provenance, not an independent rights certificate. Network
requests use public HTTPS addresses pinned for TLS, and never fetch arbitrary tool
URLs. Beat detection is a bounded energy-envelope estimate, not semantic analysis.
"""
from __future__ import annotations
from array import array
import copy
from fractions import Fraction
import hashlib
import http.client
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import ssl
import statistics
import sys
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit
import uuid

API='https://api.openverse.org/v1/'
ALLOWED_LICENSES={'cc0','pdm','by'}
MAX_DOWNLOAD=256*1024**2
FORMATS='mp3,wav,flac,ogg,aac,mov,mp4,m4a,3gp,3g2,mj2,asf,aiff,matroska,webm'
INPUT_OPTIONS=['-protocol_whitelist','file,pipe','-format_whitelist',FORMATS]

def _tool(name,description,properties,required,read=False):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,
        'required':required,'additionalProperties':False},
        'annotations':{'readOnlyHint':read,'destructiveHint':False,'openWorldHint':name in ('film_music_search','film_music_import')}}

TEXT={'type':'string'}
MUSIC_TOOLS=[
    _tool('film_music_search','Find downloadable public music in Openverse. Default CC0, Public Domain Mark, or CC BY; retain source/credits. It does not search every commercial song.',
        {'query':TEXT,'limit':{'type':'integer','minimum':1,'maximum':12,'default':12}},['query'],True),
    _tool('film_music_import','Import one result ID from film_music_search, retain catalogue attribution and original download, verify actual audio. No arbitrary URL or paid account.',
        {'track_id':TEXT},['track_id']),
    _tool('film_music_analyze','Estimate tempo and beat times from an imported audio file using bounded local energy analysis. Estimates can be half/double tempo; not a musical transcription.',
        {'asset_id':TEXT,'max_seconds':{'type':'number','minimum':1,'maximum':600,'default':600}},['asset_id'],True),
    _tool('film_music_cut_plan','Plan music-led picture cuts at exact project frames without modifying the timeline. Prefer observed onset times from imported audio; an explicit BPM grid requires an explicit or analyzed source-song beat phase. Tempo/phase remain estimates when analyzed.',
        {'duration_seconds':{'type':'number','exclusiveMinimum':0,'maximum':3600},'asset_id':TEXT,
         'bpm':{'type':'number','minimum':30,'maximum':300},
         'beat_phase_seconds':{'type':'number','minimum':0,'maximum':86400},
         'source_offset_seconds':{'type':'number','minimum':0,'maximum':86400,'default':0},
         'beats_per_cut':{'type':'integer','minimum':1,'maximum':64,'default':1},
         'timeline_start_frame':{'type':'integer','minimum':0,'maximum':1000000000,'default':0},
         'project_path':TEXT,'fps_num':{'type':'integer','minimum':1,'maximum':1000000},
         'fps_den':{'type':'integer','minimum':1,'maximum':1000000}},['duration_seconds'],True),
    _tool('film_music_score','Fit imported music to the active sequence video span, trimming or looping, level and overall fades. Add a dedicated audio track in one revision-safe transaction; keep dialogue/video unchanged. Beat markers are estimates; no automatic recutting.',
        {'asset_id':TEXT,'project_path':TEXT,'expected_revision':TEXT,
         'source_offset_seconds':{'type':'number','minimum':0,'default':0},
         'gain_db':{'type':'number','minimum':-60,'maximum':0,'default':-18},
         'fade_in_seconds':{'type':'number','minimum':0,'maximum':30,'default':0.6},
         'fade_out_seconds':{'type':'number','minimum':0,'maximum':30,'default':1.2},
         'loop':{'type':'boolean','default':True},'beat_markers':{'type':'boolean','default':True}},
        ['asset_id','project_path','expected_revision']),
]

def _number(value,label,minimum,maximum):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum<=value<=maximum:
        raise ValueError(f'{label} must be finite and between {minimum} and {maximum}')
    return float(value)

def _https_address(url):
    parts=urlsplit(url)
    if parts.scheme!='https' or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError('Music downloads require public HTTPS URLs without credentials')
    try: port=parts.port
    except ValueError as exc: raise ValueError('Invalid music URL port') from exc
    if port not in (None,443): raise ValueError('Music downloads require HTTPS port 443')
    host=parts.hostname.encode('idna').decode('ascii')
    if len(host)>253 or any(c.isspace() for c in host): raise ValueError('Invalid music host')
    records=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    addresses=[]
    for record in records:
        address=record[4][0]
        ip=ipaddress.ip_address(address.split('%')[0])
        if not ip.is_global: raise ValueError('Private or reserved music addresses are prohibited')
        if address not in addresses: addresses.append(address)
    if not addresses: raise ValueError('Music host has no public address')
    return host,addresses,parts

class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,addresses,timeout):
        super().__init__(host,443,timeout=timeout,context=ssl.create_default_context())
        self.addresses=addresses
        self.connected_socket=None
        self.deadline=time.monotonic()+timeout
        self.cancelled=lambda:False
        self.retain_socket=True

    def close(self):
        # HTTPConnection closes itself as soon as it sees Connection: close,
        # although HTTPResponse still owns its readable file. Keep the socket
        # itself available for our deadline watchdog until the body is finished.
        if self.retain_socket:
            self.sock=None
            return
        retained=self.connected_socket
        super().close()
        if retained is not None:retained.close()
        self.connected_socket=None

    def connect(self):
        last=None
        for address in self.addresses:
            remaining=self.deadline-time.monotonic()
            if remaining<=0 or self.cancelled(): raise TimeoutError('Music connection timed out or was cancelled')
            raw=None; secure=None
            try:
                raw=socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET,socket.SOCK_STREAM)
                self.connected_socket=raw
                raw.settimeout(min(self.timeout,remaining))
                raw.connect((address,443))
                remaining=self.deadline-time.monotonic()
                if remaining<=0 or self.cancelled(): raise TimeoutError('Music connection timed out or was cancelled')
                # Expose the TLS socket before its handshake so the deadline watchdog
                # can interrupt a peer that stalls before HTTP starts.
                secure=self._context.wrap_socket(raw,server_hostname=self.host,do_handshake_on_connect=False)
                self.connected_socket=secure; self.sock=secure
                secure.settimeout(min(self.timeout,remaining))
                secure.do_handshake()
                if time.monotonic()>=self.deadline or self.cancelled(): raise TimeoutError('Music connection timed out or was cancelled')
                return
            except OSError as exc:
                if secure is not None: secure.close()
                if raw is not None: raw.close()
                self.sock=None; self.connected_socket=None; last=exc
        raise last or ValueError('Cannot connect to music host')

class Music:
    def __init__(self,workspace):
        self.ws=workspace
        self.lock=threading.RLock()
        self.directory=workspace.root/'.film/music'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.catalogue_path=self.directory/'catalogue.json'
        try: self.catalogue=json.loads(self.catalogue_path.read_text())
        except (OSError,ValueError): self.catalogue={}
        self.retry_until=0
        with workspace.lock:
            if not hasattr(workspace,'music_download_slots'):
                workspace.music_download_slots=threading.BoundedSemaphore(1)
            self.download_slots=workspace.music_download_slots

    def dispatch(self,name,args):
        methods={'film_music_search':self.search,'film_music_import':self.import_track,
                 'film_music_analyze':self.analyze,'film_music_score':self.score,
                 'film_music_cut_plan':self.cut_plan}
        if name not in methods: raise ValueError('Unknown music tool')
        if not isinstance(args,dict): raise ValueError('Arguments must be an object')
        return methods[name](**args)

    def _request(self,url,max_bytes,*,destination=None,timeout=30):
        """Validate each redirect, pin a checked IP, preserve TLS SNI, cap time/bytes."""
        if time.monotonic()<self.retry_until: raise ValueError('Openverse is rate limited; wait before searching again')
        deadline=time.monotonic()+timeout
        current=url
        for redirect in range(5):
            if self.ws.stopping: raise ValueError('Workspace is closing')
            host,addresses,parts=_https_address(current)
            remaining=deadline-time.monotonic()
            if remaining<=0: raise ValueError('Music request timed out')
            connection=_PinnedHTTPS(host,addresses,min(10,remaining))
            connection.deadline=deadline
            connection.cancelled=lambda:self.ws.stopping
            response=None
            def expire():
                if connection.connected_socket is not None:
                    try: connection.connected_socket.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
            timer=threading.Timer(remaining,expire); timer.daemon=True; timer.start()
            try:
                connection.request('GET',(parts.path or '/')+('?' + parts.query if parts.query else ''),headers={
                    'User-Agent':'FilmEditor/0.2 (local open music integration)',
                    'Accept':'application/json' if destination is None else 'audio/*,application/ogg;q=0.9,application/octet-stream;q=0.5'})
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location or redirect==4: raise ValueError('Music provider redirected too many times')
                    current=urljoin(current,location)
                    continue  # The next address, protocol and TLS host are revalidated.
                if response.status==429:
                    try: delay=min(3600,max(1,int(response.getheader('Retry-After') or '60')))
                    except ValueError: delay=60
                    self.retry_until=time.monotonic()+delay
                    raise ValueError(f'Openverse/provider rate limit reached. Retry after {delay} seconds.')
                if response.status!=200: raise ValueError(f'Music provider returned HTTP {response.status}')
                declared=response.getheader('Content-Length')
                if declared and (not declared.isdecimal() or int(declared)>max_bytes): raise ValueError('Music response exceeds the size limit')
                count=0; data=bytearray()
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0 or self.ws.stopping: raise ValueError('Music request timed out or was cancelled')
                    if connection.connected_socket is not None: connection.connected_socket.settimeout(min(10,remaining))
                    chunk=response.read1(min(64*1024,max_bytes-count+1))
                    if time.monotonic()>=deadline or self.ws.stopping: raise ValueError('Music request timed out or was cancelled')
                    if not chunk: break
                    count+=len(chunk)
                    if count>max_bytes: raise ValueError('Music response exceeds the size limit')
                    if destination is None: data.extend(chunk)
                    else: destination.write(chunk)
                if declared and count!=int(declared): raise ValueError('Incomplete music download')
                if not count: raise ValueError('Music provider returned an empty file')
                if time.monotonic()>=deadline or self.ws.stopping: raise ValueError('Music request timed out or was cancelled')
                return {'data':bytes(data),'bytes':count,'url':current,'mime':response.getheader('Content-Type','').split(';')[0],
                        'rate_limits':{key:value for key,value in response.getheaders() if key.lower().startswith('x-ratelimit-')}}
            except (OSError,http.client.HTTPException) as exc:
                raise ValueError('Music network request failed: '+str(exc)) from exc
            finally:
                timer.cancel()
                if response is not None: response.close()
                connection.retain_socket=False
                connection.close()
        raise ValueError('Music redirect limit exceeded')

    @staticmethod
    def _metadata(item):
        ident=str(uuid.UUID(item['id']))
        license_=str(item.get('license','')).lower()
        if license_ not in ALLOWED_LICENSES: raise ValueError('This catalogue licence is not enabled for soundtrack edits')
        if item.get('mature') or item.get('category') not in (None,'music'): raise ValueError('This is not a suitable music result')
        version=str(item.get('license_version') or ('' if license_=='by' else '1.0'))
        if version not in ('1.0','2.0','2.5','3.0','4.0'): raise ValueError('Unknown licence version')
        license_url=('https://creativecommons.org/licenses/by/'+version+'/' if license_=='by' else
                     'https://creativecommons.org/publicdomain/'+('zero' if license_=='cc0' else 'mark')+'/1.0/')
        if license_!='by' and version!='1.0': raise ValueError('Unknown public-domain mark/dedication version')
        declared=item.get('license_url')
        if declared:
            terms=urlsplit(declared)
            expected=urlsplit(license_url).path
            if terms.scheme!='https' or terms.hostname not in ('creativecommons.org','www.creativecommons.org') or terms.username is not None or terms.password is not None or terms.query or terms.fragment or not terms.path.startswith(expected):
                raise ValueError('Catalogue licence URL does not match its declared licence')
            # Keep official ported/deed/legalcode URLs rather than replacing source terms.
            license_url=declared
        raw_url=item.get('url')
        parts=urlsplit(raw_url or '')
        if parts.scheme!='https' or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError('This result has no public HTTPS audio download')
        def text(name,fallback=''):
            return str(item.get(name) or fallback).replace('\x00','')[:3000]
        def link(name):
            value=text(name); parsed=urlsplit(value)
            return value if parsed.scheme=='https' and parsed.hostname and parsed.username is None and parsed.password is None else ''
        duration=item.get('duration')
        seconds=float(duration)/1000 if isinstance(duration,(int,float)) and not isinstance(duration,bool) and math.isfinite(duration) and duration>0 else None
        title=text('title','Untitled'); creator=text('creator','Unknown creator')
        attribution=text('attribution') or f'"{title}" by {creator}. '+('CC BY '+version if license_=='by' else 'CC0 1.0' if license_=='cc0' else 'Public Domain Mark 1.0')+'. '+license_url
        return {'id':ident,'title':title,'creator':creator,'creator_url':link('creator_url'),
                'source_url':link('foreign_landing_url'),'download_url':raw_url,'provider':text('provider'),
                'source':text('source'),'license':license_,'license_version':version,'license_url':license_url,
                'attribution':attribution,'duration_seconds':seconds,'filetype':text('filetype'),
                'preview_only':item.get('source')=='freesound','license_evidence':'Openverse/provider metadata; verify source terms for publication',
                'discovered_at':time.time()}

    def search(self,query,limit=12):
        if not isinstance(query,str) or not 1<=len(query.strip())<=200: raise ValueError('Enter a music query of 1–200 characters')
        if type(limit) is not int or not 1<=limit<=12: raise ValueError('Search limit must be 1–12')
        reply=self._request(API+'audio/?'+urlencode({'q':query.strip(),'license':'cc0,pdm,by','page_size':limit}),4*1024**2)
        data=json.loads(reply['data'])
        if not isinstance(data,dict) or not isinstance(data.get('results'),list): raise ValueError('Invalid Openverse search response')
        tracks=[]
        for item in data['results'][:limit]:
            try: track=self._metadata(item)
            except (ValueError,KeyError,TypeError): continue
            tracks.append(track)
        with self.lock:
            for track in tracks: self.catalogue[track['id']]=track
            from .core import atomic
            if len(self.catalogue)>500:
                self.catalogue=dict(sorted(self.catalogue.items(),key=lambda item:item[1].get('discovered_at',0),reverse=True)[:500])
            atomic(self.catalogue_path,json.dumps(self.catalogue,ensure_ascii=False).encode())
        return {'tracks':copy.deepcopy(tracks),'query':query.strip(),'provider':'Openverse','rate_limits':reply['rate_limits'],
                'limitations':['Search covers the public catalogue, not every commercial song. Use an authorized local file for a specific song.',
                               'Rights metadata comes from the source catalogue; retain credits and check the source before publication.']}

    def _probe(self,path):
        result=self.ws.run_media([self.ws.executable('ffprobe'),'-v','error',*INPUT_OPTIONS,
            '-show_streams','-show_format','-of','json',str(path)],timeout=30)
        if result.returncode: raise ValueError('Unsupported or unreadable audio: '+result.stderr.decode(errors='replace')[-1000:])
        if len(result.stdout)>1024**2: raise ValueError('Audio metadata is too large')
        data=json.loads(result.stdout)
        audio=next((s for s in data.get('streams',[]) if s.get('codec_type')=='audio'),None)
        if audio is None: raise ValueError('The file has no audio stream')
        duration=_number(float(data.get('format',{}).get('duration') or audio.get('duration') or 0),'Audio duration',0.001,86400)
        return data,audio,duration

    def import_track(self,track_id):
        # A single per-workspace transfer bounds network/staging use separately
        # from the single heavy media job. Waiting imports remain cancellable.
        while not self.download_slots.acquire(timeout=.2):
            if self.ws.stopping: raise ValueError('Music import cancelled while queued')
        try:
            if self.ws.stopping: raise ValueError('Workspace is closing')
            return self._import_track(track_id)
        finally: self.download_slots.release()

    def _import_track(self,track_id):
        try: ident=str(uuid.UUID(track_id))
        except (ValueError,TypeError,AttributeError) as exc: raise ValueError('Choose a track ID from music search') from exc
        with self.lock:
            if ident not in self.catalogue: raise ValueError('Search for this track before importing it')
        # Refresh at the fixed API endpoint; licence/download changes cannot be hidden by a stale cache.
        detail=self._request(API+'audio/'+ident+'/',4*1024**2)
        metadata=self._metadata(json.loads(detail['data']))
        if metadata['id']!=ident: raise ValueError('Catalogue track ID changed')
        sources=self.directory/'sources'; sources.mkdir(exist_ok=True,mode=0o700)
        source=sources/(ident+'-'+uuid.uuid4().hex+'.download')
        normalized=None
        try:
            with source.open('xb') as stream: download=self._request(metadata['download_url'],MAX_DOWNLOAD,destination=stream,timeout=120)
            with self.ws.heavy():
                info,audio,duration=self._probe(source)
                format_=info.get('format',{}).get('format_name','').split(',')[0]
                extension={'mp3':'.mp3','wav':'.wav','flac':'.flac','ogg':'.ogg','aac':'.aac','mov':'.m4a','aiff':'.aiff','asf':'.wma','matroska':'.mkv'}.get(format_)
                from .core import MEDIA
                if extension not in MEDIA:
                    normalized=sources/(uuid.uuid4().hex+'.m4a')
                    result=self.ws.run_media([self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*INPUT_OPTIONS,
                        '-i',str(source),'-map','0:a:0','-vn','-c:a','aac','-b:a','192k','-threads','1',str(normalized)],timeout=120)
                    if result.returncode: raise ValueError('Music could not be decoded: '+result.stderr.decode(errors='replace')[-1000:])
                    imported=normalized; extension='.m4a'
                else:
                    # Probe alone does not prove the audio stream decodes. A short decode rejects encrypted/broken input.
                    decoded=self.ws.run_media([self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*INPUT_OPTIONS,
                        '-i',str(source),'-map','0:a:0','-t','2','-vn','-f','null','-'],timeout=30)
                    if decoded.returncode: raise ValueError('Music audio did not decode: '+decoded.stderr.decode(errors='replace')[-1000:])
                    imported=source
            title=re.sub(r'[^\w .()-]','-',metadata['title'])[:100].strip() or 'Music'
            with imported.open('rb') as stream: asset=self.ws.ingest(stream,imported.stat().st_size,title+extension,'audio')
            source.chmod(0o444)
            metadata.update(original_download=str(source),download_bytes=download['bytes'],download_final_url=download['url'],
                            imported_at=time.time(),duration_seconds=duration,transcoded=normalized is not None,
                            changes='Audio container/codec converted for editing.' if normalized else 'Imported without changing the audio file.')
            self._set_asset_metadata(asset['id'],metadata)
            from .core import atomic
            credit=self.ws.root/'references'/('music-credit-'+ident+'.txt')
            atomic(credit,self._credit(metadata).encode())
            return {'asset':self.ws.asset(asset['id']),'credit_path':str(credit),'attribution':self._credit(metadata),
                    'preview_only':metadata['preview_only']}
        except Exception:
            source.unlink(missing_ok=True)
            raise
        finally:
            if normalized: normalized.unlink(missing_ok=True)

    def _set_asset_metadata(self,ident,metadata):
        with self.ws.lock:
            for item in self.ws.data['assets']:
                if item['id']==ident: item['music']=copy.deepcopy(metadata); self.ws.save(); return
        raise ValueError('Imported music asset disappeared')

    @staticmethod
    def _credit(metadata,changes=None):
        value=metadata.get('attribution') or 'User supplied local music; no licence assertion made by FilmEditor.'
        if metadata.get('source_url'): value+='\nSource: '+metadata['source_url']
        if metadata.get('license_url'): value+='\nTerms: '+metadata['license_url']
        return value+'\nChanges: '+(changes or metadata.get('changes') or 'None recorded.')+'\n'

    @staticmethod
    def estimate_pcm(raw,sample_rate=8000):
        """20 ms RMS onset envelope with normalized autocorrelation over 50–200 BPM."""
        samples=array('h'); samples.frombytes(raw[:len(raw)//2*2])
        if sys.byteorder!='little': samples.byteswap()
        hop=max(1,round(sample_rate*.02)); energies=[]; peak=0
        for start in range(0,len(samples),hop):
            block=samples[start:start+hop]
            if not block: break
            peak=max(peak,max(abs(value) for value in block))
            energies.append(math.sqrt(sum(value*value for value in block)/len(block))/32768)
        onsets=[]
        for index,energy in enumerate(energies):
            recent=energies[max(0,index-5):index]
            baseline=sum(recent)/len(recent) if recent else 0
            onsets.append(max(0,energy-baseline*1.15))
        norm=sum(value*value for value in onsets)
        duration=len(samples)/sample_rate
        if len(onsets)<150 or norm<1e-8:
            return {'estimated_bpm':None,'confidence':0,'beats_seconds':[],'detected_beats_seconds':[],
                    'beat_phase_seconds':None,'duration_analyzed_seconds':duration,'peak_pcm':peak/32768}
        best_lag=0; best=0
        for lag in range(15,61):
            left=sum(value*value for value in onsets[:-lag]); right=sum(value*value for value in onsets[lag:])
            correlation=sum(onsets[index]*onsets[index+lag] for index in range(len(onsets)-lag))/math.sqrt(max(left*right,1e-16))
            if correlation>best+0.002: best=correlation; best_lag=lag
        confidence=min(1,max(0,best))*min(1,duration/8)
        if best_lag==0 or best<0.3:
            return {'estimated_bpm':None,'confidence':round(confidence,3),'beats_seconds':[],'detected_beats_seconds':[],
                    'beat_phase_seconds':None,'duration_analyzed_seconds':duration,'peak_pcm':peak/32768}
        phase=max(range(best_lag),key=lambda offset:sum(onsets[offset::best_lag]))
        beats=[round(index*hop/sample_rate,4) for index in range(phase,len(onsets),best_lag)]
        # Retain observed peaks near the estimated beat grid rather than calling
        # every extrapolated grid point a detected beat. Empty/quiet windows have
        # no observed beat; consumers must preserve that distinction.
        observed=[]; radius=max(1,round(best_lag*.3)); threshold=max(onsets)*.08
        for expected in range(phase,len(onsets),best_lag):
            low=max(0,expected-radius); high=min(len(onsets),expected+radius+1)
            index=max(range(low,high),key=lambda i:onsets[i])
            if onsets[index]>max(1e-6,threshold) and (not observed or index>observed[-1]): observed.append(index)
        return {'estimated_bpm':round(60*sample_rate/(hop*best_lag),2),'confidence':round(confidence,3),
                'beats_seconds':beats,'detected_beats_seconds':[round(index*hop/sample_rate,4) for index in observed],
                'beat_phase_seconds':round(phase*hop/sample_rate,4),'onset_resolution_seconds':hop/sample_rate,
                'duration_analyzed_seconds':duration,'peak_pcm':round(peak/32768,5)}

    def analyze(self,asset_id,max_seconds=600):
        seconds=_number(max_seconds,'max_seconds',1,600)
        asset=self.ws.asset(asset_id); path=self.ws.path(asset['path'])
        stamp=hashlib.sha256(f'beat-v2:{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}:{seconds}'.encode()).hexdigest()
        cache=self.directory/('analysis-'+stamp+'.json')
        if cache.exists(): return json.loads(cache.read_text())
        with self.ws.heavy():
            _,audio,duration=self._probe(path)
            result=self.ws.run_media([self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*INPUT_OPTIONS,
                '-i',str(path),'-map','0:a:0','-vn','-t',str(seconds),'-ac','1','-ar','8000','-f','s16le','pipe:1'],timeout=120)
        if result.returncode: raise ValueError('Audio analysis failed: '+result.stderr.decode(errors='replace')[-1000:])
        if len(result.stdout)>math.ceil(seconds*8000)*2+4096: raise ValueError('Audio analysis exceeded its PCM bound')
        value=self.estimate_pcm(result.stdout)
        value.update(asset_id=asset_id,audio_duration_seconds=duration,codec=audio.get('codec_name'),
                     method='20ms RMS onset-envelope autocorrelation; heuristic confidence, not a probability',
                     estimated=True,partial=duration>seconds+.1,
                     limitations=['Rhythm estimates can be half/double tempo and may miss rubato, syncopation or quiet onsets.',
                                  'No automatic video cuts or semantic song understanding.'])
        from .core import atomic
        atomic(cache,json.dumps(value).encode())
        return value

    def _plan_fps(self,project_path,fps_num,fps_den):
        from .core import unpack
        if project_path is not None and (fps_num is not None or fps_den is not None):
            raise ValueError('Use project_path or explicit fps_num/fps_den, not both')
        path=None; revision=None
        if fps_num is not None or fps_den is not None:
            if type(fps_num) is not int or type(fps_den) is not int or not 1<=fps_num<=1000000 or not 1<=fps_den<=1000000:
                raise ValueError('Supply positive integer fps_num and fps_den together')
            fps=Fraction(fps_num,fps_den)
        else:
            path=self.ws.path(project_path) if project_path is not None else self.ws.active()
            snapshot=unpack(self.ws.forward('inspect_project',{'path':str(path)})); profile=snapshot['profile']
            revision=snapshot.get('revision')
            try:
                if 'frame_rate_num' in profile and 'frame_rate_den' in profile:
                    fps=Fraction(int(profile['frame_rate_num']),int(profile['frame_rate_den']))
                else: fps=Fraction(str(profile['fps'])).limit_denominator(1000000)
            except (TypeError,ValueError,ZeroDivisionError,KeyError) as exc: raise ValueError('Project has no usable frame rate') from exc
        _number(float(fps),'Project fps',1,240)
        return fps,path,revision

    def cut_plan(self,duration_seconds,asset_id=None,bpm=None,beat_phase_seconds=None,source_offset_seconds=0,
                 beats_per_cut=1,timeline_start_frame=0,project_path=None,fps_num=None,fps_den=None):
        """Return revision-independent frame boundaries; authoring is a later edit.

        A song's offset is independent of beat phase. We never silently assume
        that the start of a file or the start of a trimmed song is a downbeat.
        """
        duration=_number(duration_seconds,'duration_seconds',.000001,3600)
        offset=_number(source_offset_seconds,'source_offset_seconds',0,86400)
        if type(beats_per_cut) is not int or not 1<=beats_per_cut<=64: raise ValueError('beats_per_cut must be an integer from 1 to 64')
        if type(timeline_start_frame) is not int or not 0<=timeline_start_frame<=1000000000: raise ValueError('timeline_start_frame must be a nonnegative integer')
        tempo=_number(bpm,'bpm',30,300) if bpm is not None else None
        phase=_number(beat_phase_seconds,'beat_phase_seconds',0,86400) if beat_phase_seconds is not None else None
        if asset_id is None and (tempo is None or phase is None): raise ValueError('Supply an imported audio asset, or explicit bpm and beat_phase_seconds')
        if asset_id is not None: self.ws.asset(asset_id)
        fps,path,revision=self._plan_fps(project_path,fps_num,fps_den)
        rate=float(fps); frames=math.floor(duration*rate+.5)
        if frames<1: raise ValueError('The requested duration must cover at least one project frame')
        quantized_duration=frames/rate
        analysis=None; warnings=[]
        if tempo is None or phase is None:
            analysis=self.analyze(asset_id)
            if analysis.get('estimated_bpm') is None: raise ValueError('No usable beat estimate; supply explicit bpm and beat_phase_seconds after listening')
            detected=analysis.get('detected_beats_seconds') or []
            grid=analysis.get('beats_seconds') or []
            if tempo is None: tempo=_number(analysis['estimated_bpm'],'Estimated bpm',30,300)
            if phase is None:
                candidate=detected[0] if detected else analysis.get('beat_phase_seconds')
                candidate=candidate if candidate is not None else (grid[0] if grid else None)
                if candidate is None: raise ValueError('No usable beat phase; supply beat_phase_seconds after listening')
                phase=_number(candidate,'Analyzed beat phase',0,86400)
            warnings.extend(analysis.get('limitations',[]))
        tempo_origin='explicit' if bpm is not None else 'estimated'
        phase_origin='explicit' if beat_phase_seconds is not None else 'observed_onset' if analysis and analysis.get('detected_beats_seconds') else 'estimated_grid'
        observed=analysis.get('detected_beats_seconds') if analysis else None
        if analysis is not None and bpm is None and beat_phase_seconds is None:
            coverage=min(float(analysis.get('duration_analyzed_seconds',0)),float(analysis.get('audio_duration_seconds',0)))
            if offset+quantized_duration>coverage+1e-6:
                raise ValueError('Analyzed audio does not cover the requested song window; shorten the window or supply explicit bpm and beat_phase_seconds')
            source_beats=observed or analysis.get('beats_seconds',[])
            beat_source='observed_onsets' if observed else 'estimated_grid'
            audible=[float(value) for value in source_beats if offset-1e-9<=float(value)<offset+quantized_duration-1e-9]
            if observed and analysis.get('beats_seconds'):
                anchor=float(analysis['beats_seconds'][0]); interval=60/tempo
                indexed=[(math.floor((beat-anchor)/interval+.5),beat) for beat in audible]
                first_index=indexed[0][0] if indexed else 0
                selected=[beat for index,beat in indexed if (index-first_index)%beats_per_cut==0]
            else: selected=audible[::beats_per_cut]
            if not observed: warnings.append('No observed beat peaks were retained; cut times use the estimated regular grid.')
            if observed and analysis.get('beats_seconds') and len(observed)<len(analysis['beats_seconds'])*.85:
                warnings.append('Quiet beat windows were missed; cuts use observed candidates at the estimated beat-grid cadence, not a verified musical meter.')
        else:
            interval=60/tempo
            first=math.ceil((offset-phase)/interval-1e-9)
            count=max(0,math.ceil((offset+quantized_duration-phase)/interval-1e-9)-first)
            if count>18001: raise ValueError('Beat plan exceeds its event bound')
            selected=[phase+(first+index)*interval for index in range(0,count,beats_per_cut)]
            beat_source='explicit_bpm_grid' if bpm is not None else 'estimated_bpm_grid'
            if analysis: warnings.append('BPM or phase was overridden; a regular grid anchored to the selected phase is used instead of observed onset timing.')
            warnings.append('A regular BPM grid assumes constant tempo; its song duration and musical downbeat are not independently verified.')
        cuts=[]; seen=set()
        for beat in selected:
            relative=beat-offset; frame=timeline_start_frame+math.floor(relative*rate+.5)
            if not timeline_start_frame<frame<timeline_start_frame+frames or frame in seen: continue
            seen.add(frame)
            cuts.append({'frame':frame,'time_seconds':frame/rate,'relative_seconds':(frame-timeline_start_frame)/rate,
                         'source_beat_seconds':round(beat,9),'quantization_error_seconds':round((frame-timeline_start_frame)/rate-relative,9)})
        cuts.sort(key=lambda item:item['frame'])
        if not cuts: warnings.append('No internal cut boundaries fell in the requested window; this plan contains a single segment.')
        boundaries=[timeline_start_frame]+[item['frame'] for item in cuts]+[timeline_start_frame+frames]
        segments=[{'start_frame':start,'end_frame_exclusive':end,'duration_frames':end-start}
                  for start,end in zip(boundaries,boundaries[1:])]
        half_beat=30/tempo
        if cuts and segments[0]['duration_frames']/rate<half_beat:
            warnings.append('The first cut falls within half a beat of the music start; the opening is a partial beat. Review this short opening segment before authoring.')
        if cuts and segments[-1]['duration_frames']/rate<half_beat:
            warnings.append('The last segment is shorter than half a beat; the requested end creates a partial closing beat. Review this short closing segment before authoring.')
        if analysis: warnings.append('Beat detection is an onset-envelope estimate and may be half/double tempo; listen and review the resulting edit.')
        return {'asset_id':asset_id,'project_path':str(path) if path is not None else None,'project_revision':revision,
                'fps_num':fps.numerator,'fps_den':fps.denominator,'fps':rate,'bpm':tempo,'bpm_origin':tempo_origin,
                'beat_phase_seconds':phase,'phase_origin':phase_origin,'beat_source':beat_source,'beats_per_cut':beats_per_cut,
                'source_offset_seconds':offset,'timeline_start_frame':timeline_start_frame,'duration_frames':frames,
                'requested_duration_seconds':duration,'duration_seconds':quantized_duration,'end_frame_exclusive':timeline_start_frame+frames,
                'cut_frames':[item['frame'] for item in cuts],'cuts':cuts,'segments':segments,
                'heuristic_beat_confidence':analysis.get('confidence') if analysis else None,
                'analysis_partial':analysis.get('partial',False) if analysis else None,'warnings':list(dict.fromkeys(warnings)),
                'video_recut':False,'dialogue_modified':False,
                'authoring_note':'Use these exact frames in a separately authorized revision-safe picture edit; this tool only plans boundaries.'}

    @staticmethod
    def _span(snapshot):
        items=[item for track in snapshot['tracks'] if track['kind']=='video'
               for item in track['items'] if item['type']!='gap' and item.get('duration_frames',0)>0]
        if not items: raise ValueError('Add video clips to the sequence before fitting a soundtrack')
        return min(item['start_frame'] for item in items),max(item['start_frame']+item['duration_frames'] for item in items)

    def score(self,asset_id,project_path,expected_revision,source_offset_seconds=0,gain_db=-18,
              fade_in_seconds=.6,fade_out_seconds=1.2,loop=True,beat_markers=True):
        from .core import atomic, unpack
        from .editor import Editor
        gain=_number(gain_db,'gain_db',-60,0)
        offset=_number(source_offset_seconds,'source_offset_seconds',0,86400)
        fade_in=_number(fade_in_seconds,'fade_in_seconds',0,30)
        fade_out=_number(fade_out_seconds,'fade_out_seconds',0,30)
        if type(loop) is not bool or type(beat_markers) is not bool: raise ValueError('loop and beat_markers must be booleans')
        if not isinstance(expected_revision,str) or not re.fullmatch('[a-f0-9]{64}',expected_revision): raise ValueError('Supply the exact current expected_revision')
        path=self.ws.path(project_path)
        with self.ws.lock:
            if path!=self.ws.active(): raise ValueError('Active sequence changed. Inspect before scoring.')
        snapshot=unpack(self.ws.forward('inspect_project',{'path':str(path)}))
        if snapshot['revision']!=expected_revision: raise ValueError('Timeline changed. Inspect before scoring.')
        start,end=self._span(snapshot)
        fps=_number(float(snapshot['profile']['fps']),'Sequence fps',1,240)
        duration=(end-start)/fps
        if duration>3600: raise ValueError('Soundtrack fitting is bounded to one hour per sequence')
        asset=self.ws.asset(asset_id); source=self.ws.path(asset['path'])
        analysis=self.analyze(asset_id)
        source_duration=analysis['audio_duration_seconds']
        if offset>=source_duration or source_duration<.25: raise ValueError('Choose an audio offset inside a track at least 0.25 seconds long')
        frames=end-start if loop else min(end-start,math.floor((source_duration-offset)*fps))
        if frames<1:raise ValueError('The remaining song must cover at least one sequence frame without looping')
        duration=frames/fps
        # Keep the two global fades from crossing. Scale requested fades proportionally
        # for short edits, rather than passing overlapping afade intervals to FFmpeg.
        if fade_in+fade_out>duration:
            ratio=duration/(fade_in+fade_out); fade_in*=ratio; fade_out*=ratio
        title=asset.get('music',{}).get('title') or asset['name']
        track='Music'
        existing={t['name'] for t in snapshot['tracks']}
        index=2
        while track in existing: track='Music '+str(index); index+=1
        fitted=self.ws.root/'generated'/('soundtrack-'+uuid.uuid4().hex+'.wav')
        committed=False
        # Trim within the decoded stream, then reset its clock before the global
        # fades. Output -ss would discard audio after afade already ran, making
        # source offsets silently skip the fitted fade-in.
        filters=[f'atrim=start={offset:.9f}','asetpts=PTS-STARTPTS',f'volume={gain:g}dB']
        if fade_in: filters.append(f'afade=t=in:st=0:d={fade_in:.9f}')
        if fade_out: filters.append(f'afade=t=out:st={max(0,duration-fade_out):.9f}:d={fade_out:.9f}')
        command=[self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*INPUT_OPTIONS]
        if loop: command+=['-stream_loop','-1']
        command+=['-i',str(source),'-t',f'{duration:.9f}','-map','0:a:0','-vn',
                  '-af',','.join(filters),'-ac','2','-ar','48000','-c:a','pcm_s16le','-threads','1',str(fitted)]
        try:
            with self.ws.heavy():
                result=self.ws.run_media(command,timeout=180)
                if result.returncode or not fitted.is_file(): raise ValueError('Soundtrack fitting failed: '+result.stderr.decode(errors='replace')[-1200:])
                _,_,fitted_duration=self._probe(fitted)
                if abs(fitted_duration-duration)>1/fps+.01: raise ValueError('Fitted audio duration did not match the requested frame span')
            changes=f'Trimmed from {offset:g}s; '+('looped as needed; ' if loop else 'not looped; ')+f'level {gain:g} dB; overall fade in {fade_in:.3f}s and fade out {fade_out:.3f}s.'
            credit=self._credit(asset.get('music',{}),changes)
            notes=(snapshot.get('notes') or '').rstrip()
            notes=(notes+'\n\n' if notes else '')+'Soundtrack: '+title+'\n'+credit
            operations=[{'op':'add_track','kind':'audio','name':track},
                        {'op':'add_clip','track':track,'path':str(fitted),'position_frame':start,
                         'in_frame':0,'out_frame':frames-1,'mode':'overwrite','caption':'Music · '+title},
                        {'op':'set_notes','notes':notes}]
            markers=[]
            if beat_markers and analysis['beats_seconds'] and analysis['confidence']>=.3:
                repeats=math.ceil((offset+duration)/source_duration) if loop else 1
                for repeat in range(repeats):
                    for beat in analysis['beats_seconds']:
                        relative=beat+repeat*source_duration-offset
                        frame=start+round(relative*fps)
                        if 0<=relative<duration and start<=frame<start+frames: markers.append(frame)
                markers=sorted(set(markers))
                all_count=len(markers)
                if len(markers)>64: markers=[markers[round(i*(len(markers)-1)/63)] for i in range(64)]
                operations += [{'op':'add_marker','start_frame':frame,'text':'Music beat (estimated)','color':'#6699ff'} for frame in markers]
            else: all_count=0
            if len(operations)>100: raise ValueError('Soundtrack transaction exceeds the edit limit')
            # Analysis/rendering can take time: recheck active identity and revision
            # while holding the same lock used by manual/MCP edits and the selection lock.
            lock=getattr(self.ws,'edit_lock',self.lock)
            with lock,self.ws.lock:
                if path!=self.ws.active(): raise ValueError('Active sequence changed while preparing music. Inspect again.')
                self.ws.asset(asset_id)  # Reject a bin asset removed during preparation.
                current=unpack(self.ws.forward('inspect_project',{'path':str(path)}))
                if current['revision']!=expected_revision: raise ValueError('Timeline changed while preparing music. Inspect again.')
                Editor.check_locks(current,operations)
                edit=unpack(self.ws.forward('edit_project',{'project_path':str(path),'expected_revision':expected_revision,'operations':operations}))
                committed=True
                generated=self.ws.register(fitted,'Soundtrack · '+title,'audio')
                self._set_asset_metadata(generated['id'],dict(asset.get('music',{}),source_asset_id=asset_id,changes=changes))
                self.ws.data.setdefault('music_usages',[]).append({'project_path':str(path),'revision':edit['revision'],
                    'source_asset_id':asset_id,'fitted_asset_id':generated['id'],'track':track,'attribution':credit})
                self.ws.save()
            credits_path=self.ws.root/'references'/('music-credits-'+path.stem+'-'+uuid.uuid4().hex[:8]+'.txt')
            atomic(credits_path,credit.encode())
            return {'project_path':str(path),'revision':edit['revision'],'track':track,'fitted_asset':self.ws.asset(generated['id']),
                    'start_frame':start,'end_frame_exclusive':start+frames,'duration_seconds':duration,
                    'gain_db':gain,'fade_in_seconds':fade_in,'fade_out_seconds':fade_out,'loop':loop,
                    'attribution':credit,'credits_path':str(credits_path),'estimated_bpm':analysis['estimated_bpm'],
                    'beat_confidence':analysis['confidence'],'beat_markers_frames':markers,'estimated_beats_in_span':all_count,
                    'beat_markers_sampled':all_count>len(markers),'beat_analysis_partial':analysis['partial'],
                    'video_recut':False,'dialogue_modified':False,'original_audio_preserved':True,
                    'mix_note':'Music level is an explicit gain setting, not automatic dialogue ducking or final mix certification.'}
        except Exception:
            # Retain a committed soundtrack; discard a preparation that never entered MLT.
            if not committed: fitted.unlink(missing_ok=True)
            raise
