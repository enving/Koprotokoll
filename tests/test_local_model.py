"""Lokaler Modellserver mit ausschließlich erfundenen Antworten."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.summarize import LLM, summarize


@pytest.fixture
def server():
    state = {"models": ["testmodell"], "requests": [], "fail": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["authorization"] = self.headers.get("Authorization")
            self.send_response(state.get("status", 200))
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": m} for m in state["models"]]}).encode())

        def do_POST(self):
            if state.get("delay"):
                time.sleep(state["delay"])
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if state.get("on_request"):
                state["on_request"]()
            unsupported = state.get("reject_optional") and any(
                key in state["requests"][-1] for key in ("max_tokens", "reasoning_effort"))
            self.send_response(400 if unsupported else 500 if state["fail"] else 200)
            self.end_headers()
            try:
                self.wfile.write(json.dumps({"choices": [{"message": {"content": '{"overview":"Erfundener Testdialog."}'}}]}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1", state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_model_states_and_fallback(tmp_path, server):
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model="fehlt")
    llm = LLM(settings)
    assert llm.status()["server_reachable"]
    assert not llm.available()
    state["models"] = []
    assert llm.status()["models"] == []
    settings.llm_model = "testmodell"
    state["models"] = ["testmodell"]
    assert llm.available()
    segments = [dict(start=0, speaker="A", text="Ich prüfe die erfundene Liste morgen.")]
    assert summarize(segments, {}, llm)["engine"] == "llm:testmodell"
    assert "max_tokens" not in state["requests"][-1]
    assert "reasoning_effort" not in state["requests"][-1]
    settings.llm_max_tokens = 256
    settings.llm_reasoning_effort = "none"
    llm.chat("Test", "Test")
    assert state["requests"][-1]["max_tokens"] == 256
    assert state["requests"][-1]["reasoning_effort"] == "none"
    state["fail"] = True
    result = summarize(segments, {}, llm)
    assert result["engine"] == "heuristik"
    assert "fehlgeschlagen" in result["warning"]
    settings.llm_base_url = "http://127.0.0.1:1/v1"
    assert not llm.status()["server_reachable"]


def test_selection_persistence_env_and_validation(tmp_path, server, monkeypatch):
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url)
    with TestClient(create_app(settings)) as client:
        assert not client.get("/api/llm").json()["model_present"]
        assert client.put("/api/llm", json={"model": "fehlt"}).status_code == 400
        assert client.put("/api/llm", json={"model": "testmodell", "max_tokens": 200}).status_code == 400
        response = client.put("/api/llm", json={"model": "testmodell", "max_tokens": 200, "compatible_parameters": True})
        assert response.status_code == 200
        assert response.json()["model_present"]
        assert Settings(data_dir=tmp_path).llm_model == "testmodell"
        monkeypatch.setenv("KOPROTOKOLL_LLM_MODEL", "umgebung")
        overridden = Settings(data_dir=tmp_path)
        assert overridden.llm_model == "umgebung"
        assert overridden.llm_max_tokens is None
        assert client.get("/api/llm").json()["model_env_override"]


def test_timeout_is_visible(tmp_path, server):
    url, state = server
    state["delay"] = 0.1
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_model="testmodell", llm_timeout=0.01))
    result = summarize([dict(start=0, speaker="A", text="Wir prüfen die Testliste.")], {}, llm)
    assert result["engine"] == "heuristik"
    assert "Timeout" in result["warning"]


def test_hardware_stop_uses_selected_model(tmp_path, monkeypatch, server):
    from test_koprotokoll import FakeTranscriber, FakeDiarizer, wait_done
    import numpy as np
    url, state = server
    import soundfile as sf
    import koprotokoll.app as app_mod

    original = tmp_path / "hardware.wav"
    sf.write(original, np.zeros(48_000, dtype=np.float32), 48_000)

    class FakeRecorder:
        def __init__(self, _capture_dir):
            self.active = False

        def devices(self):
            return {"backend": "test", "devices": [{"id": "0", "name": "Testmikrofon"}], "details": ""}

        def status(self):
            return {"active": self.active}

        def start(self, device, channels, sample_rate):
            assert (device, channels, sample_rate) == ("0", 2, 48_000)
            self.active = True
            return {"active": True, "device": device, "started_at": 1.0}

        def stop(self):
            self.active = False
            return original, 1.0, "Testmikrofon"

    monkeypatch.setattr(app_mod, "HardwareRecorder", FakeRecorder)
    settings = Settings(data_dir=tmp_path / "data", llm_base_url=url)
    settings.save_llm("testmodell", None, None)
    app = app_mod.create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=LLM(settings))
    with TestClient(app) as c:
        devices = c.get("/api/capture/devices").json()
        assert devices["devices"] == [{"id": "0", "name": "Testmikrofon"}]
        started = c.post("/api/capture/start", json={"device": "0", "title": "Hardwaretest", "num_speakers": 2})
        assert started.status_code == 200 and started.json()["active"] is True
        # Bis zum Stopp gibt es kein neues Meeting und damit keine Inferenz.
        assert c.get("/api/meetings").json() == []
        assert state["requests"] == []
        meeting = c.post("/api/capture/stop").json()
        result = wait_done(c, meeting["id"])

    assert result["status"] == "done"
    assert result["meta"]["source"] == "hardware"
    assert result["meta"]["device"] == "Testmikrofon"
    assert (settings.media_dir / f"{meeting['id']}.original.wav").exists()
    assert (settings.media_dir / f"{meeting['id']}.normalized.wav").exists()

    assert result["summary"]["engine"] == "llm:testmodell"
    assert state["requests"][-1]["model"] == "testmodell"
