"""Windows-Tray-Kern ohne Windows: Doppeltipp, Einstellungen, Zustandsautomat, API-Client."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from koprotokoll.capture import SYSTEM_AUDIO_DEVICE
from koprotokoll.desktop_windows import (
    ApiClient,
    ApiError,
    CaptureController,
    ConfigurableShortcut,
    DesktopPreferences,
    ModifierDoubleTap,
)


# Dieselben Ereignisfolgen wie tests/fixtures/hotkey_macos_test.swift (20 Fälle).
DETECTOR_CASES = [
    ("Zwei kurze Alt-Tipps", [(0, True, False), (0.05, False, False),
                              (0.20, True, False), (0.25, False, False)], 1),
    ("Halten ist kein Doppeltipp", [(0, True, False), (0.6, False, False),
                                    (0.7, True, False), (0.75, False, False)], 0),
    ("Zu großer Abstand", [(0, True, False), (0.05, False, False),
                           (1, True, False), (1.05, False, False)], 0),
    ("Alt-Kombination unterbricht", [(0, True, False), (0.01, True, True),
                                     (0.05, False, False), (0.2, True, False),
                                     (0.25, False, False)], 0),
    ("Andere Taste zwischen Tipps", [(0, True, False), (0.05, False, False),
                                     (0.1, False, True), (0.2, True, False),
                                     (0.25, False, False)], 0),
    ("Gehaltene weitere Taste", [(0, True, True), (0.05, False, False),
                                 (0.2, True, False), (0.25, False, False)], 0),
    ("Wiederholter Zustand zählt nicht", [(0, True, False), (0.01, True, False),
                                          (0.02, True, False), (0.05, False, False)], 0),
    ("Zwei Doppeltipps schalten zweimal", [(0, True, False), (0.05, False, False),
                                           (0.2, True, False), (0.25, False, False),
                                           (0.4, True, False), (0.45, False, False),
                                           (0.6, True, False), (0.65, False, False)], 2),
]


@pytest.mark.parametrize(("name", "events", "expected"), DETECTOR_CASES, ids=[c[0] for c in DETECTOR_CASES])
def test_doppeltipp_erkennt_nur_kurze_doppelte_tipps(name, events, expected):
    detector = ModifierDoubleTap()
    count = sum(1 for now, down, other in events if detector.update(down, other, now))
    assert count == expected


def test_doppeltipp_verfallener_erster_tipp_bleibt_neutral():
    detector = ModifierDoubleTap()
    assert not detector.update(True, False, 0)
    assert not detector.update(False, False, 0.05)
    assert not detector.update(False, False, 1)
    assert not detector.needs_other_keys


def test_konfigurierbares_kürzel_setzt_beim_wechsel_zurück():
    shortcut = ConfigurableShortcut()
    shortcut.configure(enabled=True, modifier="alt")
    assert not shortcut.update(True, False, 0)
    assert not shortcut.update(False, False, 0.05)
    shortcut.configure(enabled=True, modifier="control")
    assert not shortcut.update(True, False, 0.20)
    assert not shortcut.update(False, False, 0.25)
    assert not shortcut.update(True, False, 0.40)
    assert shortcut.update(False, False, 0.45)
    shortcut.update(True, False, 1)
    shortcut.update(False, False, 1.05)
    shortcut.configure(enabled=False, modifier="control")
    assert not shortcut.update(True, False, 1.20)
    assert not shortcut.update(False, False, 1.25)
    shortcut.configure(enabled=True, modifier="control")
    assert not shortcut.update(True, False, 1.30)
    assert not shortcut.update(False, False, 1.35)


def test_konfigurierbares_kürzel_akzeptiert_unbekannte_taste_als_alt():
    shortcut = ConfigurableShortcut()
    shortcut.configure(enabled=True, modifier="unbekannt")
    assert shortcut.modifier == "alt"


def test_einstellungen_defaults_und_rundreise_über_neustart(tmp_path):
    path = tmp_path / "desktop" / "einstellungen.json"
    prefs = DesktopPreferences.load(path)
    assert prefs.audio_source is None
    assert prefs.shortcut_enabled is True
    assert prefs.shortcut_modifier == "alt"
    prefs.audio_source = "USB-Mikrofon"
    prefs.shortcut_modifier = "control"
    prefs.shortcut_enabled = False
    prefs.save()
    reopened = DesktopPreferences.load(path)  # simulierter Neustart
    assert reopened.audio_source == "USB-Mikrofon"
    assert reopened.shortcut_modifier == "control"
    assert reopened.shortcut_enabled is False


def test_einstellungen_unbekannte_werte_fallen_auf_defaults_zurück(tmp_path):
    path = tmp_path / "einstellungen.json"
    path.write_text(json.dumps({"shortcut_modifier": "unbekannte-taste", "shortcut_enabled": "ja",
                                "audio_source": "  "}), encoding="utf-8")
    prefs = DesktopPreferences.load(path)
    assert prefs.shortcut_modifier == "alt"
    assert prefs.shortcut_enabled is True
    assert prefs.audio_source is None
    path.write_text("kaputt", encoding="utf-8")
    assert DesktopPreferences.load(path).shortcut_modifier == "alt"


class FakeApi:
    """Nachbau der Aufnahme-API ohne Server; zeichnet alle Aufrufe auf."""

    def __init__(self, devices=(), fail_start=None, fail_stop=None):
        self.base_url = "http://127.0.0.1:8000"
        self.calls = []
        self._devices = list(devices)
        self._fail_start = fail_start
        self._fail_stop = fail_stop

    def devices(self):
        self.calls.append(("devices",))
        return {"devices": [{"id": d, "name": d} for d in self._devices]}

    def start(self, device, **kwargs):
        self.calls.append(("start", device))
        if self._fail_start:
            raise ApiError(self._fail_start, status=409)
        return {"active": True, "device": device}

    def stop(self):
        self.calls.append(("stop",))
        if self._fail_stop:
            raise ApiError(self._fail_stop, status=409)
        return {"id": 7, "status": "queued"}


def make_controller(tmp_path, api, **kwargs):
    prefs = DesktopPreferences.load(tmp_path / "einstellungen.json")
    now = [0.0]
    controller = CaptureController(api, prefs, clock=lambda: now[0], **kwargs)
    return controller, now


def test_controller_start_und_stopp_über_doppeltipp(tmp_path):
    opened = []
    api = FakeApi()
    controller, now = make_controller(tmp_path, api, open_url=opened.append)
    assert controller.status_line().startswith("Bereit")
    assert controller.toggle()["state"] == "aufnahme"
    assert api.calls == [("start", SYSTEM_AUDIO_DEVICE)]
    now[0] = 95.0
    assert "01:35" in controller.status_line()
    result = controller.toggle()
    assert result["state"] == "bereit"
    assert result["meeting_id"] == 7
    assert api.calls[-1] == ("stop",)
    assert opened == ["http://127.0.0.1:8000/#meeting=7"]


def test_controller_doppeltipp_schaltet_nur_einmal_pro_tipp(tmp_path):
    api = FakeApi()
    controller, _ = make_controller(tmp_path, api)
    shortcut = ConfigurableShortcut()
    shortcut.configure(enabled=True, modifier="alt")
    events = [(0, True, False), (0.05, False, False), (0.2, True, False), (0.25, False, False),
              (0.4, True, False), (0.45, False, False), (0.6, True, False), (0.65, False, False)]
    toggles = 0
    for now, down, other in events:
        if shortcut.update(down, other, now):
            controller.toggle()
            toggles += 1
    assert toggles == 2
    assert [c[0] for c in api.calls] == ["start", "stop"]
    assert controller.state == "bereit"


def test_controller_fehler_bleiben_sichtbar_und_kein_heimlicher_wechsel(tmp_path):
    api = FakeApi(fail_start="Die gewählte Audioquelle ist nicht verfügbar.")
    controller, _ = make_controller(tmp_path, api)
    result = controller.start()
    assert result["state"] == "fehler"
    assert "nicht verfügbar" in controller.status_line()
    assert api.calls == [("start", SYSTEM_AUDIO_DEVICE)]  # kein zweiter Versuch mit anderer Quelle
    # Nach dem Fehler ist ein neuer Start möglich.
    api._fail_start = None
    assert controller.start()["state"] == "aufnahme"


def test_controller_nur_mikrofon_prueft_verfuegbarkeit_der_quelle(tmp_path):
    prefs = DesktopPreferences.load(tmp_path / "einstellungen.json")
    prefs.audio_source = "Altes Mikrofon"
    api = FakeApi(devices=["Neues Mikrofon"])
    controller = CaptureController(api, prefs, system_mix=False)
    result = controller.start()
    assert result["state"] == "fehler"
    assert "Altes Mikrofon" in result["error"]
    assert ("start", "Neues Mikrofon") not in api.calls
    prefs.audio_source = "Neues Mikrofon"
    assert controller.start()["state"] == "aufnahme"
    assert api.calls[-1] == ("start", "Neues Mikrofon")


def test_controller_nur_mikrofon_ohne_quelle_startet_nicht(tmp_path):
    api = FakeApi(devices=["Neues Mikrofon"])
    controller, _ = make_controller(tmp_path, api, system_mix=False)
    result = controller.start()
    assert result["state"] == "fehler"
    assert "Keine Audioquelle" in result["error"]
    assert api.calls == []


def test_controller_doppelstart_und_stopp_ohne_aufnahme(tmp_path):
    api = FakeApi()
    controller, _ = make_controller(tmp_path, api)
    controller.start()
    assert controller.start()["state"] == "fehler"
    assert "bereits" in controller.error
    assert [c[0] for c in api.calls] == ["start"]
    controller2, _ = make_controller(tmp_path, FakeApi())
    assert controller2.stop()["state"] == "fehler"
    assert "keine Aufnahme" in controller2.error


def test_controller_stopp_fehler_wird_gemeldet(tmp_path):
    api = FakeApi(fail_stop="Systemton- und Mikrofonspur sind unterschiedlich lang")
    controller, _ = make_controller(tmp_path, api)
    controller.start()
    result = controller.stop()
    assert result["state"] == "fehler"
    assert "unterschiedlich lang" in controller.status_line()


class _Handler(BaseHTTPRequestHandler):
    def _answer(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/capture/devices":
            self._answer(200, {"devices": [{"id": "x", "name": "Mikrofon"}]})
        elif self.path == "/api/capture":
            self._answer(200, {"active": False})
        else:
            self._answer(404, {"detail": "nicht gefunden"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/capture/start":
            assert body["device"] == "Mikrofon"
            self._answer(201, {"active": True, "device": "Mikrofon"})
        elif self.path == "/api/capture/stop":
            self._answer(201, {"id": 3})
        elif self.path == "/api/capture/error":
            self._answer(409, {"detail": "Es läuft bereits eine Hardwareaufnahme."})
        else:
            self._answer(404, {"detail": "nicht gefunden"})

    def log_message(self, *_):
        pass


@pytest.fixture
def api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_client_liest_und_schreibt_gegen_echten_http_server(api_server):
    client = ApiClient(api_server)
    assert client.devices()["devices"][0]["name"] == "Mikrofon"
    assert client.status() == {"active": False}
    assert client.start("Mikrofon")["active"] is True
    assert client.stop()["id"] == 3


def test_api_client_http_fehler_und_verbindungsfehler_werden_deutsch(api_server):
    client = ApiClient(api_server)
    with pytest.raises(ApiError, match="bereits eine Hardwareaufnahme") as exc:
        client.request("POST", "/api/capture/error", {})
    assert exc.value.status == 409
    offline = ApiClient("http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(ApiError, match="nicht erreichbar"):
        offline.status()
