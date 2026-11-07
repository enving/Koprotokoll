"""Nachbesserung mit erfundenen Daten und lokalem Fake-Server."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.correction import correct
from koprotokoll.db import Database
from koprotokoll.summarize import LLM
from test_koprotokoll import FakeTranscriber, FakeDiarizer, NoLLM

SOURCE = [{"start": 1, "end": 4, "speaker": "S1", "text": "Wir kaufen Getrenke für das Fest.", "word_times": [{"start": 1, "end": 2, "text": "Wir"}]}]


@pytest.fixture
def server():
    state = {"answer": '{"text":"Wir kaufen Getränke für das Fest."}', "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            time.sleep(state.get("delay", 0))
            self.send_response(state.get("code", 200))
            self.end_headers()
            try:
                self.wfile.write(json.dumps({"choices": [{"message": {"content": state["answer"]}}]}).encode())
            except BrokenPipeError:
                pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever)
    worker.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1", state
    httpd.shutdown()
    worker.join()
    httpd.server_close()


def test_correction_and_selection(tmp_path, server):
    url, state = server
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url))
    texts, report = correct(SOURCE, llm, "klein", ["Getränke"])
    assert texts == ["Wir kaufen Getränke für das Fest."]
    assert "Getränke" in state["requests"][0]["messages"][0]["content"]
    assert state["requests"][0]["model"] == "klein"
    db = Database(tmp_path / "test.db")
    mid = db.create_meeting("Fest", "Fest")
    db.replace_segments(mid, SOURCE)
    before = db.get_segments(mid, original=True)
    db.save_correction(mid, texts, report)
    assert db.search("Getränke") and not db.search("Getrenke")
    after = db.get_segments(mid)
    assert after[0]["word_times"] == before[0]["word_times"]
    assert [(s["start"], s["end"], s["speaker"]) for s in before] == [(s["start"], s["end"], s["speaker"]) for s in after]
    assert db.get_segments(mid, original=True)[0]["text"] == SOURCE[0]["text"]
    db.select_transcript(mid, "original")
    assert db.search("Getrenke") and not db.search("Getränke")


@pytest.mark.parametrize("answer", ['kein JSON', '{"text":"Ganz neue Geschichte mit erfundenen Personen und Zahlen."}', '{"text":"Wir kaufen Getränke für das Fest.","speaker":"S2"}', '[]', '{"text":null}', '{"text":""}'])
def test_reject(tmp_path, server, answer):
    url, state = server
    state["answer"] = answer
    texts, report = correct(SOURCE, LLM(Settings(data_dir=tmp_path, llm_base_url=url)), "klein", [])
    assert texts == [SOURCE[0]["text"]]
    assert report["rejected"] == [0]


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_failure(tmp_path, server, failure):
    url, state = server
    state.update({"delay": 0.1} if failure == "timeout" else {"code": 500})
    texts, report = correct(SOURCE, LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_timeout=.01)), "klein", [])
    assert texts == [SOURCE[0]["text"]] and report["status"] == "fallback"


def test_cloud_blocked_and_labelled(tmp_path):
    settings = Settings(data_dir=tmp_path, llm_base_url="https://example.invalid/v1")
    texts, report = correct(SOURCE, LLM(settings), "klein", [])
    assert texts == [SOURCE[0]["text"]]
    assert report["external"] and report["service"] == settings.llm_base_url


def test_api_settings_and_export(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=NoLLM())
    with TestClient(app) as client:
        assert client.get("/api/transcript-correction").json()["enabled"] is False
        assert client.put("/api/transcript-correction", json={"enabled": True}).status_code == 400
        assert client.put("/api/transcript-correction", json={"enabled": True, "model": "klein", "vocabulary": ["Getränke"]}).status_code == 200
        mid = app.state.db.create_meeting("Fest", "Fest")
        app.state.db.replace_segments(mid, SOURCE)
        app.state.db.save_correction(mid, ["Wir kaufen Getränke für das Fest."], {})
        assert "Getränke" in client.get(f"/api/meetings/{mid}/export").text
        assert client.put(f"/api/meetings/{mid}/transcript-version", json={"version": "original"}).status_code == 200
        assert "Getrenke" in client.get(f"/api/meetings/{mid}/export").text
        assert client.put(f"/api/meetings/{mid}/transcript-version", json={"version": "kaputt"}).status_code == 422
        assert client.put("/api/meetings/999/transcript-version", json={"version": "original"}).status_code == 404


def test_reprocessing_keeps_first_recognition(tmp_path, monkeypatch):
    from test_koprotokoll import wav_bytes, wait_done
    app = create_app(Settings(data_dir=tmp_path), transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=NoLLM())
    with TestClient(app) as client:
        mid = client.post("/api/meetings", files={"file": ("fest.wav", wav_bytes(tmp_path), "audio/wav")}).json()["id"]
        assert wait_done(client, mid)["status"] == "done"
        before = app.state.db.get_segments(mid, original=True)
        def unexpected(*args):
            raise AssertionError("Original darf nicht neu erkannt werden")
        monkeypatch.setattr(app.state.pipeline.transcriber, "transcribe", unexpected)
        app.state.pipeline.process(mid)
        assert app.state.db.get_segments(mid, original=True) == before
        assert before[0]["word_times"]


def test_settings_persist_and_migrate(tmp_path):
    db = Database(tmp_path / "test.db")
    db.set_kv("transcript_correction", {"enabled": True, "model": "klein", "vocabulary": ["Fest"]})
    reopened = Database(tmp_path / "test.db")
    assert reopened.correction_settings()["model"] == "klein"


def test_external_confirmation_and_error_provenance(tmp_path, server, monkeypatch):
    url, state = server
    monkeypatch.setattr("koprotokoll.summarize.external_llm", lambda url: True)
    monkeypatch.setattr("koprotokoll.correction.external_llm", lambda url: True)
    settings = Settings(data_dir=tmp_path, llm_base_url=url)
    texts, report = correct(SOURCE, LLM(settings), "klein", [])
    assert not state["requests"] and texts == [SOURCE[0]["text"]]
    settings.llm_external_confirmed = True
    texts, report = correct(SOURCE, LLM(settings), "klein", [])
    assert state["requests"] and texts[0] != SOURCE[0]["text"]
    assert report["external"] and report["service"] == url
    state["code"] = 500
    texts, report = correct(SOURCE, LLM(settings), "klein", [])
    assert report["external"] and report["status"] == "fallback"


def test_pipeline_runs_correction_before_summary(tmp_path, server, monkeypatch):
    import soundfile as sf
    import numpy as np
    from koprotokoll.pipeline import Pipeline
    url, state = server
    settings = Settings(data_dir=tmp_path, llm_base_url=url)
    db = Database(tmp_path / "pipeline.db")
    mid = db.create_meeting("Fest", "Fest")
    src = tmp_path / "audio.wav"
    sf.write(src, np.zeros(16000), 16000)
    db.update_meeting(mid, audio_path=str(src))
    db.replace_segments(mid, SOURCE)
    db.set_kv("transcript_correction", {"enabled": True, "model": "klein", "vocabulary": []})
    seen = []
    monkeypatch.setattr("koprotokoll.pipeline.summarize", lambda segments, *args: seen.extend(segments) or {})
    pipeline = Pipeline(db, tmp_path, FakeTranscriber(), None, LLM(settings))
    pipeline.process(mid)
    assert seen[0]["text"] == "Wir kaufen Getränke für das Fest."
    assert db.get_segments(mid, original=True)[0]["text"] == SOURCE[0]["text"]
    state["requests"].clear()
    db.set_kv("transcript_correction", {"enabled": False})
    pipeline.process(mid)
    assert not state["requests"]
