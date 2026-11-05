# Docker und Compose (#19)

Geprüft am 4. November 2026 durch Codex (GPT-6.1): Docker Desktop/Engine 28.4.0,
Linux ARM64, Python 3.12.16 im Container. Anwendungsstand:
`2898cf84035772b07773453cc1e95ff70fab46d8` plus die hier committierten Docker-Dateien.
Alle 39 Anwendungsdateien im Image wurden per SHA-256 mit dem Arbeitsbaum verglichen.

## Build und Start

- `docker build -t koprotokoll:issues .` erfolgreich; kein Image veröffentlicht.
- `docker compose config --quiet` erfolgreich.
- `docker compose up -d --build` startet Koprotokoll und Ollama. Für die lokale
  Prüfung wurde Port 18836 statt 8000 verwendet; die öffentliche URL war entsprechend gesetzt.
- Image `koprotokoll-issues-koprotokoll`:
  `sha256:dd2cca8b7a456475b9680b0424f3cbc07446ccf81bc47cf693e6dcd467639ab8`,
  `docker image inspect … --format '{{.Size}}'`: **717.452.466 Bytes** (rund 717 MB).
- Oberfläche HTTP 200, API ohne Schlüssel HTTP 401, Containerstatus `healthy`.
  `python scripts/docker_smoke.py http://127.0.0.1:18836` prüft dies erneut.
- Echtes Playwright-Chromium startet als Benutzer `koprotokoll` (UID 10001),
  rendert eine lokale Testseite und wird wieder geschlossen.
- Ollama läuft als zweiter Compose-Service; `ollama list` funktioniert, ohne Modelle.

## Echter Upload und Neustart

Die ersten acht Sekunden des bereits vorhandenen synthetischen Testdialogs
(siehe [Audio-Nachweis](issue-31-lokale-audioaufnahme.md#synthetisches-testgespräch-auf-nutzerauftrag-03112026))
wurden als WAV hochgeladen. SHA-256 des achtsekündigen WAV:
`39809290db4da7e10961dcbe6fdddecbd5ed26bf705da0f1f78b1566c29e8e9c`.

Vorhandene Primeline-Gewichte sowie Segmentierungs- und Sprecher-Embedding-Modelle
wurden vorab ins `/data/models`-Volume kopiert. Sie liegen nicht im Image.
`HF_HUB_OFFLINE=1` war gesetzt; eine Netzwerksperre bestand nicht.
Ein lokal erzeugter API-Schlüssel wurde im Prüfprozess gehalten, nicht protokolliert.

Der erste Durchlauf mit abgeschalteter Diarisierung erkannte 14 Wörter.
Anschließend lief derselbe Upload mit der standardmäßig aktiven Diarisierung:
**14 Wörter, ein Textsegment, Status `done`, Diarisierung `completed`,
Zusammenfassung `heuristik`**. Keine Erkennungskomponente war nachgebildet.
Nach `docker compose restart koprotokoll` waren das Meeting und seine Segmente
unverändert mit demselben Schlüssel abrufbar. Auch Modellgewichte und Datenbank
überstanden das vorherige Neuerstellen des Containers.

Zum Wiederholen eigene freigegebene Audiodatei verwenden: Modelle wie in der
[Einrichtung](../einrichtung.md#betrieb-mit-docker-und-ollama) vorbereiten, einen
Schlüssel erzeugen, Datei per Oberfläche oder `POST /api/meetings` hochladen,
`done` und nichtleere Segmente prüfen, Container neu starten und dasselbe Meeting
wieder abrufen.

## Grenzen

Dies ist ein Funktionstest eines kurzen künstlichen Samples, keine Prüfung der
Erkennungsqualität echter Meetings. Keine LLM-Gewichte wurden geladen; generative
Ollama-Zusammenfassungen, GPU-Betrieb, AMD64 und echte Meeting-Bot-Beitritte wurden
hier nicht geprüft. Die vorbereitete GitHub-Workflow-Datei (`docker.yml`: Image bauen,
Start und Zugriffsschutz mit `scripts/docker_smoke.py` prüfen) ist **nicht** Teil dieses
Pull Requests, weil das Token des umsetzenden Agenten keine Workflow-Dateien
veröffentlichen darf. Sie muss von einem Maintainer mit `workflow`-Recht ergänzt werden;
bis dahin gibt es keinen GitHub-CI-Lauf für das Image.
