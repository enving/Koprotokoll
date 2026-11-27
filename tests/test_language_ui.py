"""Sprachwahl durch echte Formulare und beide Aufnahmewege weiterreichen."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from koprotokoll import app as app_mod
from koprotokoll.config import Settings
from test_integrations import _chrome_path
from test_koprotokoll import FakeTranscriber, NoLLM, wav_bytes


@pytest.mark.parametrize('width', [1280, 390])
def test_language_forms_and_retranscription(tmp_path, monkeypatch, width):
    audio = wav_bytes(tmp_path, seconds=1)

    class Recorder:
        def __init__(self, path):
            pass

        def devices(self):
            return {'devices': [{'id': 'test', 'name': 'Testmikrofon'}]}

        def status(self):
            return {'active': False}

        def start(self, *args):
            self.active = True
            return {'active': True}

        def speech_audio(self):
            import numpy as np
            return [np.zeros(32000, dtype=np.float32)]

        def stop(self):
            self.active = False
            path = tmp_path / 'capture.wav'
            path.write_bytes(audio)
            return path, 1, 'Testmikrofon'

    monkeypatch.setattr(app_mod, 'HardwareRecorder', Recorder)
    app = app_mod.create_app(Settings(data_dir=tmp_path, diarization=False),
                             transcriber=FakeTranscriber(), llm=NoLLM(), background=False)
    monkeypatch.setattr(app.state.speech_detector, 'has_speech', lambda audio: True)
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True,
                                     args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme='dark')
        errors, requests = [], []
        activity_timeout = False
        fail_retry = True
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            nonlocal fail_retry
            req = route.request
            path = urlsplit(req.url).path
            requests.append((path, req.post_data_buffer or b''))
            if path == '/api/capture/activity' and activity_timeout:
                route.fulfill(json={'speech': False, 'silence_seconds': 1})
                return
            if path.endswith('/retranscribe') and fail_retry:
                fail_retry = False
                route.fulfill(status=503, json={'detail': 'Test: Bitte erneut versuchen.'})
                return
            response = client.request(req.method, path, content=req.post_data_buffer,
                                      headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        def wait_for_transcript():
            app.state.pipeline.join()
            page.reload()
            # Nach dem Reload muss die Meeting-Ansicht stehen (sonst landet man mobil in der Liste).
            expect(page.locator('#meeting')).to_be_visible()
            page.get_by_role('tab', name='Transkript', exact=True).click()
            expect(page.locator('#t-retranscribe')).to_be_enabled()

        def record_click():
            # Auf dem Handy (390px) liegt „Aufnehmen" in der Listenansicht; ein geöffnetes
            # Meeting wird dafür erst über „Zurück" verlassen.
            if width == 390 and page.locator('#m-back').is_visible():
                page.locator('#m-back').click()
                expect(page.locator('#sidebar')).to_be_visible()
            page.locator('#btn-record').click()

        page.route('**/*', serve)
        page.goto('http://127.0.0.1/')
        page.get_by_role('button', name='Hochladen', exact=False).first.click()
        expect(page.locator('#up-language')).to_have_value('de')
        page.locator('#up-language').select_option('auto')
        page.locator('#up-file').set_input_files({'name': 'beispiel.wav', 'mimeType': 'audio/wav', 'buffer': audio})
        page.locator('#upload-form').get_by_role('button', name='Hochladen', exact=True).click()
        expect(page.locator('#dlg-upload')).not_to_be_visible()
        expect(page.locator('#meeting')).to_be_visible()
        wait_for_transcript()
        expect(page.locator('#t-language')).to_have_value('auto')
        page.locator('#t-language').select_option('de')
        page.get_by_role('button', name='Erneut transkribieren', exact=True).click()
        expect(page.locator('#t-language-error')).to_contain_text('Bitte erneut versuchen')
        page.get_by_role('button', name='Erneut transkribieren', exact=True).click()
        expect(page.locator('#m-title')).to_contain_text('(Deutsch)')
        wait_for_transcript()
        assert len(app.state.db.list_meetings()) == 2
        expect(page.locator('#t-language')).to_have_value('de')
        expect(page.locator('#m-meta')).to_contain_text('Transkriptionsmodus: Deutsch')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if os.environ.get('KOPROTOKOLL_UI_ARTIFACTS'):
            folder = Path(os.environ['KOPROTOKOLL_UI_ARTIFACTS'])
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f'sprachwahl-{width}.png'))

        for method, language in [('hardware', 'de'), ('browser', 'en')]:
            record_click()
            # Die beim Upload gewählte Sprache bleibt als Vorgabe erhalten.
            expect(page.locator('#rec-language')).to_have_value('auto' if method == 'hardware' else 'de')
            page.locator('#rec-language').select_option(language)
            page.locator('#rec-method').select_option(method)
            if method == 'hardware':
                page.locator('#rec-device').select_option('test')
            page.locator('#rec-start').click()
            expect(page.locator('#rec-stop')).to_be_visible()
            expect(page.locator('#rec-language')).to_be_disabled()
            expect(page.locator('#rec-time')).not_to_have_text('00:00')
            endpoint = '/api/capture/stop' if method == 'hardware' else '/api/meetings'
            with page.expect_response(lambda response: response.url.endswith(endpoint) and response.request.method == 'POST') as stopped:
                page.locator('#rec-stop').click()
            recorded = stopped.value.json()
            expect(page).to_have_url(re.compile(f"#meeting={recorded['id']}$"))
            expect(page.locator('#dlg-record')).not_to_be_visible()
            wait_for_transcript()
            expect(page.locator('#t-language')).to_have_value(language)

        assert any(path == '/api/capture/start' and b'"language":"de"' in body for path, body in requests)
        assert any(path == '/api/meetings' and b'name="language"\r\n\r\nen' in body for path, body in requests)

        # Der Browser stoppt und lädt die Aufnahme ohne einen manuellen Stopp hoch.
        activity_timeout = True
        record_click()
        page.locator('#rec-method').select_option('browser')
        with page.expect_response(lambda response: response.url.endswith('/api/meetings') and response.request.method == 'POST') as stopped:
            page.locator('#rec-start').click()
        recorded = stopped.value.json()
        expect(page).to_have_url(re.compile(f"#meeting={recorded['id']}$"))
        wait_for_transcript()
        expect(page.locator('#m-meta')).to_contain_text('Automatisch nach 5 Minuten ohne Sprache gestoppt')

        # Hardware-Stopp erreicht die Oberfläche über Statusabfragen, ohne Stopp-Klick.
        app.state.capture_clock = lambda: 0
        monkeypatch.setattr(app.state.speech_detector, 'has_speech', lambda audio: False)
        record_click()
        page.locator('#rec-method').select_option('hardware')
        page.locator('#rec-device').select_option('test')
        page.locator('#rec-start').click()
        expect(page.locator('#rec-stop')).to_be_visible()
        app.state.capture_clock = lambda: 300
        app.state.check_capture_silence(app.state.capture_request)
        expect(page.locator('#dlg-record')).not_to_be_visible()
        wait_for_transcript()
        expect(page.locator('#m-meta')).to_contain_text('Automatisch nach 5 Minuten ohne Sprache gestoppt')
        assert errors == []
        browser.close()
