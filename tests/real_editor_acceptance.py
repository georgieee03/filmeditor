"""Opt-in real MLT + FFmpeg + browser test, using only temporary synthetic media."""
from pathlib import Path
import hashlib,json,sys,tempfile,threading,time,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import Workspace,ROOT,unpack
from film.server import FilmServer
from playwright.sync_api import sync_playwright,expect

report={}; failures=[]
def check(value,label):
    assert value,label
    report[label]=True

with tempfile.TemporaryDirectory(prefix='filmeditor-v2-acceptance-') as tmp:
    root=Path(tmp);ws=Workspace(root/'workspace');server=FilmServer(ws,0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        source=root/'synthetic-blue.mp4'
        subprocess.run([ws.executable('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709','-c:a','aac',str(source)],check=True)
        checksum=hashlib.sha256(source.read_bytes()).hexdigest()
        unpack(ws.call('film_new_project',{'name':'Synthetic validation','width':640,'height':360}))
        with source.open('rb') as f: a=ws.ingest(f,source.stat().st_size,source.name,'footage')
        def manual_edit(rev,ops):return server.editor.edit(rev,ops,project_path=str(ws.active()))
        def history(rev,direction='undo'):return server.editor.restore(rev,direction,project_path=str(ws.active()))
        snap=server.editor.snapshot();check(len(snap['tracks'])==6,'six tracks')
        info=server.editor.media(a['id']);check(info['has_audio'] and bool(info['thumbnail']) and bool(info['waveform']),'real thumbnails and waveform')
        snap=manual_edit(snap['revision'],[{'op':'add_clip','track':'V1','path':a['path'],'position_frame':30,'caption':'Synthetic test pattern'}])
        c=next(t for t in snap['tracks'] if t['name']=='V1')['items'][1]
        check(c['start_frame']==30 and c['duration_frames']==90,'insert with gap')
        stale=snap['revision']
        snap=manual_edit(stale,[{'op':'move_item','item_ref':c['item_ref'],'target_track':'V2','position_frame':60,'ripple_source':False}])
        c=next(i for t in snap['tracks'] if t['name']=='V2' for i in t['items'] if i['type']=='clip')
        check(c['start_frame']==60,'cross track move')
        try:manual_edit(stale,[{'op':'set_notes','notes':'stale'}]);raise AssertionError('stale revision accepted')
        except ValueError: report['stale edit rejected']=True
        snap=manual_edit(snap['revision'],[{'op':'trim_item','item_ref':c['item_ref'],'edge':'start','delta':15,'ripple':False}])
        c=next(i for t in snap['tracks'] if t['name']=='V2' for i in t['items'] if i['type']=='clip')
        check(c['start_frame']==75 and c['in_frame']==15 and c['duration_frames']==75,'trim source and placement')
        before=snap['revision'];snap=history(before)
        check(snap['can_redo'],'undo available');snap=history(snap['revision'],'redo');check(snap['revision']==before,'redo exact revision')
        c=next(i for t in snap['tracks'] if t['name']=='V2' for i in t['items'] if i['type']=='clip')
        snap=manual_edit(snap['revision'],[{'op':'split_item','item_ref':c['item_ref'],'offset_frame':30}])
        check(sum(i['type']=='clip' for t in snap['tracks'] for i in t['items'])==2,'split')
        c=next(i for t in snap['tracks'] if t['name']=='V2' for i in t['items'] if i['type']=='clip')
        snap=manual_edit(snap['revision'],[{'op':'animate_clip','item_ref':c['item_ref'],'keyframes':[{'frame':0,'center_x':.5,'center_y':.5,'scale':.9,'rotation_degrees':0,'opacity':.8,'volume_db':-6}]}])
        c=next(i for t in snap['tracks'] if t['name']=='V2' for i in t['items'] if i['type']=='clip')
        snap=manual_edit(snap['revision'],[{'op':'add_filter','target':'clip','item_ref':c['item_ref'],'service':'brightness','properties':{'level':1.1,'alpha':1,'film:brightness':'1'}},{'op':'add_filter','target':'clip','item_ref':c['item_ref'],'service':'frei0r.saturat0r','properties':{'0':.15,'film:saturation':'1'}}])
        frame=server.editor.frame(snap['revision'],80,project_path=snap['path'])
        check(frame['revision']==snap['revision'] and frame['url'],'immutable real frame preview')
        ai_result=unpack(ws.call('edit_project',{'project_path':snap['path'],'expected_revision':snap['revision'],'operations':[{'op':'set_notes','notes':'Synthetic Codex/MCP history check'}]}))
        snap=server.editor.snapshot();check(snap['can_undo'] and snap['revision']==ai_result['revision'],'MCP edit joins manual undo history')
        snap=history(snap['revision'])
        job=server.editor.render(snap['revision'],preview=True,project_path=snap['path'])
        for _ in range(120):
            status=unpack(ws.forward('render_status',{'job_id':job['job_id']}))
            if status['status'] not in ('running','queued'):break
            time.sleep(.3)
        check(status['status']=='completed','real MLT render complete')
        subprocess.run([ws.executable('ffmpeg'),'-v','error','-i',status['output_path'],'-f','null','-'],check=True,capture_output=True)
        check(True,'render fully decodes')
        check(any(j.get('url') for j in server.editor.renders()),'render recovery with playable URL')
        report['render']={k:status.get(k) for k in ['status','total_frames','project_revision','consumer_properties']}
        check(True,'real brightness and saturation render')
        prores=root/'synthetic-prores.mov'
        subprocess.run([ws.executable('ffmpeg'),'-v','error','-i',str(source),'-t','1','-c:v','prores_ks','-threads','1','-profile:v','1','-c:a','pcm_s16le',str(prores)],check=True)
        with prores.open('rb') as f: proxy_asset=ws.ingest(f,prores.stat().st_size,prores.name,'footage')
        proxy=server.editor.media(proxy_asset['id'],proxy=True)
        check(proxy['proxy'] and proxy['has_audio'],'real ProRes browser proxy')
        # Fresh sequence for actual pointer and drag-and-drop interactions.
        unpack(ws.call('film_new_project',{'name':'Pointer validation','width':640,'height':360}))
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,chromium_sandbox=True)
            page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:failures.append(str(e)))
            page.goto(server.origin+'/?project=legacy#'+server.token)
            expect(page.locator('#notice')).to_contain_text('Project ready',timeout=60000)
            expect(page.locator('.asset')).to_have_count(2)
            expect(page.locator('.track-strip')).to_have_count(6)
            page.locator('.asset').first.drag_to(page.locator('.track-strip[data-track-name="V1"]'),target_position={'x':100,'y':20})
            expect(page.locator('.clip')).to_have_count(1,timeout=60000)
            check(True,'browser media bin drag inserts clip')
            first=page.locator('.clip').first
            box=first.bounding_box(); target=page.locator('.track-strip[data-track-name="V2"]').bounding_box()
            page.mouse.move(box['x']+35,box['y']+20);page.mouse.down();page.mouse.move(box['x']+35,target['y']+20,steps=15);page.mouse.up()
            expect(page.locator('.track-strip[data-track-name="V2"] .clip')).to_have_count(1,timeout=60000)
            check(True,'browser cross track pointer drag')
            first=page.locator('.clip').first
            old=float(first.get_attribute('data-duration'));handle=first.locator('.trim-handle.start').bounding_box()
            page.mouse.move(handle['x']+3,handle['y']+20);page.mouse.down();page.mouse.move(handle['x']+30,handle['y']+20,steps=10);page.mouse.up()
            page.wait_for_function('(old)=>Number(document.querySelector(".clip").dataset.duration)<old',arg=old,timeout=60000)
            check(True,'browser trim handle changes duration')
            page.locator('#undo').click();page.wait_for_function('(old)=>Number(document.querySelector(".clip").dataset.duration)===old',arg=old,timeout=60000)
            check(True,'browser undo restores trim')
            first.click(position={'x':30,'y':20});expect(page.locator('#clip-controls')).to_be_visible()
            page.locator('#duplicate').click();expect(page.locator('.clip')).to_have_count(2,timeout=60000)
            page.reload();expect(page.locator('.clip')).to_have_count(2,timeout=60000)
            check(True,'saved timeline survives browser reload')
            page.locator('.clip').first.click(position={'x':30,'y':20})
            page.locator('#effect-volume').fill('-8');page.locator('#apply-effects').click()
            expect(page.locator('#notice')).to_contain_text('Changed controls saved',timeout=60000)
            c=next(i for t in server.editor.snapshot()['tracks'] for i in t['items'] if i['type']=='clip')
            check(any(f['properties'].get('shotcut:mcpVolume')=='1' for f in c['filters']) and not any(f['properties'].get('shotcut:mcpTransform')=='1' for f in c['filters']),'volume edit does not overwrite transform')
            page.locator('.clip').first.click(position={'x':30,'y':20})
            page.locator('#color-brightness').fill('110');page.locator('#color-saturation').fill('120');page.locator('#apply-color').click()
            expect(page.locator('#notice')).to_contain_text('Color adjustments saved',timeout=60000)
            check(True,'browser color controls commit')
            # Actual file input imports through the UI transport.
            page.locator('#files').set_input_files(str(source))
            expect(page.locator('.asset')).to_have_count(3,timeout=60000)
            check(True,'browser manual file import')
            page.locator('#preview-video').click()
            expect(page.locator('#monitor-badge')).to_have_text('RENDERED',timeout=90000)
            page.locator('#play').click();page.wait_for_function('()=>document.getElementById("video").currentTime>0.1',timeout=15000)
            check(True,'browser sequence playback')
            page.locator('#play').click()
            frame=int(page.locator('.clip').first.get_attribute('data-start'))+15
            page.locator('#monitor-scrub').fill(str(frame));page.locator('#monitor-scrub').dispatch_event('input')
            page.wait_for_function('()=>!document.getElementById("video").seeking')
            page.locator('.clip').first.click(position={'x':25,'y':20})
            page.wait_for_timeout(200)
            page.screenshot(path=str(ROOT/'reports/rebuild/editor-synthetic.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':900});page.wait_for_timeout(300);page.screenshot(path=str(ROOT/'reports/rebuild/editor-mac.png'),full_page=True)
            page.set_viewport_size({'width':1280,'height':800});page.wait_for_timeout(300)
            expect(page.locator('#workspace-layout')).to_be_visible()
            preset_dimensions={'editing':(210,180,285,.54),'color':(170,290,270,.62),'audio':(190,180,285,.42),'graphics':(170,280,270,.6),'reset':(210,180,285,.54)}
            for preset,(bin_width,inspector_width,chat_width,top_fraction) in preset_dimensions.items():
                page.locator('#workspace-layout').select_option(preset)
                dimensions=page.evaluate('''() => {
                  const style=getComputedStyle(document.documentElement), editor=document.getElementById('editor');
                  return {
                    bin:style.getPropertyValue('--bin').trim(),
                    inspector:style.getPropertyValue('--inspector').trim(),
                    chat:style.getPropertyValue('--chat').trim(),
                    top:parseFloat(style.getPropertyValue('--top')),
                    height:editor.clientHeight,
                    selected:document.getElementById('workspace-layout').value,
                    saved:localStorage.getItem('film-workspace'),
                    outside: [...document.querySelectorAll('.bin,.monitor,.inspector,.chat,.timeline')].some(node=>{
                      const r=node.getBoundingClientRect();return r.left < -1 || r.right > innerWidth+1 || r.top < 48 || r.bottom > innerHeight-25;
                    }),
                    documentWidth:document.documentElement.scrollWidth
                  };
                }''')
                selected='editing' if preset=='reset' else preset
                check(dimensions['bin']==f'{bin_width}px' and dimensions['inspector']==f'{inspector_width}px' and dimensions['chat']==f'{chat_width}px' and dimensions['selected']==selected and dimensions['saved']==selected,'workspace preset applies and saves '+preset)
                check(280 <= dimensions['top'] <= dimensions['height']-250 and not dimensions['outside'] and dimensions['documentWidth']<=1280,'workspace preset fits 1280x800 '+preset)
            page.locator('#workspace-layout').select_option('editing')
            divider=page.locator('.bin .panel-resizer')
            before=page.locator('.bin').bounding_box()['width']
            handle=divider.bounding_box()
            page.mouse.move(handle['x']+handle['width']/2,handle['y']+50);page.mouse.down();page.mouse.move(handle['x']+handle['width']/2+20,handle['y']+50,steps=5);page.mouse.up()
            page.wait_for_function('(before)=>document.querySelector(".bin").getBoundingClientRect().width>before+10',arg=before)
            custom=page.locator('.bin').bounding_box()['width']
            check(custom>before+10 and float(page.evaluate("localStorage.getItem('film-layout---bin')"))==custom,'panel divider saves custom width')
            page.reload();expect(page.locator('#notice')).to_contain_text('Project ready',timeout=60000)
            page.wait_for_function('(width)=>Math.abs(document.querySelector(".bin").getBoundingClientRect().width-width)<1',arg=custom)
            check(page.locator('#workspace-layout').input_value()=='editing','panel dimensions restore after reload')
            for selector in ['#permissions','#model','#send','#notice']:
                box=page.locator(selector).bounding_box();check(bool(box) and box['y']+box['height']<=801,'visible 1280x800 '+selector)
            check(page.evaluate('''() => [...document.querySelectorAll('.bin,.monitor,.inspector,.chat,.timeline')].every(node=>{
              const r=node.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1&&r.top>=48&&r.bottom<=innerHeight-25;
            })'''),'workspace panels fit 1280x800 after saved divider reload')
            page.screenshot(path=str(ROOT/'reports/rebuild/editor-1280.png'),full_page=True)
            browser.close()
        check(not failures,'no browser JavaScript errors')
        check(hashlib.sha256(source.read_bytes()).hexdigest()==checksum,'source untouched')
    finally:
        if ws.shotcut:
            for job in server.editor.renders():
                if job['status'] in ('running','queued'):
                    ws.forward('cancel_render',{'job_id':job['job_id']})
        server.shutdown();server.library.close();server.codex.close();ws.close();server.server_close()
        report['browser_errors']=failures
        (ROOT/'reports/rebuild/real-acceptance.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
