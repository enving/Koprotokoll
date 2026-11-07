"""Anbieter-Vertrag ohne Cloudzugriff; Schlüssel entstehen zur Laufzeit."""
import secrets
import pytest
from fastapi.testclient import TestClient
from koprotokoll.app import create_app
from koprotokoll.config import Settings, external_llm
from test_local_model import server


def test_provider_persistence_secrets_and_consent(tmp_path, server, monkeypatch):
    url, state = server
    secret = secrets.token_hex(24)
    settings = Settings(data_dir=tmp_path, llm_base_url=url)
    with TestClient(create_app(settings)) as c:
        body = dict(provider="other", url=url, model="testmodell", timeout=2, api_key=secret)
        assert c.put('/api/llm/provider', json=body).status_code == 200
        assert secret not in c.get('/api/llm').text
        assert c.post('/api/llm/test').json()['inference'] == 'erfolgreich'
        saved = Settings(data_dir=tmp_path)
        assert (saved.llm_base_url, saved.llm_api_key, saved.llm_model, saved.llm_timeout) == (url, secret, 'testmodell', 2)
        assert c.put('/api/llm/provider', json={**body, 'timeout': secret}).status_code == 422
        assert secret not in c.put('/api/llm/provider', json={**body, 'timeout': secret}).text
        assert c.put('/api/llm/provider', json={**body, 'url': 'https://example.com/v1'}).status_code == 400
        assert c.put('/api/llm/provider', json={**body, 'url': 'https://example.com/v1', 'external_confirmed': True}).status_code == 200
        assert Settings(data_dir=tmp_path).llm_external_confirmed
        assert c.put('/api/llm/provider', json={**body, 'api_key': ''}).status_code == 200
        assert not c.get('/api/llm').json()['api_key_set']
        for field, attr, env in Settings.llm_fields():
            value = '5' if field == 'timeout' else 'https://example.org/v1' if field == 'url' else secrets.token_hex(8)
            monkeypatch.setenv(env, value)
            assert str(getattr(Settings(data_dir=tmp_path), attr)) == ('5.0' if field == 'timeout' else value)
        assert not Settings(data_dir=tmp_path).llm_external_confirmed


@pytest.mark.parametrize('url,external', [('http://localhost:11434/v1', False), ('http://192.168.1.2/v1', False), ('http://[::1]/v1', False), ('https://example.com/v1', True), ('http://8.8.8.8/v1', True)])
def test_address_classification(url, external):
    assert external_llm(url) == external


def test_authorization_and_http_errors(tmp_path, server):
    from koprotokoll.summarize import LLM
    url, state = server
    secret = secrets.token_hex(24)
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_api_key=secret, llm_model="testmodell"))
    assert llm.available()
    assert state['authorization'] == 'Bearer ' + secret
    for code in (401, 404):
        state['status'] = code
        result = llm.status()
        assert not result['available']
        assert secret not in str(result)


def test_external_result_label(tmp_path, server, monkeypatch):
    from koprotokoll.summarize import LLM, summarize
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model='testmodell')
    monkeypatch.setattr('koprotokoll.summarize.external_llm', lambda _: True)
    llm = LLM(settings)
    assert not llm.available()
    assert state['requests'] == []
    settings.llm_external_confirmed = True
    result = summarize([dict(start=0, speaker='A', text='Erfundener Termin.')], {}, llm)
    assert result['external_service'] == url


def test_redirect_does_not_forward_credentials(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from koprotokoll.summarize import LLM
    visited = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            visited.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/unbestaetigtes-ziel')
            self.end_headers()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    try:
        llm = LLM(Settings(data_dir=tmp_path, llm_base_url=f'http://127.0.0.1:{httpd.server_port}/v1', llm_api_key=secrets.token_hex(24)))
        assert not llm.available()
        assert visited == ['/v1/models']
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_external_fallback_keeps_service_label(tmp_path, server, monkeypatch, failure):
    from koprotokoll.summarize import LLM, summarize
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    settings.llm_external_confirmed = True
    monkeypatch.setattr("koprotokoll.summarize.external_llm", lambda _: True)
    if failure == "http":
        state["fail"] = True
    else:
        state["delay"] = 0.1
        settings.llm_timeout = 0.01
    result = summarize([dict(start=0, speaker="A", text="Erfundener Termin.")], {}, LLM(settings))
    assert result["engine"] == "heuristik"
    assert result["external_service"] == url
    assert "fehlgeschlagen" in result["warning"]


def test_unavailable_external_server_has_no_transfer_label(tmp_path, server, monkeypatch):
    from koprotokoll.summarize import LLM, summarize
    url, state = server
    state["models"] = []
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    settings.llm_external_confirmed = True
    monkeypatch.setattr("koprotokoll.summarize.external_llm", lambda _: True)
    result = summarize([dict(start=0, speaker="A", text="Erfundener Termin.")], {}, LLM(settings))
    assert "external_service" not in result
    assert state["requests"] == []
