# Lokales Textmodell: Nachweis zu Issue #37

Geprüft durch Codex (GPT-6.1-sol), 2026-11-07, ausgehend von
`a23bbd875ea661ea5d1ecaedbf394d8ffe19aa97`.

## Bedienweg und Grenzen

Die bestehende Konfiguration und der bestehende LLM-Client wurden erweitert.
Die Liste stammt aus der konfigurierten OpenAI-Verbindung. Eine Auswahl löst
keinen Download und keinen Inferenzaufruf aus. Die nachgelagerte Pipeline nutzt
dieselbe Settings-Instanz. Die Listenprüfung behauptet keinen Inferenzerfolg.
Optionale Parameter erfordern die ausdrückliche Bestätigung ihrer Unterstützung;
es gibt keine automatische Erkennung von Server- oder Modellfähigkeiten.

Fake-Server-Tests in `tests/test_local_model.py` prüfen vorhandene, fehlende und
leere Modelllisten, nicht erreichbaren Server, Auswahl und Persistenz,
Env-Vorrang, optionale Parameter, HTTP-Fehler, Timeout und den normalen
Hardwareaufnahme-Stopp mit künstlichem Audio und nachgebildeter Transkription.
Die Standardparameter enthalten weder Ausgabelimit noch Thinking-Vorgabe.

## Browser

T3 preview_status und preview_open wurden verwendet. Zwei Snapshot-Versuche
scheiterten mit „Preview automation snapshot timed out after 15000ms.“
Danach Prüfung mit lokalem Playwright/Chromium gegen eine isolierte App auf
Port 8037 mit leerem temporärem Datenverzeichnis. Desktop 1280×900 und Mobil
390×900: Modellauswahl gespeichert, Seite neu geladen, Auswahl wieder sichtbar;
kein horizontaler Überlauf, keine JavaScript-Seitenfehler. Screenshots außerhalb
des Git-Verlaufs: `/tmp/kopro37-1280.png`, `/tmp/kopro37-390.png`.
Die Bilder zeigen die Oberfläche nach der Auswahl, keine Modellinferenz.

Anti-slop: öffentliche Originalquelle sowie Copywriting- und Mobil-Regeln
gezielt gelesen. Konkrete Korrekturen: eindeutige Aufforderung bei fehlender
Auswahl, „Serverstandard“ statt eines unbelegten „Unbegrenzt“, umbrechende
Beschriftungen und mindestens 44 Pixel hohe Eingabefelder und Schaltflächen.
Keine Neugestaltung der bestehenden Integrationen.

## Realer lokaler Versuch

Bereits installiertes `qwen3:4b`, bestehender LLM-Client gegen lokales Ollama,
`temperature=0.2`, `max_tokens=700`, `reasoning_effort="none"`, JSON-Modus,
Timeout 60 Sekunden. Ein künstlicher deutscher Dialog zur Prüfung einer
Testliste, einem Pilotbeschluss und einem offenen Termin lieferte nach
16,36 Sekunden strukturiertes JSON mit Aufgabe, Beschluss und offener Frage;
`engine=llm:qwen3:4b`. Kein Download, keine echten Aufnahmen.
Das ist ein Funktionsnachweis für diesen Dialog, keine Qualitätsfreigabe für
Meetings und kein kontrollierter Geschwindigkeitsvergleich.

## Abschließende Tests

Vollständiger Aufruf: `python -m pytest -q -ra` mit der vorhandenen Projekt-venv.
Ergebnis des letzten Laufs: **295 passed in 85.47s**; keine Skips, keine Fehler.
Zusätzlich `node --check koprotokoll/static/app.js` und `git diff --check` erfolgreich.
Zwischenläufe fanden Fehler bei der Request-Modellauflösung, beim NoLLM-Testdouble
und eine Kollision von Statusrollen in bestehenden Browsertests. Diese wurden
im Anwendungscode korrigiert; bestehende Tests wurden nicht abgeschwächt.

Nicht geprüft: echte Hardwareaufnahme, echte Meetings, andere lokale Server und
weitere Modellfamilien. Parameterkompatibilität bleibt eine ausdrückliche
Nutzerbestätigung anhand der jeweiligen Dokumentation. Eine unabhängige Prüfung
des Commits steht aus; es wurde weder gepusht noch ein PR angelegt.
