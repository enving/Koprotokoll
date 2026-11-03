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
4. WAV mit `num_speakers=2` importieren, das API-Ergebnis sichern und mit
   `scripts/evaluate_audio_sample.py` messen.
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
