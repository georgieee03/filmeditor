"""Bounded reference-film measurements and reversible local audio extraction.

Pixel differences identify candidates, not an editing technique or creative
intent. Audio pulse estimates do not establish musical meter or soundtrack
identity. The caller must inspect the returned images before describing a cut.
"""
from __future__ import annotations
import bisect
import json
import math
import os
from pathlib import Path
import statistics
import struct
import uuid
import zlib

from .music import Music, _number

MAX_SECONDS=300
WIDTH,HEIGHT=96,54
FRAME_BYTES=WIDTH*HEIGHT*3
INPUT_OPTIONS=['-protocol_whitelist','file,pipe','-format_whitelist',
               'mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,mxf,mp3,wav,flac,ogg,aac,aiff,asf']

def _tool(name,description,properties,required,read=False):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,
        'required':required,'additionalProperties':False},
        'annotations':{'readOnlyHint':read,'destructiveHint':False,'openWorldHint':False}}

REFERENCE_TOOLS=[
    _tool('film_reference_analyze','Decode a bounded imported reference-video window, measure candidate hard cuts/gradual changes, and compare candidate cuts with audio pulse estimates when available. Return actual review frames/contact sheet for film_view_image. Candidate transition types require visual review; no semantic or exact cut certification.',
        {'asset_id':{'type':'string'},'start_seconds':{'type':'number','minimum':0,'maximum':86400,'default':0},
         'max_seconds':{'type':'number','minimum':1,'maximum':MAX_SECONDS,'default':60},
         'sample_fps':{'type':'integer','minimum':1,'maximum':12,'default':8}},['asset_id'],True),
    _tool('film_extract_audio','Extract the selected/default audio stream from an imported video into a new generated WAV audio asset. Preserve originals and timeline. A full source must be <=300 seconds; longer sources require an explicit bounded range. No audio is saved in a video export folder.',
        {'asset_id':{'type':'string'},'start_seconds':{'type':'number','minimum':0,'maximum':86400,'default':0},
         'duration_seconds':{'type':'number','exclusiveMinimum':0,'maximum':MAX_SECONDS},
         'audio_stream_index':{'type':'integer','minimum':0,'maximum':1024}},['asset_id']),
]

def _stream(metadata,kind,index=None):
    streams=metadata.get('streams',[])
    if not isinstance(streams,list): raise ValueError('Invalid media streams')
    def flag(item,key):
        value=item.get('disposition',{})
        return isinstance(value,dict) and value.get(key) in (1,True)
    candidates=[item for item in streams if isinstance(item,dict) and item.get('codec_type')==kind
                and not (kind=='video' and flag(item,'attached_pic'))]
    if index is not None:
        if type(index) is not int or not 0<=index<=1024: raise ValueError('audio_stream_index must be a nonnegative absolute stream index')
        selected=next((item for item in candidates if item.get('index')==index),None)
        if selected is None: raise ValueError('The selected stream is not an audio stream in this source')
    else: selected=next((item for item in candidates if flag(item,'default')),candidates[0] if candidates else None)
    if selected is not None and (type(selected.get('index')) is not int or selected['index']<0):
        raise ValueError('Selected media stream has no usable absolute index')
    return selected

def _duration(metadata,stream):
    for value in (stream.get('duration'),metadata.get('format',{}).get('duration')):
        if isinstance(value,bool): continue
        try: seconds=float(value)
        except (TypeError,ValueError): continue
        if math.isfinite(seconds) and 0<seconds<=86400*30: return seconds
    raise ValueError('Selected stream has no readable positive duration')

def _stamp(path):
    stat=path.stat()
    return [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]

def _features(frame):
    hist=[0]*48; channel_sums=[0,0,0]
    for channel in range(3):
        values=frame[channel::3]
        channel_sums[channel]=sum(values)
        for value in values: hist[channel*16+(value>>4)]+=1
    count=WIDTH*HEIGHT
    return {'histogram':[value/count for value in hist],'channel_means':[value/count/255 for value in channel_sums],
            'luma':sum(value*weight for value,weight in zip(channel_sums,(.2126,.7152,.0722)))/count/255}

def _pixel_distance(first,second):
    return sum(abs(a-b) for a,b in zip(first,second))/len(first)/255

