"""Opt-in Chromium music workflow against real local MLT, using synthetic media.

Run with the configured Mac environment. This is browser integration evidence;
native AppKit/WKWebView acceptance remains a separate check.
"""
from array import array
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT, Workspace, result, unpack
from film.server import FilmServer
from playwright.sync_api import sync_playwright, expect


def semantic(tracks):
    values=copy.deepcopy(tracks)
    for track in values:
        for item in track['items']:item.pop('item_ref',None)
    return values


def main():
    report={'synthetic_only':True,'surface':'Chromium with real local MLT and FFmpeg'}
    destination=ROOT/'reports/rebuild/music';destination.mkdir(parents=True,exist_ok=True)
    errors=[]
    with tempfile.TemporaryDirectory(prefix='filmeditor-music-ui-') as temporary:
        temp=Path(temporary);ws=Workspace(temp/'workspace');server=FilmServer(ws,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            unpack(ws.call('film_new_project',{'name':'Synthetic music UI','width':640,'height':360,'fps_num':30,'fps_den':1}))
            video=temp/'synthetic-picture.mp4';song=temp/'my-local-song.wav'
            with ws.heavy():
                generated=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-threads','1',
                    '-pix_fmt','yuv420p','-c:a','aac',str(video)],timeout=60)
            assert generated.returncode==0,generated.stderr
            with wave.open(str(song),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000)
                audio.writeframes(array('h',[round(20000*math.sin(2*math.pi*220*i/8000)) for i in range(8000*2)]).tobytes())
            hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (video,song)}
            with video.open('rb') as stream:asset=ws.ingest(stream,video.stat().st_size,video.name,'footage')
            before=server.editor.snapshot()
            before=server.editor.edit(before['revision'],[{'op':'add_clip','track':'V1','path':asset['path'],'position_frame':0}],project_path=before['path'])
            with sync_playwright() as automation:
                browser=automation.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,chromium_sandbox=True)
                page=browser.new_page(viewport={'width':1440,'height':900});page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(server.origin+'/?project=legacy#'+server.token)
                expect(page.locator('#notice')).to_contain_text('Project ready',timeout=60000)
                page.locator('#music-open').click()
                expect(page.locator('#music-dialog')).to_be_visible()
                page.locator('#music-dialog input[type=file]').set_input_files(str(song))
                expect(page.locator('#music-asset option')).to_have_count(1,timeout=60000)
                imported=next(item for item in ws.data['assets'] if item['kind']=='audio')
                assert imported['name']==song.name
                report['actual_file_input_local_song_import']=True
                page.locator('#music-gain_db').fill('-15')
                page.locator('#music-source_offset_seconds').fill('.5')
                page.locator('#music-fade_in_seconds').fill('.2')
                page.locator('#music-fade_out_seconds').fill('.4')
                page.locator('#music-score').click()
                expect(page.locator('#music-status')).to_contain_text('Added 3.00 s on Music.',timeout=90000)
                expect(page.locator('.track-strip[data-track-name="Music"] .clip')).to_have_count(1)
                after=server.editor.snapshot();previous=[track for track in after['tracks'] if track['track_id'] in {old['track_id'] for old in before['tracks']}]
                assert semantic(previous)==semantic(before['tracks'])
                report['real_fit_through_ui']=True;report['picture_dialogue_preserved']=True
                page.screenshot(path=str(destination/'music-ui.png'))
                page.locator('#music-dialog .dialog-heading button').click()
                page.locator('#export-top').click()
                page.locator('#export-preset').select_option('h264-web')
                page.locator('#export-form button[type=submit]').click()
                expect(page.locator('#jobs .job p')).to_contain_text('completed',timeout=90000)
                job=next(item for item in server.editor.renders() if item['status']=='completed' and '/exports/' in item['output_path'])
                exported=Path(job['output_path']);credits=exported.with_name(exported.name+'.credits.txt')
                assert exported.is_file() and credits.is_file()
                credit_text=credits.read_text()
                assert after['revision'] in credit_text and 'Trimmed from 0.5s' in credit_text and 'level -15 dB' in credit_text
                assert 'User supplied local music; no licence assertion' in credit_text
                with ws.heavy():
                    decoded=ws.run_media([ws.executable('ffmpeg'),'-nostdin','-v','error','-i',str(exported),'-f','null','-'],timeout=30)
                assert decoded.returncode==0,decoded.stderr
                report['real_ui_export_fully_decoded']=True;report['revision_scoped_export_credits']=True
                page.locator('#jobs-close').click();page.reload()
                expect(page.locator('.track-strip[data-track-name="Music"] .clip')).to_have_count(1,timeout=60000)
                report['music_persisted_after_reload']=True
                # Only catalogue discovery is mocked here, to assert untrusted text
                # stays inert in the UI. Fitting, importing, and export above are real.
                def catalogue(route):
                    request=route.request.post_data_json
                    if request.get('name')!='film_music_search':route.continue_();return
                    track={'id':'7d573956-f2f0-457a-9ed6-3b2de0280c4e','title':'<img src=x onerror=window.musicXss=true>',
                        'creator':'<script>window.musicXss=true</script>','license':'by','license_version':'3.0','duration_seconds':123,
                        'preview_only':False,'attribution':'<svg onload=window.musicXss=true>',
                        'source_url':'https://example.org/song','license_url':'https://creativecommons.org/licenses/by/3.0/'}
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(result({'tracks':[track]})))
                page.route('**/api/call',catalogue)
                page.locator('#music-open').click();page.locator('#music-query').fill('test catalogue')
                page.locator('#music-dialog form button[type=submit]').click()
                expect(page.locator('#music-results .music-result')).to_have_count(1)
                expect(page.locator('#music-results strong')).to_have_text('<img src=x onerror=window.musicXss=true>')
                assert page.locator('#music-results img, #music-results script, #music-results svg').count()==0
                assert not page.evaluate('Boolean(window.musicXss)')
                report['mock_catalogue_html_inert']=True
                browser.close()
            assert not errors,errors
            assert hashes=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (video,song)}
            report.update(no_javascript_errors=True,synthetic_sources_untouched=True)
        finally:
            server.shutdown();server.library.close();server.codex.close();ws.close();server.server_close()
    (destination/'ui-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
