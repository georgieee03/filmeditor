"""Technical media detection, empty-sequence configuration and render-risk checks.

Container/codec tags describe the reported signal; they do not identify a camera
look or prove a color conversion. Only allowlisted technical fields are returned.
"""
from __future__ import annotations
from collections import defaultdict
import copy
from fractions import Fraction
import math
from pathlib import Path
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from .core import IMAGE, unpack

IMAGES=IMAGE|{'.heic','.heif','.gif','.tif','.tiff','.dng'}
IMAGE_FORMATS={'png_pipe','jpeg_pipe','webp_pipe','tiff_pipe','bmp_pipe','image2','image2pipe','heif'}
DATA={'.cube','.srt','.vtt','.txt','.json'}
RAW={'.braw','.r3d','.ari','.arx','.dng','.crm','.cine'}
SDR_TRANSFERS={'bt709','gamma22','gamma28','smpte170m','smpte240m','iec61966-2-1','iec61966-2-4','bt1361e'}
SDR_PRIMARIES={'bt709','smpte170m','bt470bg','bt470m'}
SDR_MATRICES={'bt709','smpte170m','bt470bg','gbr'}
HDR_TRANSFERS={'smpte2084':'pq','arib-std-b67':'hlg'}
LOG_TRANSFERS={'log','log_sqrt','slog','slog2','slog3','clog','clog2','clog3','vlog','logc','logc3','logc4','log3g10'}
HDR_SIDE_DATA={'dovi configuration record','hdr dynamic metadata smpte2094-40',
               'mastering display metadata','content light level metadata'}
SAFE_FORMATS='mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,mxf,mp3,wav,flac,ogg,aac,aiff,asf,image2,image2pipe,png_pipe,jpeg_pipe,webp_pipe,gif'
MAX_GUARD_RESOURCES=512
GUARD_TIMEOUT=120
MAX_MLT_BYTES=32*1024**2

def _tool(name,description,properties,required,read=False):
    return {'name':name,'description':description,
        'inputSchema':{'type':'object','properties':properties,'required':required,'additionalProperties':False},
        'annotations':{'readOnlyHint':read,'destructiveHint':False,'openWorldHint':False}}

IDS={'type':'array','items':{'type':'string'},'maxItems':100,'uniqueItems':True}
FILM_INGEST_TOOLS=[
    _tool('film_detect_media','Detect reported video/audio technical metadata without guessing camera Log or a LUT. Recommend dimensions and exact rational FPS from a deterministic reference group. Missing/unsupported signal data requires analysis.',
        {'asset_ids':IDS},[],True),
    _tool('film_configure_from_media','Configure an automatic, empty active sequence once from detected media. Requires current path/revision; preserve any existing edit or explicit project settings. Unknown or unsupported color/timing returns requires_ai instead of inventing settings.',
        {'asset_ids':IDS,'project_path':{'type':'string'},'expected_revision':{'type':'string'}},['project_path','expected_revision']),
]

def _integer(value,minimum=0,maximum=10**9):
    if isinstance(value,bool):return None
    if isinstance(value,str) and re.fullmatch(r'\d{1,10}',value):value=int(value)
    return value if type(value) is int and minimum<=value<=maximum else None

def _fraction(value):
    if value is None or isinstance(value,bool):return None
    text=str(value).strip().replace(':','/')
    if len(text)>40:return None
    try:value=Fraction(text)
    except (ValueError,ZeroDivisionError):return None
    return value if value>0 and value.numerator<=10**9 and value.denominator<=10**9 else None

def _technical(value):
    if not isinstance(value,str) or value.lower() in ('unknown','unspecified','n/a','none',''):return None
    return value.lower() if re.fullmatch(r'[a-zA-Z0-9_.+-]{1,64}',value) else None

