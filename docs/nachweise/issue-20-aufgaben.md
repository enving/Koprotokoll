# Deutsche Aufgabenheuristik (#20)

Stand: 4. November 2026, Codex (GPT-6.1).

Der feste Katalog `tests/test_action_items.py::CASES` enthält 52 positive und
negative Beispielsätze. Im Vergleich zum Ausgangsstand `75679f7`:

| Prüfung auf demselben Katalog | Vorher | Nachher |
|---|---:|---:|
| Aufgabe vorhanden / nicht vorhanden korrekt | 28/52 | 52/52 |
| Zusätzlich Text, Verantwortlicher und Frist korrekt | 17/52 | 52/52 |

Gemessen wurde mit `heuristic_summary()` auf einem Segment pro Satz,
`start=65`, `speaker="S1"` und `names={"S1": "Anna"}`. Die alte Funktion wurde
unverändert aus `git show 75679f7:koprotokoll/summarize.py` geladen. Negative
Beispiele zählen bei korrektem Verwerfen als vollständig richtig.

Das ist ein bei der Umsetzung und im Review erweiterter Regressionstestkatalog,
kein unabhängiger Qualitätsbenchmark für echte Meetings. Unbekannte Formulierungen
können weiterhin fehlen oder falsch erkannt werden; ein lokales LLM bleibt der
primäre Weg. Die Heuristik ergänzt Zuständigkeiten und Fristen im gesprochenen
Wortlaut, ohne ein Datum zu erfinden.

Weitere Tests prüfen numerische Daten mitten im Satz sowie Satzenden nach Daten
vor „Ich“, „Das“, „Danach“ und „Danke“. Das unabhängige Review fand unter anderem
falsche Aufgaben aus allgemeinen Modal-Aussagen, fehlende Namen bei `muss`/`soll`
und verschluckte Zusagen nach Datumszahlen. Diese Fälle sind jetzt im Katalog.

Wiederholen: `python -m pytest tests/test_action_items.py -q`.
