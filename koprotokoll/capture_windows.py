"""WASAPI-Aufnahme unter Windows: Systemton (Loopback) und Standardmikrofon getrennt.

Das Modul ist ein Hilfsprogramm im Stil von ``capture_macos.swift`` und wird von
``capture.py`` als eigener Prozess gestartet:

    python -m koprotokoll.capture_windows --capture-audio system.wav mikrofon.wav

Zwei Aufnahmewege stehen bereit, beide mit demselben JSON-Statusprotokoll auf stdout
(``{"ready": true}``, ``{"level_db": …}``, ``{"error": "…"}``):

* ctypes-Weg (Standard unter Windows, nur ``numpy`` nötig): ``spur_aufnehmen`` liest
  beide Standardgeräte über den VTABLE-Zugriff parallel in je einem Thread mit eigener
  COM-Apartment-Initialisierung; ``vorspann_kuerzen`` beschneidet beide Spuren auf den
  Moment, in dem beide Geräte bereit waren.
* pycaw-Weg (``run_capture``/``DualTrackRecorder`` mit ``wasapi_sources``): gleicher
  Ablauf über pycaw/comtypes, falls installiert (siehe ``requirements-windows.txt``).

Gestoppt wird über stdin (Zeile ``stop`` oder EOF) UND über eine Stopp-Datei neben der
Mikrofon-Spur (``*.stop``); Windows kann SIGINT nicht an fremde Prozesse senden.
"""



from __future__ import annotations

import argparse
import ctypes
import json
import math
import signal
import struct
import sys
import threading
import time
import wave
try:  # WINFUNCTYPE gibt es nur unter Windows; Logiktests bleiben überall ausführbar.
    from ctypes import WINFUNCTYPE
except ImportError:
    WINFUNCTYPE = None
from ctypes import POINTER, byref, c_float, c_int, c_longlong, \
    c_short, c_uint, c_ulong, c_ulonglong, c_ushort, c_ubyte, c_void_p, cast
from array import array
from pathlib import Path

import numpy as np


COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 23  # objbase.h: 1|2|4|16
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
E_RENDER = 0  # mmdeviceapi.h: EDataFlow.eRender
E_CAPTURE = 1  # mmdeviceapi.h: EDataFlow.eCapture
E_CONSOLE = 0  # mmdeviceapi.h: ERole.eConsole
STOPPZEILE = "stop"
STATUS_INTERVALL = 0.5  # Sekunden zwischen zwei Pegelmeldungen
ERSTE_SAMPLES_TIMEOUT = 20.0  # Sekunden bis zur ersten ablehnenden Fehlermeldung
PUFFER_DAUER = 10_000_000  # hns (1 s) Vorlauf für den Gerätepuffer

# Konstanten der pycaw-Variante (DualTrackRecorder/WavTrackWriter).
CHANNELS = 2
SAMPLE_RATE = 48_000
FRAME_BYTES = CHANNELS * 2
CHUNK_FRAMES = 4096


# VTABLE-Positionen aus den SDK-Headern (10.0.19041.0):
# um/mmdeviceapi.h: IMMDeviceEnumerator 3 EnumAudioEndpoints, 4 GetDefaultAudioEndpoint,
#   5 GetDevice · IMMDevice 3 Activate
# um/audioclient.h: IAudioClient 3 Initialize, 4 GetBufferSize, 5 GetStreamLatency,
#   6 GetCurrentPadding, 7 IsFormatSupported, 8 GetMixFormat, 9 GetDevicePeriod,
#   10 Start, 11 Stop, 12 Reset, 13 SetEventHandle, 14 GetService ·
#   IAudioCaptureClient 3 GetBuffer, 4 ReleaseBuffer, 5 GetNextPacketSize
#
# Achtung GetBuffer: Der Live-Proxy der Audiomaschine (audioses.dll) erwartet
# in diesem Build als vierten Ausgabeparameter eine 8-Byte-Position
# (UINT64*, im Header nicht dokumentiert) und schreibt die Flags als dritten.
# Mit nur drei Ausgabeparametern schreibt der Proxy in eine Register-/Stapel-
# Lücke und stürzt mit einer Zugriffsverletzung ab (auf diesem Rechner
# nachgewiesen und mit vier Ausgabeparametern behoben). Ein Standard-Proxy
# ignoriert den vierten Zeiger, deshalb ist der Aufruf in beiden Fällen sicher.


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def guid(text: str) -> GUID:
    """GUID in „{8-4-4-4-12}"-Schreibweise in die Binärstruktur umwandeln."""
    z = text.strip("{}").replace("-", "")
    g = GUID()
    g.Data1, g.Data2, g.Data3 = int(z[:8], 16), int(z[8:12], 16), int(z[12:16], 16)
    for i in range(8):
        g.Data4[i] = int(z[16 + i * 2:18 + i * 2], 16)
    return g


