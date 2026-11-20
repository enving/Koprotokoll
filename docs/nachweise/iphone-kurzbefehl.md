# iPhone-Aufnahme über Koprotokoll · 20.11.2026

Die native T3-iPhone-App transkribiert nach dem geprüften Quellstand mit Apples
Erkennung auf dem Gerät. Diese Funktion wurde nicht geändert. Der zusätzliche
Kurzbefehl nimmt stattdessen Audio auf und verwendet Koprotokolls bereits
vorhandene `/v1/audio/transcriptions`-Schnittstelle.

Die Vorlage entsteht mit `scripts/create_iphone_shortcut.py`. Sie enthält keinen
API-Schlüssel: Der wird erst beim Import abgefragt, nachdem Apple die Vorlage
signiert hat. Danach gelangen die Audiodaten an die konfigurierte Mac-Adresse;
die Antwort wird angezeigt und in die Zwischenablage kopiert. Kein Meeting,
keine Zusammenfassung und kein automatisches Absenden in T3.

## Ausgeführte Prüfungen

- Apple-Kurzbefehl tatsächlich signiert und in macOS importiert.
- Importfrage für den Schlüssel und die sechs Aktionen in der Kurzbefehle-App
  geprüft. Erste Zwischenfassung hatte unpassende Textvariablen; korrigierte
  Fassung verwendet `WFTextTokenString`, als Textvariablen in der App sichtbar.
- Multipart-Dateifeld mit der Ausgabe der Aufnahmeaktion verknüpft.
- Eigene alte Probeimporte entfernt; keine fremden Kurzbefehle verändert.
- Synthetische deutsche Sprachprobe über die echte lokale API korrekt erkannt.
- Dieselbe Probe über einen privaten HTTPS-Tailscale-Zugang mit eigenem
  API-Schlüssel korrekt erkannt; ohne Schlüssel liefert der Zugang HTTP 401.
- Vier Regressionstests prüfen URLs ohne Zugangsdaten, sichere HTTPS-Vorgabe,
  Dateiverknüpfung und die beim nativen Import benötigte Textserialisierung.

Der persönliche Schlüssel und die personalisierte signierte Datei liegen nur in
ignorierten lokalen Daten. Die API-Adresse im Generator-Aufruf ist frei wählbar;
Beispiel und Einrichtung stehen in [API-Dokumentation](../api.md#iphone-kurzbefehl-auf-dem-mac-diktieren).

Noch offen: reale iPhone-Aufnahme, Netzberechtigungen auf dem iPhone und Einfügen
im mobilen T3-Eingabefeld durch den Nutzer. Import und HTTPS-Probe allein belegen
diesen gesamten Ablauf nicht. Mac und Koprotokoll müssen laufen, das iPhone muss
das private Netz erreichen. Die Standard-T3-Mikrofontaste bleibt unverändert.
