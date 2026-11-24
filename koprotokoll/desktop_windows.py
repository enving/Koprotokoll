"""Windows-Tray-Steuerung: globaler Doppeltipp, Systemton + Mikrofon über die vorhandene Aufnahme-API.

Dieser Modulteil ist bewusst plattformunabhängig und ohne Zusatzpakete testbar:
Einstellungen (JSON-Datei im Datenordner), die Doppeltipp-Erkennung (Port der
macOS-Logik aus ``macos_hotkey.swift``), der API-Client für den lokalen Server
(``urllib``, Loopback braucht keinen Schlüssel) und der Zustandsautomat der App.

Die nur unter Windows nötige Anbindung (Tray-Symbol, globaler Tastatur-Hook,
Einstellungsdialog) steht weiter unten und wird nur auf ``win32`` importiert.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .capture import SYSTEM_AUDIO_DEVICE

MODIFIERS = ("alt", "control", "shift")
MODIFIER_TITLES = {
    "alt": "Alt zweimal",
    "control": "Strg (Control) zweimal",
    "shift": "Umschalt zweimal",
}


@dataclass
class DesktopPreferences:
    """Gespeicherte Einstellungen der Desktop-App; überdauern einen Neustart als JSON-Datei."""

    path: Path
    audio_source: str | None = None
    shortcut_enabled: bool = True
    shortcut_modifier: str = "alt"

    @classmethod
    def load(cls, path: Path) -> "DesktopPreferences":
        data: dict = {}
        try:
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
        modifier = data.get("shortcut_modifier")
        if modifier not in MODIFIERS:
            modifier = "alt"
        enabled = data.get("shortcut_enabled", True)
        if not isinstance(enabled, bool):
            enabled = True
        source = data.get("audio_source")
        if not isinstance(source, str) or not source.strip():
            source = None
        return cls(path=path, audio_source=source, shortcut_enabled=enabled, shortcut_modifier=modifier)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "audio_source": self.audio_source,
            "shortcut_enabled": self.shortcut_enabled,
            "shortcut_modifier": self.shortcut_modifier,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class ModifierDoubleTap:
    """Doppeltipp-Erkennung für eine Zusatztaste; exakter Port aus ``macos_hotkey.swift``.

    Ein Doppeltipp sind zwei kurze Tastendrücke (loslassen innerhalb von
    ``MAX_HOLD``) mit kleinem Abstand (``MAX_GAP``). Kombinationen mit anderen
    Tasten und längeres Halten lösen nicht aus.
    """

    MAX_GAP = 0.45
    MAX_HOLD = 0.35

    def __init__(self) -> None:
        self._was_down = False
        self._pressed_at = 0.0
        self._first_release: float | None = None
        self._interrupted = False

    @property
    def needs_other_keys(self) -> bool:
        return self._was_down or self._first_release is not None

    def update(self, down: bool, other_key: bool, now: float) -> bool:
        triggered = False
        if self._first_release is not None and now - self._first_release > self.MAX_GAP:
            self._first_release = None
        if other_key:
            self._first_release = None
            self._interrupted = True
        if down and not self._was_down:
            self._pressed_at = now
            self._interrupted = other_key
        if not down and self._was_down:
            if not self._interrupted and now - self._pressed_at <= self.MAX_HOLD:
                if self._first_release is not None and now - self._first_release <= self.MAX_GAP:
                    triggered = True
                    self._first_release = None
                else:
                    self._first_release = now
            else:
                self._first_release = None
            self._interrupted = False
        self._was_down = down
        return triggered


class ConfigurableShortcut:
    """Doppeltipp mit ein-/ausschaltbarer Taste; Wechsel der Konfiguration verwirft einen begonnenen Tipp."""

    def __init__(self) -> None:
        self.enabled = False
        self.modifier = "alt"
        self._detector = ModifierDoubleTap()

    @property
    def needs_other_keys(self) -> bool:
        return self._detector.needs_other_keys

    def configure(self, enabled: bool, modifier: str) -> None:
        if modifier not in MODIFIERS:
            modifier = "alt"
        if self.enabled != enabled or self.modifier != modifier:
            self._detector = ModifierDoubleTap()
        self.enabled = enabled
        self.modifier = modifier

    def update(self, down: bool, other_key: bool, now: float) -> bool:
        return self.enabled and self._detector.update(down, other_key, now)


class ApiError(RuntimeError):
    """Sichtbarer Fehler des lokalen Koprotokoll-Servers oder der Verbindung."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ApiClient:
    """HTTP-Client für die lokale Aufnahme-API; Loopback-Anfragen brauchen keinen API-Schlüssel."""

    def __init__(self, base_url: str, timeout: float = 5.0, long_timeout: float = 75.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.long_timeout = long_timeout

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            try:
                parsed = json.loads(exc.read())
                if isinstance(parsed, dict) and parsed.get("detail"):
                    detail = str(parsed["detail"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise ApiError(f"{detail} (HTTP {exc.code})", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"Koprotokoll-Server nicht erreichbar unter {self.base_url}: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def devices(self) -> dict:
        return self.request("GET", "/api/capture/devices", timeout=self.long_timeout)

    def status(self) -> dict:
        return self.request("GET", "/api/capture")

    def start(self, device: str, title: str = "", channels: int = 2, sample_rate: int = 48_000,
              language: str | None = None) -> dict:
        body: dict = {"device": device, "title": title, "channels": channels, "sample_rate": sample_rate}
        if language:
            body["language"] = language
        return self.request("POST", "/api/capture/start", body, timeout=self.long_timeout)

    def stop(self) -> dict:
        return self.request("POST", "/api/capture/stop", {}, timeout=self.long_timeout)


class CaptureController:
    """Zustandsautomat der Tray-App: ``bereit`` → ``aufnahme`` → ``bereit``/``fehler``.

    Erst der ausdrückliche Stopp stößt über die vorhandene API die Auswertung an;
    während der Aufnahme läuft keine Transkription. Fehlende Geräte oder Zugriffe
    werden als Fehler gemeldet; die App wechselt nie still auf eine unvollständige Quelle.
    """

    def __init__(self, api: ApiClient, preferences: DesktopPreferences, *, system_mix: bool = True,
                 clock=time.monotonic, open_url=None) -> None:
        self.api = api
        self.preferences = preferences
        self.system_mix = system_mix
        self._clock = clock
        self._open_url = open_url
        self.state = "bereit"
        self.error: str | None = None
        self.started_at: float | None = None
        self.meeting_id: int | None = None

    def _fail(self, message: str) -> dict:
        self.state = "fehler"
        self.error = message
        return self.status()

    def resolve_device(self) -> str:
        """Audioquelle bestimmen; bei fehlender Quelle sichtbar abbrechen, nie still umschalten."""
        if self.system_mix:
            return SYSTEM_AUDIO_DEVICE
        source = self.preferences.audio_source
        if not source:
            raise ApiError("Keine Audioquelle in den Einstellungen gewählt.")
        available = [str(d.get("name", "")) for d in self.api.devices().get("devices", [])]
        if source not in available:
            raise ApiError(f"Die gewählte Audioquelle „{source}“ ist nicht mehr verfügbar. "
                           "Bitte in den Einstellungen eine vorhandene Quelle wählen.")
        return source

    def start(self) -> dict:
        if self.state == "aufnahme":
            return self._fail("Es läuft bereits eine Aufnahme.")
        try:
            device = self.resolve_device()
            self.api.start(device)
        except ApiError as exc:
            return self._fail(str(exc))
        self.state = "aufnahme"
        self.error = None
        self.started_at = self._clock()
        self.meeting_id = None
        return self.status()

    def stop(self) -> dict:
        if self.state != "aufnahme":
            return self._fail("Es läuft keine Aufnahme.")
        try:
            meeting = self.api.stop()
        except ApiError as exc:
            return self._fail(str(exc))
        self.meeting_id = meeting.get("id")
        self.state = "bereit"
        self.error = None
        self.started_at = None
        if self._open_url and self.meeting_id is not None:
            self._open_url(f"{self.api.base_url}/#meeting={self.meeting_id}")
        return self.status()

    def toggle(self) -> dict:
        """Globaler Doppeltipp oder App-Schaltfläche: Start und Stopp im Wechsel."""
        return self.stop() if self.state == "aufnahme" else self.start()

    def elapsed(self) -> float:
        if self.state != "aufnahme" or self.started_at is None:
            return 0.0
        return max(0.0, self._clock() - self.started_at)

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "meeting_id": self.meeting_id,
                "elapsed": self.elapsed()}

    def status_line(self) -> str:
        if self.state == "aufnahme":
            minutes, seconds = divmod(int(self.elapsed()), 60)
            return f"● Aufnahme läuft seit {minutes:02d}:{seconds:02d}"
        if self.state == "fehler":
            return f"Fehler: {self.error or 'unbekannter Fehler'}"
        if self.preferences.shortcut_enabled:
            return f"Bereit – {MODIFIER_TITLES[self.preferences.shortcut_modifier]} startet die Aufnahme"
        return "Bereit – Aufnahme über das Menü starten"
