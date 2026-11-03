# Prüfprotokoll: lokale Hardwareaufnahme und Sprecherzuordnung (#31)

Dieses Protokoll beschreibt den reproduzierbaren Nachweis. Aufnahmen und
Referenzen bleiben außerhalb des Git-Verlaufs. Es dokumentiert ausschließlich
freigegebene Testinhalte; reale Gespräche, Namen und Zugangsdaten gehören nicht
hinein.

## Aufbau

Der Test nutzt keinen Meeting-Bot, keine Browser-Audiofreigabe und keine
bestimmte Meeting-Anwendung. Die Koprotokoll-Oberfläche erfasst einen vom
Betriebssystem sichtbaren Hardware- oder Loopback-Eingang direkt. Beim Stopp
schließt sie die unveränderte WAV-Datei ab und startet erst dann die Pipeline.
Damit ist die zeitliche Trennung prüfbar: Zeitpunkt der WAV-Datei vor Import,
Pipeline-Logs und Meeting-Zeitstempel danach.

## Durchführung

1. Eine freigegebene Zwei-Sprecher-Testunterhaltung von 2–3 Minuten über einen
   vorhandenen Hardware- oder Loopback-Eingang aufzeichnen. Sie enthält
   Sprecherwechsel, kurze Einwürfe, Pausen und mindestens eine Überlappung.
2. Vor dem Import mit `ffprobe` Dauer, Samplerate, Kanäle, Pegel/Übersteuerung
   und beide Stimmen prüfen. Die Original-WAV unverändert aufbewahren.
3. Aus dem Originalton unabhängig eine Referenz mit Worttext, Sprecher und
   Zeitgrenzen erstellen. Sie darf nicht aus dem Modellresultat abgeleitet sein.
4. WAV zunächst ohne vorgegebene Sprecherzahl importieren, das API-Ergebnis
   sichern und mit `scripts/evaluate_audio_sample.py` messen. Eine zusätzliche
   Auswertung mit `num_speakers=2` getrennt als Vergleichslauf kennzeichnen.
5. Nach dem Modell-Download den gleichen Lauf mit gesperrtem externem Netzwerk
   wiederholen. Lokale Verbindungen zu `127.0.0.1` bleiben erlaubt. Die Sperre
   und jeden Verbindungsversuch protokollieren.
6. CPU getrennt messen: während der Aufnahme (nur `ffmpeg`) und während der
   Verarbeitung (Parakeet/Sherpa). Keine Nullbelastung behaupten.

Die auszufüllenden Werte sind: Commit-ID, Betriebssystem, Eingangstyp,
Audioformat, Dauer, angeforderte/erkannte Sprecherzahl, Modellstände, ASR- und
Diarisierungsdauer, WER, DER, CPU-Messung, Netzwerksperre und die Grenzen der
Überlappung. Eine kleine Prüfmatrix muss Firefox, Chromium, Safari, Desktop und
Mobil getrennt aufführen: Ohne praktischen Test bleibt eine Kombination offen.

## Gegenwärtiger Stand

Am 03.11.2026 wurde der Audioweg auf diesem Mac kurz mit der damals laufenden,
freigegebenen Unterhaltung erprobt. Die Originaldateien liegen ausschließlich
unter den ignorierten lokalen Testdaten und sind nicht Teil des Repositories.
Die Messungen belegen keinen vollständigen Mitschnitt:

- Ein direkter Hardwaremitschnitt dauerte 2,68 s bei 24 kHz und zwei Kanälen;
  der Spitzenpegel lag bei −57,8 dBFS, der RMS-Pegel bei −73,5 dBFS.
- Ein 3,14-s-Versuch über ScreenCaptureKit ergab sowohl für Systemton als auch
  Mikrofon −120 dBFS; es kam dort kein messbares Signal an.
- Ein weiterer gemischter Mitschnitt dauerte 1,14 s und erreichte −0,5 dBFS
  Spitzenpegel. Die zugehörige Mikrofonspur enthielt jedoch nur 0,10 s.