CLSID_MMDEVICE_ENUMERATOR = guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDEVICE_ENUMERATOR = guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAUDIO_CLIENT = guid("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
IID_IAUDIO_CAPTURE_CLIENT = guid("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = guid("{00000003-0000-0010-8000-00aa00389b71}")
KSDATAFORMAT_SUBTYPE_PCM = guid("{00000001-0000-0010-8000-00aa00389b71}")


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1  # mmreg.h: #include <pshpack1.h>
    _fields_ = [("wFormatTag", c_ushort), ("nChannels", c_ushort),
                ("nSamplesPerSec", c_ulong), ("nAvgBytesPerSec", c_ulong),
                ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort),
                ("cbSize", c_ushort)]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _pack_ = 1  # mmreg.h: gepackt, SubFormat liegt bei Offset 24
    _fields_ = [("Format", WAVEFORMATEX), ("wValidBitsPerSample", c_ushort),
                ("dwChannelMask", c_ulong), ("SubFormat", GUID)]


def meldung(hr: int, schritt: str) -> None:
    """HRESULT prüfen; bei Fehler mit deutscher Meldung abbrechen."""
    if hr != 0:
        raise RuntimeError(f"{schritt} schlug fehl (HRESULT 0x{hr & 0xFFFFFFFF:08X}).")


def vtable(interface: c_void_p):
    """VTABLE-Zeilen einer COM-Schnittstelle als Adressliste lesen."""
    objekt = cast(interface, POINTER(c_void_p))
    return cast(objekt[0], POINTER(c_void_p))


def gleiche_guid(a: GUID, b: GUID) -> bool:
    """Zwei GUID-Strukturen vollständig vergleichen."""
    return (a.Data1 == b.Data1 and a.Data2 == b.Data2 and a.Data3 == b.Data3
            and bytes(a.Data4) == bytes(b.Data4))


def status_zeile(objekt: dict) -> None:
    """Eine Statuszeile (JSON) nach stdout schreiben."""
    print(json.dumps(objekt, ensure_ascii=False), flush=True)


def pegel_db(spitze: float) -> float:
    """Spitzenpegel (0–1) als Dezibel mit 120-dB-Boden, gerundet auf eine Stelle."""
    if spitze <= 0.0:
        return -120.0
    return round(max(-120.0, 20.0 * math.log10(spitze)), 1)


def zu_pcm16(stueck: np.ndarray) -> bytes:
    """float32-Felder (Bereich ±1) in Little-Endian-PCM-16-Bytes wandeln."""
    begrenzt = np.clip(np.asarray(stueck, dtype=np.float32), -1.0, 1.0)
    return (begrenzt * 32767.0).astype("<i2").tobytes()


def vorspann_kuerzen(pfad: Path, bilder: int) -> int:
    """Die ersten ``bilder`` Bilder aus einer PCM-WAV entfernen und die Zahl zurückgeben.

    Die Spur, die zuerst lief, reicht beim Start über die Bereitschaft der zweiten
    Spur hinaus (Rückstau im Loopback, spätere Geräteöffnung). Beide Spuren werden
    auf den gemeinsamen Startmoment beschnitten, damit sie deckungsgleich mischen.
    """
    if bilder <= 0 or not pfad.is_file():
        return 0
    with wave.open(str(pfad), "rb") as quelle:
        vorhanden = quelle.getnframes()
        if bilder >= vorhanden:
            return 0
        zwischen = pfad.with_suffix(".teil")
        with wave.open(str(zwischen), "wb") as ziel:
            ziel.setnchannels(quelle.getnchannels())
            ziel.setsampwidth(quelle.getsampwidth())
            ziel.setframerate(quelle.getframerate())
            quelle.setpos(bilder)
            while stueck := quelle.readframes(65_536):
                ziel.writeframesraw(stueck)
    zwischen.replace(pfad)
    return bilder


def synchronisieren(system: WavSpur, mikrofon: WavSpur, sync: dict) -> None:
    """Den Stand beider Spuren festhalten, sobald beide Geräte bereit sind."""
    system.erste.wait(ERSTE_SAMPLES_TIMEOUT)
    mikrofon.erste.wait(ERSTE_SAMPLES_TIMEOUT)
    sync["Systemton"] = system.bilder
    sync["Mikrofon"] = mikrofon.bilder


class WavSpur:
    """Eine laufend geschriebene PCM-16-Spur mit Spitzenpegel."""

    def __init__(self, name: str, pfad: Path):
        self.name = name
        self.pfad = pfad
        self.rate = 0
        self.kanaele = 0
        self.bilder = 0
        self.spitze = 0.0
        self.erste = threading.Event()
        self._lock = threading.Lock()
        self._datei: wave.Wave_write | None = None

    def starten(self, rate: int, kanaele: int) -> None:
        """Spur mit dem Format der Aufnahmequelle öffnen."""
        if rate <= 0 or kanaele < 1:
            raise RuntimeError("Der Audioeingang meldete kein brauchbares Format.")
        self.rate, self.kanaele = rate, kanaele
        self._datei = wave.open(str(self.pfad), "wb")
        self._datei.setnchannels(kanaele)
        self._datei.setsampwidth(2)
        self._datei.setframerate(rate)

    def schreibe(self, stueck: np.ndarray) -> None:
        """Ein Audiofeld (float32, Bild × Kanal) in die Spur schreiben."""
        if stueck.ndim != 2 or stueck.shape[1] != self.kanaele:
            raise RuntimeError(f"{self.name}: Das Paket passt nicht zum Spurformat.")
        daten = zu_pcm16(stueck)
        with self._lock:
            if self._datei is None:
                raise RuntimeError(f"{self.name}: Die Spur ist bereits geschlossen.")
            self._datei.writeframesraw(daten)
            self.bilder += int(stueck.shape[0])
            if stueck.size:
                self.spitze = max(self.spitze, float(np.abs(stueck).max()))
            self.erste.set()

    def pegel_und_reset(self) -> float:
        """Spitzenpegel seit dem letzten Abruf; Pegel danach zurücksetzen."""
        with self._lock:
            spitze, self.spitze = self.spitze, 0.0
        return spitze

    def schliessen(self) -> None:
        """Spur schließen; die WAV bleibt auch nach Fehlern lesbar."""
        with self._lock:
            if self._datei is not None:
                self._datei.close()
                self._datei = None

    def dauer(self) -> float:
        """Bisherige Spurdauer in Sekunden."""
        return self.bilder / self.rate if self.rate else 0.0


class FehlerKanal:
    """Der erste Fehler einer Spur, threadsicher gesammelt."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.text: str | None = None

    def melden(self, text: str) -> None:
        """Nur den ersten Fehler behalten; spätere tragen nichts mehr bei."""
        with self._lock:
            if self.text is None:
                self.text = text

    def abfragen(self) -> str | None:
        """Den gesammelten Fehler auslesen."""
        with self._lock:
            return self.text


_prototypen: dict[int, object] = {}


def methode(objekt: c_void_p, index: int, restype=ctypes.c_long, argumente: tuple = (), *werte):
    """Eine VTABLE-Position einmalig als Prototyp binden und mit voller Parameterliste aufrufen."""
    adresse = int(vtable(objekt)[index])
    if adresse not in _prototypen:
        _prototypen[adresse] = WINFUNCTYPE(restype, c_void_p, *argumente)(adresse)
    return _prototypen[adresse](objekt, *werte)


def format_lesen(name: str, format_zeiger: c_void_p) -> tuple[int, int, str]:
    """Kanäle, Abtastrate und Elementtyp („float32“ oder „int16“) aus dem Mixformat lesen."""
    kopf = cast(format_zeiger, POINTER(WAVEFORMATEX))[0]
    kanale, rate, bits = int(kopf.nChannels), int(kopf.nSamplesPerSec), int(kopf.wBitsPerSample)
    if kopf.wFormatTag == WAVE_FORMAT_IEEE_FLOAT and bits == 32:
        return kanale, rate, "float32"
    if kopf.wFormatTag == 1 and bits == 16:
        return kanale, rate, "int16"
    if kopf.wFormatTag == WAVE_FORMAT_EXTENSIBLE:
        erweitert = cast(format_zeiger, POINTER(WAVEFORMATEXTENSIBLE))[0]
        if bits == 32 and gleiche_guid(erweitert.SubFormat, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT):
            return kanale, rate, "float32"
        if bits == 16 and gleiche_guid(erweitert.SubFormat, KSDATAFORMAT_SUBTYPE_PCM):
            return kanale, rate, "int16"
    raise RuntimeError(f"{name}: Das Format des Standardgeräts wird nicht unterstützt "
                       f"(Tag {int(kopf.wFormatTag)}, {bits} Bit).")


def lese_paket(name: str, typ: str, daten: c_void_p, laenge: int, kanale: int,
               stumm: bool) -> np.ndarray:
    """Ein abgerufenes Paket (interleaved) als float32-Felder (Bild × Kanal) lesen."""
    if laenge <= 0:
        return np.zeros((0, kanale), dtype=np.float32)
    if stumm or not daten.value:
        return np.zeros((laenge, kanale), dtype=np.float32)
    if typ == "float32":
        feld = np.ctypeslib.as_array(cast(daten, POINTER(c_float)), shape=(laenge * kanale,))
        return feld.reshape((laenge, kanale)).astype(np.float32, copy=False)
    if typ == "int16":
        feld = np.ctypeslib.as_array(cast(daten, POINTER(c_short)), shape=(laenge * kanale,))
        return feld.reshape((laenge, kanale)).astype(np.float32) / 32768.0
    raise RuntimeError(f"{name}: Unbekannter Elementtyp {typ}.")


def spur_aufnehmen(name: str, flow: int, loopback: bool, spur: WavSpur,
                   fehler: FehlerKanal, stop_event: threading.Event) -> None:
    """Ein Standardgerät als eigene Spur aufnehmen, bis gestoppt oder gestört wird.

    Systemton (``loopback``) nutzt das Wiedergabe-Ende mit
    ``AUDCLNT_STREAMFLAGS_LOOPBACK``, das Mikrofon das Aufnahme-Ende; beide
    Threads initialisieren ihr eigenes COM-Apartment (STA).
    """
    com = False
    format_zeiger = c_void_p()
    try:
        meldung(ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED),
                f"{name}: COM-Initialisierung")
        com = True
        enumerator = c_void_p()
        meldung(ctypes.windll.ole32.CoCreateInstance(
            byref(CLSID_MMDEVICE_ENUMERATOR), None, CLSCTX_ALL,
            byref(IID_IMMDEVICE_ENUMERATOR), byref(enumerator)), f"{name}: Geräteaufzählung")
        geraet = c_void_p()
        meldung(methode(enumerator, 4, ctypes.c_long, (c_ulong, c_ulong, POINTER(c_void_p)),
                        flow, E_CONSOLE, byref(geraet)),
                f"{name}: Standardgerät")
        client = c_void_p()
        meldung(methode(geraet, 3, ctypes.c_long,
                        (POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)),
                        byref(IID_IAUDIO_CLIENT), CLSCTX_ALL, None, byref(client)),
                f"{name}: Audiozugang")
        meldung(methode(client, 8, ctypes.c_long, (POINTER(c_void_p),), byref(format_zeiger)),
                f"{name}: Mixformat")
        kanale, rate, typ = format_lesen(name, format_zeiger)
        spur.starten(rate, kanale)
        stream_flags = AUDCLNT_STREAMFLAGS_LOOPBACK if loopback else 0
        meldung(methode(client, 3, ctypes.c_long,
                        (c_int, c_ulong, c_longlong, c_longlong, c_void_p, c_void_p),
                        AUDCLNT_SHAREMODE_SHARED, stream_flags, PUFFER_DAUER, 0,
                        format_zeiger, None),
                f"{name}: Pufferanmeldung")
        abruf = c_void_p()
        meldung(methode(client, 14, ctypes.c_long, (POINTER(GUID), POINTER(c_void_p)),
                        byref(IID_IAUDIO_CAPTURE_CLIENT), byref(abruf)),
                f"{name}: Pufferabruf")
        meldung(methode(client, 10), f"{name}: Start")
        paket = c_uint(0)
        daten = c_void_p()
        frames = c_uint(0)
        puffer_flags = c_ulong(0)
        position = c_ulonglong(0)
        stichtag = time.monotonic() + ERSTE_SAMPLES_TIMEOUT
        while not stop_event.is_set():
            meldung(methode(abruf, 5, ctypes.c_long, (POINTER(c_uint),), byref(paket)),
                    f"{name}: Paketgröße")
            if not paket.value:
                if time.monotonic() > stichtag and not spur.erste.is_set():
                    raise RuntimeError(f"{name}: Das Gerät lieferte innerhalb von "
                                       f"{ERSTE_SAMPLES_TIMEOUT:.0f} Sekunden keine Daten. "
                                       "Quelle und Wiedergabe prüfen.")
                stop_event.wait(0.02)
                continue
            meldung(methode(abruf, 3, ctypes.c_long,
                            (POINTER(c_void_p), POINTER(c_uint), POINTER(c_ulong),
                             POINTER(c_ulonglong)),
                            byref(daten), byref(frames), byref(puffer_flags),
                            byref(position)),
                    f"{name}: Puffer")
            stueck = lese_paket(name, typ, daten, int(frames.value), kanale,
                                bool(puffer_flags.value & AUDCLNT_BUFFERFLAGS_SILENT))
            spur.schreibe(stueck)
            meldung(methode(abruf, 4, ctypes.c_long, (c_uint,), int(frames.value)),
                    f"{name}: Pufferfreigabe")
    except Exception as exc:
        fehler.melden(f"{name}: {exc}")
    finally:
        stop_event.set()
        spur.schliessen()
        if format_zeiger.value:
            ctypes.windll.ole32.CoTaskMemFree(format_zeiger)
        if com:
            ctypes.windll.ole32.CoUninitialize()


def status_melden(system: WavSpur, mikrofon: WavSpur, fehler: FehlerKanal,
                  stop_event: threading.Event) -> None:
    """Bereitschaft melden und solange die Aufnahme läuft den Spitzenpegel ausgeben."""
    bereit = False
    while not stop_event.wait(STATUS_INTERVALL):
        if fehler.abfragen():
            return
        if not bereit and system.erste.is_set() and mikrofon.erste.is_set():
            bereit = True
            status_zeile({"ready": True})
        spitze = max(system.pegel_und_reset(), mikrofon.pegel_und_reset())
        status_zeile({"level_db": pegel_db(spitze)})


def stopp_bewachen(stop_event: threading.Event) -> None:
    """Auf die Stoppzeile oder EOF an stdin warten; beides beendet die Aufnahme."""
    try:
        for zeile in sys.stdin:
            if zeile.strip() == STOPPZEILE:
                break
    except (OSError, ValueError):
        pass
    stop_event.set()


class CaptureSourceError(RuntimeError):
    """Eine Audioquelle lieferte einen Fehler oder eine unvollständige Spur."""


def emit_status(payload: dict, stream=None) -> None:
    """Eine Status-Zeile im JSON-Protokoll des Helfers ausgeben."""
    print(json.dumps(payload, ensure_ascii=False), file=stream or sys.stdout, flush=True)


class WavTrackWriter:
    """Eine einzelne PCM16-WAV-Spur; zählt Frames und misst den Spitzenpegel."""

    def __init__(self, path: Path, channels: int = CHANNELS, sample_rate: int = SAMPLE_RATE):
        self.path = Path(path)
        self.channels = channels
        self.sample_rate = sample_rate
        self.frames = 0
        self.peak = 0.0
        self._raw = self.path.open("wb")
        self._wave = wave.open(self._raw, "wb")
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(2)
        self._wave.setframerate(sample_rate)

    def write(self, chunk: bytes) -> int:
        usable = len(chunk) - (len(chunk) % FRAME_BYTES)
        if usable <= 0:
            return 0
        chunk = chunk[:usable]
        self._wave.writeframesraw(chunk)
        self.frames += usable // FRAME_BYTES
        samples = array("h")
        samples.frombytes(chunk)
        if sys.byteorder != "little":
            samples.byteswap()
        peak = max((abs(sample) for sample in samples), default=0) / 32768
        self.peak = max(self.peak, peak)
        return usable

    @property
    def level_db(self) -> float:
        return max(-120.0, 20 * math.log10(self.peak)) if self.peak else -120.0

    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def close(self) -> None:
        try:
            self._wave.close()
        finally:
            self._raw.close()


class DualTrackRecorder:
    """Schreibt Systemton- und Mikrofonquelle im Gleichschritt in zwei WAV-Spuren.

    Beide Quellen liefern PCM16 mit derselben Kanal-/Abtastrate; die Spuren
    bleiben dadurch frame-genau ausgerichtet. Endet eine Quelle früher oder
    meldet sie einen Fehler, wird die Aufnahme mit Fehler beendet – die
    unvollständige Spur wird nicht still weitergeführt.
    """

    def __init__(self, system_source, mic_source, system_path: Path, mic_path: Path, stream=None,
                 stop_file: Path | None = None):
        self.system_source = system_source
        self.mic_source = mic_source
        self.system_path = Path(system_path)
        self.mic_path = Path(mic_path)
        self.stream = stream
        # Stopp-Datei: existiert sie, endet die Aufnahme sauber (SIGINT ist auf Windows
        # ohne gemeinsame Konsole nicht zuverlässig zustellbar).
        self.stop_file = Path(stop_file) if stop_file is not None else self.mic_path.with_suffix(".stop")
        self.system: WavTrackWriter | None = None
        self.mic: WavTrackWriter | None = None
        self.ready_emitted = False

    def _emit(self, payload: dict) -> None:
        emit_status(payload, self.stream)

    def run(self, poll_seconds: float = 0.5) -> tuple[Path, Path]:
        self.system = WavTrackWriter(self.system_path)
        self.mic = WavTrackWriter(self.mic_path)
        try:
            self._loop(poll_seconds)
        except CaptureSourceError as exc:
            self._emit({"error": str(exc)})
            raise
        finally:
            self.system.close()
            self.mic.close()
        self._check_complete()
        return self.system_path, self.mic_path

    def _loop(self, poll_seconds: float) -> None:
        # read() liefert PCM16-Bytes, b"" bei kurzfristig keinen Daten und None am Stream-Ende.
        want = CHUNK_FRAMES * FRAME_BYTES
        pending = {"Systemton": bytearray(), "Mikrofon": bytearray()}
        sources = {"Systemton": self.system_source, "Mikrofon": self.mic_source}
        ended: set[str] = set()
        last_status = time.monotonic()
        while len(ended) < len(sources):
            if self.stop_file.exists():
                break
            idle = True
            chunks: dict[str, bytes | None] = {}
            for name, source in sources.items():
                if name in ended:
                    continue
                try:
                    chunks[name] = source.read(want)
                except Exception as exc:
                    raise CaptureSourceError(f"Die {name}-Quelle meldete einen Fehler: {exc}") from exc
            for name, chunk in chunks.items():
                if chunk is None:
                    ended.add(name)
                elif chunk:
                    idle = False
                    pending[name].extend(chunk)
                    if not self.ready_emitted:
                        self.ready_emitted = True
                        self._emit({"ready": True})
            if len(ended) == 1 and any(chunks.get(other) for other in sources if other not in ended):
                raise CaptureSourceError(
                    "Systemton- und Mikrofonspur sind unterschiedlich lang: Eine Spur endet vor der anderen."
                )
            if idle and len(ended) < len(sources):
                time.sleep(0.01)
            for writer, name in ((self.system, "Systemton"), (self.mic, "Mikrofon")):
                if len(pending[name]) >= want:
                    writer.write(bytes(pending[name][:want]))
                    del pending[name][:want]
            now = time.monotonic()
            if self.ready_emitted and now - last_status >= poll_seconds:
                last_status = now
                level = max(self.system.level_db, self.mic.level_db)
                self._emit({"level_db": round(level, 1)})
        for writer, name in ((self.system, "Systemton"), (self.mic, "Mikrofon")):
            if pending[name]:
                writer.write(bytes(pending[name]))

    def _check_complete(self) -> None:
        assert self.system is not None and self.mic is not None
        if self.system.frames <= 0 or self.mic.frames <= 0:
            missing = "Systemton" if self.system.frames <= 0 else "Mikrofon"
            raise CaptureSourceError(f"Die {missing}-Spur enthält keine Samples; Aufnahme unvollständig.")
        tolerance = 0.5
        if abs(self.system.duration() - self.mic.duration()) > tolerance:
            raise CaptureSourceError(
                "Systemton- und Mikrofonspur sind unterschiedlich lang "
                f"({self.system.duration():.2f} s und {self.mic.duration():.2f} s)."
            )


class _WasapiCaptureSource:
    """Liest PCM16 aus einem WASAPI-Endpunkt: Loopback (eRender) oder Mikrofon (eCapture).

    Nur unter Windows mit installiertem ``pycaw``/``comtypes`` nutzbar; der
    echte Gerätetest steht auf einem Windows-Rechner aus (siehe Issue #35).
    """

    def __init__(self, *, loopback: bool):
        if sys.platform != "win32":
            raise RuntimeError("WASAPI steht nur unter Windows zur Verfügung.")
        try:
            import comtypes.client  # noqa: F401
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import (  # type: ignore
                IAudioCaptureClient, IAudioClient, IMMDeviceEnumerator, MMDeviceEnumerator,
            )
            from pycaw.constants import (  # type: ignore
                AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, eCapture, eMultimedia, eRender,
            )
        except ImportError as exc:
            raise RuntimeError("pycaw/comtypes fehlen; bitte `pip install -r requirements-windows.txt` "
                               "unter Windows ausführen.") from exc
        self._CLSCTX_ALL = CLSCTX_ALL
        self._IAudioClient = IAudioClient
        self._IAudioCaptureClient = IAudioCaptureClient
        self._loopback = loopback
        self._flags = AUDCLNT_STREAMFLAGS_LOOPBACK if loopback else 0
        self._flow = eRender if loopback else eCapture
        enumerator = comtypes.client.CreateObject(MMDeviceEnumerator, interface=IMMDeviceEnumerator)
        device = enumerator.GetDefaultAudioEndpoint(self._flow, eMultimedia)
        self._client = device.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
        self._wave_format = self._client.GetMixFormat()
        self._channels = self._wave_format.nChannels
        self._sample_rate = self._wave_format.nSamplesPerSec
        self._bits = self._wave_format.wBitsPerSample
        self._client.Initialize(AUDCLNT_SHAREMODE_SHARED, self._flags, 0, 0, self._wave_format, None)
        self._capture = self._client.GetService(IAudioCaptureClient)
        self._client.Start()

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def read(self, max_bytes: int) -> bytes:
        import ctypes

        capture = self._capture
        frames_wanted = max(1, max_bytes // (self._channels * 2))
        out = bytearray()
        while len(out) < frames_wanted * self._channels * 2:
            packet = capture.GetNextPacketSize()
            if packet == 0:
                break
            data_ptr = ctypes.c_void_p()
            flags = ctypes.c_uint32()
            num_frames = ctypes.c_uint32()
            capture.GetBuffer(ctypes.byref(data_ptr), ctypes.byref(flags), ctypes.byref(num_frames), None, None)
            raw = ctypes.string_at(data_ptr, num_frames.value * self._wave_format.nBlockAlign)
            capture.ReleaseBuffer(num_frames.value)
            out.extend(self._to_pcm16(raw, num_frames.value))
        return bytes(out)

    def _to_pcm16(self, raw: bytes, frames: int) -> bytes:
        if self._bits == 16:
            return raw[: frames * self._channels * 2]
        if self._bits == 32:  # IEEE-Float (üblicher WASAPI-Mixformat)
            floats = struct.unpack(f"<{frames * self._channels}f", raw[: frames * self._channels * 4])
            return struct.pack(f"<{frames * self._channels}h",
                               *(max(-32768, min(32767, int(sample * 32767))) for sample in floats))
        raise CaptureSourceError(f"Nicht unterstütztes WASAPI-Format mit {self._bits} Bit.")

    def close(self) -> None:
        try:
            self._client.Stop()
        except Exception:
            pass


def wasapi_sources() -> tuple[_WasapiCaptureSource, _WasapiCaptureSource]:
    """Systemton (Loopback) und Standardmikrofon als getrennte WASAPI-Quellen."""
    system = _WasapiCaptureSource(loopback=True)
    try:
        mic = _WasapiCaptureSource(loopback=False)
    except Exception:
        system.close()
        raise
    return system, mic


def run_capture(system_path: Path, mic_path: Path, sources=None, stream=None,
                stop_file: Path | None = None) -> tuple[Path, Path]:
    """Nimmt beide Spuren auf, bis SIGINT/Stopp-Datei/EOF; sauberer Abschluss mit Exit-Code 0."""
    if sources is None:
        sources = wasapi_sources()
    system_source, mic_source = sources
    recorder = DualTrackRecorder(system_source, mic_source, system_path, mic_path, stream=stream,
                                 stop_file=stop_file)

    def _stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, _stop)
    try:
        recorder.run()
    except (KeyboardInterrupt, BrokenPipeError):
        # Ausdrücklicher Stopp (SIGINT, Stopp-Datei) oder abgebrochener Statuskanal:
        # Spuren sauber schließen, danach mischt capture.py.
        if recorder.system is not None:
            recorder.system.close()
        if recorder.mic is not None:
            recorder.mic.close()
        recorder._check_complete()
        return recorder.system_path, recorder.mic_path
    finally:
        signal.signal(signal.SIGINT, previous)
        recorder.stop_file.unlink(missing_ok=True)
        for source in (system_source, mic_source):
            close = getattr(source, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
    return recorder.system_path, recorder.mic_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Befehlszeile des Helfers auswerten."""
    parser = argparse.ArgumentParser(
        description="Systemton und Standardmikrofon unter Windows getrennt aufnehmen.")
    parser.add_argument("--capture-audio", nargs=2, metavar=("SYSTEM", "MIKROFON"),
                        help="Zwei WAV-Ziele für Systemton und Standardmikrofon.")
    args = parser.parse_args(argv)
    if not args.capture_audio:
        parser.error("Bitte --capture-audio mit zwei WAV-Zielen angeben.")
    return args


def _pycaw_verfuegbar() -> bool:
    """Ob pycaw/comtypes importierbar sind (nur unter Windows relevant)."""
    if sys.platform != "win32":
        return False
    try:
        import comtypes.client  # noqa: F401
        import pycaw.pycaw  # noqa: F401
    except ImportError:
        return False
    return True


def _stopp_datei_bewachen(stop_file: Path, stop_event: threading.Event) -> None:
    """Die Stopp-Datei im 100-ms-Takt prüfen; existiert sie, endet die Aufnahme sauber."""
    while not stop_event.wait(0.1):
        if stop_file.exists():
            break
    stop_event.set()


def _main_ctypes(system_pfad: Path, mikrofon_pfad: Path, stop_event: threading.Event) -> int:
    """Beide Spuren per ctypes/WASAPI parallel aufnehmen (ohne pycaw/comtypes)."""
    fehler = FehlerKanal()
    system = WavSpur("Systemton", system_pfad)
    mikrofon = WavSpur("Mikrofon", mikrofon_pfad)
    spuren = [
        threading.Thread(target=spur_aufnehmen, daemon=True,
                         args=("Systemton", E_RENDER, True, system, fehler, stop_event)),
        threading.Thread(target=spur_aufnehmen, daemon=True,
                         args=("Mikrofon", E_CAPTURE, False, mikrofon, fehler, stop_event)),
    ]
    for spur in spuren:
        spur.start()
    sync: dict[str, int] = {}
    threading.Thread(target=synchronisieren, daemon=True,
                     args=(system, mikrofon, sync)).start()
    threading.Thread(target=status_melden, daemon=True,
                     args=(system, mikrofon, fehler, stop_event)).start()
    stop_event.wait()  # Stoppzeile auf stdin oder Stopp-Datei setzt das Event
    for spur in spuren:
        spur.join(timeout=ERSTE_SAMPLES_TIMEOUT)
    text = fehler.abfragen()
    if text:
        status_zeile({"error": text})
        return 1
    for name, spur in (("Systemton", system), ("Mikrofon", mikrofon)):
        spur.bilder -= vorspann_kuerzen(spur.pfad, sync.get(name, 0))
    status_zeile({"stopped": True,
                  "seconds": round(max(system.dauer(), mikrofon.dauer()), 3)})
    return 0


def main(argv: list[str] | None = None) -> int:
    """Beide Spuren aufnehmen; Stopp über die stdin-Zeile „stop“ oder die Stopp-Datei.

    Unter Windows ohne pycaw/comtypes läuft die Aufnahme über den ctypes-Weg
    (``_main_ctypes``), sonst über ``run_capture``; beide liefern dasselbe
    JSON-Statusprotokoll auf stdout.
    """
    args = parse_args(argv)
    system_pfad, mikrofon_pfad = (Path(p) for p in args.capture_audio)
    stop_file = mikrofon_pfad.with_suffix(".stop")
    stop_event = threading.Event()
    threading.Thread(target=stopp_bewachen, args=(stop_event,), daemon=True).start()
    threading.Thread(target=_stopp_datei_bewachen, args=(stop_file, stop_event), daemon=True).start()
    if sys.platform == "win32" and WINFUNCTYPE is not None and not _pycaw_verfuegbar():
        return _main_ctypes(system_pfad, mikrofon_pfad, stop_event)
    try:
        run_capture(system_pfad, mikrofon_pfad, stop_file=stop_file)
    except (CaptureSourceError, RuntimeError) as exc:
        emit_status({"error": str(exc)})
        return 1
    finally:
        stop_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())