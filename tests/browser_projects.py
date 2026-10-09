"""Historical project-picker browser test for the retired loopback UI.

It starts a local FilmServer and Chromium with fixture engines; it does not test
the native macOS app or its WKWebView. Run only when explicitly validating the
old browser host: ``python3 tests/browser_projects.py --legacy-server``.
"""
from pathlib import Path
import os
import sys
if __name__=='__main__' and '--legacy-server' not in sys.argv:
    raise SystemExit('Retired browser test. Pass --legacy-server to start its loopback server.')
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from film.core import ROOT, Workspace
from film.server import FilmServer
from playwright.sync_api import sync_playwright, expect

with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'FILM_SHOTCUT_MCP':str(ROOT/'tests/fixture_engine.py')}):
    root=Path(tmp); ws=Workspace(root/'library');server=FilmServer(ws,0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    for name in ('one.mp4','two.mp4','look.png'): (root/name).write_bytes(b'fixture')
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,chromium_sandbox=True)
            page=browser.new_page(viewport={'width':1600,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            home=server.origin+'/#'+server.token
            page.goto(home)
            expect(page.locator('#project-picker')).to_be_visible()
            expect(page.locator('#editor')).to_be_hidden()
            def create(name):
                page.locator('#create-project').click()
                page.locator('#project-form input[name=name]').fill(name)
                page.locator('#project-form button[type=submit]').click()
                expect(page.locator('#project-title')).to_have_text(name)
                expect(page.locator('#notice')).to_contain_text('Project ready')
            create('First film')
            first_url=page.url
            page.locator('#files').set_input_files([str(root/'one.mp4'),str(root/'two.mp4')])
            expect(page.locator('#asset-count')).to_have_text('2')
            expect(page.locator('#assets .asset-label')).to_have_text(['Video 1','Video 2'])
            page.locator('#import-kind').select_option('reference')
            page.locator('#files').set_input_files(str(root/'look.png'))
            expect(page.locator('#references .asset-label')).to_have_text(['Reference 1'])
            page.locator('#references button',has_text='Delete').click()
            page.locator('#delete-cancel').click()
            expect(page.locator('#references .asset')).to_have_count(1)
            page.locator('#references button',has_text='Delete').click()
            page.locator('#delete-confirm').click()
            expect(page.locator('#references .asset')).to_have_count(0)
            page.locator('#assets .asset').first.get_by_role('button',name='Delete',exact=True).click()
            page.locator('#delete-confirm').click()
            expect(page.locator('#assets .asset-label')).to_have_text(['Video 1'])
            expect(page.locator('#assets .asset strong')).to_have_text(['two.mp4'])
            page.reload()
            expect(page.locator('#assets .asset-label')).to_have_text(['Video 1'])
            page.locator('#back-projects').click()
            expect(page.locator('.project-card')).to_have_count(1)
            create('Second film')
            expect(page.locator('#asset-count')).to_have_text('0')
            page.locator('#back-projects').click()
            expect(page.locator('.project-card')).to_have_count(2)
            page.screenshot(path=str(ROOT/'reports/project-picker.png'),full_page=True)
            page.locator('.project-card').filter(has_text='First film').get_by_role('button',name='Open project').click()
            expect(page.locator('#assets .asset strong')).to_have_text(['two.mp4'])
            page.screenshot(path=str(ROOT/'reports/project-editor.png'),full_page=True)
            page.locator('#back-projects').click()
            page.locator('.project-card').filter(has_text='Second film').get_by_role('button',name='Delete',exact=True).click()
            page.locator('#delete-confirm').click()
            expect(page.locator('.project-card')).to_have_count(1)
            page.reload()
            expect(page.locator('.project-card')).to_have_count(1)
            assert not errors,errors
            browser.close()
        print('PASS: create, isolate, open, reload, cancel deletion, delete media/reference/project, order labels; fixture timelines only.')
    finally:
        server.library.close();server.shutdown();server.codex.close();ws.close();server.server_close()
