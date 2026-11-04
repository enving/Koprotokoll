"""Zeitreihen, Namensschutz und Browser-Brücke für Plattform-Sprecherhinweise."""

import json
from types import SimpleNamespace

import pytest

from koprotokoll.bot.platforms import Adapter, GoogleMeet
from koprotokoll.bot.runner import BotSession, CAPTURE_JS
from koprotokoll.config import Settings
from koprotokoll.db import Database
from koprotokoll.speaker_names import names_from_activity
from test_integrations import _chrome_path


def activity(names, start=0, end=4):
    return [{"time": step / 2, "names": names} for step in range(int(start * 2), int(end * 2))]


@pytest.mark.parametrize("samples,segments,expected", [
    (activity(["Anna"]), [{"start": 0, "end": 4, "speaker": "S1"}], {"S1": "Anna"}),
    (activity(["Anna"]) + activity(["Ben"], 4, 8),
     [{"start": 0, "end": 4, "speaker": "S1"}, {"start": 4, "end": 8, "speaker": "S2"}],
     {"S1": "Anna", "S2": "Ben"}),
    (activity(["Anna", "Ben"]), [{"start": 0, "end": 4, "speaker": "S1"}], {}),
    (activity(["Anna"], 0, 2) + activity(["Ben"], 2, 4), [{"start": 0, "end": 4, "speaker": "S1"}], {}),
    ([{"time": 0, "names": ["Anna"]}, {"time": 9, "names": ["Anna"]}],
     [{"start": 0, "end": 10, "speaker": "S1"}], {}),
    (activity(["Anna"], 0, 1), [{"start": 0, "end": 1, "speaker": "S1"}], {}),
    (activity(["Anna"]), [{"start": 0, "end": 4, "speaker": "S1"},
                          {"start": 0, "end": 4, "speaker": "S2"}], {}),
    ([], [{"start": 0, "end": 4, "speaker": "S1"}], {}),
])
def test_names_require_unambiguous_sufficient_coverage(samples, segments, expected):
    assert names_from_activity(segments, samples) == expected


def test_manual_and_legacy_names_survive_processing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    mid = db.create_meeting("Test", None, meta={"source": "bot"})
    db.update_meeting(mid, speakers={"S1": "Alter manueller Name"})
    db.merge_speaker_names(mid, {"S2": "Sprecher 2"}, manual=True)
    db.merge_speaker_names(mid, {"S1": "Anna", "S2": "Ben", "S3": "Carl"}, diarization={"status": "completed"})
    meeting = db.get_meeting(mid)
    assert meeting["speakers"] == {"S1": "Alter manueller Name", "S2": "Sprecher 2", "S3": "Carl"}
    assert meeting["meta"]["manual_speakers"] == {"S1": "Alter manueller Name", "S2": "Sprecher 2"}
    assert meeting["meta"]["source"] == "bot"


