# Sprechernamen aus Plattform-Hinweisen (#16)

Stand: 4. November 2026, Codex (GPT-6.1).

`python -m pytest tests/test_speaker_names.py -q` prüft:

- eindeutige Zuordnung, Sprecherwechsel und die Mindestdauer/-abdeckung;
- mehrere aktive Namen, überlappende Segmente, Lücken und fehlende Hinweise;
- alte manuelle Namen sowie ausdrücklich manuell vergebene Standardlabels;
- eine Namensänderung während laufender Diarisierung in der echten Pipeline;
- Filterung des Bot-Namens und ungültiger Browser-Ereignisse;
- mit echtem Chromium: sichtbare Meet-Kacheln, Ausschluss der eigenen Kachel,
  Ereignisse über `__ntEvent`, Recorder-Zeitbasis und Ende der Beobachtung beim Stoppen.

Die DOM-Abfrage nutzt Teilnehmerkacheln mit `data-participant-id`, Namensfelder
`span.notranslate` und die Sprechklassen `Oaajhc`/`HX2H7`. Dass solche internen
Klassen verwendet werden, ist in einer unabhängigen offenen Implementierung
[öffentlich nachvollziehbar](https://github.com/Vexa-ai/vexa/blob/main/core/meetings/modules/gmeet-capture/src/gmeet-speakers.ts).
Dies ist kein Google-Schnittstellenvertrag; diese Beobachtung ersetzt keinen Live-Test.

**Offen:** Ein echtes Google Meet mit mehreren Personen wurde nicht durchgeführt.
Vor Schließen von #16 müssen die tatsächlich angezeigten Namen, Sprecherwechsel,
Zeitabgleich und Schutz einer manuellen Korrektur dort geprüft werden. Die
Audio-Diarisierung bleibt maßgeblich; unbekannte oder mehrdeutige DOM-Hinweise
sollen keine Namen erzeugen.

Das unabhängige Review fand, dass geglättete Textsegmente überlappende Stimmen und
Pausen verdecken können. Die Pipeline verwendet deshalb die ursprünglichen
Diarisierungszeiten mit derselben Label-Map wie das Transkript. Zwei Pipeline-
Gegenfälle decken Überlappung und kurze Wortinseln mit Pausen ab. Erneute Verarbeitung
entfernt inaktive Labels aus der Anzeige und erhält manuelle Namen als Reserve.
