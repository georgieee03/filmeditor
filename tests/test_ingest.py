"""Technical detection, active empty-sequence safety and actual MLT acceptance."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT, Workspace, result, unpack
from film.ingest import Ingest, _depth

VIDEO={'format':{'format_name':'mov,mp4,m4a,3gp,3g2,mj2','duration':'2'},'streams':[
    {'index':0,'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'avg_frame_rate':'30000/1001',
     'r_frame_rate':'30000/1001','sample_aspect_ratio':'1:1','display_aspect_ratio':'16:9','pix_fmt':'yuv420p',
     'bits_per_raw_sample':'8','field_order':'progressive','color_transfer':'bt709','color_primaries':'bt709',
     'color_space':'bt709','color_range':'tv','disposition':{'default':1}},
    {'index':1,'codec_type':'audio','codec_name':'aac','sample_rate':'48000','channels':2,'channel_layout':'stereo'}]}

def media(**changes):
    value=copy.deepcopy(VIDEO);value['streams'][0].update(changes);return value

class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.ws=Workspace(Path(self.temporary.name)/'workspace');self.ingest=Ingest(self.ws)
    def tearDown(self):self.ws.close();self.temporary.cleanup()
    def asset(self,name='source.mp4'):
        return self.ws.ingest(io.BytesIO(b'synthetic fixture'),17,name,'footage')
    def timeline(self,sources,unused=None):
        root=ET.Element('mlt',producer='main',root=str(self.ws.root));main=ET.SubElement(root,'tractor',id='main')
        for index,source in enumerate(sources):
            ident='clip'+str(index);chain=ET.SubElement(root,'chain',id=ident)
            ET.SubElement(chain,'property',name='mlt_service').text='avformat'
            ET.SubElement(chain,'property',name='resource').text=str(source)
            ET.SubElement(main,'track',producer=ident)
        if unused:
            chain=ET.SubElement(root,'chain',id='unused');ET.SubElement(chain,'property',name='resource').text=str(unused)
        path=self.ws.root/'edits/test.mlt';path.write_bytes(ET.tostring(root));return path

    def test_rotation_rational_fps_color_audio_and_private_tags(self):
        source=self.asset();metadata=media(side_data_list=[{'side_data_type':'Display Matrix','rotation':-90}],
            tags={'rotate':'90','location':'PRIVATE LOCATION','comment':'PRIVATE COMMENT'})
        metadata['format']['tags']={'location':'GPS SECRET'}
        value=Ingest.classify(source,metadata);video=value['video']
        self.assertEqual((video['width'],video['height']),(1080,1920));self.assertEqual(video['rotation_degrees'],270)
        self.assertEqual((video['fps_num'],video['fps_den']),(30000,1001));self.assertEqual(video['color_type'],'sdr')
        self.assertEqual(video['bit_depth'],8);self.assertEqual(value['audio'][0]['sample_rate'],48000)
        self.assertEqual((video['display_aspect_num'],video['display_aspect_den']),(9,16))
        self.assertFalse(value['requires_ai']);self.assertIsNone(video['camera_transform'])
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('GPS SECRET',json.dumps(value))
        self.assertNotIn('constant',video['cadence_evidence'])

    def test_hdr_and_log_are_detected_without_claiming_a_ready_pipeline(self):
        source=self.asset()
        for transfer,color in [('smpte2084','pq'),('arib-std-b67','hlg'),('log','unknown'),('log316','unknown')]:
            with self.subTest(transfer=transfer):
                value=Ingest.classify(source,media(pix_fmt='yuv420p10le',bits_per_raw_sample='10',
                    color_transfer=transfer,color_primaries='bt2020',color_space='bt2020nc'))
                self.assertEqual(value['video']['color_type'],color);self.assertTrue(value['requires_ai']);self.assertFalse(value['pipeline_ready'])
                self.assertIsNone(value['video']['camera_log_identity']);self.assertIsNone(value['video']['camera_transform'])
        value=Ingest.classify(source,media(side_data_list=[{'side_data_type':'DOVI configuration record'}]))
        self.assertEqual(value['video']['color_type'],'sdr');self.assertTrue(value['requires_ai'])

    def test_component_depth_does_not_confuse_chroma_or_storage_digits(self):
        for pixel,depth in [('yuv420p',8),('yuvj420p',8),('yuv422p10le',10),('p010le',10),('rgb24',8),('argb',8),('rgb48le',16),('madeup420p',None)]:
            with self.subTest(pixel=pixel):self.assertEqual(_depth({'pix_fmt':pixel,'bits_per_raw_sample':'0'})[0],depth)

    def test_still_images_and_cover_art_do_not_choose_video_profile(self):
        source=self.asset('cover.png');still=media()
        still['format']['format_name']='png_pipe';still['streams'][0].update(codec_name='png',nb_frames='1')
        self.assertEqual(Ingest.classify(source,still)['kind'],'image')
        cover=copy.deepcopy(VIDEO);cover['format']['format_name']='mp3';cover['streams'][0]['disposition']={'attached_pic':1}
        self.assertEqual(Ingest.classify(self.asset('song.mp3'),cover)['kind'],'audio')
        # Suffixes cannot disguise the actual video container/signal.
        self.assertEqual(Ingest.classify(source,media(color_transfer='smpte2084'))['kind'],'video')

    def test_majority_and_ties_are_deterministic_in_original_bin_order(self):
        a=self.asset('first.mp4');b=self.asset('second.mp4');c=self.asset('third.mp4')
        values={a['id']:media(width=640,height=360),b['id']:media(width=320,height=180),c['id']:media(width=320,height=180)}
        with patch.object(self.ws,'probe',side_effect=lambda ident:values[ident]):
            detected=self.ingest.detect([c['id'],b['id'],a['id']]);tie=self.ingest.detect([b['id'],a['id']])
        self.assertEqual(detected['reference_asset_id'],b['id']);self.assertEqual(detected['recommended_profile']['width'],320)
        self.assertEqual(tie['reference_asset_id'],a['id']);self.assertTrue(detected['mixed_formats'])

    def test_uncertain_rotation_sar_cadence_and_metadata_require_analysis(self):
        source=self.asset()
        for changes in ({'side_data_list':[{'rotation':45}]},{'sample_aspect_ratio':'4:3'},
                        {'avg_frame_rate':'24000/1001','r_frame_rate':'30'}, {'field_order':'tt'},
                        {'color_transfer':'unknown'}, {'color_primaries':'smpte432'}, {'pix_fmt':'unknown','bits_per_raw_sample':'0'}):
            with self.subTest(changes=changes):self.assertTrue(Ingest.classify(source,media(**changes))['requires_ai'])
        with patch.object(self.ws,'probe',side_effect=ValueError('SECRET PATH /private/foo')):
            detected=self.ingest.detect([source['id']])
        self.assertTrue(detected['requires_ai']);self.assertNotIn('SECRET',json.dumps(detected))

    def configure_fixture(self):
        source=self.asset();path=self.timeline([]);self.ws.data['active_project']=str(path.relative_to(self.ws.root));self.ws.save()
        snapshot={'path':str(path),'revision':'a'*64,'profile':{'width':'640','height':'360','progressive':'1','fps':30},
            'color_workflow':{'dynamic_range':'sdr'},'tracks':[{'track_id':'v1','name':'V1','kind':'video','properties':{},'filters':[],
                'items':[{'type':'gap','start_frame':0,'duration_frames':1}]}]};edits=[]
        def forward(name,args):
            if name=='inspect_project':return result(copy.deepcopy(snapshot))
            self.assertEqual(name,'edit_project');edits.append(copy.deepcopy(args));snapshot['revision']='b'*64
            snapshot['profile'].update({key:str(value) for key,value in args['operations'][0].items() if key not in ('op','preserve_frame_numbers')})
            return result({'revision':'b'*64})
        return source,path,snapshot,edits,forward

    def test_configure_one_revision_transaction_once_and_preserve_tracks(self):
        source,path,snapshot,edits,forward=self.configure_fixture();tracks=copy.deepcopy(snapshot['tracks'])
        with patch.object(self.ws,'probe',return_value=VIDEO),patch.object(self.ws,'forward',side_effect=forward):
            value=self.ingest.configure(str(path),'a'*64,[source['id']])
            again=self.ingest.configure(str(path),'b'*64,[source['id']])
        self.assertTrue(value['configured']);self.assertEqual(again['status'],'already_configured');self.assertEqual(len(edits),1)
        operations=edits[0]['operations'];self.assertEqual([op['op'] for op in operations],['set_profile','set_color_workflow'])
        self.assertEqual((operations[0]['frame_rate_num'],operations[0]['frame_rate_den']),(30000,1001));self.assertTrue(operations[0]['preserve_frame_numbers'])
        self.assertNotIn('progressive',operations[0]);self.assertEqual(snapshot['tracks'],tracks)
        self.assertIn(str(path),json.loads(self.ws.statefile.read_text())['auto_configured_sequences'])

    def test_explicit_settings_nonempty_and_uncertain_signal_never_reconfigure(self):
        for scenario in ('manual','video','audio','unknown'):
            with self.subTest(scenario=scenario):
                source,path,snapshot,edits,forward=self.configure_fixture()
                self.ws.data.pop('auto_configured_sequences',None);self.ws.data['auto_configure']=scenario!='manual'
                if scenario in ('video','audio'):
                    snapshot['tracks'][0]['kind']=scenario;snapshot['tracks'][0]['items']=[{'type':'clip','duration_frames':30}]
                metadata=media(color_transfer='unknown') if scenario=='unknown' else VIDEO
                with patch.object(self.ws,'probe',return_value=metadata),patch.object(self.ws,'forward',side_effect=forward):
                    value=self.ingest.configure(str(path),'a'*64,[source['id']])
                self.assertFalse(value['configured']);self.assertEqual(edits,[])
                self.assertEqual(value['status'],'manual_settings_preserved' if scenario=='manual' else 'needs_ai' if scenario=='unknown' else 'nonempty_preserved')

    def test_revision_and_active_identity_are_rechecked_after_probe(self):
        for scenario in ('revision','identity'):
            with self.subTest(scenario=scenario):
                source,path,snapshot,edits,forward=self.configure_fixture()
                def probe(ident):
                    if scenario=='revision':snapshot['revision']='c'*64
                    else:self.ws.data['active_project']='edits/other.mlt'
                    return VIDEO
                with patch.object(self.ws,'probe',side_effect=probe),patch.object(self.ws,'forward',side_effect=forward):
                    with self.assertRaisesRegex(ValueError,'changed while detecting'):self.ingest.configure(str(path),'a'*64,[source['id']])
                self.assertEqual(edits,[])

    def test_render_guard_only_reachable_sources_and_legacy_8bit(self):
        ordinary=self.asset('legacy.mp4');unused=self.asset('unused-hdr.mp4');path=self.timeline([ordinary['path']],unused['path'])
        untagged=media(color_transfer='unknown',color_primaries='unknown',color_space='unknown')
        with patch.object(self.ws,'probe',side_effect=lambda ident,**kwargs:untagged if ident==ordinary['id'] else media(color_transfer='smpte2084')) as probe:
            value=self.ingest.ensure_render_ready(str(path))
        self.assertTrue(value['ready']);self.assertEqual(probe.call_count,1);self.assertIn('working assumption',value['warnings'][0])

    def test_render_guard_rejects_positive_risk_even_for_renamed_image(self):
        source=self.asset('looks-like-an-image.png');path=self.timeline([source['path']])
        for metadata in (media(color_transfer='smpte2084'),media(color_transfer='arib-std-b67'),media(color_transfer='log316'),
                         media(color_primaries='smpte432'),media(pix_fmt='yuv420p10le',bits_per_raw_sample='10',color_transfer='unknown'),
                         media(pix_fmt='unknown',bits_per_raw_sample='0'),media(side_data_list=[{'side_data_type':'DOVI configuration record'}])):
            with self.subTest(metadata=metadata),patch.object(self.ws,'probe',return_value=metadata):
                with self.assertRaisesRegex(ValueError,'Render requires'):self.ingest.ensure_render_ready(str(path))

    def test_timewarp_guard_uses_actual_input_not_stale_original_metadata(self):
        safe=self.asset('safe.mp4');risk=self.asset('hdr.mp4');path=self.timeline([risk['path']]);root=ET.parse(path)
        producer=root.getroot().find('chain');producer.find('property[@name="mlt_service"]').text='timewarp'
        producer.find('property[@name="resource"]').text='0.5:'+risk['path']
        ET.SubElement(producer,'property',name='shotcut:mcpOriginalResource').text=safe['path'];root.write(path)
        with patch.object(self.ws,'probe',return_value=media(color_transfer='smpte2084')):
            with self.assertRaisesRegex(ValueError,'HDR PQ'):self.ingest.ensure_render_ready(str(path))

    def test_render_resource_limit_and_total_probe_deadline_are_explicit(self):
        first=self.asset('first.mp4');second=self.asset('second.mp4');path=self.timeline([first['path'],second['path']])
        with patch('film.ingest.MAX_GUARD_RESOURCES',1),patch.object(self.ws,'probe') as probe:
            with self.assertRaisesRegex(ValueError,'unique media resources'):self.ingest.ensure_render_ready(str(path))
            probe.assert_not_called()
        path=self.timeline([first['path']]);clock=[0]
        def slow(ident,**kwargs):
            self.assertLessEqual(kwargs['timeout'],60);clock[0]=150;return VIDEO
        with patch('film.ingest.time.monotonic',side_effect=lambda:clock[0]),patch.object(self.ws,'probe',side_effect=slow):
            with self.assertRaisesRegex(ValueError,'timed out'):self.ingest.ensure_render_ready(str(path))


def real_acceptance():
    """Actual rotated, rational-rate tagged SDR plus metadata-only HDR fixture."""
    from film.server import FilmServer
    report={'synthetic_only':True}
    with tempfile.TemporaryDirectory(prefix='filmeditor-ingest-acceptance-') as temporary:
        temp=Path(temporary);ws=Workspace(temp/'workspace');server=FilmServer(ws,0);ingest=Ingest(ws)
        try:
            unpack(ws.call('film_new_project',{'name':'Auto detection validation','width':640,'height':360}))
            sdr=temp/'tagged-sdr.mp4';rotated=temp/'rotated-sdr.mov';hdr=temp/'tagged-pq.mp4'
            with ws.heavy():
                generated=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30000/1001',
                    '-frames:v','30','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-color_trc','bt709','-color_primaries','bt709',
                    '-colorspace','bt709','-color_range','tv','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709',str(sdr)],timeout=60)
                assert generated.returncode==0,generated.stderr
                turned=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-display_rotation','90','-i',str(sdr),'-c','copy',str(rotated)],timeout=30)
                assert turned.returncode==0,turned.stderr
            with rotated.open('rb') as stream:source=ws.ingest(stream,rotated.stat().st_size,rotated.name,'footage')
            before=server.editor.snapshot();tracks=copy.deepcopy(before['tracks']);history=len(server.editor.history.get(before['path'],{}).get('undo',[]))
            detection=ingest.detect([source['id']]);assert not detection['requires_ai'],detection
            video=detection['assets'][0]['video'];assert (video['width'],video['height'])==(180,320),video
            assert (video['fps_num'],video['fps_den'])==(30000,1001)
            value=ingest.configure(before['path'],before['revision'],[source['id']]);after=server.editor.snapshot()
            assert value['configured'] and after['tracks']==tracks
            assert int(after['profile']['width'])==180 and int(after['profile']['height'])==320
            assert int(after['profile']['frame_rate_num'])==30000 and int(after['profile']['frame_rate_den'])==1001
            assert len(server.editor.history[before['path']]['undo'])==history+1
            report.update(rotation_detected_without_double_transform=True,exact_30000_1001_preserved=True,
                empty_profile_configured_atomically=True,existing_tracks_preserved=True,one_shared_history_transaction=True,
                selected_profile={key:after['profile'][key] for key in ('width','height','frame_rate_num','frame_rate_den','sample_aspect_num','sample_aspect_den','display_aspect_num','display_aspect_den')})
            after=server.editor.edit(after['revision'],[{'op':'add_clip','track':'V1','path':source['path'],'position_frame':0}],project_path=after['path'])
            assert ingest.ensure_render_ready(after['path'])['ready']
            job=server.editor.render(after['revision'],preview=True,project_path=after['path'])
            for _ in range(150):
                status=unpack(ws.forward('render_status',{'job_id':job['job_id']}))
                if status['status'] not in ('queued','running'):break
                time.sleep(.2)
            assert status['status']=='completed',status
            with ws.heavy():
                decoded=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-i',status['output_path'],'-f','null','-'],timeout=30)
                assert decoded.returncode==0,decoded.stderr
                info=ws.run_media([ws.executable('ffprobe'),'-v','error','-show_streams','-of','json',status['output_path']],timeout=30)
                stream=json.loads(info.stdout)['streams'][0]
                assert (stream['width'],stream['height'])==(180,320)
                generated=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30',
                    '-frames:v','8','-c:v','libx264','-threads','1','-pix_fmt','yuv420p10le','-color_trc','smpte2084',
                    '-color_primaries','bt2020','-colorspace','bt2020nc','-color_range','tv',
                    '-x264-params','colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',str(hdr)],timeout=60)
                assert generated.returncode==0,generated.stderr
            with hdr.open('rb') as stream:high=ws.ingest(stream,hdr.stat().st_size,hdr.name,'footage')
            high_info=ingest.detect([high['id']]);assert high_info['assets'][0]['video']['color_type']=='pq',high_info
            assert high_info['requires_ai'] and not high_info['pipeline_ready']
            # Importing unused HDR does not change or prohibit the actual SDR sequence.
            assert ingest.ensure_render_ready(after['path'])['ready']
            report.update(actual_mlt_portrait_export_fully_decoded=True,tagged_hdr_metadata_recognized=True,
                hdr_pipeline_review_required=True,unused_hdr_does_not_block_sdr_export=True,
                hdr_evidence='Synthetic PQ/BT.2020 tags, not a real camera or verified HDR color conversion')
            (ROOT/'reports/rebuild/ingest').mkdir(parents=True,exist_ok=True)
            (ROOT/'reports/rebuild/ingest/synthetic-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
        finally:server.library.close();server.codex.close();ws.close();server.server_close()
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    if '--real' in sys.argv:real_acceptance()
    else:unittest.main()
