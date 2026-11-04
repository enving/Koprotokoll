import pytest

from koprotokoll.evaluation import diarization_error_rate, word_error_rate


def test_word_error_rate_normalizes_case_and_punctuation():
    report = word_error_rate(
        [{"start": 0, "text": "Hallo, schöne Welt!"}],
        [{"start": 0, "text": "hallo schöne welt"}],
    )
    assert report["wer"] == 0
    assert report["reference_words"] == 3


def test_word_error_rate_reports_substitution():
    report = word_error_rate(
        [{"start": 0, "text": "eins zwei drei"}],
        [{"start": 0, "text": "eins vier drei"}],
    )
    assert report["substitutions"] == 1
    assert report["wer"] == pytest.approx(1 / 3)


def test_der_maps_arbitrary_labels_and_handles_overlap():
    reference = [
        {"speaker": "Anna", "start": 0, "end": 2},
        {"speaker": "Ben", "start": 1, "end": 3},
    ]
    hypothesis = [
        {"speaker": 8, "start": 0, "end": 2},
        {"speaker": 3, "start": 1, "end": 3},
    ]
    report = diarization_error_rate(reference, hypothesis, resolution=0.1, collar=0)
    assert report["der"] == 0
    assert report["mapping"] == {"3": "Ben", "8": "Anna"}
    assert report["reference_speaker_seconds"] == pytest.approx(4.0)


def test_der_counts_a_wrong_speaker_as_confusion():
    reference = [{"speaker": "Anna", "start": 0, "end": 2}]
    hypothesis = [{"speaker": "S1", "start": 0, "end": 2}]
    # Bei nur einem Sprecher ist die ID-Zuordnung korrekt; ein zusätzlicher
    # unzuordenbarer Sprecher erzeugt dagegen einen Fehler.
    hypothesis.append({"speaker": "S2", "start": 0, "end": 2})
    report = diarization_error_rate(reference, hypothesis, resolution=0.1, collar=0)
    assert report["false_alarm_seconds"] == pytest.approx(2.0)
    assert report["der"] == pytest.approx(1.0)


def test_der_counts_false_alarms_in_pauses_and_after_reference():
    reference = [{"speaker": "A", "start": 0, "end": 1},
                 {"speaker": "A", "start": 2, "end": 3}]
    hypothesis = [{"speaker": "x", "start": 0, "end": 4}]
    report = diarization_error_rate(reference, hypothesis, collar=0)
    assert report["reference_speaker_seconds"] == pytest.approx(2)
    assert report["false_alarm_seconds"] == pytest.approx(2)
    assert report["der"] == pytest.approx(1)
    assert report["error_frames"][0]["start"] == pytest.approx(1)


def test_der_reports_overlap_separately_with_global_mapping():
    reference = [{"speaker": "A", "start": 0, "end": 3},
                 {"speaker": "B", "start": 1, "end": 2}]
    hypothesis = [{"speaker": "x", "start": 0, "end": 3}]
    report = diarization_error_rate(reference, hypothesis, collar=0)
    assert report["mapping"] == {"x": "A"}
    assert report["der"] == pytest.approx(0.25)
    assert report["overlap"]["scored_seconds"] == pytest.approx(1)
    assert report["overlap"]["missed_seconds"] == pytest.approx(1)
    assert report["overlap"]["der"] == pytest.approx(0.5)


def test_der_without_reference_speech_is_undefined_but_counts_false_alarms():
    report = diarization_error_rate([], [{"speaker": "x", "start": 0, "end": 1}], collar=0)
    assert report["der"] is None
    assert report["false_alarm_seconds"] == pytest.approx(1)
    assert report["overlap"]["der"] is None
    assert word_error_rate([], [{"start": 0, "text": "Hallo"}])["wer"] is None


def test_der_collar_excluding_all_speech_is_not_perfect_recognition():
    report = diarization_error_rate([{"speaker": "A", "start": 0, "end": 0.2}], [])
    assert report["der"] is None
    assert report["reference_speaker_seconds"] == 0


def test_der_clips_final_frame_to_evaluation_window():
    report = diarization_error_rate([{"speaker": "A", "start": 0, "end": 0.15}], [], collar=0)
    assert report["missed_seconds"] == pytest.approx(0.15)
    assert report["der"] == 1


@pytest.mark.parametrize("resolution,collar", [(0, 0), (float("nan"), 0), (float("inf"), 0),
                                             (0.1, -1), (0.1, float("nan")), (0.1, float("inf"))])
def test_der_rejects_invalid_scoring_parameters(resolution, collar):
    with pytest.raises(ValueError):
        diarization_error_rate([], [], resolution, collar)


@pytest.mark.parametrize("start,end", [(-1, 1), (1, 1), (2, 1), (0, float("inf")), (float("nan"), 1)])
def test_der_rejects_invalid_turn_times(start, end):
    with pytest.raises(ValueError):
        diarization_error_rate([{"speaker": "A", "start": start, "end": end}], [])


def test_evaluation_cli_accepts_reference_list_and_reports_actual_parameters(tmp_path, capsys):
    import json
    from scripts.evaluate_audio_sample import main

    reference = tmp_path / "reference.json"
    result = tmp_path / "result.json"
    turns = [{"speaker": "A", "start": 0, "end": 2, "text": "Hallo"}]
    reference.write_text(json.dumps(turns))
    result.write_text(json.dumps({"segments": turns, "meta": {"diarization": {
        "status": "completed", "turns": turns}}}))
    assert main([str(reference), str(result), "--resolution", "0.05", "--collar", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["wer"]["wer"] == 0
    assert report["der"]["der"] == 0
    assert report["der"]["resolution_seconds"] == 0.05
    assert "0.05-s-Raster" in report["rules"]["der"]
    assert "±0 s" in report["rules"]["der"]


@pytest.mark.parametrize("result", [[], {}, {"segments": [], "meta": {"diarization": {
    "status": "failed", "turns": [{"speaker": "x", "start": 0, "end": 1}]}}}])
def test_evaluation_cli_rejects_missing_or_failed_diarization(tmp_path, capsys, result):
    import json
    from scripts.evaluate_audio_sample import main

    reference = tmp_path / "reference.json"
    output = tmp_path / "result.json"
    reference.write_text('{"turns": []}')
    output.write_text(json.dumps(result))
    with pytest.raises(SystemExit) as exc:
        main([str(reference), str(output)])
    assert exc.value.code == 2
    assert "Auswertung nicht möglich" in capsys.readouterr().err
