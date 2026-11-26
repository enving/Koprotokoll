# API, Webhooks & MCP

[← zurück zum README](../README.md)

Vollständige, interaktive Doku: **http://127.0.0.1:8000/docs** (OpenAPI unter `/openapi.json`).

Anfragen direkt vom eigenen Rechner brauchen keinen Schlüssel. Von anderen Rechnern, hinter
einem Reverse-Proxy oder aus Tools wie n8n/Zapier/Make: API-Schlüssel in der Oberfläche oder per
`python -m koprotokoll create-key n8n` erzeugen und mitsenden. Mit `KOPROTOKOLL_REQUIRE_KEY=1` gilt
die Schlüsselpflicht auch lokal.

Ändernde Browser-Anfragen mit fremdem `Origin` oder `Sec-Fetch-Site: cross-site`
werden abgewiesen. CLI-Aufrufe ohne diese Browser-Header bleiben möglich.

```bash
KEY=kp_…
# Bot in ein Meeting schicken
curl -X POST http://localhost:8000/api/bots -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" -d '{"meeting_url": "https://meet.google.com/abc-defg-hij"}'
# Meetings, Transkript, Suche, Export
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings/1
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/search?q=budget"
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/meetings/1/export?format=md"
curl -H "Authorization: Bearer $KEY" -o aufnahme.wav "http://localhost:8000/api/meetings/1/audio?download=1"
```

| Bereich | Endpunkte |
|---|---|
| Meetings | `GET/POST /api/meetings`, `GET/PATCH/DELETE /api/meetings/{id}`, `POST /api/meetings/bulk-delete` (`{"ids": […]}` löscht mehrere auf einmal), `…/audio` (`?download=1` als Datei), `…/export`, `…/summarize`, `…/ask`, `…/bookmarks` |
| Suche | `GET /api/search?q=` |
| Bots | `POST/GET /api/bots`, `GET/DELETE /api/bots/{id}` |
| Webhooks | `GET/POST /api/webhooks`, `DELETE /api/webhooks/{id}`, `POST /api/webhooks/{id}/test` |
| Transkription (OpenAI-kompatibel) | `POST /v1/audio/transcriptions` |
| Schlüssel | `GET/POST /api/keys`, `DELETE /api/keys/{id}`, `GET /api/tokens` (Schlüssel prüfen) |
| Google | `GET/DELETE /api/integrations/google`, `PUT …/client`, `GET …/connect`, `PATCH …/settings`, `POST …/sync` |
| Kalender-Quellen | `GET/POST /api/calendars`, `GET /api/calendars/presets`, `PATCH/DELETE /api/calendars/{id}`, `GET/POST /api/calendars/{id}/test`, `POST /api/calendars/sync` |

## Diktieren: `/v1/audio/transcriptions` (OpenAI-kompatibel)

Für kurze Aufnahmen – etwa aus **Kodiktat** oder jedem Programm, das die OpenAI-Transkriptions-API
spricht. Standardmäßig wird **kein Meeting angelegt**, keine Sprechererkennung und keine
Zusammenfassung gemacht; die Datei liegt nur während der Anfrage in `data/tmp` und wird danach
gelöscht. Mit `save_meeting=1` wird die Aufnahme zusätzlich als Meeting gespeichert (siehe unten).

