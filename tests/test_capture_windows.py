"""Windows-Aufnahme-Helfer ohne echtes Audiogerät prüfen.

Vereint beide Testsuiten: die pycaw-Variante (``DualTrackRecorder``/``run_capture``,
Stopp-Datei, Integration in ``capture.HardwareRecorder``) und die ctypes-Variante
(``WavSpur``/``format_lesen``/``lese_paket``/``vorspann_kuerzen``/``synchronisieren``,
Stoppzeile auf stdin).
"""

import ctypes
import io
import json
import math
import struct
import threading
import time
import wave
from ctypes import c_void_p
from pathlib import Path

import numpy as np
import pytest

from koprotokoll import capture_windows
from koprotokoll import capture_windows as cw
from koprotokoll.capture_windows import (
    CaptureSourceError,
    DualTrackRecorder,
    WavTrackWriter,
    emit_status,
    run_capture,
)

class FakeSource:
    """PCM16-Quelle: Sinus oder Stille, danach Stream-Ende (None)."""

    def __init__(self, frames: int, amplitude: int = 8000, chunk_frames: int = 1024, fail_at: int | None = None):
        self.remaining = frames
        self.amplitude = amplitude
        self.chunk_frames = chunk_frames
        self.fail_at = fail_at
        self.served = 0

    def read(self, max_bytes: int) -> bytes | None:
        if self.fail_at is not None and self.served >= self.fail_at:
            raise RuntimeError("Gerät wurde abgezogen")
        if self.remaining <= 0:
            return None
        frames = min(self.chunk_frames, self.remaining, max(1, max_bytes // 4))
        self.remaining -= frames
        self.served += frames
        samples = [int(self.amplitude * math.sin(2 * math.pi * 440 * (self.served - frames + i) / 48000))
                   for i in range(frames * 2)]
        return struct.pack(f"<{len(samples)}h", *samples)


def read_status(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_dualspur_schreibt_zwei_ausgerichtete_wavs(tmp_path):
    stream = io.StringIO()
    system_path = tmp_path / "system.wav"
    mic_path = tmp_path / "mikrofon.wav"
    system, mic = run_capture(system_path, mic_path,
                              sources=(FakeSource(48_000, amplitude=9000), FakeSource(48_000, amplitude=3000)),
                              stream=stream)
    assert (system, mic) == (system_path, mic_path)
    for path in (system_path, mic_path):
        with wave.open(str(path), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (2, 2, 48_000)
            assert audio.getnframes() == 48_000
    status = read_status(stream)
    assert status[0] == {"ready": True}


def test_dualspur_meldet_laufenden_pegel(tmp_path):
    stream = io.StringIO()
    recorder = DualTrackRecorder(FakeSource(48_000, amplitude=9000), FakeSource(48_000, amplitude=3000),
                                 tmp_path / "s.wav", tmp_path / "m.wav", stream=stream)
    recorder.run(poll_seconds=0)
    status = read_status(stream)
    assert status[0] == {"ready": True}
    levels = [entry["level_db"] for entry in status if "level_db" in entry]
    assert levels and max(levels) == pytest.approx(20 * math.log10(9000 / 32768), abs=0.1)


def test_dualspur_pegel_und_dauer_des_schreibers(tmp_path):
    writer = WavTrackWriter(tmp_path / "spur.wav")
    chunk = struct.pack("<4h", 16000, -16000, 0, 8000)
    assert writer.write(chunk + b"\x01") == 8  # ungerades Byte wird abgeschnitten
    writer.close()
    assert writer.frames == 2
    assert writer.duration() == pytest.approx(2 / 48_000)
    assert writer.level_db == pytest.approx(20 * math.log10(16000 / 32768), abs=0.01)


def test_dualspur_meldet_quellenfehler_und_beendet_mit_fehler(tmp_path):
    stream = io.StringIO()
    recorder = DualTrackRecorder(FakeSource(48_000), FakeSource(48_000, fail_at=2048),
                                 tmp_path / "system.wav", tmp_path / "mikrofon.wav", stream=stream)
    with pytest.raises(CaptureSourceError, match="Mikrofon-Quelle meldete einen Fehler"):
        recorder.run(poll_seconds=0.01)
    assert any("Fehler" in entry.get("error", "") for entry in read_status(stream))


def test_dualspur_prueft_vollstaendigkeit_und_gleichstand(tmp_path):
    stream = io.StringIO()
    short_mic = DualTrackRecorder(FakeSource(48_000), FakeSource(8_000),
                                  tmp_path / "s.wav", tmp_path / "m.wav", stream=stream)
    with pytest.raises(CaptureSourceError, match="unterschiedlich lang"):
        short_mic.run(poll_seconds=0.01)
    early_end = DualTrackRecorder(FakeSource(0), FakeSource(1000),
                                  tmp_path / "s2.wav", tmp_path / "m2.wav", stream=io.StringIO())
    with pytest.raises(CaptureSourceError, match="endet vor der anderen"):
        early_end.run(poll_seconds=0.01)
    empty = DualTrackRecorder(FakeSource(0), FakeSource(0),
                              tmp_path / "s3.wav", tmp_path / "m3.wav", stream=io.StringIO())
    with pytest.raises(CaptureSourceError, match="keine Samples"):
        empty.run(poll_seconds=0.01)


def test_run_capture_ueber_main_mit_gefakten_quellen(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture_windows, "wasapi_sources",
                        lambda: (FakeSource(4800), FakeSource(4800)))
    code = capture_windows.main(["--capture-audio", str(tmp_path / "s.wav"), str(tmp_path / "m.wav")])
    assert code == 0
    for name in ("s.wav", "m.wav"):
        with wave.open(str(tmp_path / name), "rb") as audio:
            assert audio.getnframes() == 4800
    assert {"ready": True} in read_status(io.StringIO(capsys.readouterr().out))


def test_run_capture_main_fehler_gibt_exit_1(tmp_path, monkeypatch, capsys):
    def broken():
        raise RuntimeError("pycaw/comtypes fehlen")

    monkeypatch.setattr(capture_windows, "wasapi_sources", broken)
    code = capture_windows.main(["--capture-audio", str(tmp_path / "s.wav"), str(tmp_path / "m.wav")])
    assert code == 1
    assert "pycaw" in capsys.readouterr().out


def test_emit_status_ist_eine_json_zeile():
    stream = io.StringIO()
    emit_status({"level_db": -30.5}, stream)
    assert json.loads(stream.getvalue()) == {"level_db": -30.5}


# --- Anbindung an die vorhandene Aufnahme-API (capture.HardwareRecorder) ---

def test_windows_systemton_startet_python_hilfsprozess(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import koprotokoll.capture as capture

    commands = []

    class FakeProc:
        def __init__(self, command):
            commands.append(command)
            self.stdout = iter([json.dumps({"ready": True}).encode()])
            self.returncode = 0

        def poll(self):
            return None

        def wait(self, **kwargs):
            return 0

        def send_signal(self, sig):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture.subprocess, "Popen", lambda command, **kwargs: FakeProc(command))
    recorder = capture.HardwareRecorder(tmp_path)
    status = recorder.start(capture.SYSTEM_AUDIO_DEVICE)
    assert status["active"] is True
    assert commands[0][0] == sys.executable
    assert commands[0][1].endswith("capture_windows.py")
    assert commands[0][2] == "--capture-audio"
    assert commands[0][3].endswith(".system.wav")
    assert commands[0][4].endswith(".microphone.wav")


def test_windows_geraeteliste_enthält_wasapi_systemton(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout='[dshow @ 0] "USB-Mikrofon" (audio)\n', stderr="", returncode=0))
    recorder = capture.HardwareRecorder(tmp_path)
    result = recorder.devices()
    assert result["backend"] == "dshow"
    assert result["devices"][0] == {"id": capture.SYSTEM_AUDIO_DEVICE,
                                    "name": "Systemton + Standardmikrofon (Windows WASAPI-Loopback)"}
    assert result["devices"][1]["name"] == "USB-Mikrofon"


def test_linux_systemton_bleibt_abgelehnt(tmp_path, monkeypatch):
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.sys, "platform", "linux")
    recorder = capture.HardwareRecorder(tmp_path)
    with pytest.raises(ValueError, match="macOS und Windows"):
        recorder.start(capture.SYSTEM_AUDIO_DEVICE)


class EndlessSource:
    """Endlose PCM-Quelle (wie WASAPI ohne Stopp)."""

    def read(self, max_bytes: int) -> bytes:
        return b"\x01\x00\x02\x00" * (max_bytes // 4)


def test_stoppdatei_beendet_aufnahme_sauber_und_wird_entfernt(tmp_path):
    import threading
    import time

    stop = tmp_path / "m.stop"

    def touch():
        time.sleep(0.2)
        stop.touch()

    threading.Thread(target=touch, daemon=True).start()
    system, mic = run_capture(tmp_path / "s.wav", tmp_path / "m.wav",
                              sources=(EndlessSource(), EndlessSource()))
    assert not stop.exists()
    frames = []
    for path in (system, mic):
        with wave.open(str(path), "rb") as audio:
            frames.append(audio.getnframes())
    assert frames[0] > 0 and abs(frames[0] - frames[1]) <= 24_000  # Höchstens 0,5 s Versatz


def test_windows_stopp_legt_stoppdatei_an_und_ueberlebt_sigint_valueerror(tmp_path, monkeypatch):
    import sys
    import time
    from types import SimpleNamespace

    import koprotokoll.capture as capture

    class FakeProc:
        returncode = 0

        def poll(self):
            return None

        def send_signal(self, sig):
            raise ValueError("Unsupported signal: 2")  # Windows ohne gemeinsame Konsole

        def wait(self, **kwargs):
            return 0

        def kill(self):
            pass

    system = tmp_path / "a.system.wav"
    mic = tmp_path / "a.microphone.wav"
    mixed = tmp_path / "a.wav"
    for path in (system, mic, mixed):
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\x00\x00\x00\x00" * 4_800)
    recorder = capture.HardwareRecorder(tmp_path)
    recorder._active = capture.ActiveCapture(
        device=capture.SYSTEM_AUDIO_DEVICE, path=mixed, process=FakeProc(), channels=2,
        sample_rate=48_000, started_at=time.time(), system_path=system, mic_path=mic)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "_validate_stem_durations", lambda s, m: (0.1, 0.1))
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stderr=""))
    path, duration, device = recorder.stop()
    assert path == mixed and duration == pytest.approx(0.1) and device == capture.SYSTEM_AUDIO_DEVICE
    # Die Stopp-Datei ist der Handshake an den Windows-Helper (der sie beim Beenden entfernt).
    assert mic.with_suffix(".stop").exists()


