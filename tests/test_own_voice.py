"""Automatische Ich-Klassifikation: eine dominante Stimme wird als „Ich“ erkannt."""

from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.speech import Turn, dominant_speaker
from test_koprotokoll import FakeDiarizer, FakeTranscriber, NoLLM, wav_bytes


def test_dominant_speaker_shares():
    assert dominant_speaker([Turn(0, 9, 0), Turn(9, 10, 1)]) == (0, 0.9)
    assert dominant_speaker([]) == (None, 0.0)
    # Gleichstand: die zuerst erkannte Stimme gewinnt.
    assert dominant_speaker([Turn(0, 5, 0), Turn(5, 10, 1)])[0] == 0


class DominantDiarizer:
    """95 % eine Stimme, 5 % eine zweite – typisch für ein Sprachmemo."""

    def diarize(self, audio, num_speakers, progress):
        progress(1.0)
        return [Turn(0, 22.5, 0), Turn(22.5, 25, 1)]


class SingleDiarizer:
    def diarize(self, audio, num_speakers, progress):
        progress(1.0)
        return [Turn(0, 25, 0)]


def _make(tmp_path, diarizer, **settings):
    return create_app(Settings(data_dir=tmp_path, **settings), transcriber=FakeTranscriber(),
                      diarizer=diarizer, llm=NoLLM(), background=False)


def _upload(client, tmp_path, name="memo.wav", **data):
    return client.post("/api/meetings",
                       files={"file": (name, wav_bytes(tmp_path), "audio/wav")}, data=data).json()["id"]


def test_dominant_voice_collapses_to_ich_and_is_reversible(tmp_path):
    app = _make(tmp_path, DominantDiarizer())
    with TestClient(app) as client:
        mid = _upload(client, tmp_path)
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{mid}").json()
        assert result["speakers"] == {"S1": "Ich"}
        assert {s["speaker"] for s in result["segments"]} == {"S1"}
        assert result["num_speakers"] == 1
        own = result["meta"]["diarization"]["own_voice"]
        assert own["status"] == "collapsed" and own["speakers_before"] == 2
        assert 0.8 <= own["share"] <= 1.0
        assert "speaker_correction" in result["meta"]
        # Rückgängig über den bestehenden Wiederherstellungs-Endpunkt.
        assert client.delete(f"/api/meetings/{mid}/single-speaker").status_code == 200
        restored = client.get(f"/api/meetings/{mid}").json()
        assert restored["num_speakers"] is None  # Modus „Automatisch“
        assert restored["meta"]["diarization"]["speaker_count"] == 2
        assert {s["speaker"] for s in restored["segments"]} == {"S1", "S2"}
        assert "own_voice" not in restored["meta"]["diarization"]
        assert "speaker_correction" not in restored["meta"]


def test_single_detected_voice_is_named_ich(tmp_path):
    app = _make(tmp_path, SingleDiarizer())
    with TestClient(app) as client:
        mid = _upload(client, tmp_path)
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{mid}").json()
        assert result["speakers"] == {"S1": "Ich"}
        assert result["meta"]["diarization"]["own_voice"]["status"] == "single"
        assert result["meta"]["diarization"]["speaker_count"] == 1


def test_balanced_two_speakers_stay_separate(tmp_path):
    app = _make(tmp_path, FakeDiarizer())
    with TestClient(app) as client:
        mid = _upload(client, tmp_path, name="meeting.wav")
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{mid}").json()
        assert result["speakers"] == {"S1": "Sprecher 1", "S2": "Sprecher 2"}
        assert "own_voice" not in result["meta"]["diarization"]


def test_explicit_speaker_count_is_not_collapsed(tmp_path):
    app = _make(tmp_path, DominantDiarizer())
    with TestClient(app) as client:
        mid = _upload(client, tmp_path, num_speakers="2")
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{mid}").json()
        assert result["num_speakers"] == 2
        assert "own_voice" not in result["meta"]["diarization"]


def test_share_threshold_is_configurable(tmp_path):
    app = _make(tmp_path, DominantDiarizer(), dominant_speaker_share=0.99)
    with TestClient(app) as client:
        mid = _upload(client, tmp_path)
        app.state.pipeline.join()
        result = client.get(f"/api/meetings/{mid}").json()
        assert len(result["speakers"]) == 2  # nicht zusammengefasst
        assert "own_voice" not in result["meta"]["diarization"]