| Feld (multipart) | Bedeutung |
|---|---|
| `file` | Pflicht. WAV (16 kHz wird direkt gelesen), sonst alles, was ffmpeg kann (mp3, m4a, ogg, webm …) |
| `response_format` | `json` (Standard, `{"text": …, "asr": …}`), `text`, `verbose_json` (mit `segments`, `start`/`end` in Sekunden), `srt`, `vtt` |
| `language` | optional: `auto`, `de`, `en`; wählt tatsächlich das lokale Backend gemäß [Sprachwahl](einrichtung.md#sprachwahl-und-cpu-modelle); ohne Angabe gilt der Serverstandard |
| `save_meeting` | optional: `1` speichert die Aufnahme dauerhaft als Meeting – Audio in `data/media`, Titel aus den ersten Worten des Transkripts. Schlank: Segmente kommen direkt aus dieser Transkription, die Pipeline läuft nicht erneut (kein zweites Transkribieren, keine Sprechererkennung, keine Zusammenfassung), Status sofort `done`. Antwort bleibt unverändert, Header `X-Koprotokoll-Meeting: <id>` |
| `num_speakers` | optional: Sprecherzahl nur für das gespeicherte Meeting; `1` deaktiviert die Sprechertrennung (Monolog) |
| `model`, `prompt`, `temperature` | Kompatibilitätsfelder ohne Wirkung; `model` überschreibt die Serverkonfiguration nicht |

```bash
curl http://localhost:8000/v1/audio/transcriptions -H "Authorization: Bearer $KEY" \
     -F file=@notiz.wav -F language=auto -F response_format=verbose_json
```

JSON-Antworten enthalten unter `asr` das ausgewählte `model`, `mode`, `backend`,
`model_path`, `quantization`, den CPU-Provider und `language_forced: false`.
Das Feld `language` in `verbose_json` bleibt leer: der verwendete Decoder liefert
hier keinen verlässlichen erkannten Sprachcode. `asr.mode` ist die angeforderte
Auswahl, keine Sprachmessung. Auch bei `language=en` entscheidet v3 selbst über
die Ausgabesprache; es gibt keine Spracherzwingung und keine Übersetzung.

Alle Erfolgsformate, auch Text/SRT/VTT, liefern die Header
`X-Koprotokoll-ASR-Model`, `X-Koprotokoll-ASR-Mode` und
`X-Koprotokoll-ASR-Backend`. Memo-Aufrufer können so das verwendete Modell
pro Antwort prüfen. `GET /api/status` zeigt unter `asr` die konfigurierte
Standardauswahl, ohne das Modell zu laden oder dessen Betriebsbereitschaft zu garantieren.
Fehlende Modelle liefern `503`, unbekannte Sprachmodi `400`; kein Cloud-Fallback.

### Meeting-Import mit Sprachwahl

`POST /api/meetings` nimmt zusätzlich das Multipart-Feld `language` an.
Es verwendet dieselben Modi und denselben Standard wie die Transkriptions-API:

```bash
curl http://localhost:8000/api/meetings -H "Authorization: Bearer $KEY" \
     -F file=@besprechung.wav -F language=en -F title="Testbesprechung"
```

Die Queue speichert den Modus unter `meta.asr_language`, die ausgewählte Engine
unter `meta.asr`. Sie stehen nach Verarbeitung über `GET /api/meetings/{id}` zur
Verfügung, auch bei einem Modellfehler zusammen mit `status=error` und `error`.
Sprechererkennung, Zeitmarken und Zusammenfassung laufen anschließend wie bisher.
Die Oberfläche bietet beim Hochladen und Aufnehmen `de`, `auto` und `en` an und
merkt sich die letzte Auswahl im Browser. Ohne gespeicherte Auswahl ist dort
Deutsch voreingestellt. API-Aufrufe ohne `language` und Bot-Aufnahmen verwenden
weiterhin den Serverstandard.

`POST /api/capture/start` akzeptiert das optionale JSON-Feld `language` mit denselben
Werten. Die Auswahl wird vor dem Aufnahmestart validiert und beim Stopp in die
Meeting-Metadaten übernommen. Die Mac-Menüleisten-App bietet dafür eine gespeicherte
Auswahl „Transkriptionssprache“ in ihren Einstellungen (Vorgabe: Deutsch).

### Bestehende Aufnahme erneut transkribieren

`POST /api/meetings/{id}/retranscribe` mit `{"language":"de"}` erstellt eine neue
Meeting-Fassung samt eigener Audiokopie und antwortet mit `201` und dem neuen Meeting.
Die Queue führt die Erkennung, Sprecherzuordnung und Zusammenfassung erneut aus.
`meta.retranscribed_from` verweist auf die bisherige Fassung. Deren Transkript,
Markierungen und manuelle Sprechernamen bleiben erhalten; sie werden nicht in die
neue Fassung übernommen. Auch nach dem Löschen der bisherigen Fassung bleibt die
neue Audiokopie verfügbar. Ein Verarbeitungsfehler betrifft nur die neue Fassung.

Noch laufende Aufnahmen liefern `409`, fehlende Meetings oder Audiodateien `404`,
unbekannte Sprachmodi `400`. Fehlende Modelle werden wie beim Import als
Verarbeitungsfehler gemeldet. Die Oberfläche bietet den Vorgang im Tab **Transkript**
unter **Transkriptionssprache → Erneut transkribieren** an.

### Eine Stimme wurde irrtümlich als mehrere Sprecher erkannt

`POST /api/meetings/{id}/single-speaker` ordnet alle vorhandenen Äußerungen `S1`
zu und setzt `num_speakers=1`. Text, Wortzeiten, Äußerungs-IDs, Nachbesserungen,
Markierungen und Suchindex bleiben erhalten. Die bisherige Aufteilung samt Namen
wird als `meta.speaker_correction` gesichert. Wiederholte Aufrufe überschreiben
diese Sicherung nicht. `DELETE` auf demselben Pfad stellt die gesicherte Aufteilung
wieder her. Laufende Verarbeitung oder ein nicht mehr passendes Transkript liefern
`409`, fehlende Meetings `404`.

Die Übersicht bietet dafür unter **Sprecher & Redeanteil** „Alle als eine Person
zusammenführen“ und anschließend „Frühere Sprecheraufteilung wiederherstellen“ an.
Die Änderung löst keine neue Transkription oder Zusammenfassung aus. Ein Hinweis
erinnert daran, die Zusammenfassung bei Bedarf neu zu erstellen. Die Sicherung
der Sprecheraufteilung wird nicht in Webhooks versendet.

Bei neuen Aufnahmen mit `num_speakers=1` wird die automatische Sprechertrennung
übersprungen. Das Transkript erhält genau ein Sprecherlabel; Metadaten nennen
`diarization.status="fixed"` und `source="setting"`. Eine nachträgliche Korrektur
nennt `source="manual"`. Beide sind eine Vorgabe, keine gemessene Sprecherzahl.
Die Mac-Menüleisten-App speichert die Auswahl **Anzahl Sprecher** für Aufnahmen
per Tastenkürzel; Vorgabe bleibt Automatisch.

### Automatischer Stopp nach fünf Minuten ohne Sprache

Hardwareaufnahmen (einschließlich Mac-Tastenkürzel) überwacht der Server unabhängig
vom Browser. Lokales Silero-VAD prüft die laufenden Audiospuren. Nach fünf Minuten
ohne erkannte Sprache stoppt und sichert der Server die Aufnahme und startet die
Auswertung. `GET /api/capture` liefert danach `active=false`, `meeting_id` und
`stop_reason="silence"`; das Meeting erhält ebenfalls `meta.stop_reason="silence"`.
Eine neue Aufnahme setzt den Zustand zurück. Ein manueller Stopp bleibt jederzeit möglich.

Browseraufnahmen schicken kurze Ausschnitte an `POST /api/capture/activity`:
Multipart-Feld `file`, 0,5–2 Sekunden rohes PCM16-Mono, little endian, 16 kHz.
Antwort: `{"speech":false,"silence_seconds":300}`. Diese Ausschnitte werden nicht
gespeichert und an keinen externen Dienst gesendet. Beim automatischen Stopp lädt
der Browser die Aufnahme über `/api/meetings` mit `stop_reason=silence` hoch.

Fehlende/unlesbare Samples oder Detektorfehler zählen nicht als bestätigte Stille;
die Aufnahme läuft weiter und meldet bei Detektorfehlern eine Warnung. Silero-VAD
muss lokal installiert sein (Standardinstaller bzw. `--all-asr`). Browseraufnahme
braucht einen weiterlaufenden Tab; nach längerer Unterbrechung zählt die Wartezeit
neu. Musik, Rauschen oder sehr leise Sprache können die Erkennung beeinflussen.
Die Meeting-Bots behalten ihre eigene konfigurierte Stillegrenze.

`GET /api/tokens` antwortet mit `200`, wenn der Schlüssel gültig ist, sonst mit `401` – so prüft
Kodiktat die Verbindung.

**Kodiktat einrichten:** Anbieter „Heimserver“ wählen, als **Basis-URL** die Adresse des
Koprotokoll-Servers ohne `/v1` eintragen (z. B. `http://192.168.1.20:8000`) und als **Schlüssel**
einen API-Schlüssel aus „Integrationen & API“ (`kp_…`). Läuft Koprotokoll auf einem anderen
Rechner, ihn mit `--host 0.0.0.0` starten oder hinter einen Reverse-Proxy mit HTTPS stellen.

## Webhooks

Ereignisse `meeting.completed`, `meeting.failed`, `bot.status` werden als `POST` mit
`{"event", "created_at", "data"}` zugestellt. Prüfung der Signatur:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Koprotokoll-Signature"])
```

## MCP-Server (Claude, Cursor & Co.)

```bash
claude mcp add koprotokoll -- python -m koprotokoll mcp
# entfernter Server: KOPROTOKOLL_URL=https://… KOPROTOKOLL_API_KEY=kp_… python -m koprotokoll mcp
```

Werkzeuge: `list_meetings`, `search_meetings`, `get_summary`, `get_transcript`, `ask_meeting`, `send_bot`.

## E-Mail-Einstellungen

- `GET /api/integrations/email`: gewählter Versandweg, Recap-Optionen und SMTP-Konfiguration. Enthält nur `smtp.password_set`, niemals das gespeicherte Passwort.
- `PUT /api/integrations/email`: `provider` (`gmail`, `microsoft`, `smtp`, `none`), `host`, `port`, `tls`, `user`, `password`, `sender`, `send_recap`, `recap_to_attendees`. Fehlende Felder bleiben erhalten; `password: ""` löscht das Passwort. Umgebungsvariablen haben Vorrang.
- `POST /api/integrations/email/test`: Test-Mail an die eigene gespeicherte Adresse. Kein beliebiger Empfänger im Request.

Alle drei Endpunkte verwenden die normale API-Authentifizierung. SMTP und Gmail
verwenden dieselbe Text-/HTML-Vorlage aus `build_recap()`.

## E-Mail-Entwürfe

- `GET /api/integrations/email/drafts`: verfügbare `targets` (`eml`, lokal gegebenenfalls `thunderbird` und `outlook`).
- `GET /api/meetings/{id}/recap.eml`: fertiges Meeting als MIME-Mail mit Text, HTML und Empfängern aus `meta.attendees`.
- `POST /api/meetings/{id}/recap/draft?ziel=thunderbird` bzw. `ziel=outlook`: öffnet einen Entwurf, versendet nichts. Nur direkt vom Serverrechner ohne Proxy erlaubt; externer Zugriff erhält 403.

Unfertige Meetings liefern 409. Fehlende Mailprogramme liefern einen deutschen
Fehler mit Hinweis auf den EML-Ausweichweg. Alle Endpunkte benötigen die normale
API-Authentifizierung.

## Lokaler Outlook-Kalender

`GET /api/calendars/capabilities` meldet mit `outlook`, ob klassisches Outlook
unter Windows mit COM verfügbar ist. `POST /api/calendars` mit
`{"kind":"outlook"}` fügt dessen Standardkalender hinzu. Ein zweiter Eintrag
desselben Standardkalenders liefert 409. Test, Deaktivierung und Entfernen
verwenden die bestehenden `/api/calendars/{id}`-Endpunkte.

## Microsoft-Gerätecode

- `POST /api/integrations/microsoft/device`: startet nach Nutzeraktion eine Code-Anmeldung; liefert `user_code`, `verification_uri`, `expires_in`, `interval`. Der private Gerätecode bleibt lokal.
- `POST /api/integrations/microsoft/device/poll`: liefert `status: pending` mit Warteintervall oder `status: connected` mit bereinigtem Kontostatus. Zu frühe Abfragen gehen nicht an Microsoft; `slow_down` erhöht das Intervall. Abgelaufene oder abgelehnte Anmeldungen liefern einen deutschen 400-Fehler.

Beide Endpunkte benötigen die normale API-Authentifizierung. Die bestehende
Anmeldung mit Weiterleitung bleibt für Clients mit Secret verfügbar.

## Aufgaben

- `GET /api/meetings/{id}/aufgaben.ics`: Aufgaben eines fertig ausgewerteten Meetings als VTODO mit stabilen UIDs, Fälligkeit und Quellenlink.
- `GET /api/tasks/targets`: erreichbare Aufgabenlisten und gegebenenfalls Verbindungsfehler. CalDAV-Zugangsdaten werden nicht ausgegeben.
- `POST /api/meetings/{id}/tasks/export` mit `{"target":"<id aus targets>"}`: expliziter Export in die ausgewählte Liste, Antwort `created`/`skipped`. Wiederholungen überspringen dieselben Aufgaben.
- `POST /api/integrations/microsoft/device?tasks=1`: erneute Anmeldung mit ausdrücklich angeforderten `Tasks.ReadWrite`-Rechten für To Do.

Alle Endpunkte nutzen die normale Authentifizierung. Unfertige Meetings liefern
409. Exportiert wird ausschließlich in ein aktuell verbundenes Ziel. Bei einem
unterbrochenen Export lassen sich dieselben Aufgaben erneut übergeben.
Links `/#meeting={id}&t={Sekunden}` öffnen die zugehörige Player-Stelle.

### Lokales Textmodell

`GET /api/llm` liefert `models`, `model`, `url`, `enabled`, `server_reachable`,
`model_present`, `available`, `inference`, `message`, `reason`, `model_env_override`,
`max_tokens` und `reasoning_effort`. `available` bedeutet ausschließlich: aktiviert
und ausgewähltes Modell in der Serverliste vorhanden. `inference` bleibt bei dieser
reinen Listenprüfung „nicht geprüft“. `reason` nennt den konkreten Zustand:
`disabled`, `external_blocked`, `server_unreachable`, `server_error`, `model_missing`
oder `ok`; `message` nennt dabei URL bzw. Modellnamen. Das tatsächliche Ergebnis steht
im Meeting unter `summary.engine`; bei Heuristik übernimmt `summary.warning` denselben
konkreten Grund.

`PUT /api/llm` speichert zum Beispiel `{"model":"testmodell"}` lokal. Optionale
Felder: `max_tokens` (1–32768 oder null), `reasoning_effort` (null, `none`, `low`,
`medium`, `high`). Für gesetzte optionale Parameter muss
`compatible_parameters: true` ihre Unterstützung bestätigen. Unbekannte Modelle
oder ungültige Parameter ergeben 400; fehlerhafte Body-Typen 422.
Alle Endpunkte verwenden die bestehende API-Authentifizierung.

### Sprachmodell aus der App laden (nur lokaler Ollama)

- `GET /api/llm/setup`: Status plus `setup_step` (`install_server`, `pull_model`,
  `select_model`, `ready` oder `disabled`), `pull_supported`, `suggested_model`,
  `size_hint` („ca. 5 GB“) und bei `install_server` die deutschen `instructions`
  (Installationsschritte inklusive Link).
- `POST /api/llm/pull` mit `{"model":"qwen2.5:7b-instruct"}`: startet den Download
  über die Ollama-API (`POST /api/pull`, NDJSON). Antwort 201 mit Status-Snapshot.
  400 bei nicht lokalem Ollama-Server oder ungültigem Modellnamen, 422 bei falschem
  Body-Typ, 409 wenn bereits ein Download läuft.
- `GET /api/llm/pull`: `running`, `model`, `status`, `total`, `completed`, `done`,
  `error` und `supported` – Fortschritt in Bytes aus dem NDJSON-Stream.
- `POST /api/llm/pull/cancel`: bricht den laufenden Download ab; 409 wenn keiner läuft.

Nur lokale Ollama-Server (Anbieter „ollama“, lokale URL) werden angefragt; externe
Ziele und Umleitungen werden abgelehnt. Kein Cloud-Zwang: Ohne Modell bleibt die
Heuristik aktiv.

### Sprachmodell-Anbieter

`GET /api/llm` liefert Status, Modellliste, `provider`, `timeout`, `api_key_set`,
`env_overrides`, `external` und `external_confirmed`, niemals den Schlüssel.
`PUT /api/llm/provider` speichert `provider` (`ollama`, `mistral`, `openai`, `other`),
`url`, `model`, `timeout` (0,01–3600 Sekunden), optional `api_key` und `external_confirmed`.
Das Preset `mistral` füllt `https://api.mistral.ai/v1` als URL vor. Ein fehlender/null
Schlüssel bleibt erhalten, `""` löscht ihn. Externe URLs erfordern
`external_confirmed: true`. `POST /api/llm/test` prüft den gespeicherten Anbieter:
Erreichbarkeit, Modell vorhanden, kurzer Probeaufruf als getrennte Felder.

### Coding CLI als Sprachmodell-Quelle (z. B. Vibe)

- `GET /api/llm/cli-sources`: gefundene CLI-Quellen ohne Schlüsselmaterial, z. B.
  `{"sources":[{"source":"vibe","label":"Mistral Vibe CLI","provider":"mistral",
  "url":"https://api.mistral.ai/v1","model":"mistral-large-4-0","models":[…],
  "key_available":true,"key_source":"macOS-Schlüsselbund (ai.mistral.vibe)",
  "config":"…/.vibe/config.toml"}]}`. Quelle: `~/.vibe/config.toml` (überschreibbar
  mit `KOPROTOKOLL_VIBE_CONFIG`); Schlüssel aus `MISTRAL_API_KEY` oder macOS-Schlüsselbund.
- `POST /api/llm/cli-import` mit `{"source":"vibe","external_confirmed":true}` und
  optional `api_key` (manuell nachgetragen): übernimmt Endpunkt und Modell und speichert
  den Schlüssel lokal in `llm-settings.json` (0600). Antwort ist `GET /api/llm`
  (`api_key_set: true`, niemals der Schlüssel). 400 bei unbekannter Quelle, fehlendem
  oder ungültigem Schlüssel, ungültigem Modellnamen oder externer URL ohne
  `external_confirmed: true`. Ohne CLI-Konfiguration oder Schlüssel: manuelle Eingabe
  über `PUT /api/llm/provider`.

### Transkript-Nachbesserung

- `GET /api/transcript-correction`: gespeicherte Einstellungen; standardmäßig
  `{"enabled":false,"model":"","vocabulary":[]}`.
- `PUT /api/transcript-correction`: dieselben Felder speichern; bei Aktivierung ist ein
  Modellname erforderlich. Maximal 200 Wortlisteneinträge à 100 Zeichen, Modellname maximal
  200 Zeichen. Gilt beim nächsten Verarbeitungslauf, nutzt die bestehende Anbieterfreigabe.
- `PUT /api/meetings/{id}/transcript-version` mit `{"version":"original"}` oder
  `{"version":"corrected"}` wählt Anzeige-, Such- und Exportfassung. Ohne Korrektur wird
  Originaltext angezeigt. Unbekanntes Meeting: 404; ungültige Fassung: 422.

Meetingsegmente enthalten zusätzlich `original_text`, `corrected_text` (nullable) und
`word_times` (unveränderte ASR-Wörter; bei alten Daten leere Liste). `text` ist die gewählte
Fassung. `meta.transcript_version` speichert die Auswahl; `meta.correction` nennt Modell,
Dienstadresse, `external`, `status` (`completed`/`fallback`) und nullbasierte Indizes der
verworfenen Äußerungen in `rejected`. Es werden keine Modellfehlermeldungen mit möglicherweise
vertraulichen Antworten gespeichert. Die Schnittstellen unterliegen der vorhandenen API-Authentifizierung.

### iPhone-Kurzbefehl: auf dem Mac diktieren

`scripts/create_iphone_shortcut.py` erzeugt eine importierbare Vorlage. Der
Kurzbefehl nimmt Audio auf, sendet es an die Transkriptions-API und kopiert den
zurückgegebenen Text in die Zwischenablage. Diesen Text danach z. B. in T3 Code
einfügen. Beim Ausführen auf dem iPhone liegt er direkt in dessen Zwischenablage;
ein Ergebnisfenster muss nicht bestätigt werden. Die Spracherkennung des iPhones
wird dabei nicht verwendet.

```bash
python scripts/create_iphone_shortcut.py \
  --endpoint https://mac.example.ts.net:8444/v1/audio/transcriptions \
  --output mac-diktat.shortcut
shortcuts sign --mode anyone --input mac-diktat.shortcut --output Mac-Diktat-fuer-T3.shortcut
```

Die Signierung benötigt macOS. Apple erhält dabei nur die Vorlage mit einem
Platzhalter; der API-Schlüssel wird beim Import abgefragt. Den Schlüssel vorher
in Koprotokoll unter **Integrationen & API → API-Schlüssel** erzeugen. Importiert
man die Vorlage ohne Konfiguration, muss im ersten Textfeld der Platzhalter
anschließend durch den Schlüssel ersetzt werden. Keine Datei mit eingetragenem
Schlüssel veröffentlichen oder zur Signierung hochladen.

Der Mac muss erreichbar sein, beispielsweise über das eigene Tailscale-Netz mit
HTTPS-Weiterleitung zum lokalen Koprotokoll-Server. Am iPhone muss die Verbindung
zu diesem Netz aktiv sein. Externe Anfragen benötigen weiterhin den API-Schlüssel;
eine Portfreigabe am Router ist dafür nicht nötig. Im Kurzbefehl nur die selbst
kontrollierte HTTPS-Adresse verwenden. Die Vorlage wählt Deutsch; die verfügbaren
Sprachmodi beschreibt der Transkriptionsabschnitt oben.

Einmalig den Zugriff auf Mikrofon und eigenen Mac erlauben. Danach Kurzbefehl
starten, sprechen, die Aufnahme durch Antippen beenden und nach Abschluss in der
Ziel-App **Einfügen** wählen. Die Mikrofontaste der offiziellen T3-App bleibt ein
separater, unveränderter Eingabeweg.

Die Vorlage sendet `save_meeting=1` und `num_speakers=1`: Jede Aufnahme wird auf
dem Mac als Meeting gespeichert – Audiodatei dauerhaft in `data/media`, Titel aus
den ersten Worten des Transkripts (zum schnellen Wiedererkennen), Sprechertrennung
deaktiviert. In der Oberfläche erscheint sie in der Meeting-Liste, Audio und
Transkript sind dort erreichbar; der Text landet zusätzlich in der Zwischenablage.
In der Liste lassen sich mehrere Aufnahmen per Häkchen auswählen und über
„Ausgewählte löschen“ auf einmal entfernen.

Während der Aufnahme zeigt iOS in der Dynamic Island oben automatisch das orange
Mikrofon-Symbol an. Dieser Indikator ist bei jedem Mikrofonzugriff aktiv und lässt
sich nicht abschalten. Die Aufnahme-Aktion der Kurzbefehle legt sich während der
gesamten Aufnahme als Vollbild-Fenster über den Bildschirm („Tippe, um die
Aufnahme zu beenden“); der Bildschirm ist in dieser Zeit nicht nutzbar, egal ob der
Kurzbefehl aus der App, vom Home-Bildschirm oder per Back Tap gestartet wird.
Beendet wird die Aufnahme durch Antippen des Fensters; der Generator stellt das
fest auf „Bei Tippen“ ein (`WFRecordingEnd: 'On Tap'`). Ein Tap direkt auf die
Dynamic Island stoppt nur die systemseitige Bildschirmaufnahme (rote Anzeige); die
Bildschirmaufnahme lässt sich aus einem Kurzbefehl heraus weder starten noch
stoppen.

Wer den Bildschirm während der Aufnahme weiter nutzen will, zeichnet dafür in
**Sprachmemos** auf: Dort läuft die Aufnahme im Hintergrund weiter, während andere
Apps benutzbar bleiben, und das orange Symbol in der Dynamic Island zeigt sie an.
Beendet wird sie in der Sprachmemos-App. Die fertige m4a-Datei überträgt man per
AirDrop auf den Mac und lädt sie dort per API hoch
(`curl http://localhost:8000/v1/audio/transcriptions -H "Authorization: Bearer $KEY" -F file=@aufnahme.m4a`)
oder importiert sie in der Koprotokoll-Oberfläche. Ein automatisches Senden an den
Mac ist auf diesem Weg nicht möglich, weil Kurzbefehle nicht auf die
Sprachmemos-Mediathek zugreifen dürfen.