_gehalten: list = []  # ctypes-Strukturen am Leben halten, solange ihre Zeiger gelesen werden.


def _format(tag: int, bits: int, channels: int = 2, rate: int = 48_000) -> c_void_p:
    kopf = cw.WAVEFORMATEX()
    kopf.wFormatTag = tag
    kopf.nChannels = channels
    kopf.nSamplesPerSec = rate
    kopf.wBitsPerSample = bits
    _gehalten.append(kopf)
    return ctypes.cast(ctypes.byref(kopf), c_void_p)


def _extensible(subformat: cw.GUID, bits: int) -> c_void_p:
    erweitert = cw.WAVEFORMATEXTENSIBLE()
    erweitert.Format.wFormatTag = cw.WAVE_FORMAT_EXTENSIBLE
    erweitert.Format.nChannels = 2
    erweitert.Format.nSamplesPerSec = 48_000
    erweitert.Format.wBitsPerSample = bits
    erweitert.SubFormat = subformat
    _gehalten.append(erweitert)
    return ctypes.cast(ctypes.byref(erweitert), c_void_p)


def test_pegel_db_begruendet_stille_und_rundet():
    assert cw.pegel_db(0.0) == -120.0
    assert cw.pegel_db(0.5) == -6.0
    assert cw.pegel_db(0.9) == -0.9


