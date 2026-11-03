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

Die Testumgebung besitzt mehrere lokale Eingänge einschließlich Mikrofonen und
eines Loopback-Geräts. Der Standardausgang ist jedoch kein Loopback; damit ist
Systemton noch **nicht** als erfasst nachgewiesen. Der echte Hardware-Durchlauf,
die Offline-Wiederholung, CPU-Werte, Browser-/Mobilmatrix und Bildschirmaufnahme
stehen bewusst aus, bis ein freigegebener Testton über den gewählten Eingang
vorliegt. Diese Datei behauptet keinen Qualitätswert, bevor die Messdaten
vorliegen.
