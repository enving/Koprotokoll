"""Oberflaeche: Heuristik-Hinweis am Meeting und Einrichten-Dialog, Desktop- und Handybreite (#57)."""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.summarize import LLM, summarize
from test_integrations import _chrome_path
from test_koprotokoll import FakeDiarizer, FakeTranscriber
from test_llm_pull import PULL_LINES, ollama  # noqa: F401 – Fixture


@pytest.mark.parametrize('width', [1280, 390])
def test_meeting_hint_and_setup_dialog(tmp_path, ollama, width):  # noqa: F811
    url, state = ollama
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model='qwen2.5:7b-instruct')
    app = create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(),
                     llm=LLM(settings), background=False)
    db = app.state.db
    mid = db.create_meeting('Testmeeting', 'test')
    db.replace_segments(mid, [{'start': 0, 'end': 5, 'speaker': 'S1',
                               'text': 'Wir besprechen den Test und die Liste.', 'word_times': []}])
    db.update_meeting(mid, status='done', speakers={'S1': 'Alex'},
                      summary=summarize(db.get_segments(mid), {'S1': 'Alex'}, LLM(settings)))
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def serve(route):
            req = route.request
            response = client.request(req.method, urlsplit(req.url).path, content=req.post_data_buffer,
                                      headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('**/*', serve)
        page.goto(f'http://127.0.0.1/#meeting={mid}')
        # Heuristik-Ergebnis ist als solches markiert; die Statuszeile nennt den konkreten Grund.
        expect(page.locator('#tab-summary')).to_contain_text('ohne Sprachmodell')
        expect(page.locator('#tab-summary')).to_contain_text('ca. 5 GB')
        expect(page.locator('#m-meta')).to_contain_text('fehlt auf dem Server')
        page.get_by_role('button', name='Sprachmodell einrichten', exact=True).click()
        dialog = page.locator('#dlg-llm-setup')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('Modell „qwen2.5:7b-instruct“ fehlt')
        state['pull_lines'] = PULL_LINES
        page.get_by_role('button', name='Modell laden', exact=True).click()
        expect(dialog).to_contain_text('Modell geladen', timeout=15000)
        page.get_by_role('button', name='Schließen', exact=True).click()
        # Einstellungen: Erklaerung plus Knopf, danach ist das Modell vorhanden.
        page.goto('http://127.0.0.1/#integrations')
        expect(page.locator('#local-model')).to_contain_text('Ohne Sprachmodell')
        page.get_by_role('button', name='Sprachmodell einrichten', exact=True).click()
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('ist vorhanden')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        browser.close()


def test_setup_dialog_offers_cli_import(tmp_path, ollama, monkeypatch):
    """Der Einrichten-Dialog bietet die Coding-CLI-Übernahme an – der Schlüssel taucht nie im UI auf."""
    import koprotokoll.cli_llm as cli_llm
    url, _state = ollama
    cfg = tmp_path / "vibe.toml"
    cfg.write_text('active_model = "mistral-large-4-0"\n\n[[models]]\nname = "zai-glm-5-3"\nprovider = "mistral"\n',
                   encoding="utf-8")
    monkeypatch.setenv("KOPROTOKOLL_VIBE_CONFIG", str(cfg))
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-ui-test-789")
    monkeypatch.setattr(cli_llm, "_keychain_key", lambda: None)
    # Import-Ziel auf den lokalen Fake-Server legen, damit der Test keine echte externe Adresse ruft.
    monkeypatch.setattr(cli_llm, "MISTRAL_API_URL", url)
    settings = Settings(data_dir=tmp_path / "data", llm_base_url=url, llm_model='qwen2.5:7b-instruct')
    app = create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(),
                     llm=LLM(settings), background=False)
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def serve(route):
            req = route.request
            response = client.request(req.method, urlsplit(req.url).path, content=req.post_data_buffer,
                                      headers={'content-type': req.headers.get('content-type', '')})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('**/*', serve)
        page.goto('http://127.0.0.1/#integrations')
        page.get_by_role('button', name='Sprachmodell einrichten', exact=True).click()
        dialog = page.locator('#dlg-llm-setup')
        expect(dialog).to_contain_text('Eigenen Endpunkt oder Coding CLI')
        expect(dialog).to_contain_text('Mistral Vibe CLI')
        expect(dialog).to_contain_text('Schlüssel gefunden')
        dialog.locator('input[type=checkbox]').first.check()
        page.get_by_role('button', name='Konfiguration übernehmen', exact=True).click()
        expect(dialog).to_contain_text('Übernommen', timeout=10000)
        assert 'sk-ui-test-789' not in page.content()
        page.get_by_role('button', name='Schließen', exact=True).click()
        expect(page.get_by_label('Anbieter', exact=True)).to_contain_text('Mistral API')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        browser.close()
