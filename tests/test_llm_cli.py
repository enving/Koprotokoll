"""Sprachmodell aus der Coding CLI (Vibe) übernehmen – Schlüssel nie in der API, externe Übertragung nur bestätigt."""
import json

import pytest
from fastapi.testclient import TestClient

import koprotokoll.cli_llm as cli_llm
from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.summarize import LLM
from test_koprotokoll import FakeDiarizer, FakeTranscriber
from test_llm_pull import ollama  # noqa: F401 – Fixture

VIBE_CONFIG = """
active_model = "mistral-large-4-0"

[[models]]
name = "zai-glm-5-3"
provider = "mistral"
alias = "glm-5-3"
"""


@pytest.fixture
def vibe_config(tmp_path, monkeypatch):
    path = tmp_path / "vibe-config.toml"
    path.write_text(VIBE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("KOPROTOKOLL_VIBE_CONFIG", str(path))
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(cli_llm, "_keychain_key", lambda: None)
    return path


@pytest.fixture
def app(tmp_path, vibe_config):
    settings = Settings(data_dir=tmp_path / "data", llm_base_url="http://localhost:11434/v1")
    return create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(),
                      llm=LLM(settings), background=False), settings


def test_cli_sources_returns_no_key_material(app, vibe_config):
    app, _settings = app
    with TestClient(app) as c:
        r = c.get("/api/llm/cli-sources")
        assert r.status_code == 200
        sources = r.json()["sources"]
        assert len(sources) == 1
        src = sources[0]
        assert src["source"] == "vibe" and src["label"] == "Mistral Vibe CLI"
        assert src["model"] == "mistral-large-4-0"
        assert src["url"] == "https://api.mistral.ai/v1"
        assert src["models"] == ["zai-glm-5-3"]
        assert src["key_available"] is False
        assert set(src) == {"source", "label", "config", "provider", "url", "model",
                            "models", "key_available", "key_source"}
        assert "api_key" not in json.dumps(r.json())


def test_cli_sources_missing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KOPROTOKOLL_VIBE_CONFIG", str(tmp_path / "fehlt.toml"))
    assert cli_llm.cli_sources() == []


def test_cli_import_with_env_key_stores_locally_and_hides_key(app, monkeypatch, vibe_config, ollama):  # noqa: F811
    app, settings = app
    url, _state = ollama
    # Import-Ziel auf den Fake-Server umbiegen, damit keine echte externe Adresse aufgerufen wird.
    monkeypatch.setattr(cli_llm, "MISTRAL_API_URL", url)
    monkeypatch.setattr("koprotokoll.config.external_llm", lambda _: True)
    monkeypatch.setattr("koprotokoll.summarize.external_llm", lambda _: True)
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-test-schluessel-123")
    with TestClient(app) as c:
        r = c.post("/api/llm/cli-import", json={"source": "vibe", "external_confirmed": True})
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "mistral" and body["url"] == url
        assert body["model"] == "mistral-large-4-0" and body["api_key_set"] is True
        assert body["external_confirmed"] is True
        assert "sk-test-schluessel-123" not in json.dumps(body)
        saved_path = settings.data_dir / "llm-settings.json"
        saved = json.loads(saved_path.read_text())
        assert saved["api_key"] == "sk-test-schluessel-123"
        assert saved["provider"] == "mistral" and saved["confirmed_url"] == url
        assert saved_path.stat().st_mode & 0o777 == 0o600
        # Nach der Übernahme: Status prüft den (Fake-)Server, Pull bleibt verweigert.
        setup = c.get("/api/llm/setup").json()
        assert setup["provider"] == "mistral" and setup["pull_supported"] is False
        assert c.post("/api/llm/pull", json={"model": "m"}).status_code == 400


def test_cli_import_requires_external_confirmation(app, monkeypatch, vibe_config):
    app, _settings = app
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-test-schluessel-123")
    with TestClient(app) as c:
        r = c.post("/api/llm/cli-import", json={"source": "vibe", "external_confirmed": False})
        assert r.status_code == 400
        assert "bestätigen" in r.json()["detail"]


def test_cli_import_with_pasted_key_and_unknown_source(app, vibe_config):
    app, settings = app
    with TestClient(app) as c:
        assert c.post("/api/llm/cli-import", json={"source": "unbekannt"}).status_code == 400
        # Ohne gefundenen Schlüssel und ohne eingegebenen: 400 mit Hinweis.
        r = c.post("/api/llm/cli-import", json={"source": "vibe", "external_confirmed": True})
        assert r.status_code == 400 and "kein API-Schlüssel" in r.json()["detail"]
        # Manuell eingetragener Schlüssel funktioniert und steht nicht in der Antwort.
        r = c.post("/api/llm/cli-import", json={"source": "vibe", "external_confirmed": True,
                                                "api_key": "sk-eingetragen-456"})
        assert r.status_code == 200 and r.json()["api_key_set"] is True
        assert "sk-eingetragen-456" not in json.dumps(r.json())
        assert json.loads((settings.data_dir / "llm-settings.json").read_text())["api_key"] == "sk-eingetragen-456"
        assert c.post("/api/llm/cli-import", json={"source": "vibe", "external_confirmed": True,
                                                   "api_key": "bad\nkey"}).status_code == 400


def test_provider_preset_mistral_accepted(app, vibe_config):
    app, _settings = app
    with TestClient(app) as c:
        r = c.put("/api/llm/provider", json={"provider": "mistral", "url": "https://api.mistral.ai/v1",
                                             "model": "mistral-large-4-0", "timeout": 60,
                                             "api_key": "sk-abc", "external_confirmed": True})
        assert r.status_code == 200
        assert c.get("/api/llm").json()["provider"] == "mistral"


def test_chat_handles_content_list_like_mistral(tmp_path, ollama):  # noqa: F811
    """Mistral liefert message.content als Liste mit Thinking- und Text-Teil."""
    from koprotokoll.summarize import content_text, summarize
    url, state = ollama
    state["list_content"] = True
    state["models"] = ["m"]
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_model="m"))
    assert llm.chat("s", "u") == '{"overview":"Erfunden."}'
    assert content_text("plain") == "plain"
    assert content_text([{"type": "text", "text": "a"}, {"type": "thinking", "thinking": []}, {"type": "text", "text": "b"}]) == "ab"
    result = summarize([dict(start=0, speaker="A", text="Wir testen kurz.")], {}, llm)
    assert result["engine"] == "llm:m" and result["overview"] == "Erfunden."
