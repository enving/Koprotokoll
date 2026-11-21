"""Fünf-Minuten-Grenze ohne echte Wartezeit und Sprachprüfung ohne Download."""

import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from koprotokoll.activity import SilenceTimer
from koprotokoll.capture import pcm_tail
from koprotokoll.config import Settings
from koprotokoll.app import create_app
from test_koprotokoll import FakeTranscriber, NoLLM, wav_bytes


def test_silence_timer_resets_on_speech_and_detector_failure():
    timer = SilenceTimer(10)
    assert not timer.check(False, 309)
    assert timer.check(False, 310)
    assert not timer.check(True, 310)
    assert not timer.check(False, 609)
    assert not timer.check(None, 610)
    assert not timer.check(False, 909)
    assert timer.check(False, 910)


def test_pcm_tail_reads_growing_wav_without_trusting_frame_count(tmp_path):
    path = tmp_path / 'growing.wav'
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        output.writeframes(np.full((48000 * 3, 2), 16000, dtype='<i2').tobytes())
    raw = bytearray(path.read_bytes())
    raw[40:44] = b'\0' * 4  # Der Dateischreiber hat die Länge noch nicht aktualisiert.
    path.write_bytes(raw)
    tail = pcm_tail(path, 2, 48000)
    assert tail.shape == (32000,)
    assert np.allclose(tail, 16000 / 32768)
    assert pcm_tail(tmp_path / 'missing.wav', 2, 48000) is None


@pytest.mark.parametrize('sample_rate', [16000, 24000, 44100, 48000])
@pytest.mark.parametrize('channels', [1, 2])
def test_pcm_tail_converts_every_supported_capture_rate(tmp_path, sample_rate, channels):
    path = tmp_path / 'capture.wav'
    for signal in [np.zeros(sample_rate * 3),
                   0.5 * np.sin(2 * np.pi * 1000 * np.arange(sample_rate * 3) / sample_rate)]:
        pcm = np.repeat((signal * 32767).astype('<i2')[:, None], channels, axis=1)
        with wave.open(str(path), 'wb') as output:
            output.setparams((channels, 2, sample_rate, 0, 'NONE', 'not compressed'))
            output.writeframes(pcm.tobytes())
        tail = pcm_tail(path, channels, sample_rate)
        assert tail is not None and tail.shape == (32000,)
        assert tail.dtype == np.float32
        if np.any(signal):
            assert np.max(np.abs(tail)) > 0.45
            assert np.argmax(np.abs(np.fft.rfft(tail))) * 16000 / len(tail) == 1000
        else:
            assert not np.any(tail)


def test_activity_endpoint_accepts_pcm_and_rejects_invalid_audio(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, diarization=False),
                     transcriber=FakeTranscriber(), llm=NoLLM(), background=False)
    monkeypatch.setattr(app.state.speech_detector, 'has_speech', lambda audio: bool(np.max(np.abs(audio)) > 0.1))
    with TestClient(app) as client:
        for value, speech in [(0, False), (16000, True)]:
            audio = np.full(16000, value, dtype='<i2').tobytes()
            response = client.post('/api/capture/activity', files={'file': ('audio.pcm', audio)})
            assert response.status_code == 200 and response.json()['speech'] is speech
            assert response.json()['silence_seconds'] == 300
        for audio in [b'', b'x', bytes(64002)]:
            assert client.post('/api/capture/activity', files={'file': ('audio.pcm', audio)}).status_code == 400


def test_hardware_auto_stop_saves_once_and_does_not_stop_next_capture(tmp_path, monkeypatch):
    from koprotokoll import app as app_mod

    clock = [0.0]
    speech = [False]
    calls = []

    class Recorder:
        active = False

        def __init__(self, path):
            pass

        def start(self, *args):
            self.active = True
            return {'active': True}

        def status(self):
            return {'active': self.active}

        def speech_audio(self):
            return [np.zeros(32000, dtype=np.float32)]

        def stop(self):
            calls.append('stop')
            self.active = False
            path = tmp_path / 'capture.wav'
            path.write_bytes(wav_bytes(tmp_path, seconds=1))
            return path, 1, 'Testmikrofon'

    monkeypatch.setattr(app_mod, 'HardwareRecorder', Recorder)
    app = create_app(Settings(data_dir=tmp_path, diarization=False),
                     transcriber=FakeTranscriber(), llm=NoLLM(), background=False)
    monkeypatch.setattr(app.state.speech_detector, 'has_speech', lambda audio: speech[0])
    app.state.capture_clock = lambda: clock[0]
    with TestClient(app) as client:
        client.post('/api/capture/start', json={'device': 'test', 'language': 'de'})
        previous = app.state.capture_request
        clock[0] = 299
        app.state.check_capture_silence(previous)
        assert client.get('/api/capture').json()['active']
        speech[0] = True
        clock[0] = 300
        app.state.check_capture_silence(previous)
        speech[0] = False
        clock[0] = 599
        app.state.check_capture_silence(previous)
        assert calls == []
        clock[0] = 600
        def failed_detector(audio):
            raise RuntimeError('Testfehler')
        monkeypatch.setattr(app.state.speech_detector, 'has_speech', failed_detector)
        app.state.check_capture_silence(previous)
        assert calls == [] and 'nicht verfügbar' in client.get('/api/capture').json()['warning']
        monkeypatch.setattr(app.state.speech_detector, 'has_speech', lambda audio: False)
        clock[0] = 899
        app.state.check_capture_silence(previous)
        assert calls == []
        clock[0] = 900
        app.state.check_capture_silence(previous)
        app.state.pipeline.join()
        status = client.get('/api/capture').json()
        assert status['active'] is False and status['stop_reason'] == 'silence'
        result = client.get(f"/api/meetings/{status['meeting_id']}").json()
        assert result['status'] == 'done' and result['meta']['stop_reason'] == 'silence'
        client.post('/api/capture/start', json={'device': 'test'})
        app.state.check_capture_silence(previous)
        assert client.get('/api/capture').json()['active'] and calls == ['stop']
        client.post('/api/capture/stop')
