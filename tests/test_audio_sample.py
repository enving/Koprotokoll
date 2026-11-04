"""Audio-Prüfskripte müssen Originale erhalten und Offline-Belege korrekt prüfen."""

import numpy as np
import pytest

from scripts import generate_audio_sample as sample


def test_composition_preserves_overlap_and_separate_voices(monkeypatch):
    monkeypatch.setattr(sample, "DIALOGUE", [("A", 1, "Erste Stimme"), ("B", -1, "Einwurf")])
    mixed, stems, reference = sample.compose([np.ones(20), np.ones(20)], sample_rate=10)
    # Überlagerte Stimmen dürfen nicht clippen; die Referenz muss beide enthalten.
    assert len(mixed) == 50
    assert np.allclose(mixed, stems["A"] + stems["B"])
    assert np.max(np.abs(mixed)) == pytest.approx(0.85)
    assert np.count_nonzero((stems["A"] != 0) & (stems["B"] != 0)) == 10
    assert [(t["start"], t["end"]) for t in reference["turns"]] == [(1, 3), (2, 4)]
    assert reference["human_reviewed"] is False


def test_sample_generation_never_overwrites_existing_directory(tmp_path, monkeypatch):
    original = tmp_path / "testgespraech.wav"
    original.write_bytes(b"vorhandenes Original")
    monkeypatch.setattr("sys.argv", ["generate_audio_sample.py", str(tmp_path)])
    with pytest.raises(FileExistsError):
        sample.main()
    assert original.read_bytes() == b"vorhandenes Original"


@pytest.mark.parametrize("error", [None, TimeoutError("Zeitüberschreitung"), ConnectionRefusedError("Abgewiesen")])
def test_offline_proof_rejects_allowed_connections_and_unspecific_errors(monkeypatch, error):
    from scripts.run_audio_sample import verify_network_block

    class ProbeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect(self, address):
            if error is not None:
                raise error

    monkeypatch.setattr("socket.socket", lambda *args: ProbeSocket())
    with pytest.raises(RuntimeError):
        verify_network_block()


def test_real_audio_import_keeps_filename_and_original(tmp_path, monkeypatch):
    import hashlib
    import json
    import soundfile as sf
    from scripts import run_audio_sample
    from koprotokoll.speech import Turn, Word

    class TestTranscriber:
        def __init__(self, *args):
            pass

        def transcribe(self, audio, progress):
            return [Word(0, 0.5, "Hallo.")]

    class TestDiarizer:
        def __init__(self, *args):
            pass

        def diarize(self, audio, num_speakers, progress):
            return [Turn(0, 0.5, 0)]

    audio = tmp_path / "echte-aufnahme.wav"
    sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000)
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    models = tmp_path / "models"
    models.mkdir()
    output = tmp_path / "run"
    monkeypatch.setattr(run_audio_sample, "create_transcriber", lambda settings: TestTranscriber())
    monkeypatch.setattr(run_audio_sample, "SherpaDiarizer", TestDiarizer)
    monkeypatch.setattr(run_audio_sample.sys, "addaudithook", lambda hook: None)
    monkeypatch.setattr("sys.argv", ["run_audio_sample.py", str(audio), str(output), "--models", str(models)])
    run_audio_sample.main()
    result = json.loads((output / "ergebnis.json").read_text())
    assert result["title"] == audio.stem
    assert result["meta"]["diarization"]["status"] == "completed"
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == digest
