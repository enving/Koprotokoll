"""Deutsche Aufgaben und Gegenbeispiele für die lokale Zusammenfassung."""

import pytest

from koprotokoll.summarize import heuristic_summary


# Feste Beispiele für Issue #20; Fristen bleiben hier im gesprochenen Wortlaut.
CASES = [
    ("Ich kümmere mich drum.", True, "Anna", None),
    ("Ich übernehme das.", True, "Anna", None),
    ("Ich schicke die Kostenaufstellung bis Freitag.", True, "Anna", "bis Freitag"),
    ("Wir müssen noch die Datenschutzprüfung abschließen.", True, None, None),
    ("Kannst du bis Montag den Bericht schicken?", True, None, "bis Montag"),
    ("Ben, schickst du bitte den Bericht?", True, "Ben", None),
    ("Ben übernimmt die Dokumentation.", True, "Ben", None),
    ("Anna wird den Vertrag prüfen.", True, "Anna", None),
    ("Bitte schick die Unterlagen bis zum 3.11.", True, None, "bis zum 3.11."),
    ("Ich liefere nächste Woche die Zahlen.", True, "Anna", "nächste Woche"),
    ("Wir erstellen Ende des Monats die Übersicht.", True, None, "Ende des Monats"),
    ("Ich melde mich morgen bei Ben.", True, "Anna", "morgen"),
    ("Wir senden das Angebot heute.", True, None, "heute"),
    ("Könnt ihr die Ergebnisse bis Donnerstag prüfen?", True, None, "bis Donnerstag"),
    ("Ich werde die Folien aktualisieren.", True, "Anna", None),
    ("Aufgabe: Den Entwurf nachfassen.", True, None, None),
    ("Bitte die Präsentation überarbeiten.", True, None, None),
    ("Ich prüfe die Zahlen bis übermorgen.", True, "Anna", "bis übermorgen"),
    ("Wir sollten den Bericht bis Dienstag fertigstellen.", True, None, "bis Dienstag"),
    ("Ben, kannst du bis zum 03.11.2026 die Liste ergänzen?", True, "Ben", "bis zum 03.11.2026"),
    ("Das hat Anna schon erledigt.", False, None, None),
    ("Wir sollten mal über das Budget reden.", False, None, None),
    ("Ich würde vielleicht die Zahlen prüfen.", False, None, None),
    ("Wir müssen nichts mehr schicken.", False, None, None),
    ("Bitte keine weiteren Unterlagen schicken.", False, None, None),
    ("Ich werde die Unterlagen nicht schicken.", False, None, None),
    ("Die Aufgabe ist bereits abgeschlossen.", False, None, None),
    ("Das Budget reicht bis Freitag.", False, None, None),
    ("Bis Montag bin ich im Urlaub.", False, None, None),
    ("Wann startet der Pilot?", False, None, None),
    ("Danke für den Bericht.", False, None, None),
    ("Ben hat den Bericht bereits geschickt.", False, None, None),
    ("Wir haben die Aufgabe gestern erledigt.", False, None, None),
    ("Ich schicke heute keine Einladung.", False, None, None),
    ("Wir sollten eventuell eine neue Lösung suchen.", False, None, None),
    ("Das nächste Meeting ist nächste Woche.", False, None, None),
    ("Bitte?", False, None, None),
    ("Die Deadline war gestern.", False, None, None),
    ("Anna ist bis Freitag abwesend.", False, None, None),
    ("Es gibt dazu keine neue Aufgabe.", False, None, None),
]


@pytest.mark.parametrize("text,expected,owner,due", CASES)
def test_german_action_items(text, expected, owner, due):
    result = heuristic_summary(
        [{"start": 65, "end": 70, "speaker": "S1", "text": text}], {"S1": "Anna"}
    )["action_items"]
    assert bool(result) is expected
    if expected:
        assert len(result) == 1
        assert result[0] == {"owner": owner, "task": text, "due": due, "time": "01:05"}


def test_numeric_due_date_does_not_split_sentence():
    text = "Ben, schick bitte bis zum 3.11. die Unterlagen. Danke."
    result = heuristic_summary(
        [{"start": 0, "end": 5, "speaker": "S1", "text": text}], {}
    )["action_items"]
    assert result == [{"owner": "Ben", "task": text.removesuffix(" Danke."),
                       "due": "bis zum 3.11.", "time": "00:00"}]