def _duration(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (ValueError,TypeError):return None
    return value if math.isfinite(value) and 0<=value<=86400*30 else None

def _depth(stream):
    raw=_integer(stream.get('bits_per_raw_sample'),1,32)
    if raw:return raw,'bits_per_raw_sample'
    pixel=_technical(stream.get('pix_fmt')) or ''
    # Match component-depth positions only. The 420/422/444 digits describe
    # chroma subsampling; RGB24/48 storage is split among three components.
    if re.fullmatch(r'(?:yuv|yuva|yuvj)(?:410|411|420|422|440|444)p',pixel):return 8,'pixel_format'
    match=re.fullmatch(r'(?:(?:yuv|yuva)(?:420|422|440|444)p|gbrp|gbrap|gray)(9|10|12|14|16)(?:le|be)',pixel)
    if match:return int(match.group(1)),'pixel_format'
    mapping={'nv12':8,'nv21':8,'yuyv422':8,'uyvy422':8,'rgb24':8,'bgr24':8,'rgba':8,'bgra':8,
        'argb':8,'abgr':8,'gbrp':8,'gbrap':8,'gray':8,'gray8':8,'rgb48le':16,'rgb48be':16,
        'rgba64le':16,'rgba64be':16,'p010le':10,'p010be':10,'p012le':12,'p012be':12,'p016le':16,'p016be':16}
    return mapping.get(pixel),'pixel_format' if pixel in mapping else None

class Ingest:
    def __init__(self,workspace):self.ws=workspace;self.lock=threading.RLock()

    def dispatch(self,name,args):
        if not isinstance(args,dict):raise ValueError('Arguments must be an object')
        if name=='film_detect_media':return self.detect(**args)
        if name=='film_configure_from_media':return self.configure(**args)
        raise ValueError('Unknown ingest tool')

    @staticmethod
    def classify(asset,metadata):
        """Normalize reported fields; never return location/comments or camera guesses."""
        suffix=Path(asset.get('name','')).suffix.lower();reasons=[];warnings=[]
        streams=metadata.get('streams',[]) if isinstance(metadata,dict) else []
        if not isinstance(streams,list):streams=[]
        streams=[stream for stream in streams if isinstance(stream,dict)]
        def disposition(stream,key):
            flags=stream.get('disposition',{})
            return isinstance(flags,dict) and flags.get(key) in (1,True)
        videos=[stream for stream in streams if stream.get('codec_type')=='video' and not disposition(stream,'attached_pic')]
        audio=[{'stream_index':_integer(stream.get('index')),'codec':_technical(stream.get('codec_name')),
            'sample_rate':_integer(stream.get('sample_rate'),1,768000),'channels':_integer(stream.get('channels'),1,64),
            'channel_layout':_technical(stream.get('channel_layout'))} for stream in streams if stream.get('codec_type')=='audio']
        format_=metadata.get('format',{}) if isinstance(metadata,dict) else {};format_=format_ if isinstance(format_,dict) else {}
        formats=set(str(format_.get('format_name') or '').split(','))
        still=bool(formats & IMAGE_FORMATS) and all((_integer(stream.get('nb_frames'),0) or 1)<=1 for stream in videos)
        declared_data=suffix in DATA and asset.get('kind') in ('reference','lut') and not videos and not audio
        kind='image' if still else 'data' if declared_data else 'video' if videos else 'audio' if audio else 'unknown'
        value={'asset_id':asset.get('id'),'name':str(asset.get('name') or 'Media')[:300],'kind':kind,'video':None,'audio':audio,
            'duration_seconds':_duration(format_.get('duration')),
            'requires_ai':False,'detection_complete':True,'pipeline_ready':True,'reasons':reasons,'warnings':warnings}
        if kind=='unknown':reasons.append('No readable video or audio stream was reported.')
        if kind=='data':return value
        if not videos:
            value.update(requires_ai=bool(reasons),detection_complete=not reasons,pipeline_ready=not reasons)
            return value
        stream=next((item for item in videos if disposition(item,'default')),videos[0])
        if len(videos)>1:
            warnings.append('The default stream, or first stream, is reported as the technical reference.')
            if kind=='video':reasons.append('Multiple non-artwork video streams require an explicit stream/color selection or normalized single-stream source.')
        coded_width=_integer(stream.get('width'),16,32768);coded_height=_integer(stream.get('height'),16,32768)
        side=stream.get('side_data_list',[]);side=side if isinstance(side,list) else []
        rotation_values=[item.get('rotation') for item in side if isinstance(item,dict) and 'rotation' in item]
        tags=stream.get('tags',{});tags=tags if isinstance(tags,dict) else {}
        legacy=tags.get('rotate');rotation_values=rotation_values or ([legacy] if legacy is not None else [0])
        rotations=[]
        for rotation in rotation_values:
            try:rotation=float(rotation)
            except (TypeError,ValueError):rotation=float('nan')
            if not math.isfinite(rotation) or abs(rotation)>36000:reasons.append('Invalid display rotation metadata.');continue
            rotations.append(rotation%360)
        rotation=rotations[0] if rotations else None
        if rotation is not None and abs(rotation/90-round(rotation/90))>1e-4:reasons.append('Non-orthogonal display rotation requires a fitting decision.')
        if any(abs(((item-rotation+180)%360)-180)>1e-3 for item in rotations[1:]):reasons.append('Conflicting display rotation records.')
        width,height=coded_width,coded_height
        if rotation is not None and round(rotation)%360 in (90,270):width,height=height,width
        if not width or not height:reasons.append('Video dimensions are missing or unsupported.')
        elif width%2 or height%2:reasons.append('Odd display dimensions need a padding/crop decision for the current export codecs.')
        avg=_fraction(stream.get('avg_frame_rate'));base=_fraction(stream.get('r_frame_rate'));fps=avg or base
        if kind=='video':
            if fps is None or not 1<=fps<=240:reasons.append('A usable 1–240 fps timing rate was not reported.')
            elif avg is None:reasons.append('Only a guessed r_frame_rate was reported; the sequence timing is unresolved.')
        disagreement=bool(avg and base and abs(float(avg/base)-1)>.001)
        if disagreement and kind=='video':reasons.append('Average and guessed frame rates differ; variable cadence or field timing needs analysis.')
        sar=_fraction(stream.get('sample_aspect_ratio'));dar=_fraction(stream.get('display_aspect_ratio'))
        if sar is None and dar is not None and coded_width and coded_height:sar=dar*coded_height/coded_width
        if kind=='video' and sar is None:reasons.append('Sample/display aspect metadata is missing; do not assume square pixels.')
        if sar is not None and sar!=1:reasons.append('Non-square source pixels require explicit aspect handling.')
        display_sar=1/sar if sar and rotation is not None and round(rotation)%360 in (90,270) else sar
        display_aspect=Fraction(width,height)*display_sar if width and height and display_sar else None
        field_order=_technical(stream.get('field_order'))
        progressive=field_order=='progressive'
        if kind=='video' and not progressive:reasons.append('Interlaced or missing field-order metadata needs an output/deinterlace decision.')
        depth,depth_source=_depth(stream)
        transfer=_technical(stream.get('color_transfer'));primaries=_technical(stream.get('color_primaries'));matrix=_technical(stream.get('color_space'))
        color_range=_technical(stream.get('color_range'))
        hdr_side=[item['side_data_type'] for item in side if isinstance(item,dict) and isinstance(item.get('side_data_type'),str)
                  and item['side_data_type'].lower() in HDR_SIDE_DATA]
        color_type=HDR_TRANSFERS.get(transfer,'sdr' if transfer in SDR_TRANSFERS else 'unknown')
        log_hint=transfer if transfer in LOG_TRANSFERS else None
        raw_camera=suffix in RAW or _technical(stream.get('codec_name')) in {'braw','r3d','prores_raw','cdng','dng'}
        color_reasons=[]
        if kind=='video':
            if depth is None:color_reasons.append('Component bit depth is unknown.')
            if transfer is None:color_reasons.append('Transfer metadata is missing; SDR, HDR or camera Log cannot be established.')
            elif color_type=='unknown':color_reasons.append('The transfer is not a supported SDR/PQ/HLG signal; camera Log identity/LUT cannot be guessed.')
            if primaries is None:color_reasons.append('Color primaries are not reported.')
            if matrix is None:color_reasons.append('Color matrix coefficients are not reported.')
            if color_range not in ('tv','pc'):color_reasons.append('Full/limited signal range is not reported.')
            if color_type in ('pq','hlg'):color_reasons.append('Known HDR metadata; current preview/export normalization is unverified.')
            if hdr_side:color_reasons.append('HDR/Dolby Vision side data requires a verified color/delivery workflow.')
            if color_type=='sdr' and (primaries=='bt2020' or matrix in ('bt2020nc','bt2020c')):color_reasons.append('Wide-gamut SDR/HDR tag combination is outside the verified SDR pipeline.')
            elif color_type=='sdr' and ((primaries and primaries not in SDR_PRIMARIES) or (matrix and matrix not in SDR_MATRICES)):
                color_reasons.append('The reported SDR primaries/matrix are outside the supported automatic working color spaces.')
            if raw_camera:color_reasons.append('Camera RAW requires a supported decoder and explicit camera color interpretation.')
        reasons.extend(color_reasons)
        codec=_technical(stream.get('codec_name'))
        if kind=='video' and codec is None:reasons.append('Video codec was not reported.')
        video={'stream_index':_integer(stream.get('index')),'non_artwork_video_streams':len(videos),'codec':codec,'coded_width':coded_width,'coded_height':coded_height,
            'width':width,'height':height,'rotation_degrees':rotation,'autorotate':True,'fps_num':fps.numerator if fps else None,
            'fps_den':fps.denominator if fps else None,'fps':float(fps) if fps else None,'fps_source':'avg_frame_rate' if avg else 'r_frame_rate' if base else None,
            'avg_frame_rate':str(avg) if avg else None,'r_frame_rate':str(base) if base else None,'rate_disagreement':disagreement,
            'cadence_evidence':'rate disagreement observed' if disagreement else 'no rate disagreement observed; cadence not frame-inspected',
            'sample_aspect_num':display_sar.numerator if display_sar else None,'sample_aspect_den':display_sar.denominator if display_sar else None,
            'display_aspect_num':display_aspect.numerator if display_aspect else None,'display_aspect_den':display_aspect.denominator if display_aspect else None,
            'field_order':field_order,'progressive':progressive,'pixel_format':_technical(stream.get('pix_fmt')),'bit_depth':depth,'bit_depth_source':depth_source,
            'transfer':transfer,'primaries':primaries,'matrix':matrix,'range':color_range,'color_type':color_type,
            'hdr_side_data':hdr_side,'log_hint':log_hint,'camera_log_identity':None,'camera_transform':None,'raw_camera':raw_camera,
            'color_conversion_verified':False}
        value['video']=video
        metadata_missing=not all((width,height,avg,depth,transfer,primaries,matrix,color_range,sar,field_order,codec)) if kind=='video' else False
        value.update(requires_ai=bool(reasons) if kind=='video' else False,detection_complete=not metadata_missing,
                     pipeline_ready=not reasons if kind=='video' else True)
        return value

    def _assets(self,asset_ids):
        if asset_ids is not None and (not isinstance(asset_ids,list) or len(asset_ids)>100 or any(not isinstance(i,str) or not i for i in asset_ids) or len(set(asset_ids))!=len(asset_ids)):
            raise ValueError('asset_ids must be a list of at most 100 unique imported IDs')
        with self.ws.lock:assets=copy.deepcopy(self.ws.data['assets'])
        if asset_ids is None:
            if len(assets)>100:raise ValueError('Select at most 100 representative assets for detection')
            return assets
        selected=set(asset_ids)
        if not selected<={asset['id'] for asset in assets}:raise ValueError('Unknown imported asset ID')
        return [asset for asset in assets if asset['id'] in selected]

    def detect(self,asset_ids=None):
        assets=self._assets(asset_ids);detections=[]
        for asset in assets:
            if self.ws.stopping:raise ValueError('Workspace is closing')
            if Path(asset['name']).suffix.lower() in DATA and asset.get('kind') in ('reference','lut'):
                detections.append(self.classify(asset,{}));continue
            try:detections.append(self.classify(asset,self.ws.probe(asset['id'])))
            except (ValueError,OSError,TimeoutError,subprocess.SubprocessError) as exc:
                detections.append({'asset_id':asset['id'],'name':asset['name'][:300],'kind':'unknown','video':None,'audio':[],
                    'requires_ai':True,'detection_complete':False,'pipeline_ready':False,'reasons':['Technical probe failed; inspect/convert the source before editing.'],
                    'warnings':[],'probe_error':type(exc).__name__})
        candidates=[value for value in detections if value['kind']=='video' and all(value['video'].get(key) for key in ('width','height','fps_num','fps_den','display_aspect_num','display_aspect_den'))]
        groups=defaultdict(list)
        for value in candidates:
            video=value['video'];key=tuple(video.get(k) for k in ('width','height','fps_num','fps_den','sample_aspect_num','sample_aspect_den','color_type','primaries','matrix'))
            groups[key].append(value)
        group=max(groups.values(),key=len) if groups else []
        reference=group[0] if group else None;profile=None
        if reference:
            video=reference['video'];profile={key:video[key] for key in ('width','height','fps_num','fps_den','sample_aspect_num','sample_aspect_den','display_aspect_num','display_aspect_den','progressive')}
            profile.update(color_type=video['color_type'],colorspace=709 if video['matrix'] in ('bt709','gbr') else 601 if video['matrix'] in ('smpte170m','bt470bg','smpte240m') else None,
                           processing_mode='Native10Cpu' if (video['bit_depth'] or 0)>8 else 'Native8Cpu')
        reasons=[f"{value['name']}: {reason}" for value in detections for reason in value['reasons']]
        return {'assets':detections,'recommended_profile':profile,'reference_asset_id':reference['asset_id'] if reference else None,
            'selection_strategy':'largest consistent group; ties use original bin order' if len(group)>1 else 'first video in original bin order with usable geometry/timing',
            'reference_group_asset_ids':[value['asset_id'] for value in group],'mixed_formats':len(groups)>1,
            'requires_ai':any(value['requires_ai'] for value in detections),'detection_complete':all(value['detection_complete'] for value in detections),
            'pipeline_ready':all(value['pipeline_ready'] for value in detections),'reasons':reasons,
            'warnings':['Metadata is not camera/color calibration or proof of constant frame cadence.']+
                (['Mixed source dimensions/rates follow the reference profile; sources are fitted without a stretch filter.'] if len(groups)>1 else [])}

    @staticmethod
    def _empty(snapshot):return not any(item.get('type')!='gap' for track in snapshot['tracks'] for item in track['items'])

    def configure(self,project_path,expected_revision,asset_ids=None):
        if not isinstance(project_path,str) or not Path(project_path).is_absolute():raise ValueError('Supply an absolute active project_path')
        if not isinstance(expected_revision,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_revision):raise ValueError('Supply the exact current expected_revision')
        path=self.ws.path(project_path);lock=getattr(self.ws,'edit_lock',self.lock)
        with lock,self.ws.lock:
            if path!=self.ws.active():raise ValueError('Active sequence changed. Inspect before configuring.')
            before=unpack(self.ws.forward('inspect_project',{'path':str(path)}))
            if before['revision']!=expected_revision:raise ValueError('Timeline changed. Inspect before configuring.')
        detection=self.detect(asset_ids)
        with lock,self.ws.lock:
            if path!=self.ws.active():raise ValueError('Active sequence changed while detecting media.')
            current=unpack(self.ws.forward('inspect_project',{'path':str(path)}))
            if current['revision']!=expected_revision:raise ValueError('Timeline changed while detecting media.')
            for asset in detection['assets']:self.ws.asset(asset['asset_id'])
            status='needs_ai' if detection['requires_ai'] else 'no_video'
            if not self._empty(current):status='nonempty_preserved'
            elif self.ws.data.get('auto_configure',True) is False:status='manual_settings_preserved'
            elif str(path) in self.ws.data.get('auto_configured_sequences',{}):status='already_configured'
            elif detection['recommended_profile'] and not detection['requires_ai']:status='configured'
            value=dict(detection,configured=False,status=status,project_path=str(path),revision=current['revision'],profile=current['profile'],color_workflow=current.get('color_workflow'))
            if status!='configured':return value
            profile=detection['recommended_profile']
            if profile['colorspace'] is None:
                value.update(status='needs_ai',requires_ai=True,pipeline_ready=False)
                value['reasons'].append('The reported SDR matrix has no supported automatic MLT colorspace mapping.')
                return value
            # Installed set_profile advertises progressive as bool but currently
            # validates it as an integer. Empty fresh projects are progressive;
            # preserve that flag instead of sending an invalid engine operation.
            if str(current['profile'].get('progressive','1'))!='1':
                value.update(status='needs_ai',requires_ai=True,pipeline_ready=False)
                value['reasons'].append('The current non-progressive profile needs an explicit engine-compatible update.')
                return value
            op={'op':'set_profile','preserve_frame_numbers':True,**{key:profile[key] for key in ('width','height','sample_aspect_num','sample_aspect_den','display_aspect_num','display_aspect_den')},
                'frame_rate_num':profile['fps_num'],'frame_rate_den':profile['fps_den']}
            operations=[op,{'op':'set_color_workflow','workflow':'sdr','processing_mode':profile['processing_mode'],'colorspace':profile['colorspace']}]
            from .editor import Editor
            Editor.check_locks(current,operations)
            edit=unpack(self.ws.forward('edit_project',{'project_path':str(path),'expected_revision':expected_revision,'operations':operations}))
            configured=unpack(self.ws.forward('inspect_project',{'path':str(path)}))
            decision={'reference_asset_id':detection['reference_asset_id'],'profile':profile,'revision':edit['revision'],'selection_strategy':detection['selection_strategy']}
            self.ws.data.setdefault('auto_configured_sequences',{})[str(path)]=decision
            try:self.ws.save()
            except OSError:value['warnings'].append('Sequence configured, but its automatic-setting decision could not be persisted.')
            value.update(configured=True,revision=configured['revision'],profile=configured['profile'],color_workflow=configured.get('color_workflow'))
            if edit.get('warnings'):value['warnings'].extend(edit['warnings'])
            return value

    def _referenced_paths(self,path,visited=None,depth=0,active=None,budget=None):
        visited=set() if visited is None else visited
        active=frozenset() if active is None else active
        budget={'deadline':time.monotonic()+GUARD_TIMEOUT,'bytes':0,'resources':set()} if budget is None else budget
        if self.ws.stopping or time.monotonic()>=budget['deadline']:raise ValueError('Render-risk inspection timed out or was cancelled')
        if path in active:raise ValueError('Nested timeline recursion cycle requires resolution before rendering')
        if path in visited:return set()
        if depth>8 or len(visited)>=32:raise ValueError('Nested timeline inspection exceeded its bound')
        active=active|{path}
        visited.add(path)
        with path.open('rb') as stream:raw=stream.read(16*1024**2+1)
        budget['bytes']+=len(raw)
        if len(raw)>16*1024**2 or budget['bytes']>MAX_MLT_BYTES:raise ValueError('Timeline XML exceeds the render-risk inspection size limit')
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Timeline XML entities are not supported by render-risk inspection')
        document=ET.fromstring(raw);nodes={};tractors=[]
        for node in document.iter():
            ident=node.get('id')
            if ident:
                if ident in nodes:raise ValueError('Duplicate MLT IDs prevent unambiguous render-risk inspection')
                nodes[ident]=node
            if node.tag=='tractor':tractors.append(node)
        root_id=document.get('producer');main=nodes.get(root_id) if root_id else None
        if main is None:
            marked=[node for node in tractors if any(prop.get('name')=='shotcut' and prop.text=='1' for prop in node.findall('property'))]
            main=marked[0] if len(marked)==1 else tractors[0] if len(tractors)==1 else None
        if main is None:raise ValueError('Cannot identify the rendered MLT root for automatic color-risk inspection')
        base=Path(document.get('root') or path.parent);base=base if base.is_absolute() else path.parent/base
        reached=set();pending=[main];resources=set()
        while pending:
            if self.ws.stopping or time.monotonic()>=budget['deadline']:raise ValueError('Render-risk inspection timed out or was cancelled')
            node=pending.pop();identity=id(node)
            if identity in reached:continue
            reached.add(identity)
            for child in node.iter():
                if child.tag in ('track','entry') and child.get('producer'):
                    producer=nodes.get(child.get('producer'))
                    if producer is None:raise ValueError('A timeline references an unresolved MLT producer')
                    pending.append(producer)
            if node.tag not in ('producer','chain'):continue
            props={prop.get('name'):prop.text for prop in node.findall('property')}
            service=props.get('mlt_service') or ''
            if service in ('color','colour','pango','qtext','count'):continue
            # warp_resource/resource are rendered input, while mcpOriginalResource
            # is undo metadata and must not hide a later substituted input.
            resource=(props.get('warp_resource') or props.get('resource')) if service=='timewarp' else props.get('resource')
            if not resource:
                if service in ('avformat','avformat-novalidate','timewarp','xml'):raise ValueError('A rendered media producer has no actual input resource')
                continue
            if service=='timewarp' and not props.get('warp_resource'):
                match=re.match(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+):(.+)$',resource)
                if match:resource=match.group(1)
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://',resource):raise ValueError('Network resources are not supported by the local render-risk guard')
            candidate=Path(resource);candidate=self.ws.path(candidate if candidate.is_absolute() else base/candidate)
            if not candidate.is_file():raise ValueError('A referenced render input is missing')
            if candidate.suffix.lower()=='.mlt':resources.update(self._referenced_paths(candidate,visited,depth+1,active,budget))
            else:
                budget['resources'].add(candidate)
                if len(budget['resources'])>MAX_GUARD_RESOURCES:raise ValueError('Render-risk inspection exceeds 512 unique media resources')
                resources.add(candidate)
        return resources

    def ensure_render_ready(self,project_path):
        """Block unsafe color assumptions only for reachable media in this MLT."""
        budget={'deadline':time.monotonic()+GUARD_TIMEOUT,'bytes':0,'resources':set()}
        path=self.ws.path(project_path);resources=self._referenced_paths(path,budget=budget)
        with self.ws.lock:
            assets=copy.deepcopy(self.ws.data['assets']+self.ws.data.get('deleted_assets',[]))
        by_path={Path(asset['path']).resolve():asset for asset in assets};checked=[];warnings=[]
        for source in sorted(resources):
            remaining=budget['deadline']-time.monotonic()
            if remaining<=0 or self.ws.stopping:raise ValueError('Render-risk inspection timed out or was cancelled')
            asset=by_path.get(source) or {'id':None,'name':source.name,'path':str(source)}
            try:
                if asset.get('id') and any(item['id']==asset['id'] for item in self.ws.data['assets']):metadata=self.ws.probe(asset['id'],timeout=min(60,remaining))
                else:
                    probed=self.ws.run_media([self.ws.executable('ffprobe'),'-v','error','-protocol_whitelist','file,pipe','-format_whitelist',SAFE_FORMATS,
                        '-show_format','-show_streams','-of','json',str(source)],timeout=min(60,remaining))
                    if probed.returncode:raise ValueError('Referenced source could not be probed')
                    import json
                    metadata=json.loads(probed.stdout)
                value=self.classify(asset,metadata)
            except (ValueError,OSError,TimeoutError,subprocess.SubprocessError):
                raise ValueError('Render requires a verified supported SDR source/working color configuration: '+asset['name']+': source interpretation is unresolved') from None
            if time.monotonic()>=budget['deadline'] or self.ws.stopping:raise ValueError('Render-risk inspection timed out or was cancelled')
            if value['kind']!='video':
                if source.suffix.lower() in RAW:raise ValueError('Render requires camera RAW decoding/color interpretation before export: '+asset['name'])
                elif value['kind']=='unknown':raise ValueError('Render requires a readable media source: '+asset['name'])
                continue
            video=value['video'];risk=[]
            if video['non_artwork_video_streams']>1:risk.append('Multiple video streams are unresolved; normalize the selected stream before rendering')
            if video['color_type'] in ('pq','hlg'):risk.append('HDR '+video['color_type'].upper()+' requires verified normalization/delivery')
            if video['hdr_side_data']:risk.append('HDR/Dolby Vision side data is present')
            if video['log_hint'] or video['raw_camera']:risk.append('Log/RAW camera interpretation is unverified')
            if video['bit_depth'] is None:risk.append('Component precision is unknown')
            elif video['color_type']=='unknown' and video['bit_depth']>8:risk.append('High-bit-depth transfer is unknown')
            if video['primaries']=='bt2020' or video['matrix'] in ('bt2020nc','bt2020c'):risk.append('Wide-gamut conversion is unverified')
            elif (video['primaries'] and video['primaries'] not in SDR_PRIMARIES) or (video['matrix'] and video['matrix'] not in SDR_MATRICES):risk.append('Unsupported working color primaries/matrix')
            if video['transfer'] is not None and video['color_type']=='unknown':risk.append('Explicit transfer interpretation is unsupported')
            if risk:raise ValueError('Render requires a verified supported SDR source/working color configuration: '+asset['name']+': '+'; '.join(risk))
            elif video['color_type']=='unknown':warnings.append(asset['name']+': untagged known 8-bit source is using an SDR working assumption, not confirmed SDR.')
            elif video['bit_depth']>8:warnings.append(asset['name']+': an 8-bit H.264 deliverable reduces this tagged SDR source precision.')
            checked.append({'asset_id':asset.get('id'),'name':asset['name'],'color_type':video['color_type'],'bit_depth':video['bit_depth']})
        return {'ready':True,'checked_assets':checked,'warnings':warnings,'resource_count':len(resources),'probe_budget_seconds':GUARD_TIMEOUT}
