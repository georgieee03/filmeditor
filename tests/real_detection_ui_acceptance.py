"""Real ffprobe/MLT auto-configuration; AI dispatch is mocked and explicitly labeled."""
import hashlib,json,sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT,Workspace,unpack
from film.server import FilmServer
from playwright.sync_api import sync_playwright,expect

def main():
    report={'synthetic_only':True,'ai_provider':'mock dispatch only; no claim of AI editing'}
    destination=ROOT/'reports/rebuild/detection';destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='filmeditor-detection-ui-') as temporary:
        root=Path(temporary);ws=Workspace(root/'workspace');server=FilmServer(ws,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            unpack(ws.call('film_new_project',{'name':'Detection acceptance','width':640,'height':360}))
            tagged=root/'portrait-tagged.mp4';untagged=root/'unknown-color.mp4'
            for path,extra in [(tagged,['-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709']), (untagged,[])]:
                r=ws.run_media([ws.executable('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=270x480:rate=24000/1001',
                    '-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',*extra,str(path)],timeout=60)
                assert r.returncode==0,r.stderr
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tagged,untagged)}
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,chromium_sandbox=True)
                page=browser.new_page(viewport={'width':1440,'height':900});errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                def capture(route):
                    requests.append(route.request.post_data_json)
                    route.fulfill(status=200,content_type='application/json',body=json.dumps({'thread_id':'mock','turn_id':'mock'}))
                page.route('**/api/codex/start',capture)
                page.goto(server.origin+'/?project=legacy#'+server.token)
                page.wait_for_function('() => timeline?.revision && !refreshing')
                # This check uses a fresh empty project, so the first-run guide appears.
                expect(page.locator('#quickstart-dialog')).to_be_visible(timeout=10000)
                page.locator('#quickstart-dialog .dialog-heading button').click()
                page.locator('#files').set_input_files(str(tagged))
                page.wait_for_function('() => Number(timeline?.profile?.width)===270 && Number(timeline?.profile?.height)===480',timeout=60000)
                snap=server.editor.snapshot();assert abs(snap['profile']['fps']-24000/1001)<.0001
                assert not requests,'Known tagged SDR unexpectedly escalated to AI'
                report['tagged_portrait_exact_rational_fps_auto_configured']=True
                report['known_technical_metadata_avoids_ai']=True
                page.locator('#media-info').click();expect(page.locator('#media-detection-dialog')).to_be_visible()
                expect(page.locator('#media-detection-dialog')).to_contain_text('270 × 480')
                page.locator('#media-detection-dialog .dialog-heading button').click()
                page.locator('#files').set_input_files(str(untagged))
                page.wait_for_function("() => document.getElementById('notice').textContent.includes('imported')",timeout=60000)
                page.wait_for_timeout(300)
                assert any('Resolve only the inconclusive technical setup' in r['text'] for r in requests)
                assert all('do not make a creative edit or export' in r['text'] for r in requests)
                report['unknown_color_dispatches_setup_only_ai_fallback']=True
                page.locator('#ai-edit-open').click();page.locator('#ai-edit-brief').fill('Make a one-second portrait test and export it.')
                page.locator('#ai-edit-start').click()
                expect(page.locator('#ai-edit-dialog')).not_to_be_visible(timeout=10000)
                assert any('Make a one-second portrait test' in r['text'] and 'poll it to completion' in r['text'] for r in requests)
                report['explicit_ai_edit_and_render_request_dispatch']=True
                assert not errors,errors
                report['no_javascript_errors']=True
                assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p.name] for p in (tagged,untagged))
                report['source_untouched']=True
                page.screenshot(path=str(destination/'detection-ui.png'))
                browser.close()
        finally:
            server.shutdown();server.library.close();server.codex.close();ws.close();server.server_close()
    (destination/'ui-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
