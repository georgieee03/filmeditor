"""Historical browser smoke for the retired loopback UI.

It starts a local FilmServer and Chromium using protocol fixtures; it does not test
the native macOS app or its WKWebView. Run only when explicitly validating the old
browser host: ``python3 tests/browser_smoke.py --legacy-server``. Requires
Playwright and Chromium (FILM_TEST_CHROMIUM can override).
"""
import sys
if __name__=='__main__' and '--legacy-server' not in sys.argv:
    raise SystemExit('Retired browser test. Pass --legacy-server to start its loopback server.')
from pathlib import Path
import json
import os
import subprocess
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT, Workspace, atomic
from film.server import FilmServer
from playwright.sync_api import sync_playwright, expect

def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); ws=Workspace(root/'workspace')
        fixture=ROOT/'tests/fixture_engine.py'
        source=root/'synthetic-camera-test.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=360x640:rate=30','-t','1','-c:v','libx264','-threads','1',str(source)],check=True)
        with patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(fixture),'FILM_CODEX':str(fixture),'FILM_HYPERFRAMES':str(fixture)}):
            server=FilmServer(ws,0); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            atomic(ws.root/'.film/connection.json',json.dumps({'url':server.origin,'token':server.token}).encode())
            errors=[]
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(executable_path=os.environ.get('FILM_TEST_CHROMIUM','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),headless=True,chromium_sandbox=True)
                    page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(server.origin+'/?project=legacy#'+server.token)
                    expect(page.locator('#notice')).to_contain_text('Project ready')
                    page.locator('#files').set_input_files(str(source))
                    expect(page.locator('#asset-count')).to_have_text('1')
                    page.locator('#assets button',has_text='View').first.click()
                    page.get_by_text('Creative brief',exact=True).click()
                    page.locator('#brief').fill('Synthetic acceptance test: preserve source, build a vertical timeline, and keep motion source editable.')
                    page.locator('#save-brief').click()
                    page.locator('#new-project').click()
                    page.locator('#new-form button[type=submit]').click()
                    expect(page.locator('#projects')).to_have_value(__import__('re').compile(r'.*\.mlt'))
                    page.locator('#append').click()
                    expect(page.locator('.clip').first).to_be_visible()
                    page.locator('#source-frame').click()
                    expect(page.locator('#image')).to_be_visible()
                    page.locator('#connect-codex').click()
                    expect(page.locator('#model option')).to_have_count(2)
                    page.locator('#prompt').fill('Read the active project and verify the shared workspace.')
                    page.locator('#send').click()
                    expect(page.locator('#messages')).to_contain_text('Fixture edit response')
                    page.screenshot(path=str(ROOT/'reports/ui-smoke.png'),full_page=True)
                    page.locator('.tab[data-page=motion]').click()
                    page.locator('#scene-template').click()
                    page.locator('#scene-name').fill('smoke-scene')
                    # No real GSAP/HyperFrames installation is represented by this fixture check.
                    page.locator('#scene-source').fill('<div data-composition-id="test" data-width="1080" data-height="1920"></div>')
                    page.locator('#scene-save').click()
                    expect(page.locator('#notice')).to_contain_text('Scene saved')
                    page.locator('#scene-render').click()
                    expect(page.locator('#jobs')).to_contain_text('completed',timeout=30000)
                    assert not errors, errors
                    assert len(ws.data['assets'])==2
                    browser.close()
                    print('Browser smoke passed: load, streamed import, source frame, timeline create/append, fixture Codex chat, motion fixture round-trip; zero JS errors.')
            finally:
                server.shutdown(); server.codex.close(); ws.close(); server.server_close(); thread.join()

if __name__=='__main__': main()
