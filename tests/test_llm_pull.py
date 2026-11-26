"""Sprachmodell aus der App laden: Setup-Schritte und Ollama-Pull mit Fortschritt/Abbruch, gegen Fake-Server (#57)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.summarize import LLM, PullCancelled
from test_koprotokoll import FakeDiarizer, FakeTranscriber


@pytest.fixture
def ollama():
    """Fake-Ollama: GET /v1/models, POST /v1/chat/completions und POST /api/pull (NDJSON)."""
    state = {"models": [], "pull_lines": [], "pull_delay": 0.0, "pull_body": None,
             "pull_finished": False, "pull_aborted": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def do_GET(self):
            assert self.path == "/v1/models"
            self._json({"data": [{"id": m} for m in state["models"]]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/chat/completions":
                self._json({"choices": [{"message": {"content": '{"overview":"Erfunden."}'}}]})
            elif self.path == "/api/pull":
                state["pull_body"] = body
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for line in state["pull_lines"]:
                    if state["pull_delay"]:
                        time.sleep(state["pull_delay"])
                    try:
                        self.wfile.write(json.dumps(line).encode() + b"\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        state["pull_aborted"] = True
                        return
                state["pull_finished"] = True
                if any(line.get("status") == "success" for line in state["pull_lines"]):
                    if body["name"] not in state["models"]:
                        state["models"].append(body["name"])
            else:
                self._json({"error": "unbekannter Pfad"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1", state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def _app(tmp_path, url, model="qwen2.5:7b-instruct"):
    settings = Settings(data_dir=tmp_path, llm_base_url=url, llm_model=model)
    return create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(),
                      llm=LLM(settings), background=False), settings


PULL_LINES = [
    {"status": "pulling manifest"},
    {"status": "downloading", "digest": "sha256:abc", "total": 100, "completed": 40},
    {"status": "downloading", "digest": "sha256:abc", "total": 100, "completed": 100},
    {"status": "verifying sha256 digest", "digest": "sha256:abc", "total": 100, "completed": 100},
    {"status": "success"},
]


def test_ollama_root_only_for_local_ollama_provider(tmp_path):
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url="http://localhost:11434/v1"))
    assert llm.ollama_root() == "http://localhost:11434"
    assert llm.pull_supported()
    assert LLM(Settings(data_dir=tmp_path, llm_base_url="http://localhost:11434")).ollama_root() == "http://localhost:11434"
    settings = Settings(data_dir=tmp_path, llm_base_url="https://llm.example.com/v1")
    assert LLM(settings).ollama_root() is None
    assert not LLM(settings).pull_supported()
    settings = Settings(data_dir=tmp_path, llm_base_url="http://localhost:11434/v1")
    settings.llm_provider = "other"
    assert not LLM(settings).pull_supported()


def test_pull_model_direct_with_progress_and_validation(tmp_path, ollama):
    url, state = ollama
    state["pull_lines"] = PULL_LINES
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_model="qwen2.5:7b-instruct"))
    seen = []
    result = llm.pull_model("qwen2.5:7b-instruct", progress=lambda s, t, c: seen.append((s, t, c)))
    assert result["status"] == "success" and result["total"] == 100 and result["completed"] == 100
    assert state["pull_body"] == {"name": "qwen2.5:7b-instruct", "stream": True}
    assert ("downloading", 100, 40) in seen and seen[-1][0] == "success"
    assert state["pull_finished"] and "qwen2.5:7b-instruct" in state["models"]
    with pytest.raises(ValueError):
        llm.pull_model("../boes")
    with pytest.raises(ValueError):
        llm.pull_model("")


def test_pull_model_cancel(tmp_path, ollama):
    url, state = ollama
    state["pull_delay"] = 0.2
    state["pull_lines"] = [{"status": "downloading", "total": 100, "completed": i} for i in range(20)]
    state["pull_lines"].append({"status": "success"})
    llm = LLM(Settings(data_dir=tmp_path, llm_base_url=url, llm_model="m"))
    cancel = threading.Event()
    outcome = {}

    def run():
        try:
            llm.pull_model("m", cancel=cancel)
            outcome["result"] = "ok"
        except PullCancelled:
            outcome["result"] = "cancelled"

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.5)
    cancel.set()
    llm.cancel_pull()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert outcome["result"] == "cancelled"


def test_setup_steps_pull_ready_and_install_instructions(tmp_path, ollama):
    url, state = ollama
    app, _settings = _app(tmp_path, url)
    with TestClient(app) as c:
        r = c.get("/api/llm/setup").json()
        assert r["setup_step"] == "pull_model" and r["pull_supported"] is True
        assert r["suggested_model"] == "qwen2.5:7b-instruct" and r["size_hint"]
        assert r["instructions"] == [] and r["reason"] == "model_missing"
        state["models"] = ["qwen2.5:7b-instruct"]
        r = c.get("/api/llm/setup").json()
        assert r["setup_step"] == "ready" and r["reason"] == "ok"
        assert c.get("/api/llm").json()["reason"] == "ok"
    app2, _ = _app(tmp_path / "d2", "http://127.0.0.1:1/v1")
    with TestClient(app2) as c:
        r = c.get("/api/llm/setup").json()
        assert r["setup_step"] == "install_server" and r["reason"] == "server_unreachable"
        assert r["instructions"] and r["instructions"][0]["link"] == "https://ollama.com/download"
        assert c.get("/api/llm").json()["reason"] == "server_unreachable"


def test_pull_endpoints_progress_done_and_validation(tmp_path, ollama):
    url, state = ollama
    state["pull_lines"] = PULL_LINES
    app, _settings = _app(tmp_path, url)
    with TestClient(app) as c:
        r = c.post("/api/llm/pull", json={"model": "qwen2.5:7b-instruct"})
        assert r.status_code == 201 and r.json()["running"] is True
        st = None
        for _ in range(200):
            st = c.get("/api/llm/pull").json()
            if st["done"]:
                break
            time.sleep(0.05)
        assert st["done"] and st["status"] == "success"
        assert st["total"] == 100 and st["completed"] == 100
        assert not st["running"] and st["error"] is None
        assert c.get("/api/llm").json()["model_present"] is True
        assert c.get("/api/llm/setup").json()["setup_step"] == "ready"
        assert c.post("/api/llm/pull", json={"model": "../boes"}).status_code == 400
        assert c.post("/api/llm/pull", json={"model": ""}).status_code == 400
        assert c.post("/api/llm/pull", json={"model": 5}).status_code == 422
        assert c.post("/api/llm/pull/cancel").status_code == 409


def test_pull_cancel_endpoint(tmp_path, ollama):
    url, state = ollama
    state["pull_delay"] = 0.2
    state["pull_lines"] = [{"status": "downloading", "total": 100, "completed": i} for i in range(20)]
    state["pull_lines"].append({"status": "success"})
    app, _settings = _app(tmp_path, url, model="m")
    with TestClient(app) as c:
        assert c.post("/api/llm/pull", json={"model": "m"}).status_code == 201
        time.sleep(0.4)
        assert c.post("/api/llm/pull/cancel").json() == {"cancelled": True}
        st = None
        for _ in range(200):
            st = c.get("/api/llm/pull").json()
            if st["done"]:
                break
            time.sleep(0.05)
        assert st["done"] and st["status"] == "abgebrochen" and st["error"] is None
        assert c.post("/api/llm/pull/cancel").status_code == 409
        assert "m" not in state["models"]


def test_pull_refused_for_external_url_and_other_provider(tmp_path, ollama):
    url, _state = ollama
    # Externe URL: kein Pull, keine Netzwerkverbindung (Status prüft vorher die Freigabe).
    app, _settings = _app(tmp_path, "https://llm.example.com/v1", model="m")
    with TestClient(app) as c:
        r = c.get("/api/llm/setup").json()
        assert r["pull_supported"] is False and r["setup_step"] == "disabled"
        assert c.post("/api/llm/pull", json={"model": "m"}).status_code == 400
        assert c.get("/api/llm/pull").json()["supported"] is False
    # Lokale URL, aber anderer Anbieter: Pull ebenfalls nicht unterstützt.
    app2, settings2 = _app(tmp_path / "d2", url, model="m")
    settings2.llm_provider = "other"
    app2 = create_app(settings2, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(),
                      llm=LLM(settings2), background=False)
    with TestClient(app2) as c:
        r = c.get("/api/llm/setup").json()
        assert r["pull_supported"] is False and r["setup_step"] == "select_model"
        assert c.post("/api/llm/pull", json={"model": "m"}).status_code == 400
