"""WASAPI-Loopback (Systemton) und Standardmikrofon unter Windows als zwei getrennte WAV-Spuren.

Hilfsprozess der Aufnahme-API, gestartet von ``capture.HardwareRecorder``:

    python -m koprotokoll.capture_windows --capture-audio system.wav mikrofon.wav

Der Status läuft als JSON-Zeilen auf stdout (Dasselbe Protokoll wie der
macOS-Helper ``capture_macos.swift``): ``{"ready": true}``, ``{"level_db": …}``
und ``{"error": "…"}``. SIGINT beendet die Aufnahme sauber mit Exit-Code 0;
die fertigen Spuren übernimmt danach ``capture.HardwareRecorder.stop()``, das
sie auf Vollständigkeit prüft und mischt. Es wird kein Bildschirm aufgenommen.

Die WASAPI-Quellen brauchen ``pycaw``/``comtypes`` (nur Windows, siehe
requirements-windows.txt). Der Schreib- und Statusteil ist plattformunabhängig
und ohne Zusatzpakete testbar.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import struct
import sys
import time
import wave
from array import array
from pathlib import Path

CHANNELS = 2
SAMPLE_RATE = 48_000
FRAME_BYTES = CHANNELS * 2
CHUNK_FRAMES = 4096


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="koprotokoll.capture_windows",
                                     description="Systemton (WASAPI-Loopback) und Mikrofon als getrennte WAV-Spuren.")
    parser.add_argument("--capture-audio", nargs=2, required=True, metavar=("SYSTEM.wav", "MIKROFON.wav"))
    args = parser.parse_args(argv)
    system_path, mic_path = (Path(p) for p in args.capture_audio)
    try:
        run_capture(system_path, mic_path)
    except (CaptureSourceError, RuntimeError) as exc:
        emit_status({"error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