def test_reprocessing_removes_inactive_labels_and_preserves_manual_reserve(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    mid = db.create_meeting("Test", None)
    db.update_meeting(mid, speakers={"S1": "Sprecher 1", "S2": "Sprecher 2", "S3": "Anna"})
    db.merge_speaker_names(mid, {"S1": "Ben"})
    assert db.get_meeting(mid)["speakers"] == {"S1": "Ben"}
    db.merge_speaker_names(mid, {"S1": "Ben", "S3": "Anderer Vorschlag"})
    assert db.get_meeting(mid)["speakers"] == {"S1": "Ben", "S3": "Anna"}


def test_bot_sanitizes_observations_and_stores_no_bot_name(tmp_path):
    manager = SimpleNamespace(s=Settings(data_dir=tmp_path))
    session = BotSession(manager, 1)
    session._on_event(None, json.dumps({"type": "speakers", "time": 1.23456,
                                      "names": [" Anna ", "Anna", "Koprotokoll", 3, "x" * 200]}))
    session._on_event(None, json.dumps({"type": "speakers", "time": -1, "names": ["Ben"]}))
    assert session.speaker_activity == [{"time": 1.235, "names": ["Anna"]}]


def test_raw_observations_are_not_sent_to_webhooks():
    from koprotokoll.pipeline import meeting_payload

    meeting = {"meta": {"source": "bot", "speaker_activity": activity(["Anna"]),
                         "manual_speakers": {"S3": "Alter Name"}, "automatic_speakers": {"S1": "Anna"}}}
    assert meeting_payload(meeting)["meta"] == {"source": "bot"}
    assert "speaker_activity" in meeting["meta"]


def test_pipeline_uses_activity_and_keeps_edit_made_during_diarization(tmp_path):
    from test_integrations import make_client
    from test_koprotokoll import FakeDiarizer, wav_bytes, wait_done

    with make_client(tmp_path) as client:
        db, pipeline = client.app.state.db, client.app.state.pipeline
        source = tmp_path / "input.wav"
        source.write_bytes(wav_bytes(tmp_path))
        mid = db.create_meeting("Test", None, meta={"source": "bot", "speaker_activity": activity(["Anna"], 0, 25)})
        db.update_meeting(mid, audio_path=str(source))

        class EditDuringDiarization(FakeDiarizer):
            def diarize(self, *args):
                assert client.patch(f"/api/meetings/{mid}", json={"speakers": {"S1": "Mein Name"}}).status_code == 200
                return super().diarize(*args)

        pipeline.diarizer = EditDuringDiarization()
        pipeline.submit(mid)
        result = wait_done(client, mid)
        assert result["speakers"] == {"S1": "Mein Name", "S2": "Anna"}
        assert result["meta"]["source"] == "bot"
        assert result["meta"]["diarization"]["status"] == "completed"


@pytest.mark.parametrize("case", ["overlap", "pauses"])
def test_pipeline_retains_raw_diarization_intervals(tmp_path, case):
    from test_integrations import make_client
    from test_koprotokoll import wav_bytes, wait_done
    from koprotokoll.speech import Turn, Word

    with make_client(tmp_path) as client:
        db, pipeline = client.app.state.db, client.app.state.pipeline
        source = tmp_path / "input.wav"
        source.write_bytes(wav_bytes(tmp_path, seconds=5))
        mid = db.create_meeting("Überlappung", None, meta={"speaker_activity": activity(["Anna"])})
        db.update_meeting(mid, audio_path=str(source))
        words = [Word(0, 2, "Eins"), Word(2, 4, "zwei.")] if case == "overlap" else [
            Word(at, at + 0.1, "Wort") for at in (0, 1.5, 3, 4.5)]
        turns = [Turn(0, 4, 0), Turn(0, 4, 1)] if case == "overlap" else [
            Turn(word.start, word.end, 0) for word in words]
        pipeline.transcriber = SimpleNamespace(transcribe=lambda audio, progress: words)
        pipeline.diarizer = SimpleNamespace(diarize=lambda *args: turns)
        pipeline.submit(mid)
        result = wait_done(client, mid)
        assert result["speakers"] == {"S1": "Sprecher 1"}


def test_google_observations_follow_recorder_clock_and_stop():
    from playwright.sync_api import sync_playwright

    assert Adapter.speaker_script is None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chrome_path(), headless=True,
                                     args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        events = []
        page.expose_function("__ntEvent", lambda payload: events.append(json.loads(payload)))
        page.expose_function("__ntChunk", lambda payload: None)
        page.set_content('''<div data-participant-id="self"><span data-self-name="Bot"></span><span class="notranslate">Bot</span><i class="Oaajhc"></i></div>
          <div data-participant-id="a"><span class="notranslate">Anna</span><i class="Oaajhc"></i></div>
          <div data-participant-id="b"><span class="notranslate">Ben</span><i></i></div>
          <div hidden data-participant-id="hidden"><span class="notranslate">Unsichtbar</span><i class="Oaajhc"></i></div>''')
        page.evaluate(CAPTURE_JS)
        page.evaluate("() => window.__ntObserveSpeakers(" + GoogleMeet.speaker_script + ")")
        page.wait_for_timeout(550)
        assert not events
        page.evaluate("window.__ntStart(100)")
        page.wait_for_timeout(650)
        page.evaluate("document.querySelector('[data-participant-id=a] i').className = ''")
        page.wait_for_timeout(550)
        page.evaluate("window.__ntStop()")
        count = len([event for event in events if event["type"] == "speakers"])
        page.wait_for_timeout(600)
        samples = [event for event in events if event["type"] == "speakers"]
        assert len(samples) == count >= 3
        assert 0 <= samples[0]["time"] < 0.2
        assert samples[0]["names"] == ["Anna"] and samples[-1]["names"] == []
        browser.close()