def test_zu_pcm16_begrenzt_uebersteuerung():
    feld = np.array([[0.5], [-2.0]], dtype=np.float32)
    erwartet = (np.clip(feld, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    assert cw.zu_pcm16(feld) == erwartet


def test_wav_spur_schreibt_liest_und_begrenzt_spitzenpegel(tmp_path):
    spur = cw.WavSpur("Systemton", tmp_path / "system.wav")
    spur.starten(48_000, 2)
    spur.schreibe(np.array([[0.25, -0.5], [0.125, 0.0]], dtype=np.float32))
    assert spur.pegel_und_reset() == pytest.approx(0.5)
    assert spur.pegel_und_reset() == 0.0
    assert spur.dauer() == pytest.approx(2 / 48_000)
    spur.schliessen()
    with wave.open(str(tmp_path / "system.wav"), "rb") as datei:
        assert (datei.getframerate(), datei.getnchannels(), datei.getsampwidth()) == (48_000, 2, 2)
        assert datei.getnframes() == 2
    with pytest.raises(RuntimeError, match="geschlossen"):
        spur.schreibe(np.zeros((1, 2), dtype=np.float32))


def test_format_lesen_erkennt_float32_und_pcm16():
    assert cw.format_lesen("Systemton", _format(cw.WAVE_FORMAT_IEEE_FLOAT, 32)) == (2, 48_000, "float32")
    assert cw.format_lesen("Mikrofon", _format(1, 16, channels=1, rate=44_100)) == (1, 44_100, "int16")
    assert cw.format_lesen("Mikrofon", _extensible(cw.KSDATAFORMAT_SUBTYPE_PCM, 16)) == (2, 48_000, "int16")
    assert cw.format_lesen("Systemton", _extensible(cw.KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, 32)) == (2, 48_000, "float32")


def test_format_lesen_lehnt_unterstuetzte_formate_ab():
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Systemton", _format(1, 24))
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Systemton", _extensible(cw.KSDATAFORMAT_SUBTYPE_PCM, 32))
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Mikrofon", _format(7, 16))


def test_lese_paket_wandelt_pcm16_mit_vorzeichen_und_stille():
    werte = np.array([-16384, 16384, 0, 1, -1, 32767, -32768, 2], dtype="<i2")
    feld = cw.lese_paket("Mikrofon", "int16", werte.ctypes.data_as(c_void_p), 4, 2, False)
    assert feld[:, 0].tolist() == pytest.approx([-0.5, 0.0, -1 / 32768, -1.0])
    assert feld[0, 1] == pytest.approx(0.5)
    stille = cw.lese_paket("Mikrofon", "int16", c_void_p(), 4, 2, True)
    assert stille.shape == (4, 2) and not stille.any()


def test_lese_paket_wandelt_float32_und_verwirft_leere_pakete():
    feld32 = np.array([[0.25, -0.125], [0.0, 0.5]], dtype=np.float32)
    feld = cw.lese_paket("Systemton", "float32", feld32.ctypes.data_as(c_void_p), 2, 2, False)
    assert feld.tolist() == [[0.25, -0.125], [0.0, 0.5]]
    leer = cw.lese_paket("Systemton", "float32", c_void_p(), 0, 2, False)
    assert leer.shape == (0, 2)


def test_stoppzeile_oder_eof_beendet_bewachung(monkeypatch):
    stop_event = threading.Event()
    monkeypatch.setattr(cw.sys, "stdin", io.StringIO("stop\n"))
    cw.stopp_bewachen(stop_event)
    assert stop_event.is_set()

    stop_event = threading.Event()
    monkeypatch.setattr(cw.sys, "stdin", io.StringIO(""))
    cw.stopp_bewachen(stop_event)
    assert stop_event.is_set()


def test_befehlszeile_verlangt_zwei_ziele():
    with pytest.raises(SystemExit):
        cw.parse_args([])


def test_fehler_kanal_behaelt_nur_den_ersten_text():
    fehler = cw.FehlerKanal()
    fehler.melden("erst")
    fehler.melden("zweit")
    assert fehler.abfragen() == "erst"


def test_status_zeilen_protokoll_meldet_bereitschaft_und_pegel(monkeypatch, capsys):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.02)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    system.erste.set()
    mikrofon.erste.set()
    time.sleep(0.12)
    stop_event.set()
    thread.join(timeout=2)
    zeilen = [json.loads(zeile) for zeile in capsys.readouterr().out.splitlines()]
    assert {"ready": True} in zeilen
    assert {"level_db": -120.0} in zeilen


def test_status_meldet_bereitschaft_erst_nach_beiden_spuren(monkeypatch, capsys):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.02)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    system.erste.set()  # Nur eine Spur hat geliefert; „ready" bleibt aus.
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    time.sleep(0.1)
    stop_event.set()
    thread.join(timeout=2)
    zeilen = [json.loads(zeile) for zeile in capsys.readouterr().out.splitlines()]
    assert zeilen and not any(zeile == {"ready": True} for zeile in zeilen)


