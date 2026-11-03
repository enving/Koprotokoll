# Hardwareaufnahme ohne Meeting- oder Browserabhängigkeit

Koprotokoll verarbeitet lokale Audiodateien. Für die Aufnahme ist deshalb
nicht entscheidend, ob der Ton aus Alfaview, einem Browser, einem Telefon oder
einem anderen Programm kommt. Entscheidend ist allein ein vom Betriebssystem
bereitgestellter Audioeingang:

```text
Mikrofon / USB-Interface / System-Loopback
                  ↓
          WAV-Originaldatei
                  ↓
      Koprotokoll-Dateiimport
                  ↓
 Transkription → Sprechererkennung → Zusammenfassung
```

Die Koprotokoll-Aufnahme öffnet den lokalen Systemeingang direkt über ffmpeg;
Firefox, Chromium, Safari und eine bestimmte Meeting-Anwendung sind dafür nicht
erforderlich. Während der Aufnahme entstehen nur PCM-Samples, eine lokale WAV
und ein laufender Spitzenpegel. Parakeet und Sherpa starten erst nach dem
ausdrücklichen Stopp.

## Geeigneten Eingang wählen

1. Vor dem Gespräch alle Beteiligten über die Aufnahme informieren und die
   rechtlich nötige Einwilligung einholen.
2. Den vorhandenen Eingang auflisten:

   ```bash
   python scripts/capture_audio.py --list
   ```

   Der Befehl verwendet nur `ffmpeg` und die Geräte-API des jeweiligen
   Betriebssystems: AVFoundation unter macOS, PulseAudio/PipeWire unter Linux
   und DirectShow unter Windows. Es wird dabei nichts aufgenommen oder an einen
   Dienst übertragen.
3. In Koprotokoll **Aufnehmen → Audioeingang dieses Rechners** wählen und eine
   Quelle auswählen. Unter macOS 15+ gibt es zusätzlich **Systemton +
   Standardmikrofon**. Die laufende Pegelanzeige bestätigt Samples am Eingang;
   sie allein bestätigt noch nicht, dass beide Gesprächsseiten enthalten sind.
   Für den eigenständigen Entwickler-/CLI-Test kann eine Quelle gezielt
   ausgewählt werden. Auf macOS ist das die Gerätenummer aus der Liste; unter
   Linux und Windows der Gerätename:

   ```bash
   python scripts/capture_audio.py --device 3 --seconds 180 \
     --output ./testaufnahme.wav
   ```

   Die Originaldatei ist verlustfreies WAV (standardmäßig 48 kHz, Stereo) und
   wird wegen `ffmpeg -n` nie überschrieben. Sie bleibt vor der Konvertierung
   erhalten. Bei der macOS-Systemtonaufnahme vergleicht Koprotokoll vor dem Start
   der Pipeline die Dauer der beiden Spuren. Weichen sie um mehr als 0,5 Sekunden
   ab, wird das Meeting nicht angelegt; die Spuren bleiben lokal für die Prüfung.
4. Sofort prüfen, ob sie wirklich beide Seiten enthält:

   ```bash
   ffprobe -v error -show_entries format=duration:stream=codec_name,codec_type,sample_rate,channels \
     -of default=noprint_wrappers=1 ./testaufnahme.wav
   ```

   Die Datei mit einem lokalen Player vollständig anhören, insbesondere
   Sprecherwechsel und gleichzeitiges Sprechen. Eine Pegelanzeige allein ist
   kein Nachweis.

Für ein Raumgespräch reicht ein Mikrofon, sofern beide Stimmen verständlich
ankommen. Für ein Online-Gespräch müssen die Stimmen der Freunde zusätzlich als
Signalquelle sichtbar sein: etwa über einen vom Betriebssystem oder
  Audiointerface angebotenen Loopback-Eingang. Unter macOS 15+ kann die
  Systemtonfunktion die aktuelle Audioausgabe plus Standardmikrofon erfassen;
  beim ersten Einsatz kann macOS die Bildschirmaufnahmeberechtigung verlangen.
  Für diesen nativen Weg braucht der lokale Entwicklungsrechner `swiftc` aus den
  Apple-Entwicklerwerkzeugen; ohne Compiler wird die Option nicht angeboten.
  Die App wählt keinen Treiber und ändert weder Standard-Ein- noch
  Standard-Ausgabe. Auf Linux und Windows hängt Systemtonzugriff davon ab, ob
  die Audioinfrastruktur einen Monitor-/Loopback-Eingang anbietet. Auf Geräten
  ohne passenden Eingang bleibt der Import einer lokalen Aufnahmedatei verfügbar.

## In Koprotokoll importieren und prüfen

Die WAV-Datei über **Hochladen** importieren oder lokal per API senden:

```bash
curl -F file=@./testaufnahme.wav -F title='Hardwaretest' -F num_speakers=2 \
  http://127.0.0.1:8000/api/meetings
```

Danach liefert `GET /api/meetings/<id>` neben Segmenten die Rohzeiten der
Sprechererkennung unter `meta.diarization.turns`. Die Oberfläche zeigt einen
fehlgeschlagenen oder von der angeforderten Sprecherzahl abweichenden Lauf
sichtbar an; ein einzelnes Label `S1` wird damit nicht mehr als Erfolg
missverstanden.

Die Messung gegen eine unabhängig erstellte Referenz läuft vollständig lokal:

```bash
curl http://127.0.0.1:8000/api/meetings/1 > ergebnis.json
python scripts/evaluate_audio_sample.py referenz.json ergebnis.json
```

`referenz.json` enthält `turns` mit `speaker`, `start`, `end` und `text`.
Das Skript normalisiert Groß-/Kleinschreibung und Satzzeichen für WER. DER wird
mit 100-ms-Raster, globaler Zuordnung beliebiger Sprecher-IDs und einem
offengelegten 250-ms-Korridor an Referenzgrenzen berechnet; gleichzeitiges
Sprechen zählt für jede aktive Referenzstimme. Das ist eine transparente,
dependency-freie Vergleichsmessung, kein Ersatz für die Metrik eines anderen
Werkzeugs.

## Geräte- und Zugriffsgrenzen

| Weg | Voraussetzung | Ausweichweg |
| --- | --- | --- |
| Direkter Hardwareeingang | Betriebssystem sieht Mikrofon/Interface/Loopback | WAV auf einem anderen Gerät aufnehmen und importieren |
| Systemton | macOS 15+ mit Bildschirmaufnahmeberechtigung; andernorts Monitor-/Loopback-Eingang | Freigegebene lokale Aufnahmedatei importieren |
| Browseraufnahme | Sichere Seite, Browser-Berechtigung, passende Audiofreigabe | Hardware-WAV importieren |
| Mobiles Gerät | Lokale Aufnahme-App oder geeigneter Eingang | Datei in das lokale Netz übertragen und importieren |

Die Hardwareaufnahme läuft auf dem Rechner, auf dem der Koprotokoll-Server
läuft. Für Zugriffe aus dem lokalen Netz muss ein API-Schlüssel gesendet werden.
Mobile Geräte können eine lokale Aufnahme über den gemeinsamen Dateiimport
beisteuern. Nicht jedes Betriebssystem stellt Systemton als Eingang bereit;
diese Einschränkung wird pro Gerät benannt.
