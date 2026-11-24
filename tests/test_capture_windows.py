"""WASAPI-Helper unter Windows: Dualspur-Schreiber und Statusprotokoll ohne pycaw testen."""

import io
import json
import math
import struct
import wave
from pathlib import Path

import pytest

from koprotokoll import capture_windows
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
    assert commands[0][:4] == [sys.executable, "-m", "koprotokoll.capture_windows", "--capture-audio"]
    assert commands[0][4].endswith(".system.wav")
    assert commands[0][5].endswith(".microphone.wav")


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
