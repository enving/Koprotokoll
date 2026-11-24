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


# ---------------------------------------------------------------------------
# Nur-Windows-Anbindung: Tray-Symbol, globaler Tastatur-Hook, Einstellungsdialog.
# Der Hook und das Nachrichtenfenster laufen über ctypes (Standardbibliothek),
# das Tray-Symbol über pystray/Pillow, der Dialog über tkinter (Standardbibliothek).
# Dieser Teil wird nur auf win32 ausgeführt; der Kern oben bleibt ohne Zusatzpakete testbar.
# ---------------------------------------------------------------------------

# Virtuelle Tasten der Zusatztasten, je links/rechts.
VK_MODIFIERS = {"alt": (0xA4, 0xA5), "control": (0xA2, 0xA3), "shift": (0xA0, 0xA1)}
VK_MODIFIER_CODES = {code for codes in VK_MODIFIERS.values() for code in codes}
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WH_KEYBOARD_LL = 13


def _require_windows() -> None:
    import sys

    if sys.platform != "win32":
        raise SystemExit("Die Windows-Tray-App ist nur unter Windows verfügbar. "
                         "Auf dem Mac: python -m koprotokoll desktop")


class WindowsHotkeyHook:
    """Globaler Low-Level-Keyboard-Hook (WH_KEYBOARD_LL) für den Doppeltipp.

    Der Hook-Thread meldet jedes Zusatztasten-Ereignis über ``on_event(down, other_key, now)``;
    „other_key“ ist wahr, solange eine andere (Nicht-Zusatz-)Taste gedrückt ist.
    """

    def __init__(self, on_event) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._on_event = on_event
        self._pressed_others: set[int] = set()
        self._hook = None
        self._thread = None
        self._quit = False

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

        self._struct = KBDLLHOOKSTRUCT
        self._callback_type = ctypes.CFUNCTYPE(wintypes.LRESULT, wintypes.INT,
                                               wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT))

    def _callback(self, code, wparam, lparam):
        ctypes = self._ctypes
        if code == 0 and wparam in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
            event = lparam.contents
            vk = event.vkCode
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if vk in VK_MODIFIER_CODES:
                self._on_event(down, bool(self._pressed_others), time.monotonic())
            elif down:
                self._pressed_others.add(vk)
            else:
                self._pressed_others.discard(vk)
        return ctypes.windll.user32.CallNextHookEx(None, code, wparam, ctypes.byref(lparam.contents)
                                                    if lparam else None)

    def _run(self) -> None:
        ctypes = self._ctypes
        user32 = ctypes.windll.user32
        self._callback_ref = self._callback_type(self._callback)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback_ref, None, 0)
        msg = self._wintypes.MSG()
        while not self._quit:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)

    def start(self) -> None:
        import threading

        self._thread = threading.Thread(target=self._run, name="koprotokoll-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._quit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _tray_image(recording: bool, error: bool):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), (24, 24, 24))
    draw = ImageDraw.Draw(image)
    color = (220, 70, 60) if recording else ((230, 180, 50) if error else (90, 200, 120))
    draw.ellipse((14, 14, 50, 50), fill=color)
    return image


class TrayApp:
    """Tray-App: Zustand im Menü, Start/Stopp per Doppeltipp oder Menü, Einstellungsdialog."""

    def __init__(self, controller: CaptureController, preferences: DesktopPreferences,
                 shortcut: ConfigurableShortcut, api: ApiClient, port: int, hook: WindowsHotkeyHook,
                 events) -> None:
        self.controller = controller
        self.preferences = preferences
        self.shortcut = shortcut
        self.api = api
        self.port = port
        self.hook = hook
        self.events = events
        self._icon = None
        self._root = None

    def _refresh(self) -> None:
        if self._icon is not None:
            recording = self.controller.state == "aufnahme"
            error = self.controller.state == "fehler"
            self._icon.icon = _tray_image(recording, error)
            self._icon.title = "Koprotokoll – " + self.controller.status_line()
            self._icon.update_menu()

    def _toggle(self, *_args) -> None:
        self.controller.toggle()
        self._refresh()

    def _menu(self):
        import pystray

        recording = self.controller.state == "aufnahme"
        return pystray.Menu(
            pystray.MenuItem(self.controller.status_line(), None, enabled=False),
            pystray.MenuItem("Stoppen" if recording else "Aufnehmen", self._toggle),
            pystray.MenuItem("Einstellungen …", self._open_settings),
            pystray.MenuItem("Koprotokoll öffnen", lambda: self._open_browser()),
            pystray.MenuItem("Beenden", self._quit),
        )

    def _open_browser(self, url: str | None = None) -> None:
        import webbrowser

        webbrowser.open(url or f"{self.api.base_url}/")

    def _open_settings(self, *_args) -> None:
        if self._root is not None:
            self._root.after(0, self._build_settings)

    def _build_settings(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = self._root
        dialog = tk.Toplevel(root)
        dialog.title("Koprotokoll-Einstellungen")
        recording = self.controller.state == "aufnahme"

        tk.Label(dialog, text="Audioquelle:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        try:
            names = [str(d.get("name", "")) for d in self.api.devices().get("devices", [])]
        except ApiError as exc:
            names = []
            tk.Label(dialog, text=str(exc), foreground="red").grid(row=3, column=0, columnspan=2, padx=8)
        source = ttk.Combobox(dialog, values=names, state="disabled" if recording else "readonly")
        source.set(self.preferences.audio_source or "")
        source.grid(row=0, column=1, padx=8, pady=4)

        tk.Label(dialog, text="Tastenkürzel:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        enabled = tk.BooleanVar(value=self.preferences.shortcut_enabled)
        tk.Checkbutton(dialog, text="Globaler Doppeltipp aktiv", variable=enabled,
                       state="disabled" if recording else "normal").grid(row=1, column=1, sticky="w")
        modifier = tk.StringVar(value=self.preferences.shortcut_modifier)
        for value, title in MODIFIER_TITLES.items():
            tk.Radiobutton(dialog, text=title, variable=modifier, value=value,
                           state="disabled" if recording else "normal").grid(
                row=2 + list(MODIFIER_TITLES).index(value), column=1, sticky="w")

        def save() -> None:
            self.preferences.audio_source = source.get().strip() or None
            self.preferences.shortcut_enabled = bool(enabled.get())
            self.preferences.shortcut_modifier = modifier.get()
            self.preferences.save()
            self.shortcut.configure(self.preferences.shortcut_enabled, self.preferences.shortcut_modifier)
            dialog.destroy()
            self._refresh()

        tk.Button(dialog, text="Speichern", command=save).grid(row=6, column=0, columnspan=2, pady=8)
        if recording:
            tk.Label(dialog, text="Während einer Aufnahme sind die Einstellungen gesperrt.").grid(
                row=5, column=0, columnspan=2, padx=8)

    def _quit(self, *_args) -> None:
        if self._icon is not None:
            self._icon.stop()
        self.hook.stop()
        if self._root is not None:
            self._root.after(0, self._root.destroy)

    def run(self) -> None:
        import queue as queue_module

        import pystray

        self._icon = pystray.Icon("Koprotokoll", _tray_image(False, False), "Koprotokoll", self._menu())
        self._icon.run_detached()
        self.hook.start()
        import tkinter as tk

        self._root = tk.Tk()
        self._root.withdraw()

        def poll() -> None:
            try:
                while True:
                    if self.events.get_nowait() == "toggle":
                        self._toggle()
            except queue_module.Empty:
                pass
            self._refresh()
            self._root.after(500, poll)

        self._root.after(200, poll)
        self._root.mainloop()


def _server_erreichbar(api: ApiClient) -> bool:
    try:
        api.status()
        return True
    except ApiError:
        return False


def main(port: int = 8000) -> None:
    """Tray-App unter Windows starten; legt den lokalen Server an, falls er nicht läuft."""
    _require_windows()
    import queue
    import subprocess
    import sys
    import time
    import webbrowser

    from .config import Settings

    settings = Settings()
    settings.ensure_dirs()
    preferences = DesktopPreferences.load(settings.data_dir / "desktop" / "einstellungen.json")
    api = ApiClient(f"http://127.0.0.1:{port}")
    if not _server_erreichbar(api):
        server = subprocess.Popen([sys.executable, "-m", "koprotokoll", "serve", "--port", str(port)])
        for _ in range(60):
            if _server_erreichbar(api):
                break
            if server.poll() is not None:
                raise SystemExit("Der lokale Koprotokoll-Server ließ sich nicht starten.")
            time.sleep(0.5)
        else:
            raise SystemExit("Der lokale Koprotokoll-Server antwortet nicht auf Port "
                             f"{port}; bitte `python -m koprotokoll serve --port {port}` prüfen.")
    events: queue.Queue = queue.Queue()
    shortcut = ConfigurableShortcut()
    shortcut.configure(preferences.shortcut_enabled, preferences.shortcut_modifier)
    hook = WindowsHotkeyHook(
        lambda down, other_key, now: shortcut.update(down, other_key, now) and events.put("toggle"))
    controller = CaptureController(api, preferences, system_mix=True, open_url=webbrowser.open)
    TrayApp(controller, preferences, shortcut, api, port, hook, events).run()
