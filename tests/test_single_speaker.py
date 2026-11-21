"""Eine Person vorgeben und irrtümlich getrennte Stimmen verlustfrei zusammenführen."""

import pytest
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from test_koprotokoll import FakeTranscriber, FakeDiarizer, NoLLM, wav_bytes


def test_single_speaker_does_not_run_automatic_clustering(tmp_path):
    class UnexpectedDiarizer:
        def diarize(self, *args):
            raise AssertionError('Bei einem vorgegebenen Sprecher darf keine automatische Aufteilung laufen')

    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(),
                     diarizer=UnexpectedDiarizer(), llm=NoLLM(), background=False)
    with TestClient(app) as client:
        m = client.post('/api/meetings', files={'file': ('test.wav', wav_bytes(tmp_path), 'audio/wav')},
                        data={'num_speakers': '1'}).json()
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{m['id']}").json()
        assert result['status'] == 'done'
        assert result['speakers'] == {'S1': 'Sprecher 1'}
        assert {s['speaker'] for s in result['segments']} == {'S1'}
        assert result['meta']['diarization'] == {
            'status': 'fixed', 'speaker_count': 1, 'requested_speakers': 1, 'source': 'setting'}


def test_single_speaker_correction_preserves_text_times_search_and_can_be_undone(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(),
                     diarizer=FakeDiarizer(), llm=NoLLM(), background=False)
    with TestClient(app) as client:
        m = client.post('/api/meetings', files={'file': ('test.wav', wav_bytes(tmp_path), 'audio/wav')}).json()
        mid = m['id']
        app.state.pipeline.join()
        client.patch(f'/api/meetings/{mid}', json={'speakers': {'S1': 'Alex'}})
        client.post(f'/api/meetings/{mid}/bookmarks', json={'start': 1, 'end': 2, 'kind': 'comment', 'note': 'Prüfen'})
        segments = app.state.db.get_segments(mid)
        app.state.db.save_correction(mid, [s['text'] + ' korrigiert' for s in segments],
                                     {'status': 'completed', 'rejected': []})
        before = client.get(f'/api/meetings/{mid}').json()
        assert len(before['speakers']) == 2
        assert client.post(f'/api/meetings/{mid}/single-speaker').status_code == 200
        after = client.get(f'/api/meetings/{mid}').json()
        assert after['speakers'] == {'S1': 'Alex'}
        assert after['num_speakers'] == 1
        assert {s['speaker'] for s in after['segments']} == {'S1'}
        assert after['meta']['diarization']['source'] == 'manual'
        assert [{k: v for k, v in s.items() if k != 'speaker'} for s in after['segments']] == [
            {k: v for k, v in s.items() if k != 'speaker'} for s in before['segments']]
        assert after['bookmarks'] == before['bookmarks']
        assert after['summary'] == before['summary']
        assert after['meta']['summary_speakers_changed'] is True
        assert client.get('/api/search', params={'q': 'korrigiert', 'meeting_id': mid}).json()
        # Auch nach erneutem Verarbeiten oder doppeltem Klick bleibt die Sicherung erhalten.
        app.state.pipeline.process(mid)
        assert client.get(f'/api/meetings/{mid}').json()['meta']['summary_speakers_changed'] is False
        assert client.post(f'/api/meetings/{mid}/single-speaker').status_code == 200
        assert client.delete(f'/api/meetings/{mid}/single-speaker').status_code == 200
        restored = client.get(f'/api/meetings/{mid}').json()
        assert restored['segments'] == before['segments']
        assert restored['speakers'] == before['speakers']
        assert restored['num_speakers'] == before['num_speakers']
        assert restored['meta']['diarization'] == before['meta']['diarization']
        assert 'speaker_correction' not in restored['meta']


@pytest.mark.parametrize('status', ['queued', 'transcribing'])
def test_speaker_correction_refuses_running_pipeline(tmp_path, status):
    app = create_app(Settings(data_dir=tmp_path, diarization=False), transcriber=FakeTranscriber(),
                     llm=NoLLM(), background=False)
    with TestClient(app) as client:
        mid = app.state.db.create_meeting('Test', 'test')
        app.state.db.update_meeting(mid, status=status)
        assert client.post(f'/api/meetings/{mid}/single-speaker').status_code == 409


@pytest.mark.parametrize('width', [1280, 390])
def test_merge_and_restore_speakers_in_browser(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright
    from test_integrations import _chrome_path

    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(),
                     diarizer=FakeDiarizer(), llm=NoLLM(), background=False)
    with TestClient(app) as client, sync_playwright() as pw:
        m = client.post('/api/meetings', files={'file': ('test.wav', wav_bytes(tmp_path), 'audio/wav')}).json()
        mid = m['id']
        app.state.pipeline.join()
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
        page.get_by_role('button', name='Alle als eine Person zusammenführen', exact=True).click()
        restore = page.get_by_role('button', name='Frühere Sprecheraufteilung wiederherstellen', exact=True)
        expect(restore).to_be_visible()
        expect(page.locator('#m-meta')).to_contain_text('auf eine Person korrigiert')
        assert len(client.get(f'/api/meetings/{mid}').json()['speakers']) == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if os.environ.get('KOPROTOKOLL_UI_ARTIFACTS'):
            folder = Path(os.environ['KOPROTOKOLL_UI_ARTIFACTS'])
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f'ein-sprecher-{width}.png'))
        restore.click()
        expect(page.get_by_role('button', name='Alle als eine Person zusammenführen', exact=True)).to_be_visible()
        assert len(client.get(f'/api/meetings/{mid}').json()['speakers']) == 2
        assert errors == []
        browser.close()


@pytest.mark.parametrize('width', [1280, 390])
def test_speaker_selection_is_clear_persistent_and_sent_on_upload(tmp_path, width):
    """Die Vorgabe übersteht Neuladen und gelangt bis zur Sprechererkennung."""
    from playwright.sync_api import expect, sync_playwright
    from test_integrations import _chrome_path

    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(),
                     diarizer=FakeDiarizer(), llm=NoLLM(), background=False)
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            req = route.request
            response = client.request(req.method, urlsplit(req.url).path, content=req.post_data_buffer,
                                      headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('**/*', serve)
        page.goto('http://127.0.0.1/')
        page.get_by_role('button', name='⇪ Hochladen').click()
        select = page.locator('select#up-speakers')
        expect(select).to_have_value('1')
        select.select_option('2')
        page.reload()
        page.get_by_role('button', name='● Aufnehmen', exact=True).click()
        expect(page.locator('#rec-speakers')).to_have_value('2')
        page.locator('#rec-speakers').select_option('')
        page.locator('#rec-cancel').click()
        page.get_by_role('button', name='⇪ Hochladen').click()
        expect(select).to_have_value('')
        select.select_option('1')
        page.locator('#up-file').set_input_files({'name': 'test.wav', 'mimeType': 'audio/wav',
                                                'buffer': wav_bytes(tmp_path)})
        page.locator('#upload-form button[value="ok"]').click()
        expect(page.locator('#m-title')).to_have_text('test')
        app.state.pipeline.join()
        mid = app.state.db.list_meetings()[0]['id']
        result = client.get(f'/api/meetings/{mid}').json()
        assert result['num_speakers'] == 1
        assert set(result['speakers']) == {'S1'}
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        browser.close()