Diese kurzen Versuche enthalten keine geprüfte Referenz und erlauben keine
Aussage über Sprecherzahl, Vollständigkeit oder Transkriptionsqualität. Sie
zeigen, dass Pegel und Spurlänge vor dem Start der Pipeline geprüft werden
müssen. Koprotokoll verwirft die Systemtonaufnahme inzwischen, wenn sich die
Dauer der System- und Mikrofonspur um mehr als 0,5 s unterscheidet; der konkrete
1,14-s/0,10-s-Fehlversuch wird damit vor dem Anlegen eines Meetings gestoppt.
Ein verwendbarer Zwei-Sprecher-Durchlauf, Offline-Wiederholung, CPU-Werte,
Browser-/Mobilmatrix und Bildschirmaufnahme stehen aus. Es werden
keine privaten Gesprächsinhalte oder Audio-Dateien veröffentlicht.

## Fortsetzung nach der Issue-Aktualisierung vom 03.11.2026

Ausgangspunkt dieser Prüfung: `bb0fb50` auf
`agent/31-lokaler-audionachweis`, macOS 26.7.2 (25G332).
`python scripts/capture_audio.py --list` meldete BlackHole 2ch und DJI MIC MINI.
Firefox und OBS liefen als Prozesse; eine eigenständige alfaview-Anwendung
war in der Prozessliste nicht zu sehen. Daraus lässt sich weder die tatsächliche
Tonquelle noch ein funktionierender Aufnahmeweg ableiten. Es wurde kein neues
Gespräch aufgezeichnet.

`ffprobe` konnte die Dauer aller 17 vorhandenen lokalen WAV-Dateien lesen:
0,02 bis 3,141271 Sekunden. Keine erfüllt die geforderte Länge. Referenz,
Vollständigkeit beider Stimmen und Freigabe zur Veröffentlichung sind für diese
Dateien nicht nachgewiesen. Der vollständige Durchlauf bleibt offen.

Das bestehende Messwerkzeug hatte einen reproduzierbaren Fehler: Bei einer
Referenzstimme von Sekunde 0 bis 1 und einer Hypothese von 0 bis 2 meldete es
mit `collar=0` DER 0. Die zusätzliche Sekunde in der Referenzpause wurde
ausgelassen. Die Korrektur zählt sie als eine Sekunde Fehlalarm und liefert
DER 1. Die Tests verwenden ausschließlich künstliche Zeitsegmente und Texte;
sie sind kein Nachweis der Modellqualität. Bewertungsregeln und neue Felder
stehen zentral in der [Aufnahmeanleitung](../hardware-aufnahme.md#in-koprotokoll-importieren-und-prüfen).

Zusätzlich korrigiert: Referenzen als JSON-Liste verursachen keinen Absturz
mehr; geänderte Raster-/Toleranzparameter erscheinen korrekt im Bericht.
Fehlende Referenzzeit wird als nicht bestimmbar ausgewiesen, Überlappungen
werden gesondert bewertet und Fehlerzeitstellen sind auslesbar.

Die bisher ergänzte native ScreenCaptureKit-Integration überschreitet die
aktuelle Abgrenzung von #31 zu #17. Sie wurde in dieser Fortsetzung weder
erweitert noch als bestandener Nachweis gewertet; ihre Zuordnung ist vor
einem PR zu klären. Der vorhandene Hardwarepfad startet die Verarbeitung direkt
beim Stopp. Eine vorgelagerte Hörprüfung durch die Person ist damit weiterhin
nur über die separat gespeicherte Datei mit anschließendem Import möglich.

Offen bleiben der reale Zwei-Sprecher-Durchlauf, menschlich geprüfte Referenz,
Modell- und CPU-Messungen, nachgewiesene Netzwerksperre, Browser-/Gerätematrix,
freigegebenes öffentliches Sample und hörbare Bildschirmaufnahme. In dieser
Fortsetzung wurden keine Browser- oder Mobiltests ausgeführt und keine
Oberfläche geändert.

Prüfung der Korrektur gegenüber dem oben genannten Ausgangscommit:
`.venv/bin/pytest -q -ra` — **81 bestanden in 31,77 s**, keine übersprungenen
Tests. Davon prüfen 24 Tests das Messwerkzeug; der Ausgangsstand hatte insgesamt
61 bestandene Tests. `git diff --check` meldete keine Formatfehler.
Diese Ergebnisse gelten für die gemeinsam mit diesem Nachweis committete
Änderung, nicht für einen echten Modelllauf.
