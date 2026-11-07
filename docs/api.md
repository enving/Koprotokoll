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
```

| Bereich | Endpunkte |
|---|---|
| Meetings | `GET/POST /api/meetings`, `GET/PATCH/DELETE /api/meetings/{id}`, `…/audio`, `…/export`, `…/summarize`, `…/ask`, `…/bookmarks` |
| Suche | `GET /api/search?q=` |
| Bots | `POST/GET /api/bots`, `GET/DELETE /api/bots/{id}` |
| Webhooks | `GET/POST /api/webhooks`, `DELETE /api/webhooks/{id}`, `POST /api/webhooks/{id}/test` |
| Transkription (OpenAI-kompatibel) | `POST /v1/audio/transcriptions` |
| Schlüssel | `GET/POST /api/keys`, `DELETE /api/keys/{id}`, `GET /api/tokens` (Schlüssel prüfen) |
| Google | `GET/DELETE /api/integrations/google`, `PUT …/client`, `GET …/connect`, `PATCH …/settings`, `POST …/sync` |
| Kalender-Quellen | `GET/POST /api/calendars`, `GET /api/calendars/presets`, `PATCH/DELETE /api/calendars/{id}`, `GET/POST /api/calendars/{id}/test`, `POST /api/calendars/sync` |

## Diktieren: `/v1/audio/transcriptions` (OpenAI-kompatibel)

Für kurze Aufnahmen – etwa aus **Kodiktat** oder jedem Programm, das die OpenAI-Transkriptions-API
spricht. Es wird **kein Meeting angelegt**, keine Sprechererkennung und keine Zusammenfassung
gemacht; die Datei liegt nur während der Anfrage in `data/tmp` und wird danach gelöscht.

| Feld (multipart) | Bedeutung |
|---|---|
| `file` | Pflicht. WAV (16 kHz wird direkt gelesen), sonst alles, was ffmpeg kann (mp3, m4a, ogg, webm …) |
| `response_format` | `json` (Standard, `{"text": …, "asr": …}`), `text`, `verbose_json` (mit `segments`, `start`/`end` in Sekunden), `srt`, `vtt` |
| `language` | optional: `auto`, `de`, `en`; wählt tatsächlich das lokale Backend gemäß [Sprachwahl](einrichtung.md#sprachwahl-und-cpu-modelle); ohne Angabe gilt der Serverstandard |
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
Bot und Hardwareaufnahme verwenden den Serverstandard; die Oberfläche erhält
in dieser Änderung keine neue Sprachauswahl.

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
`model_present`, `available`, `inference`, `message`, `model_env_override`,
`max_tokens` und `reasoning_effort`. `available` bedeutet ausschließlich: aktiviert
und ausgewähltes Modell in der Serverliste vorhanden. `inference` bleibt bei dieser
reinen Listenprüfung „nicht geprüft“. Das tatsächliche Ergebnis steht im Meeting
unter `summary.engine` und bei Heuristik unter `summary.warning`.

`PUT /api/llm` speichert zum Beispiel `{"model":"testmodell"}` lokal. Optionale
Felder: `max_tokens` (1–32768 oder null), `reasoning_effort` (null, `none`, `low`,
`medium`, `high`). Für gesetzte optionale Parameter muss
`compatible_parameters: true` ihre Unterstützung bestätigen. Unbekannte Modelle
oder ungültige Parameter ergeben 400; fehlerhafte Body-Typen 422.
Alle Endpunkte verwenden die bestehende API-Authentifizierung. Keine Downloads.

### Sprachmodell-Anbieter

`GET /api/llm` liefert Status, Modellliste, `provider`, `timeout`, `api_key_set`,
`env_overrides`, `external` und `external_confirmed`, niemals den Schlüssel.
`PUT /api/llm/provider` speichert `provider` (`ollama`, `openai`, `other`), `url`,
`model`, `timeout` (0,01–3600 Sekunden), optional `api_key` und `external_confirmed`.
Ein fehlender/null Schlüssel bleibt erhalten, `""` löscht ihn. Externe URLs erfordern
`external_confirmed: true`. `POST /api/llm/test` prüft den gespeicherten Anbieter:
Erreichbarkeit, Modell vorhanden, kurzer Probeaufruf als getrennte Felder.

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
