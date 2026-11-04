"""Künstliche Testdaten müssen reproduzierbar, übersteuerungsfrei und getrennt bleiben."""

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
