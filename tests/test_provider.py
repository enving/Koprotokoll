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


@pytest.mark.parametrize("failure", [False, True])
def test_running_summary_keeps_provider_when_settings_change(tmp_path, server, monkeypatch, failure):
    from koprotokoll.summarize import LLM, summarize
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    settings.llm_external_confirmed = True
    monkeypatch.setattr("koprotokoll.summarize.external_llm", lambda target: target == url)
    state["fail"] = failure
    state["on_request"] = lambda: settings.save_provider(dict(
        url="http://127.0.0.1:1/v1", model="anderes-modell", confirmed_url=None))
    segments = [dict(start=0, speaker="A", text="Erfundener Prüftext.")]
    llm = LLM(settings)
    result = summarize(segments, {}, llm)
    assert state["requests"][0]["model"] == "testmodell"
    assert result["external_service"] == url
    assert result["engine"] == ("heuristik" if failure else "llm:testmodell")
    assert settings.llm_model == "anderes-modell"
    following = summarize(segments, {}, llm)
    assert following["engine"] == "heuristik"
    assert "external_service" not in following
    assert len(state["requests"]) == 1


def test_running_question_keeps_model_when_settings_change(tmp_path, server):
    from koprotokoll.summarize import LLM, ask
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    state["on_request"] = lambda: settings.save_provider(dict(
        url="http://127.0.0.1:1/v1", model="anderes-modell", confirmed_url=None))
    result = ask("Was wurde besprochen?", [dict(start=0, speaker="A", text="Erfundener Prüftext.")],
                 {}, LLM(settings), [])
    assert result["engine"] == "llm:testmodell"
    assert settings.llm_model == "anderes-modell"


@pytest.mark.parametrize("change", ["url", "model"])
def test_provider_change_clears_old_parameters_before_next_summary(tmp_path, server, change):
    import json
    from koprotokoll.summarize import LLM, summarize
    url, state = server
    previous_model = "altes-modell" if change == "model" else "testmodell"
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model=previous_model)
    settings.save_provider(dict(url=url, model=previous_model))
    settings.save_llm(previous_model, 200, "high")
    following_url = url + "/anderer-anbieter" if change == "url" else url
    with TestClient(create_app(settings)) as client:
        response = client.put("/api/llm/provider", json=dict(
            provider="other", url=following_url, model="testmodell", timeout=2))
        assert response.status_code == 200
    reloaded = Settings(data_dir=tmp_path)
    for config in (settings, reloaded):
        assert config.llm_max_tokens is None
        assert config.llm_reasoning_effort is None
    saved = json.loads((tmp_path / "llm-settings.json").read_text())
    assert saved.get("max_tokens") is None and saved.get("reasoning_effort") is None
    state["reject_optional"] = True
    result = summarize([dict(start=0, speaker="A", text="Erfundener Prüftext.")], {}, LLM(reloaded))
    assert result["engine"] == "llm:testmodell"
    assert "max_tokens" not in state["requests"][-1]
    assert "reasoning_effort" not in state["requests"][-1]


def test_provider_same_connection_keeps_confirmed_parameters(tmp_path, server):
    url, _ = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    settings.save_provider(dict(url=url, model="testmodell"))
    settings.save_llm("testmodell", 200, "high")
    settings.save_provider(dict(url=url, model="testmodell", timeout=5))
    for config in (settings, Settings(data_dir=tmp_path)):
        assert (config.llm_max_tokens, config.llm_reasoning_effort) == (200, "high")
    settings.save_llm("neues-modell", 300, "low")
    for config in (settings, Settings(data_dir=tmp_path)):
        assert (config.llm_max_tokens, config.llm_reasoning_effort) == (300, "low")


def test_changed_url_from_environment_does_not_inherit_saved_parameters(tmp_path, server, monkeypatch):
    url, _ = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell")
    settings.save_provider(dict(url=url, model="testmodell"))
    settings.save_llm("testmodell", 200, "high")
    monkeypatch.setenv("KOPROTOKOLL_LLM_URL", url + "/anderer-anbieter")
    reloaded = Settings(data_dir=tmp_path)
    assert reloaded.llm_max_tokens is None
    assert reloaded.llm_reasoning_effort is None


def test_legacy_parameters_without_provider_are_not_rebound(tmp_path, server):
    import json
    url, _ = server
    (tmp_path / "llm-settings.json").write_text(json.dumps(dict(
        model="testmodell", max_tokens=200, reasoning_effort="high")))
    settings = Settings(data_dir=tmp_path, llm_base_url=url)
    assert settings.llm_max_tokens is None and settings.llm_reasoning_effort is None
    settings.save_provider(dict(url=url, model="testmodell"))
    reloaded = Settings(data_dir=tmp_path)
    assert reloaded.llm_max_tokens is None and reloaded.llm_reasoning_effort is None
