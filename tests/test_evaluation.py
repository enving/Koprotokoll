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
