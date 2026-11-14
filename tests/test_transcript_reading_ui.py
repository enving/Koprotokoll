"""Lesetext verbindet technische Segmente, während die zeitbasierte Ansicht erhalten bleibt."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from test_integrations import _chrome_path
from test_koprotokoll import FakeTranscriber, NoLLM


@pytest.mark.parametrize('width,multiple', [(1280, False), (390, False), (1280, True)])
def test_reading_view_joins_segments_and_keeps_timed_tools(tmp_path, width, multiple):
    app = create_app(Settings(data_dir=tmp_path, diarization=False),
                     transcriber=FakeTranscriber(), llm=NoLLM(), background=False)
    db = app.state.db
    mid = db.create_meeting('Lesetest', 'test')
    texts = ['Wir sehen uns', 'als Anwendungslabor. Wir prüfen zuerst,', 'was im Alltag funktioniert.']
    segments = [{'start': i * 5, 'end': (i + 1) * 5, 'speaker': 'S2' if multiple and i == 2 else 'S1',
                 'text': text, 'word_times': [{'start': i * 5, 'end': (i + 1) * 5, 'text': text}]}
                for i, text in enumerate(texts)]
    db.replace_segments(mid, segments)
    db.update_meeting(mid, status='done', speakers={'S1': 'Alex', **({'S2': 'Sam'} if multiple else {})})
    before = db.get_segments(mid)
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme='dark')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            req = route.request
            response = client.request(req.method, urlsplit(req.url).path, content=req.post_data_buffer,
                                      headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('**/*', serve)
        page.goto(f'http://127.0.0.1/#meeting={mid}')
        page.get_by_role('tab', name='Transkript', exact=True).click()
        expect(page.get_by_label('Transkriptansicht', exact=True)).to_have_value('read')
        expect(page.locator('#transcript .ts')).to_have_count(0)
        expect(page.locator('#transcript .who')).to_have_count(0)
        if multiple:
            expect(page.locator('.reading-speaker')).to_have_text(['Alex', 'Sam'])
        else:
            expect(page.locator('#transcript')).to_have_text(' '.join(texts))
            expect(page.locator('.reading-speaker')).to_have_count(0)
            # Ein Suchbegriff kann über die frühere technische Segmentgrenze laufen.
            page.locator('#t-filter').fill('uns als')
            expect(page.locator('#transcript mark')).to_have_text(['uns', 'als'])
            page.locator('#t-filter').fill('nicht vorhanden')
            expect(page.locator('#transcript')).to_have_text('Keine passenden Textstellen.')
            page.locator('#t-filter').fill('')
        page.evaluate("document.querySelector('#player').currentTime = 7; document.querySelector('#player').dispatchEvent(new Event('timeupdate'))")
        expect(page.locator('.reading-segment.current')).to_have_text(texts[1])
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if os.environ.get('KOPROTOKOLL_UI_ARTIFACTS'):
            folder = Path(os.environ['KOPROTOKOLL_UI_ARTIFACTS']); folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f'lesetext-{width}-{multiple}.png'))
        page.get_by_label('Transkriptansicht', exact=True).select_option('timed')
        expect(page.locator('#transcript .seg')).to_have_count(3)
        expect(page.locator('#transcript .ts')).to_have_text(['00:00', '00:05', '00:10'])
        page.locator('#transcript .seg').first.hover()
        page.locator('#transcript .mark').first.click()
        expect(page.locator('#transcript .seg')).to_have_count(3)
        page.reload()
        page.get_by_role('tab', name='Transkript', exact=True).click()
        expect(page.get_by_label('Transkriptansicht', exact=True)).to_have_value('timed')
        assert db.get_segments(mid) == before
        assert len(db.list_bookmarks(mid)) == 1
        assert errors == []
        browser.close()
