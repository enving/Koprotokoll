# Sprecherzahl vorgeben · 20.11.2026

Eine einzelne Stimme kann von der automatischen Sprechererkennung in mehrere
Sprecher aufgeteilt werden. Aufnahme und Upload bieten deshalb ausdrücklich
„Nur ich (1 Person)“, 2–20 Personen und „Automatisch erkennen“ an. Ohne bisherige
Auswahl ist „Nur ich“ voreingestellt. Die Auswahl bleibt im jeweiligen Browser
bzw. in der Mac-App gespeichert. Eine bereits explizit gewählte Mac-Automatik
bleibt erhalten. Browser und Mac-App speichern ihre Auswahl getrennt.

Bei einer Person überspringt die vorhandene Pipeline die Sprechererkennung.
Die automatische Erkennung selbst wurde nicht verändert. Bei mehreren Personen
wird die Vorgabe an das vorhandene Clustering weitergereicht; eine allgemeine
Verbesserung der Erkennungsqualität wird daraus nicht abgeleitet.

## Prüfung

- `pytest -q`: **358 bestanden**, keine übersprungenen Tests, 139,85 Sekunden.
- Neuer Browser-Regressionstest zuerst fehlgeschlagen: Auswahlfeld fehlte.
- `pytest -q tests/test_single_speaker.py tests/test_desktop.py`: 13 bestanden.
- Browserprüfung bei 1280 und 390 Pixeln: Vorauswahl, feste Anzahl, Automatik,
  Speicherung nach Neuladen, Übernahme zwischen Aufnahme und Upload und
  Upload bis zur Pipeline geprüft; keine JavaScript-Fehler oder Überbreite.
- Swift-Test: neue Installation mit einer Person, explizite Automatik und eine
  Person jeweils nach erneutem Lesen der Einstellungen geprüft.
- Mac-App mit `build_app(Settings(), configure_launcher=False)` tatsächlich
  kompiliert; neu gestartete App zeigt „Nur ich (1 Person)“ in den Einstellungen.
- `node --check koprotokoll/static/app.js`, `git diff --check` und mechanische
  Oberflächenprüfung ohne Befund.

Vorhandene Tests sichern weiterhin die verlustfreie Ein-Sprecher-Korrektur und
Wiederherstellung einschließlich Text, Wortzeiten, Markierungen und Suchindex ab.
Keine neue Aufnahme oder Qualitätsmessung der automatischen Erkennung durchgeführt.
