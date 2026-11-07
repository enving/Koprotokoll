# Einrichtung & Betrieb

[← zurück zum README](../README.md)

> **Community-Installer:** Mit [Pinokio](https://desktop.pinokio.co/):
> [Koprotokoll in Pinokio öffnen](https://pinokio.co/apps/github-com-bogotrax-koprotokoll-pinokio) → **Installieren** → **Starten**.
> Die manuelle Installation steht im [README](../README.md#-schnellstart).

## Lokales LLM für Zusammenfassungen (empfohlen)

Ohne LLM erkennt die lokale Heuristik auch deutsche Aufgaben, ausdrücklich genannte
Verantwortliche und Fristen wie „bis Freitag“, „nächste Woche“ oder „bis zum 3.11.“.
Fristen bleiben zunächst im gesprochenen Wortlaut. Bei „wir“ oder einer Frage an
„du“ ohne Namen bleibt die Zuständigkeit offen. Vage Vorschläge, verneinte und
bereits erledigte Aufgaben werden gefiltert. Die Regeln ersetzen keine inhaltliche
Prüfung; insbesondere verschachtelte Aussagen und indirekte Zusagen können fehlen.

Ohne LLM erstellt die App eine einfache extraktive Zusammenfassung. Für echte
KI-Zusammenfassungen ein lokales Modell über eine OpenAI-kompatible API bereitstellen,
z. B. mit [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b-instruct
python -m koprotokoll   # nutzt standardmäßig http://localhost:11434/v1
```

Genauso funktionieren llama.cpp (`llama-server`), LM Studio oder vLLM über `KOPROTOKOLL_LLM_URL`.

## Meeting-Bot (Teilnehmer wie bei Fireflies)

Der Bot startet einen eigenen Chromium, öffnet den Meeting-Link, setzt seinen Namen,
schaltet Kamera und Mikrofon aus und klickt „Beitreten“ bzw. „Teilnahme anfragen“.
Im Meeting greift er die Audiospuren aller Teilnehmer direkt an den WebRTC-Verbindungen ab
(keine virtuelle Soundkarte nötig) und nimmt sie auf. Er verlässt das Meeting, wenn es endet,
er allein ist, zu lange Stille herrscht oder die Höchstdauer erreicht ist. Danach läuft die
normale Verarbeitung (Parakeet, Sprecher, Zusammenfassung).

- **Manuell**: Meet-Link in das Feld in der Seitenleiste einfügen oder `POST /api/bots`
- **Automatisch**: Google oder Microsoft verbinden → der Bot tritt allen Kalenderterminen mit
  Meeting-Link bei (abgelehnte und abgesagte Termine werden übersprungen). Geplante Bots
  erscheinen bis zu 24 Stunden im Voraus in der Bot-Liste und lassen sich dort stornieren;
  beigetreten wird etwa eine Minute vor Beginn, entfallene Termine räumt der Abgleich automatisch weg.
- **Eigenes Google-Konto für den Bot (empfohlen)**: Google lässt anonyme Gäste oft gar nicht
  oder nur nach Freigabe herein. Einmalig `python -m koprotokoll bot-login` ausführen und im
  geöffneten Browser mit einem eigenen Bot-Konto anmelden.
- Läuft der Bot auf einem Server ohne Bildschirm und wird erkannt, hilft
  `KOPROTOKOLL_BOT_HEADLESS=0` zusammen mit `xvfb-run python -m koprotokoll`.

Unterstützt wird zurzeit **Google Meet**, **Jitsi** (meet.jit.si sowie eigene Instanzen
über `KOPROTOKOLL_JITSI_HOSTS`, kommagetrennte Hostnames) und **Microsoft Teams** über den
Web-Client (Gastbeitritt unter dem Bot-Namen). Voraussetzung für Teams: Die Organisation muss
**anonyme Beitritte erlauben** – sonst weist der Bot den Termin mit einer klaren Meldung ab
(Alternativ: Begleit-App, siehe `docs/plan.md`). Benötigt wird der **Einladungslink** aus
„Teilnehmer einladen → Link kopieren“ (…/l/meetup-join/… bzw. …/meet/…) – die Adresszeile der
„Sofortbesprechung“-Seite ist kein Meeting-Link. Zoom-Links werden weiterhin nur erkannt, der
Beitritt ist noch nicht umgesetzt. Mit `platform: "web"` nimmt der Bot den
Ton einer beliebigen Webseite auf (z. B. Webinar).

**BigBlueButton:** Der Adapter unterstützt Greenlight-Gastlinks (`/b/…` und
`/rooms/…/join`). `demo.bigbluebutton.org` wird erkannt; eigene Instanzen als
kommagetrennte Hostnamen in `KOPROTOKOLL_BBB_HOSTS` eintragen, beispielsweise
`bbb.example.org,konferenz.example.org`. Der Bot setzt seinen sichtbaren Namen,
wartet auf die Raumfreigabe und wählt ausschließlich **Nur zuhören**. Fehlt diese
Audio-Option oder verlangt der Raum einen nicht unterstützten Zugang, bricht er
mit einer Meldung ab. Geschlossene Installationen mit Anmeldung sind nicht abgedeckt.
Der Adapter wurde gegen eine lokale Browser-Nachbildung geprüft; ein echter BBB-
Beitritt samt Tonaufnahme steht noch aus ([Nachweis](nachweise/issue-13-bbb.md)).

> Hinweis: Aufzeichnungen nur mit Einverständnis der Teilnehmenden. Der Bot tritt sichtbar
> unter seinem Namen bei.

### Sprechernamen aus Google Meet

Der Meet-Adapter liest alle 500 ms sichtbare Sprechindikatoren und Namen aus der
Oberfläche. Die Zeitreihe liegt lokal im Meeting unter `meta.speaker_activity`;
sie enthält Zeiten relativ zum Aufnahmestart. Die Zuordnung nach der
Sprechererkennung benötigt mindestens zwei Sekunden eindeutiger Beobachtung und
60 Prozent der gesamten Redezeit dieses Sprecherlabels. Lücken von mehr als einer
Sekunde und gleichzeitig aktive Namen liefern keine Zuordnung. Manuelle Namen
werden auch bei erneutem Verarbeiten erhalten.

Diese Erkennung hängt von internen Meet-DOM-Klassen ab. Unbekannte Oberflächen
lassen die normalen Sprecherlabels bestehen. Die Browser-Brücke und Zuordnung
sind lokal getestet; die Prüfung in einem echten Meet mit mehreren Personen steht
noch aus. Andere Plattformen können dieselbe Adapter-Schnittstelle
`speaker_script` ergänzen. Namen und Zeitreihen werden nicht protokolliert.

## Betrieb mit Docker und Ollama

Voraussetzung: Docker Engine bzw. Docker Desktop mit Compose. Im Repository:

```bash
docker compose up -d --build
docker compose exec koprotokoll python -m koprotokoll create-key Browser
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

Unter `http://localhost:8000` den einmal ausgegebenen Schlüssel eintragen. Der
Container verlangt ihn auch bei lokalem Zugriff. Ollama lädt das Sprachmodell
ausdrücklich mit `pull`; bis es verfügbar ist, verwendet Koprotokoll die lokale
Heuristik für Zusammenfassungen. Die [Ollama-Dokumentation](https://docs.ollama.com/docker)
beschreibt den CPU-Betrieb und zusätzliche GPU-Konfigurationen. Diese Compose-Datei
nutzt CPU und veröffentlicht keinen Ollama-Port.

Das Volume `koprotokoll-data` enthält Datenbank, Medien, Bot-Profil und
Erkennungsmodelle unter `/data`; `ollama-models` enthält die LLM-Gewichte. Das Image
enthält keine Modelle. Für einen späteren Offline-Betrieb die Erkennungsmodelle
vorher laden:

```bash
docker compose exec koprotokoll python scripts/download_models.py
```

`docker compose down` erhält diese Volumes. `down -v` entfernt auch die Daten.
Die Anwendung läuft im Container als Benutzer mit UID 10001. Bei eigenen
Bind-Mounts muss dieser Benutzer auf das Datenverzeichnis schreiben können.

Standardmäßig ist Port 8000 nur am Loopback des Hosts erreichbar. Für einen
freigegebenen LAN-Zugang beispielsweise:

```bash
KOPROTOKOLL_BIND=0.0.0.0 KOPROTOKOLL_PUBLIC_URL=http://server.example:8000 docker compose up -d
```

Die Schlüsselpflicht bleibt dabei aktiv. `KOPROTOKOLL_PORT`,
`KOPROTOKOLL_PUBLIC_URL` und `KOPROTOKOLL_LLM_MODEL` können ebenfalls über die
Shell oder eine lokale `.env` für Compose gesetzt werden. Weitere Anwendungs-
Einstellungen bei Bedarf unter `environment` im Service ergänzen. Für HTTPS einen
lokalen Reverse-Proxy vorschalten. Native Outlook-COM-Funktionen sind in diesem
Linux-Container nicht verfügbar; Microsoft Graph und CalDAV bleiben nutzbar.

Der [Docker-Nachweis](nachweise/issue-19-docker.md) trennt Build-/Startprüfungen,
echten Audio-Upload und nicht geprüfte Betriebsarten.

## Google Kalender & Gmail einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die
   **Google Calendar API** und **Gmail API** aktivieren.
2. OAuth-Zustimmungsbildschirm konfigurieren (intern oder extern mit dir als Testnutzer).
3. Anmeldedaten → OAuth-Client-ID → **Webanwendung**, autorisierte Weiterleitungs-URI:
   `http://localhost:8000/api/integrations/google/callback` (bzw. `<KOPROTOKOLL_PUBLIC_URL>/api/integrations/google/callback`).
4. Client-ID und -Secret in der App unter **Integrationen & API** eintragen (oder per
   `KOPROTOKOLL_GOOGLE_CLIENT_ID` / `KOPROTOKOLL_GOOGLE_CLIENT_SECRET`) und **Mit Google verbinden**.

Berechtigungen: Kalender nur lesen, Gmail nur senden. Nach jedem Bot-Meeting geht die
Zusammenfassung an dich (optional auch an alle Termin-Teilnehmer).

## Microsoft Kalender & Outlook einrichten

> **Kurz:** Für Privatkonten (outlook.com, live.com) und Microsoft-365-Konten. Die Anmeldung
> erfolgt per **„Mit Microsoft verbinden"** (OAuth) – ganz ohne Kalender-Links zu kopieren.

1. Im [Azure-Portal](https://portal.azure.com) anmelden – geht auch mit einem privaten
   Microsoft- oder GitHub-Konto, ein Azure-Abonnement ist nicht nötig.
2. **App-Registrierungen** → **Neue Registrierung**:
   - **Name:** z. B. `Koprotokoll`
   - **Unterstützte Kontotypen:** unbedingt
     **„Any Entra ID Tenant + Personal Microsoft accounts"** („Alle Mandanten + persönliche
     Microsoft-Konten") wählen – sonst schlägt die Anmeldung mit privaten Konten fehl
     (Fehler „Mandant … nicht vorhanden").
   - **Umleitungs-URI:** Plattform **„Web"**, URI
     `http://localhost:8000/api/integrations/microsoft/callback`
     (bzw. `<KOPROTOKOLL_PUBLIC_URL>/api/integrations/microsoft/callback`)
3. **Client-ID** kopieren (Übersicht → „Application (client) ID").
4. **Client-Secret** anlegen: **Certificates & secrets** → **New client secret** →
   den **Wert** (Spalte „Value") **sofort kopieren**.
   > **Falle:** In der Tabelle stehen „Secret ID" und „Value" nebeneinander. Gebraucht wird
   > der **Wert** – die Secret ID führt zum Fehler `AADSTS7000215: Invalid client secret`.
   > Der Wert wird nur einmal angezeigt; ist er verloren, hilft nur ein neues Secret.
5. **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** → `Calendars.Read` und `Mail.Send` hinzufügen.
6. In Koprotokoll unter **Integrationen & API** → Microsoft-Karte → **Erweiterte Einstellungen**
   Client-ID und Secret eintragen (oder per `KOPROTOKOLL_MICROSOFT_CLIENT_ID` /
   `KOPROTOKOLL_MICROSOFT_CLIENT_SECRET`) und **Mit Microsoft verbinden**.

Berechtigungen: Kalender nur lesen, Mail nur senden. Die Termine der nächsten **7 Tage**
siehst du direkt auf der Startseite – inklusive Hinweis, ob dafür ein Bot eingeplant wird.
Kurz vor Terminbeginn (~15 Minuten) legt Koprotokoll den Bot automatisch an.

> **Für Organisationen:** Die App-Registrierung ist einmalig pro Mandant – am besten durch
> die IT erstellen lassen. Privatnutzende registrieren ihre eigene App (Schritte oben).

### Microsoft-Anmeldung mit Gerätecode

Die Anmeldung mit Code benötigt eine Entra-App-Registrierung mit delegierten
Rechten `User.Read`, `Calendars.Read`, `Mail.Send` und Offline-Zugriff.
Unter **Authentifizierung → Erweiterte Einstellungen** öffentliche Clientflows
erlauben. Für diesen Weg sind weder Client-Secret noch Weiterleitungs-URI nötig.
In Koprotokoll nur die Client-ID speichern, **Mit Microsoft per Code anmelden**
wählen, den angezeigten Code auf der verlinkten Microsoft-Seite bestätigen und
anschließend **Anmeldung prüfen** wählen. Warteintervall und Ablauf werden angezeigt.

Die bisherige Anmeldung mit Weiterleitung und Secret bleibt verfügbar.
Nach der Anmeldung erscheinen Kalendertermine wie bisher. Für Recaps unter
**E-Mail** den Versandweg **Microsoft 365 / Outlook.com** wählen und den Versand
einschalten. Er bleibt für neu eingerichtete Microsoft-Verbindungen zunächst aus.
Microsoft- und SMTP-Optionen werden getrennt gespeichert; es sendet nur der
gewählte Weg. Token werden lokal gespeichert und bei Bedarf einschließlich
eines erneuerten Refresh-Tokens ersetzt. Tokens und private Gerätecodes werden
nicht in Statusantworten angezeigt.

Die Gerätecode- und Mailwege sind mit nachgebildeten Microsoft-Endpunkten
geprüft. Ein echtes Konto wurde dabei nicht angemeldet, keine Mail extern versendet.

## Weitere Kalender-Quellen: CalDAV & ICS

### Klassisches Outlook unter Windows

Mit installiertem klassischem Outlook und `pip install -r requirements-windows.txt`
erscheint in **Kalender-Quellen** der Knopf **Outlook-Kalender lokal hinzufügen**.
Er verbindet den Standardkalender des lokalen Outlook-Profils, ohne Microsoft-
Cloud-Anmeldung. Serien werden als einzelne Vorkommen gelesen; abgelehnte
Termine plant der Scheduler nicht als Bot-Meeting ein. Die Quelle lässt sich
wie andere Kalender deaktivieren und entfernen. Es wird nichts in den Kalender
geschrieben. Das neue Outlook stellt diese COM-Schnittstelle nicht bereit.

Ein manueller Test mit einem echten Windows-Outlook steht aus. Automatisiert
geprüft sind COM-Aufrufreihenfolge, Windows-Datumsformat, Serien-IDs,
Teilnehmer und API-Verwaltung mit Nachbildungen. Outlook-Profil, lokale
Zugriffsabfragen und Zeitzonen auf einem echten Windows-System sind dadurch
nicht bestätigt.

Nach unabhängigem Review werden Termine über Outlooks `StartUTC`/`EndUTC`
gelesen. Die Windows-Formatierung erhält die lokalen Uhrfelder ausdrücklich;
der Jet-Filter liest ganze lokale Tage, danach begrenzt Python exakt auf das
angefragte UTC-Fenster. Regressionen bilden die pywin32-Konvertierung und eine
angebrochene letzte Minute nach. Der echte Windows-Test bleibt weiterhin offen.

### CalDAV und ICS

Neben Google und Microsoft lassen sich weitere Kalender als Quellen für die Bot-Einplanung
hinzufügen (mehrere Quellen parallel) – unter **Integrationen & API** → **Kalender-Quellen**.
Voraussetzung für beide Quellen sind die Zusatzpakete `icalendar` und `recurring-ical-events`
(`pip install icalendar recurring-ical-events`, in `requirements-dev.txt` enthalten).

**CalDAV-Konto (Hauptweg, „Anmeldung mit Passwort“):** Anbieter wählen (iCloud, Posteo,
mailbox.org, Fastmail, Nextcloud / eigener Server oder „Anderer Anbieter“), Benutzername und
Passwort eingeben. Koprotokoll sucht die Kalender des Kontos automatisch (RFC 6764) und wertet
sie **nur lesend** aus. Bei den meisten Anbietern muss ein **App-Passwort** angelegt werden
(Posteo: Einstellungen → Passwörter; iCloud: appleid.apple.com → App-Passwörter; Fastmail:
Settings → Privacy & Security → App passwords). Schlägt die Erkennung fehl, öffnen sich die
**Erweiterten Einstellungen** – dort lassen sich Server- und Kalender-URL von Hand eintragen
(z. B. bei Nextcloud `https://cloud.example.com/remote.php/dav` bzw. die URL der einzelnen
Kalender-Kollektion aus den Kalender-Einstellungen).

**ICS-Abolink (Experten-Option):** Unter „ICS-Abolink (Experten-Option)“ lässt sich ein
iCalendar-Abo eintragen (Outlook „Kalender veröffentlichen“, Google „geheime iCal-Adresse“,
Nextcloud, iCloud …) – inklusive Serien-Terminen und Zeitzonen, ebenfalls nur lesend.
Lokale `.ics`-Dateien müssen im Datenordner (`KOPROTOKOLL_DATA`, Standard `./data`) liegen.

> **Sicherheit:** Das CalDAV-Passwort wird **im Klartext** in der lokalen Datenbank
> (`koprotokoll.sqlite3` im Datenordner) gespeichert. Legt deshalb unbedingt ein
> **App-Passwort** an (nicht das Haupt-Kontopasswort) und widerruft es beim Entfernen
> der Quelle. Das Passwort lässt sich später nicht mehr einsehen – die API gibt es nie
> zurück, nur die Kennzeichnung, dass eines hinterlegt ist.

Beide Quellen erscheinen unter „Deine nächsten Termine“ und treiben die automatische
Bot-Einplanung an. Quellen lassen sich einzeln aktivieren/deaktivieren und wieder entfernen.

> Die Preset-Adressen sind die clients-üblichen CalDAV-Einstiege der Anbieter (Stand 10/2026)
> und nur teilweise gegen die Anbieter-Dokumentation geprüft. Weicht ein Anbieter ab, genügt
> der Eintrag der Server- oder Kalender-URL unter „Erweiterte Einstellungen“.

## E-Mail über SMTP

Unter **Integrationen & API → E-Mail** den Versandweg **SMTP** wählen,
Server, Port, Verschlüsselung, Benutzername und eigene Absenderadresse eintragen.
Falls der Anbieter es unterstützt, ein App-Passwort verwenden.
Speichern und mit **Test-Mail an mich** prüfen. Automatische Recaps sind für SMTP
zunächst aus; sie lassen sich für Bot-Meetings an die eigene Adresse und zusätzlich
an die Termin-Teilnehmenden aktivieren. Es wird jeweils nur der gewählte Mailweg benutzt.

Beispielwerte, geprüft anhand der verlinkten Quellen am 04.11.2026, ohne Anmeldung
bei einem Anbieter:

| Anbieter | Server | Port | Verschlüsselung | Quelle / Voraussetzung |
|---|---|---:|---|---|
| GMX | `mail.gmx.net` | 587 | STARTTLS | [GMX-Hilfe](https://hilfe.gmx.net/pop-imap/imap/imap-serverdaten.html), Zugriff für Mailprogramme aktivieren |
| WEB.DE | `smtp.web.de` | 587 | STARTTLS | [WEB.DE-Hilfe](https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html), Zugriff für Mailprogramme aktivieren |
| Posteo | `posteo.de` | 587 | STARTTLS | [Thunderbird-Anbieterkonfiguration](https://autoconfig.thunderbird.net/v1.1/posteo.de); alternativ Port 465 mit SSL |
| Outlook.com | `smtp-mail.outlook.com` | 587 | STARTTLS | [Microsoft-Hilfe](https://support.microsoft.com/de-de/outlook/pop-imap-and-smtp-settings-for-outlook-com): verlangt OAuth2; der SMTP-Passwortweg von Koprotokoll unterstützt diese Anmeldung nicht. Dafür den Microsoft-Anschluss verwenden. |

Umgebungsvariablen haben Vorrang vor gespeicherten Einstellungen:
`KOPROTOKOLL_SMTP_HOST`, `KOPROTOKOLL_SMTP_PORT` (Standard 587),
`KOPROTOKOLL_SMTP_USER`, `KOPROTOKOLL_SMTP_PASSWORD`, `KOPROTOKOLL_SMTP_FROM`
und `KOPROTOKOLL_SMTP_TLS` (`starttls`, `ssl`; `none` nur für Loopback-Testserver).
Bei gesetztem `SMTP_HOST` wird SMTP vorausgewählt; der automatische Versand
bleibt ausgeschaltet, bis er in der Oberfläche aktiviert wird.

Passwörter aus der Oberfläche liegen im Klartext in der lokalen Datenbank.
Die API liefert nur `password_set`, kein gespeichertes Passwort. Ein leeres
Passwortfeld beim Speichern behält den bisherigen Wert; zum Entfernen die
eigene Option **Gespeichertes Passwort löschen** nutzen. Die SMTP-Verbindung
prüft TLS-Zertifikate. Serverfehler geben keine Anmeldedaten aus.

## Zusammenfassung als E-Mail-Entwurf

Im fertig ausgewerteten Meeting öffnet **Als E-Mail öffnen** die verfügbaren Ziele.
Die **EML-Datei** enthält Betreff, Termin-Teilnehmende sowie Text- und HTML-Teil.
Du öffnest sie mit deinem Mailprogramm, prüfst den Entwurf und sendest selbst.
Ob ein Programm die Datei direkt als bearbeitbaren Entwurf öffnet, hängt vom
Mailprogramm ab; die Datei enthält dafür den Hinweis `X-Unsent: 1`.

Läuft Koprotokoll auf demselben Rechner, werden zusätzlich installiertes
**Thunderbird** und unter Windows **klassisches Outlook** angeboten.
Für Outlook zuerst `pip install -r requirements-windows.txt` ausführen.
Das neue Outlook bietet kein COM und wird so nicht unterstützt.
Bei Zugriff über ein anderes Gerät oder einen Proxy bleibt die EML-Datei
verfügbar; die API startet dort keine Programme auf dem Server.

Thunderbird wird mit `-compose` gestartet. Seine lokale HTML-Vorlage bleibt
unter `<Datenordner>/drafts/`, weil das Mailprogramm sie verzögert liest.
Argumentwerte werden vor der Übergabe kodiert; Meeting-Titel erzeugen dadurch
keine zusätzlichen Kommandozeilenfelder. Die Auswertung öffnet keinen Entwurf
automatisch. Kein Entwurfsziel sendet eine Nachricht.

Mit echtem Thunderbird und Windows-Outlook wurde dieser Weg hier nicht getestet.
Die automatisierten Tests prüfen Dateiformat und nachgebildete Programmaufrufe.

## Aufgaben exportieren

In der Meeting-Übersicht unter den Aufgaben **Aufgaben exportieren** öffnen.
Die **ICS-Datei** enthält VTODO-Aufgaben und funktioniert ohne verbundenes Konto.
Zusätzlich lassen sich Aufgaben gezielt in eine gewählte Liste eines verbundenen
CalDAV-Kontos, in lokale Outlook-Aufgaben oder in Microsoft To Do schreiben.
Die Ziele werden erst beim Öffnen des Exportbereichs geladen; geschrieben wird
erst nach **In gewählte Aufgabenliste exportieren**.

CalDAV verwendet die gespeicherten Zugangsdaten und sucht Listen mit VTODO-Unterstützung.
Klassisches Outlook benötigt die optionale Windows-Einrichtung aus dem Kalenderabschnitt.
Für Microsoft To Do in der Microsoft-Karte **Microsoft-Aufgabenrechte freigeben**
wählen und die zusätzliche delegierte Berechtigung `Tasks.ReadWrite` bestätigen.
Die normale Kalender-/Mail-Anmeldung fordert diese Aufgabenrechte nicht an.

Die Fälligkeit wird relativ zum Datum des Kalendertermins bestimmt, falls vorhanden,
sonst zum Anlegedatum des Meetings auf dem Server. „Bis Freitag“ meint den nächsten
Freitag einschließlich des gleichen Tages; „nächsten Freitag“ liegt bei gleichem
Wochentag eine Woche später. „Nächste Woche“ wird zum nächsten Montag, „Ende des Monats“
zum letzten Kalendertag. Numerische Daten ohne Jahr verwenden das Meetingjahr,
auch wenn dieser Tag bereits vergangen ist. Unklare Fristen bleiben ohne automatisch
gesetztes Datum; der ursprüngliche Wortlaut steht in der Aufgabenbeschreibung.

Wiederholter Export unveränderter Aufgaben erzeugt im selben Ziel keine Dubletten.
Vorhandene Aufgaben werden belassen; es gibt keinen laufenden Abgleich ihrer
Erledigung oder später geänderter Fristen. Ein geänderter Aufgabentext oder eine
andere Zuständigkeit, Frist oder Quellzeit kann eine neue Aufgabe erzeugen. Unterschiedliche
Vorkommen bleiben auch bei gleichem Text und gleicher Zuständigkeit getrennt. CalDAV nutzt stabile UIDs
und bedingtes Anlegen, Outlook eine eigene Eigenschaft und Microsoft To Do eine
verknüpfte Quellen-ID. Beschreibung und Link verweisen auf die Transkriptstelle,
soweit eine Zeitmarke vorliegt.

Getestet sind ein lokaler CalDAV-Testserver, nachgebildetes COM und Microsoft
Graph sowie ICS-Parsing mit Umlauten und langen Zeilen. Echter Windows-/To-Do-/
Nextcloud-Betrieb und der Import in konkrete Aufgabenprogramme sind hier nicht geprüft.

## Konfiguration

### Sprachwahl und CPU-Modelle

Der Standardmodus `auto` verwendet **NVIDIA Parakeet TDT 0.6B v3**. Das
[Modell](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) unterstützt 25 Sprachen,
darunter Deutsch und Englisch, und bestimmt die Ausgabesprache selbst. Die vorhandene
[onnx-asr-Runtime](https://github.com/istupakov/onnx-asr) lädt den
[ONNX-Export von istupakov](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx).
ASR und Silero-VAD verwenden ausdrücklich `CPUExecutionProvider`, Primeline `provider="cpu"`.
Eine GPU, CUDA und ein Cloud-Dienst werden nicht benötigt.

| Modus (`language` / `KOPROTOKOLL_ASR_LANGUAGE`) | Auswahl |
|---|---|
| `auto` | Mehrsprachiges v3; auch Englisch landet immer hier |
| `en` | Dasselbe v3; keine technische Spracherzwingung und keine Übersetzung |
| `de` | Deutsches Primeline; mit ausdrücklich gesetztem `KOPROTOKOLL_ASR_MODEL=nemo-parakeet-tdt-0.6b-v3` weiterhin v3 |

`KOPROTOKOLL_ASR_MODEL=parakeet-primeline` gilt nur für den Modus `de`.
Bei `auto` oder `en` hat die mehrsprachige Auswahl Vorrang. Andere Sprachcodes
und unbekannte Modellnamen erzeugen eine Fehlermeldung. Die übrigen v3-Sprachen
können über `auto` erkannt werden; für einzelne Codes bietet diese API keine Auswahl.
Wer dauerhaft nur Primeline verwenden möchte, setzt
`KOPROTOKOLL_ASR_LANGUAGE=de` und `KOPROTOKOLL_ASR_MODEL=parakeet-primeline`.

Modelle vor der ersten Verwendung ausdrücklich installieren:

```bash
python scripts/download_models.py                  # Standardmodus plus Sprechererkennung
python scripts/download_models.py --all-asr         # zusätzlich de vorbereiten
python scripts/download_models.py --language en --asr-only
```

Der Installer verwendet dieselbe Konfiguration wie der Server. `--all-asr` folgt
auch einer ausdrücklich konfigurierten v3-Wahl für `de`. Nach der Installation
läuft die ASR offline; bei fehlenden oder unvollständigen Dateien bricht sie sichtbar
ab, ohne Download oder anderes Backend. Vorhandene vollständige Modellordner werden
vom Installer beibehalten. Die festen Download-Revisionen und Dateilisten stehen in
[`speech.py`](../koprotokoll/speech.py). Darunter liegen standardmäßig:

- `models/parakeet-tdt-0.6b-v3-onnx/` für v3 (INT8 rund 670 MB),
- `models/silero-vad-onnx/` für die Erkennung von Sprachabschnitten (rund 2,3 MB),
- `models/parakeet-primeline-onnx/` für Primeline (rund 670 MB).

Die Pfade beziehen sich auf den Datenordner. Die Sprechererkennung bleibt unverändert;
für vollständig netzlosen Meeting-Betrieb auch ihre Modelle vorab installieren und
gegebenenfalls externe Kalender-/Mail-Integrationen abschalten. Eine Abschaltung des
lokalen LLM erfolgt mit `KOPROTOKOLL_LLM=0`.

### Migration vorhandener Modellpfade

`KOPROTOKOLL_ASR_PATH` bleibt als Altpfad erhalten. Die Dateistruktur ordnet ihn
Primeline (`tokens.txt`/Joiner) oder v3 (`vocab.txt` und `config.json`) zu. Er wird nur
an dieses Backend weitergereicht. Ein alter Primeline-Pfad unter `auto` führt deshalb
zum separaten v3-Standardordner; Primeline bleibt unter `de` am alten Ort verfügbar.
Fehlt der Altpfad oder ist er nicht eindeutig, erscheint eine Migrationsmeldung.

Für neue Installationen getrennte Pfade verwenden. Beispiel mit erfundenem Datenort:

```bash
export KOPROTOKOLL_ASR_MODEL=auto
export KOPROTOKOLL_ASR_LANGUAGE=auto
export KOPROTOKOLL_ASR_V3_PATH=/srv/koprotokoll/models/parakeet-tdt-0.6b-v3-onnx
export KOPROTOKOLL_ASR_PRIMELINE_PATH=/srv/koprotokoll/models/parakeet-primeline-onnx
export KOPROTOKOLL_ASR_VAD_PATH=/srv/koprotokoll/models/silero-vad-onnx
python scripts/download_models.py --all-asr
```

Ein modellspezifischer Pfad hat Vorrang vor dem Altpfad. Die Verzeichnisstruktur
verhindert eine Verwechslung zwischen den beiden Exportformaten; sie prüft weder
die Herkunft eigener Gewichte noch deren Inhalt kryptografisch. Bereits im
Hugging-Face-Hub-Cache liegende v3-Dateien können über `ASR_V3_PATH` auf ihren
Snapshot-Ordner verwendet werden. Silero benötigt zusätzlich seinen lokalen Pfad.
Bestehende Meetings werden nicht automatisch neu transkribiert. Neue Jobs sowie
vorher unterbrochene Jobs ohne Sprachangabe verwenden den nun eingestellten Standard.

### Herkunft und Grenzen

NVIDIA und der v3-ONNX-Export veröffentlichen das Modell unter **CC BY 4.0**:
bei Weitergabe NVIDIA sowie den Export von istupakov nennen und Lizenzhinweise
erhalten. Das deutsche Primeline ist eine Nachschulung von primeline auf NVIDIA-Basis;
sein [ONNX-Export von flozen1981](https://huggingface.co/flozen1981/parakeet-primeline-onnx)
steht ebenfalls unter CC BY 4.0. onnx-asr und
[Silero-VAD](https://huggingface.co/istupakov/silero-vad-onnx) stehen unter MIT;
der Silero-Lizenztext wird mitgeladen. Die Projektlizenz ersetzt keine Modelllizenz.

v3 verwendet Silero-VAD und höchstens 25 Sekunden lange Sprachabschnitte.
Wortzeiten werden aus Tokenzeiten abgeleitet. Kurze Wörter, Geräusche und
Sprachwechsel können falsch erkannt oder von VAD ausgelassen werden; `en` erzwingt
auch bei anderssprachigem Audio keine englische Ausgabe. Primeline teilt lange
Aufnahmen an leisen Stellen in höchstens 90 Sekunden, verstärkt leise Abschnitte
begrenzt und versucht leere Ergebnisse in höchstens 20 Sekunden erneut.
Ein leerer Text bei Stille ist ein erfolgreiches Ergebnis.
Die [synthetischen CPU-Proben](nachweise/issue-49-cpu.md) ersetzen keinen
Qualitätstest mit echten Meetings, Akzenten und überlappender Sprache.

| Variable | Standard | Bedeutung |
|---|---|---|
| `KOPROTOKOLL_DATA` | `./data` | Datenbank, Mediendateien, Modelle |
| `KOPROTOKOLL_ASR_MODEL` | `auto` | Auswahl gemäß Sprachmodus oben; explizites v3 gilt auch für `de` |
| `KOPROTOKOLL_ASR_LANGUAGE` | `auto` | Standardmodus für Imports, Bot, Hardwareaufnahme und Transkriptions-API |
| `KOPROTOKOLL_ASR_QUANT` | `int8` | v3: `int8` oder leer für fp32; Primeline bleibt INT8 |
| `KOPROTOKOLL_ASR_PATH` | – | abwärtskompatibler, anhand der Dateistruktur zugeordneter Altpfad |
| `KOPROTOKOLL_ASR_V3_PATH` | `models/parakeet-tdt-0.6b-v3-onnx` im Datenordner | eigener v3-Modellordner |
| `KOPROTOKOLL_ASR_PRIMELINE_PATH` | `models/parakeet-primeline-onnx` im Datenordner | eigener deutscher Modellordner |
| `KOPROTOKOLL_ASR_VAD_PATH` | `models/silero-vad-onnx` im Datenordner | lokales Silero-VAD für v3 |
| `KOPROTOKOLL_DIARIZATION` | `1` | `0` schaltet Sprechererkennung ab |
| `KOPROTOKOLL_DIARIZATION_THRESHOLD` | `0.8` | kleiner = mehr Sprecher |
| `KOPROTOKOLL_LLM` | `1` | `0` = nur Offline-Heuristik |
| `KOPROTOKOLL_LLM_URL` | `http://localhost:11434/v1` | OpenAI-kompatibler Endpunkt |
| `KOPROTOKOLL_LLM_MODEL` | `qwen2.5:7b-instruct` | Modellname |
| `KOPROTOKOLL_PUBLIC_URL` | `http://localhost:8000` | Basis-URL für OAuth-Weiterleitung und Links in E-Mails |
| `KOPROTOKOLL_REQUIRE_KEY` | `0` | `1` = API-Schlüssel auch für lokale Anfragen |
| `KOPROTOKOLL_GOOGLE_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ in der Oberfläche) |
| `KOPROTOKOLL_MICROSOFT_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ unter „Erweiterte Einstellungen“) |
| `KOPROTOKOLL_BOT_NAME` | `Koprotokoll` | Anzeigename des Bots im Meeting |
| `KOPROTOKOLL_BOT_HEADLESS` | `1` | `0` = sichtbares Browserfenster |
| `KOPROTOKOLL_BOT_CHROME` | – | Pfad zu Chrome/Chromium statt Playwright-Chromium |
| `KOPROTOKOLL_BOT_SILENCE_MINUTES` | `1` | Meeting verlassen nach so langer Stille |
| `KOPROTOKOLL_BOT_MAX_MINUTES` | `180` | Höchstdauer einer Aufnahme |
| `KOPROTOKOLL_BOT_ALONE_SECONDS` | `15` | Allein-Erkennung-Zeitraum (Sekunden) |
| `KOPROTOKOLL_BOT_ADMIT_MINUTES` | `10` | Wartezeit auf Einlass |
| `KOPROTOKOLL_JITSI_HOSTS` | – | Eigene Jitsi-Instanzen (kommagetrennte Hostnames) |
| `KOPROTOKOLL_BBB_HOSTS` | – | Eigene BigBlueButton-Instanzen (kommagetrennte Hostnamen) |

### Lokales Textmodell auswählen

Unter „Integrationen & API → Lokales Textmodell“ die Modellliste aktualisieren und
bewusst ein installiertes Modell auswählen. Koprotokoll ruft dazu `/v1/models`
über die bestehende LLM-Verbindung ab. Es lädt keine Modelle herunter und wechselt
nicht automatisch. „Modell vorhanden“ bestätigt die Liste, keinen erfolgreichen
Inferenzaufruf. Erst die Zusammenfassung nennt `llm:<Modell>`; bei einem Fehler
oder Timeout zeigt sie eine Warnung und verwendet die Heuristik.

Die Auswahl liegt in `KOPROTOKOLL_DATA/llm-settings.json` und gilt auch für die
Auswertung nach dem ausdrücklichen Hardwareaufnahme-Stopp. Während der Aufnahme
findet darüber keine Inferenz statt. `KOPROTOKOLL_LLM_MODEL` hat Vorrang vor der
gespeicherten Auswahl; dieser Vorrang wird angezeigt. URL, Aktivierung und Timeout
bleiben über `KOPROTOKOLL_LLM_URL`, `KOPROTOKOLL_LLM` und
`KOPROTOKOLL_LLM_TIMEOUT` konfiguriert.

Ausgabelimit (1–32768 Tokens) und Thinking sind optional. Leere Werte senden keinen
zusätzlichen Parameter. Nur aktivieren, wenn die Dokumentation des verwendeten
Servers **und Modells** `max_tokens` beziehungsweise `reasoning_effort` bestätigt;
die Oberfläche verlangt diese Bestätigung. „Aus“ sendet `none`. Nicht jedes Modell
unterstützt alle Stufen. Ein abgelehnter Parameter wird als Inferenzfehler sichtbar;
es gibt keinen stillen zweiten Versuch mit geänderten Parametern.

Bei einem Wechsel der Adresse oder des Modells werden diese Zusatzparameter
zurückgesetzt. Aktiviere sie für die neue Verbindung nur, wenn sie dort unterstützt
werden. Laufende Auswertungen verwenden ihren bisherigen Anbieterstand; Änderungen
wirken auf die nächste Auswertung.

Originalquellen: [Ollama OpenAI-Kompatibilität](https://docs.ollama.com/api/openai-compatibility),
[Thinking](https://docs.ollama.com/capabilities/thinking),
[JSON-Ausgabe](https://docs.ollama.com/capabilities/structured-outputs).

### Eigenen Sprachmodell-Anbieter einrichten

Unter **Sprachmodell** eine Vorlage wählen, Basis-URL (einschließlich `/v1`),
Modellname, optional API-Schlüssel und Timeout eintragen. Die Modellliste kann einen
Namen übernehmen. Zuerst speichern, dann den gespeicherten Anbieter testen.
Für externe Adressen die Übertragung von Transkripttext ausdrücklich bestätigen;
ohne Bestätigung bleibt die Auswertung gesperrt. Externe Auswertungen tragen am
Meeting-Ergebnis die verwendete Dienstadresse. Lokale Defaults bleiben erhalten.

Die Werte liegen in `llm-settings.json` im Datenordner (Dateirechte 0600, Schlüssel
im Klartext auf dem Server; Datenordner schützen). Schlüssel werden nicht zurückgegeben;
leer lassen behält sie, die Löschoption entfernt sie. Die Umgebungsvariablen
`KOPROTOKOLL_LLM_URL`, `KOPROTOKOLL_LLM_API_KEY`, `KOPROTOKOLL_LLM_MODEL` und
`KOPROTOKOLL_LLM_TIMEOUT` haben jeweils Vorrang, auch beim Löschen eines gespeicherten
Schlüssels. Die Oberfläche nennt diese Felder als vom Server vorgegeben.
Adresswechsel benötigen eine erneute externe Bestätigung. Als lokal gelten localhost,
Loopback, private IPv4-Netze und IPv6-ULA; andere Hostnamen gelten vorsorglich als extern.
