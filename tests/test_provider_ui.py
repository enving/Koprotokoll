"""Reproduzierbare Anbieter-Demo gegen lokalen Fake-Server."""
from urllib.parse import urlsplit
import os
from pathlib import Path
import pytest
from test_local_model import server
from test_integrations import _chrome_path
from fastapi.testclient import TestClient
from koprotokoll.app import create_app
from koprotokoll.config import Settings


@pytest.mark.parametrize('width', [1280, 390])
def test_provider_ui(tmp_path, server, width):
    from playwright.sync_api import sync_playwright, expect
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model='testmodell')
    with TestClient(create_app(settings)) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def serve(route):
            req = route.request
            response = client.request(req.method, urlsplit(req.url).path, content=req.post_data_buffer, headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
        page.route('**/*', serve)
        page.goto('http://127.0.0.1/#integrations')
        page.get_by_label('Anbieter', exact=True).select_option('other')
        page.get_by_label('Basis-URL', exact=True).fill(url)
        page.get_by_label('Modellname', exact=True).fill('testmodell')
        with page.expect_response(lambda response: response.url.endswith('/api/llm') and response.request.method == 'GET'):
            page.get_by_role('button', name='Anbieter speichern', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_label('Basis-URL', exact=True)).to_have_value(url)
        page.get_by_role('button', name='Gespeicherten Anbieter testen', exact=True).click()
        expect(page.locator('#local-model')).to_contain_text('Probeaufruf: erfolgreich')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        if os.environ.get('KOPROTOKOLL_UI_ARTIFACTS'):
            folder = Path(os.environ['KOPROTOKOLL_UI_ARTIFACTS'])
            folder.mkdir(parents=True, exist_ok=True)
            page.locator('#local-model').screenshot(path=str(folder / f'anbieter-{width}.png'))
        browser.close()