def _hist_distance(first,second):
    return sum(abs(a-b) for a,b in zip(first['histogram'],second['histogram']))/6

def measure_transitions(raw,sample_fps,start_seconds=0):
    """Pure sampled-pixel heuristic, testable without launching a media engine."""
    if len(raw)%FRAME_BYTES: raise ValueError('Reference scan returned incomplete RGB frames')
    frames=[memoryview(raw)[start:start+FRAME_BYTES] for start in range(0,len(raw),FRAME_BYTES)]
    if len(frames)<2: raise ValueError('The reference window must contain at least two sampled frames')
    features=[_features(frame) for frame in frames]
    pixel=[0.]+[_pixel_distance(frames[i-1],frames[i]) for i in range(1,len(frames))]
    hist=[0.]+[_hist_distance(features[i-1],features[i]) for i in range(1,len(frames))]
    step=1/sample_fps; candidates=[]; hard=set(); excluded=set()
    for index in range(1,len(frames)):
        if index in excluded: continue
        # A fixed absolute threshold misses dark references. Compare a peak with
        # nearby motion using robust medians/MAD, while retaining small absolute
        # floors so codec noise is not promoted into cut evidence. Histogram and
        # pixel changes must both rise; pans with a stable palette often fail it.
        neighborhood=[i for i in range(max(1,index-sample_fps),min(len(frames),index+sample_fps+1)) if abs(i-index)>1]
        local_pixel=statistics.median(pixel[i] for i in neighborhood) if neighborhood else 0.
        pixel_mad=statistics.median(abs(pixel[i]-local_pixel) for i in neighborhood) if neighborhood else 0.
        local_hist=statistics.median(hist[i] for i in neighborhood) if neighborhood else 0.
        hist_mad=statistics.median(abs(hist[i]-local_hist) for i in neighborhood) if neighborhood else 0.
        adaptive_pixel=max(.012,local_pixel*3,local_pixel+6*pixel_mad)
        adaptive_hist=max(.025,local_hist+3*hist_mad)
        large=(pixel[index]>=.22 and hist[index]>=.12) or (pixel[index]>=.12 and hist[index]>=.65)
        adaptive=pixel[index]>=adaptive_pixel and hist[index]>=adaptive_hist
        strong=large or adaptive
        previous=pixel[index-1]; following=pixel[index+1] if index+1<len(frames) else 0.
        if not strong or min(previous,following)>pixel[index]*.65: continue
        kind='hard_cut_candidate'; end=index
        if index+1<len(frames) and _pixel_distance(frames[index-1],frames[index+1])<max(.012,pixel[index]*.3) and following>=max(.012,pixel[index]*.55):
            kind='unknown_abrupt_change_candidate'; end=index+1; excluded.add(end)
        hard.add(index)
        candidates.append({'kind':kind,'transition_type':'unknown' if kind!='hard_cut_candidate' else 'candidate hard cut',
            'time_seconds':round(start_seconds+index*step,6),
            'start_seconds':round(start_seconds+(index-1)*step,6),'end_seconds':round(start_seconds+(end+1)*step,6),
            'sample_index':index,'pixel_change':round(pixel[index],4),'histogram_change':round(hist[index],4),
            'change_scale':'large' if large else 'adaptive low contrast',
            'local_pixel_baseline':round(local_pixel,5),'adaptive_pixel_threshold':round(adaptive_pixel,5),
            'adaptive_histogram_threshold':round(adaptive_hist,5),
            'reason':'A brief return to the prior sampled image may be a flash or motion.' if kind!='hard_cut_candidate' else 'Abrupt sampled color/pixel change; motion, lighting and graphics can also trigger it.',
            'requires_visual_review':True})
    index=1
    while index<len(frames):
        if index in hard or index in excluded or pixel[index]<.008:
            index+=1; continue
        begin=index
        while index<len(frames) and index not in hard and index not in excluded and .008<=pixel[index]<.22:
            index+=1
        if index==begin: index+=1; continue
        end=index-1; length=end-begin+1
        net=_pixel_distance(frames[begin-1],frames[end]); elapsed=length*step
        if length<3 or net<.10 or elapsed>2.5: continue
        luma=[features[i]['luma'] for i in range(begin-1,end+1)]
        slopes=[b-a for a,b in zip(luma,luma[1:])]
        rising=sum(delta>.0005 for delta in slopes)/len(slopes)>=.8
        falling=sum(delta<-.0005 for delta in slopes)/len(slopes)>=.8
        subtype='unknown (possible dissolve, wipe, animation or camera motion)'; kind='gradual_transition_candidate'
        begin_channels=features[begin-1]['channel_means']; end_channels=features[end]['channel_means']
        if rising and max(begin_channels)<.08: subtype='candidate fade from black'; kind='fade_candidate'
        elif falling and max(end_channels)<.08: subtype='candidate fade to black'; kind='fade_candidate'
        elif rising and min(end_channels)>.92: subtype='candidate fade to white'; kind='fade_candidate'
        elif falling and min(begin_channels)>.92: subtype='candidate fade from white'; kind='fade_candidate'
        candidates.append({'kind':kind,'transition_type':subtype,'time_seconds':round(start_seconds+(begin+end-1)*step/2,6),
            'start_seconds':round(start_seconds+(begin-1)*step,6),'end_seconds':round(start_seconds+end*step,6),
            'sample_index':(begin+end)//2,'duration_seconds':round(elapsed,6),'net_pixel_change':round(net,4),
            'reason':'Several consecutive samples change gradually; the measurement does not identify the editing technique.',
            'requires_visual_review':True})
    candidates.sort(key=lambda item:item['time_seconds'])
    total=len(candidates); candidates=candidates[:64]
    for index,item in enumerate(candidates): item['candidate_id']=index+1
    hard_times=[item['time_seconds'] for item in candidates if item['kind']=='hard_cut_candidate']
    intervals=[b-a for a,b in zip(hard_times,hard_times[1:])]
    return {'sampled_frames':len(frames),'sample_interval_seconds':step,'candidates':candidates,
            'candidate_count_before_limit':total,'candidates_truncated':total>len(candidates),
            'candidate_cut_intervals_seconds':intervals,
            'median_candidate_cut_interval_seconds':statistics.median(intervals) if intervals else None,
            'unclassified_change_samples':sum(value>=.008 for i,value in enumerate(pixel) if i not in hard and i not in excluded),
            'limitations':['Sampled boundaries have at least one sample interval of timing uncertainty.',
                           'Similar-looking cuts may be missed; motion, flashes and overlays can resemble transitions.',
                           'Dissolve/wipe/zoom/whip transition identity requires reviewing actual images; no semantic model was run.']}

