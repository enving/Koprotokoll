"""Primeline-Audiovorbereitung ohne Modell oder Netzwerk prüfen."""

from types import SimpleNamespace

import numpy as np
import pytest

from koprotokoll.audio import SAMPLE_RATE
from koprotokoll.config import Settings
from koprotokoll.speech import (PRIMELINE_FILES, PRIMELINE_REVISION, ParakeetTranscriber,
                               SherpaParakeetTranscriber, create_transcriber,
                               ensure_primeline_model, split_audio, Turn, speaker_for)


def test_chunks_preserve_samples_and_choose_pause():
    audio = np.full(190 * SAMPLE_RATE, 0.1, dtype=np.float32)
    audio[80 * SAMPLE_RATE:81 * SAMPLE_RATE] = 0
    chunks = list(split_audio(audio))
    assert all(0 < len(part) <= 90 * SAMPLE_RATE for _, part in chunks)
    assert 80 * SAMPLE_RATE <= chunks[1][0] <= 81 * SAMPLE_RATE
    assert [offset for offset, _ in chunks] == [0] + list(np.cumsum([len(c) for _, c in chunks[:-1]]))
    np.testing.assert_array_equal(np.concatenate([part for _, part in chunks]), audio)


def test_model_download_pins_revision_and_all_files(tmp_path, monkeypatch):
    calls = []

    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        for filename in kwargs["allow_patterns"]:
            (tmp_path / filename).write_bytes(b"modell")

    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    assert ensure_primeline_model(tmp_path) == tmp_path
    ensure_primeline_model(tmp_path)
    assert len(calls) == 1
    assert calls[0][1]["revision"] == PRIMELINE_REVISION
    assert set(calls[0][1]["allow_patterns"]) == set(PRIMELINE_FILES)


def test_factory_preserves_v3_choice(tmp_path):
    assert isinstance(create_transcriber(Settings(data_dir=tmp_path, asr_model="parakeet-primeline", asr_language="de")),
                      SherpaParakeetTranscriber)
    assert isinstance(create_transcriber(Settings(data_dir=tmp_path, asr_model="nemo-parakeet-tdt-0.6b-v3")),
                      ParakeetTranscriber)


def test_retry_gain_and_global_word_timestamps(tmp_path):
    seen = []

    class Recognizer:
        def create_stream(self):
            def accept(sr, audio):
                assert sr == SAMPLE_RATE
                seen.append(audio.copy())
            return SimpleNamespace(accept_waveform=accept)

        def decode_stream(self, stream):
            if len(seen[-1]) > 20 * SAMPLE_RATE:
                stream.result = SimpleNamespace(tokens=[], timestamps=[], text="")
            else:
                stream.result = SimpleNamespace(tokens=["▁Hal", "lo", "▁Welt"],
                                                timestamps=[0.1, 0.2, 0.5], text="Hallo Welt")

    asr = SherpaParakeetTranscriber(tmp_path)
    asr._recognizer = Recognizer()
    progress = []
    audio = np.full(100 * SAMPLE_RATE, 0.01, dtype=np.float32)
    original = audio.copy()
    words = asr.transcribe(audio, progress.append)
    assert words and words[0].text == "Hallo" and words[1].text == "Welt"
    assert max(w.start for w in words) > 80
    assert all(a.end <= b.start for a, b in zip(words, words[1:]))
    assert all(0 <= w.start <= w.end <= 100 for w in words)
    assert max(float(np.abs(part).max()) for part in seen) == pytest.approx(0.3)
    np.testing.assert_array_equal(audio, original)
    assert progress == sorted(progress) and progress[-1] == 1


def test_empty_input_never_loads_model(tmp_path):
    progress = []
    asr = SherpaParakeetTranscriber(tmp_path)
    assert asr.transcribe(np.array([], dtype=np.float32), progress.append) == []
    assert progress == [1]
    assert asr._recognizer is None


def test_last_word_ends_before_silence_and_next_speaker(tmp_path):
    class Recognizer:
        def create_stream(self):
            return SimpleNamespace(accept_waveform=lambda sr, audio: None)

        def decode_stream(self, stream):
            stream.result = SimpleNamespace(tokens=["▁Hallo"], timestamps=[0.1], durations=[0.3], text="Hallo")

    asr = SherpaParakeetTranscriber(tmp_path)
    asr._recognizer = Recognizer()
    words = asr.transcribe(np.full(90 * SAMPLE_RATE, 0.1, dtype=np.float32), lambda _: None)
    assert words[0].end == pytest.approx(0.4)
    assert speaker_for(words[0], [Turn(0, 1, 0), Turn(60, 90, 1)]) == 0
