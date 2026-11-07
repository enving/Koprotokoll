# Issue #49: mehrsprachige CPU-Transkription

Geprüft am 06.11.2026 mit GPT-6.1-Sol auf Apple M1, Python 3.12,
onnx-asr 0.12.0, ONNX Runtime 1.31.1 und sherpa-onnx 2.0.1.
Die Dateien wurden mit macOS `say` erzeugt: Anna (`de_DE`) und Samantha
(`en_US`), jeweils 160 Wörter pro Minute. Alle Inhalte sind erfunden.
Aufnahmen und maschinenlesbare Messdaten bleiben außerhalb des Repositorys.

## Durchführung

1. v3 INT8 und Silero mit `scripts/download_models.py --language auto --asr-only`
   installiert; feste Revisionen siehe [`speech.py`](../../koprotokoll/speech.py).
   Für `de` einen vorhandenen vollständigen Primeline-Ordner verwendet.
2. TTS-Dateien mit dem vorhandenen ffmpeg-Weg in Mono-16-kHz-WAV umgewandelt;
   gemischter Clip: Deutsch, 0,5 Sekunden Pause, Englisch. Stille: drei Sekunden Nullen.
3. Die echten Backends direkt und über FastAPI `TestClient` aufgerufen.
   Der Prozess lief mit `HF_HUB_OFFLINE=1` und macOS `sandbox-exec` mit
   `(version 1) (allow default) (deny network*)`. Vor den Proben wurden externe
   IPv4- und IPv6-Verbindungsversuche beide mit `EPERM` abgewiesen.
4. Nach Modellladen die tatsächlichen ONNX-Sessions für Encoder, Decoder/Joiner
   und VAD geprüft: jeweils ausschließlich `CPUExecutionProvider`.
   Primeline wird mit explizitem Sherpa-Provider `cpu` geladen.
5. Gemischten Clip zusätzlich über die Meeting-Queue mit echter Sprechererkennung
   und ausgeschaltetem LLM verarbeitet. Ergebnis: `done` nach 2,93 Sekunden,
   Transkript und ASR-Metadaten vorhanden, Diarisierung `completed`.

## Direkte CPU-Proben

Die erste Probe je Backend enthält das Laden des Modells. Die übrigen nutzen
bereits geladene Gewichte. Laufzeiten sind Einzelmessungen.

| Audio | Modus / Modell | Audiodauer | Laufzeit | Ergebnis |
|---|---|---:|---:|---|
| Deutsch | `auto` / v3 | 6,373 s | 2,096 s | 15 Wörter, deutscher Text vollständig |
| Englisch | `auto` / v3 | 6,087 s | 0,236 s | 17 Wörter, englischer Text vollständig |
| Englisch | `en` / v3 | 6,087 s | 0,233 s | identischer englischer Text |
| Gemischt | `auto` / v3 | 12,960 s | 0,486 s | beide Abschnitte in Originalsprache, 32 Wörter |
| Stille | `auto` / v3 | 3,000 s | 0,012 s | leer, 0 Wörter |
| Deutsch | `de` / Primeline | 6,373 s | 2,509 s | 15 Wörter; Satzzeichen anders als v3 |
| Stille | `de` / Primeline | 3,000 s | <0,001 s | leer, 0 Wörter |

Die deutschen und englischen v3-Ergebnisse entsprechen diesen erfundenen Vorgaben:

> Guten Morgen. Wir testen heute die lokale Spracherkennung. Der blaue Roboter fährt morgen nach Berlin.

> Good morning. Today we are testing local speech recognition. The blue robot will travel to Berlin tomorrow.

Alle zurückgegebenen Wortzeiten lagen innerhalb der jeweiligen Audiodauer.
Die API-Proben prüften `auto`, `de`, `en`, gemischtes Audio und Stille,
einschließlich tatsächlicher Modellnamen, Modus, Antwortheader und leerem
`verbose_json.language` statt vorgetäuschter Spracherkennung.

## Fehler und Regressionen

`pytest -q`: **291 bestanden**, keine übersprungenen Tests.

- Fehlendes lokales Modell: echte API antwortet `503` mit Installationshinweis.
- Primeline-Ordner als v3-Pfad: wird vor Inferenz mit Konfigurationsfehler abgewiesen.
- Beschädigte WAV-Eingabe und unbekannter Sprachcode: API antwortet `400`.
- Automatisierte Tests prüfen unter anderem Altpfade, getrennte Modellpfade,
  ausdrückliche v3-Wahl für `de`, Queue-Metadaten, CPU-Provider, gepinnte Downloads,
  fehlende Modelle ohne Nachladen und Bereinigung temporärer Uploads.

## Grenzen

Das sind synthetische Funktionsproben, keine Qualitätsbewertung echter Meetings.
Die Sprechererkennung fasste beide TTS-Stimmen als einen Sprecher zusammen;
bestätigt ist ihr erfolgreicher Ablauf, keine korrekte Sprechertrennung.
Eine Messung der Sprecherfehlerrate wurde nicht durchgeführt. Überlappende Sprache,
Akzente, verrauschte oder lange reale Aufnahmen sowie fp32 wurden hier nicht geprüft.
Die Integration hat keine laufenden Dienste neu gestartet.