def compare_audio(candidates,analysis,start_seconds,sample_fps):
    """Compare only observed/estimated beats in the actual decoded audio window."""
    tempo=analysis.get('estimated_bpm'); observed=analysis.get('detected_beats_seconds') or []
    beats=observed or analysis.get('beats_seconds') or []
    tolerance=1/sample_fps+float(analysis.get('onset_resolution_seconds') or .02)
    value={'available':tempo is not None and bool(beats),'estimated_audio_pulse_bpm':tempo,
           'heuristic_confidence':analysis.get('confidence',0),'beat_source':'observed_onsets' if observed else 'estimated_grid',
           'beat_phase_source_seconds':start_seconds+float(beats[0]) if beats else None,
           'duration_analyzed_seconds':analysis.get('duration_analyzed_seconds',0),
           'timing_tolerance_seconds':tolerance,'cut_beat_comparisons':[],
           'limitations':['An audio pulse estimate is not a verified music BPM, downbeat or musical meter.',
                          'Candidate boundaries and onset timing are approximate; proximity does not prove an intentional beat cut.',
                          'Audio estimates may be half/double tempo or fail with speech, syncopation, silence or changing tempo.']}
    if not value['available']: return value
    source_beats=[start_seconds+float(beat) for beat in beats]
    cuts=[item for item in candidates if item['kind']=='hard_cut_candidate']
    for cut in cuts:
        t=cut['time_seconds']
        if t<start_seconds or t>=start_seconds+analysis.get('duration_analyzed_seconds',0): continue
        index=bisect.bisect_left(source_beats,t)
        choices=source_beats[max(0,index-1):min(len(source_beats),index+1)]
        if not choices: continue
        closest=min(choices,key=lambda beat:abs(t-beat)); difference=t-closest
        value['cut_beat_comparisons'].append({'candidate_id':cut['candidate_id'],'candidate_time_seconds':t,
            'nearest_beat_source_seconds':round(closest,6),'signed_distance_seconds':round(difference,6),
            'within_sampling_tolerance':abs(difference)<=tolerance})
    intervals=[second['time_seconds']-first['time_seconds'] for first,second in zip(cuts,cuts[1:])]
    ratios=[interval*tempo/60 for interval in intervals]
    value.update(candidate_cut_intervals_in_estimated_beats=[round(ratio,3) for ratio in ratios],
                 median_estimated_beats_per_cut=round(statistics.median(ratios),3) if ratios else None,
                 measured_cut_rate_per_minute=round(60/statistics.median(intervals),3) if intervals else None)
    return value

