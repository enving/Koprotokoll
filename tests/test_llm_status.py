"""Konkrete Gründe statt Sammelmeldung: LLM-Status und Heuristik-Warnung (#57)."""
from koprotokoll.config import Settings
from koprotokoll.summarize import LLM, summarize
from test_local_model import server

SEGMENTS = [dict(start=0, speaker="A", text="Wir besprechen den Test und die Liste.")]


def test_status_reason_model_missing_names_model_and_url(tmp_path, server):
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="fehlt")
    st = LLM(settings).status()
    assert st["server_reachable"] and not st["available"]
    assert st["reason"] == "model_missing"
    assert "fehlt" in st["message"] and url in st["message"]
    state["models"] = ["fehlt"]
    st = LLM(settings).status()
    assert st["reason"] == "ok" and st["available"]


def test_status_reason_server_unreachable_names_url(tmp_path):
    settings = Settings(data_dir=tmp_path, llm_base_url="http://127.0.0.1:1/v1", llm_model="m")
    st = LLM(settings).status()
    assert st["reason"] == "server_unreachable"
    assert "nicht erreichbar" in st["message"] and "http://127.0.0.1:1/v1" in st["message"]


def test_status_reason_disabled(tmp_path):
    settings = Settings(data_dir=tmp_path, llm_base_url="http://127.0.0.1:1/v1", llm_enabled=False)
    st = LLM(settings).status()
    assert st["reason"] == "disabled" and "ausgeschaltet" in st["message"]
    assert not st["enabled"]


def test_summarize_warning_names_concrete_reason(tmp_path, server):
    url, _state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="fehlt")
    result = summarize(SEGMENTS, {}, LLM(settings))
    assert result["engine"] == "heuristik"
    assert "Modell „fehlt“ fehlt auf dem Server" in result["warning"]
    assert "Heuristik" in result["warning"]
    # Die alte Sammelmeldung darf nicht mehr auftauchen.
    assert "deaktiviert, Modell fehlt oder Server nicht erreichbar" not in result["warning"]

    settings.llm_base_url = "http://127.0.0.1:1/v1"
    result = summarize(SEGMENTS, {}, LLM(settings))
    assert "nicht erreichbar unter http://127.0.0.1:1/v1" in result["warning"]

    settings.llm_base_url = url
    settings.llm_enabled = False
    result = summarize(SEGMENTS, {}, LLM(settings))
    assert "Sprachmodell ausgeschaltet" in result["warning"]


def test_summarize_without_llm_object_still_works():
    result = summarize(SEGMENTS, {}, None)
    assert result["engine"] == "heuristik"
    assert "kein Sprachmodell konfiguriert" in result["warning"]