def test_status_melden_endet_bei_spurfehler(monkeypatch):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.01)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    fehler.melden("Systemton: kein Gerät")
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_vorspann_kuerzen_entfernt_anfangsbilder(tmp_path):
    pfad = tmp_path / "spur.wav"
    with wave.open(str(pfad), "wb") as datei:
        datei.setnchannels(2)
        datei.setsampwidth(2)
        datei.setframerate(48_000)
        datei.writeframes(b"".join(struct.pack("<hh", i, -i) for i in range(10)))
    assert cw.vorspann_kuerzen(pfad, 3) == 3
    with wave.open(str(pfad), "rb") as datei:
        assert (datei.getframerate(), datei.getnchannels(), datei.getnframes()) == (48_000, 2, 7)
        assert struct.unpack("<hh", datei.readframes(1)) == (3, -3)


def test_vorspann_kuerzen_ist_bei_null_und_zu_grossem_wert_harmlos(tmp_path):
    pfad = tmp_path / "spur.wav"
    with wave.open(str(pfad), "wb") as datei:
        datei.setnchannels(1)
        datei.setsampwidth(2)
        datei.setframerate(44_100)
        datei.writeframes(struct.pack("<hh", 1, 2))
    assert cw.vorspann_kuerzen(pfad, 0) == 0
    assert cw.vorspann_kuerzen(pfad, 5) == 0
    assert cw.vorspann_kuerzen(tmp_path / "fehlt.wav", 3) == 0
    with wave.open(str(pfad), "rb") as datei:
        assert datei.getnframes() == 2


def test_synchronisieren_haelt_stand_beider_spuren_fest(tmp_path):
    system = cw.WavSpur("Systemton", tmp_path / "system.wav")
    mikrofon = cw.WavSpur("Mikrofon", tmp_path / "mikrofon.wav")
    system.bilder = 5
    mikrofon.bilder = 2
    sync: dict[str, int] = {}
    thread = threading.Thread(target=cw.synchronisieren,
                              args=(system, mikrofon, sync), daemon=True)
    thread.start()
    time.sleep(0.05)  # Ohne Bereitschaft wartet die Synchronisierung noch.
    assert sync == {}
    system.erste.set()
    mikrofon.erste.set()
    thread.join(timeout=2)
    assert sync == {"Systemton": 5, "Mikrofon": 2}