def _contact_sheet(path,frames):
    """Encode measured RGB pixels as a small diagnostic PNG using stdlib only."""
    columns=min(3,len(frames)); rows=math.ceil(len(frames)/columns); zoom=3
    width=columns*WIDTH*zoom; height=rows*HEIGHT*zoom; data=bytearray()
    for y in range(height):
        data.append(0)
        tile_y=y//(HEIGHT*zoom); pixel_y=(y//zoom)%HEIGHT
        for column in range(columns):
            index=tile_y*columns+column
            source=frames[index] if index<len(frames) else b'\0'*FRAME_BYTES
            row=source[pixel_y*WIDTH*3:(pixel_y+1)*WIDTH*3]
            for x in range(WIDTH): data.extend(bytes(row[x*3:x*3+3])*zoom)
    def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    from .core import atomic
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b'')
    atomic(path,png)

class Reference:
    def __init__(self,workspace): self.ws=workspace

    def dispatch(self,name,args):
        if not isinstance(args,dict): raise ValueError('Arguments must be an object')
        if name=='film_reference_analyze': return self.analyze(**args)
        if name=='film_extract_audio': return self.extract_audio(**args)
        raise ValueError('Unknown reference tool')

    def _command(self,source,start):
        return [self.ws.executable('ffmpeg'),'-nostdin','-v','error','-threads','1',*INPUT_OPTIONS,
                '-ss',f'{start:.9f}','-i',str(source)]

    @staticmethod
    def _check_result(result,label):
        if result.returncode: raise ValueError(label+': '+result.stderr.decode(errors='replace')[-1000:])

    def _reviews(self,source,video,start,window,sample_fps,raw,measurement,directory):
        frames=measurement['sampled_frames']; selected={0,frames-1}
        for candidate in measurement['candidates'][:5]:
            indices=[candidate['sample_index']-1,candidate['sample_index']]
            if candidate['kind'] in ('gradual_transition_candidate','fade_candidate'):
                indices=[round((candidate['start_seconds']-start)*sample_fps),candidate['sample_index'],
                         round((candidate['end_seconds']-start)*sample_fps)]
            selected.update(max(0,min(frames-1,index)) for index in indices)
        indices=sorted(selected)[:12]; previews=[]; warning=[]
        for index in indices:
            second=min(start+index/sample_fps,start+window-.001)
            destination=directory/('frame-'+str(index)+'.png')
            try:
                with self.ws.heavy():
                    command=self._command(source,second)+['-map','0:'+str(video['index']),'-an','-sn','-dn','-frames:v','1',
                        '-vf','scale=640:640:force_original_aspect_ratio=decrease','-threads','1','-n',str(destination)]
                    result=self.ws.run_media(command,timeout=60)
                self._check_result(result,'Reference review frame failed')
                if not destination.is_file() or destination.stat().st_size>4*1024**2: raise ValueError('Reference review frame missing or too large')
                previews.append({'sample_index':index,'source_seconds':round(second,6),'path':str(destination)})
            except (ValueError,OSError) as exc:
                destination.unlink(missing_ok=True); warning.append(str(exc)[:1000])
        sheet=directory/'contact-sheet.png'
        _contact_sheet(sheet,[memoryview(raw)[index*FRAME_BYTES:(index+1)*FRAME_BYTES] for index in indices])
        return previews,str(sheet),[{'tile':i+1,'sample_index':index,'source_seconds':round(start+index/sample_fps,6)} for i,index in enumerate(indices)],warning

    def analyze(self,asset_id,start_seconds=0,max_seconds=60,sample_fps=8):
        start=_number(start_seconds,'start_seconds',0,86400); maximum=_number(max_seconds,'max_seconds',1,MAX_SECONDS)
        if type(sample_fps) is not int or not 1<=sample_fps<=12: raise ValueError('sample_fps must be an integer from 1 to 12')
        asset=self.ws.asset(asset_id); source=self.ws.path(asset['path']); before=_stamp(source)
        warnings=[]; audio_analysis=None
        with self.ws.heavy():
            metadata=self.ws.probe(asset_id,timeout=30); video=_stream(metadata,'video')
            if video is None: raise ValueError('Reference analysis requires an imported video with a real picture stream')
            duration=_duration(metadata,video)
            if start>=duration: raise ValueError('Choose a reference start inside the video')
            window=min(maximum,duration-start)
            command=self._command(source,start)+['-map','0:'+str(video['index']),'-an','-sn','-dn','-t',f'{window:.9f}',
                '-vf',f'fps={sample_fps}:start_time=0:round=near,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=rgb24',
                '-frames:v',str(math.ceil(window*sample_fps)+1),'-threads','1','-f','rawvideo','pipe:1']
            scan=self.ws.run_media(command,timeout=180); self._check_result(scan,'Reference video scan failed')
            if len(scan.stdout)>(math.ceil(window*sample_fps)+1)*FRAME_BYTES: raise ValueError('Reference scan exceeded its pixel bound')
            audio=_stream(metadata,'audio')
            if audio is not None:
                audio_command=self._command(source,start)+['-map','0:'+str(audio['index']),'-vn','-sn','-dn','-t',f'{window:.9f}',
                    '-af','aresample=8000:async=1:first_pts=0','-ac','1','-ar','8000','-threads','1','-f','s16le','pipe:1']
                try:
                    decoded=self.ws.run_media(audio_command,timeout=120); self._check_result(decoded,'Reference audio scan failed')
                    if len(decoded.stdout)>math.ceil(window*8000)*2+4096: raise ValueError('Reference audio scan exceeded its PCM bound')
                    audio_analysis=Music.estimate_pcm(decoded.stdout)
                except ValueError as exc: warnings.append(str(exc))
        if _stamp(source)!=before: raise ValueError('Reference source changed during analysis; analyze it again')
        measurement=measure_transitions(scan.stdout,sample_fps,start)
        token=uuid.uuid4().hex[:16]; directory=self.ws.path('cache/reference-'+token); directory.mkdir(mode=0o700)
        previews,sheet,tiles,preview_warnings=self._reviews(source,video,start,window,sample_fps,scan.stdout,measurement,directory)
        warnings+=preview_warnings
        if _stamp(source)!=before: raise ValueError('Reference source changed while preparing review images')
        self.ws.asset(asset_id)
        audio_report=compare_audio(measurement['candidates'],audio_analysis,start,sample_fps) if audio_analysis is not None else {
            'available':False,'reason':'No readable audio stream was decoded for this window.'}
        report={'asset_id':asset_id,'source_name':asset['name'],'video_stream_index':video['index'],
            'audio_stream_index':audio['index'] if audio is not None else None,'source_duration_seconds':duration,
            'start_seconds':start,'duration_requested_seconds':maximum,'duration_analyzed_seconds':window,
            'partial':start>0 or start+window<duration-.001,'sample_fps':sample_fps,'sample_width':WIDTH,'sample_height':HEIGHT,
            **measurement,'audio':audio_report,'review_frames':previews,'contact_sheet_path':sheet,'contact_sheet_tiles':tiles,
            'contact_sheet_note':'Tiles contain the actual 96x54 analysis samples, enlarged for inspection; use the individual source frames for detail.',
            'report_path':str(directory/'report.json'),'warnings':warnings,'visual_review_completed':False,
            'method':'Bounded FFmpeg RGB sampling with pixel/color-change candidates and 20ms audio onset-envelope estimates.',
            'timeline_modified':False,'original_media_preserved':True}
        from .core import atomic
        atomic(directory/'report.json',json.dumps(report,indent=2).encode())
        return report

    def extract_audio(self,asset_id,start_seconds=0,duration_seconds=None,audio_stream_index=None):
        start=_number(start_seconds,'start_seconds',0,86400)
        requested=_number(duration_seconds,'duration_seconds',.000001,MAX_SECONDS) if duration_seconds is not None else None
        asset=self.ws.asset(asset_id); source=self.ws.path(asset['path']); before=_stamp(source)
        token=uuid.uuid4().hex
        output=self.ws.path('generated/reference-audio-'+token+'.wav')
        staging=output.with_name('.'+output.name); published=False
        try:
            with self.ws.heavy():
                metadata=self.ws.probe(asset_id,timeout=30)
                if _stream(metadata,'video') is None: raise ValueError('Audio extraction requires an imported video')
                audio=_stream(metadata,'audio',audio_stream_index)
                if audio is None: raise ValueError('This video has no audio stream to extract')
                duration=_duration(metadata,audio)
                if start>=duration: raise ValueError('Choose an extraction start inside the audio stream')
                remaining=duration-start
                if requested is None:
                    if remaining>MAX_SECONDS: raise ValueError('Full audio extraction is bounded to 300 seconds; supply an explicit duration_seconds range')
                    window=remaining
                else:
                    if requested>remaining+.02: raise ValueError('The requested audio range exceeds the selected stream duration')
                    window=min(requested,remaining)
                if window<.001: raise ValueError('The extraction window is too short')
                command=self._command(source,start)+['-map','0:'+str(audio['index']),'-vn','-sn','-dn','-t',f'{window:.9f}',
                    '-map_metadata','-1','-map_chapters','-1','-ac','2','-ar','48000','-c:a','pcm_s16le','-threads','1','-n',str(staging)]
                result=self.ws.run_media(command,timeout=180); self._check_result(result,'Audio extraction failed')
                if not staging.is_file() or not 44<staging.stat().st_size<=math.ceil(window*48000)*4+65536:
                    raise ValueError('Extracted audio is missing or exceeded its output bound')
                # A new derivative must be decoded/probed before it is advertised.
                probe=self.ws.run_media([self.ws.executable('ffprobe'),'-v','error',*INPUT_OPTIONS,
                    '-show_format','-show_streams','-of','json',str(staging)],timeout=30)
                self._check_result(probe,'Extracted audio validation failed')
                if len(probe.stdout)>1024**2: raise ValueError('Extracted audio metadata is too large')
                info=json.loads(probe.stdout); extracted=_stream(info,'audio')
                if extracted is None: raise ValueError('Extracted file has no readable audio')
                if extracted.get('codec_name')!='pcm_s16le' or str(extracted.get('sample_rate'))!='48000' or extracted.get('channels')!=2:
                    raise ValueError('Extracted audio is not the requested 48kHz stereo PCM format')
                actual=_duration(info,extracted)
                if abs(actual-window)>.03: raise ValueError('Extracted audio duration did not match the selected range')
                if _stamp(source)!=before: raise ValueError('Source changed during audio extraction')
                with self.ws.lock:
                    if self.ws.stopping: raise ValueError('Workspace is closing')
                    self.ws.asset(asset_id)
                    os.replace(staging,output)
                    generated=self.ws.register(output,'Audio from '+asset['name'],'audio')
                    published=True
                    for item in self.ws.data['assets']:
                        if item['id']==generated['id']:
                            item['audio_extraction']={'source_asset_id':asset_id,'source_audio_stream_index':audio['index'],
                                'start_seconds':start,'duration_seconds':actual,'codec':'pcm_s16le','sample_rate':48000,
                                'channels':2,'changes':'Decoded selected video audio to 48kHz stereo PCM; no volume normalization or timeline edit.'}
                            self.ws.save(); break
            return {'asset':self.ws.asset(generated['id']),'source_asset_id':asset_id,'audio_stream_index':audio['index'],
                    'start_seconds':start,'duration_seconds':actual,'full_selected_audio':start==0 and abs(actual-duration)<.03,
                    'output_path':str(output),'original_media_preserved':True,'timeline_modified':False,
                    'rights_note':'Extraction does not establish permission to reuse a reference soundtrack.'}
        finally:
            staging.unlink(missing_ok=True)
            if not published: output.unlink(missing_ok=True)
