# Nachweise für Pull Requests

Hier liegen kurze Beschreibungen und bei Bedarf kleine, bereinigte Skripte für
reproduzierbare Prüfungen. Große Aufnahmen nicht in Git einchecken: Ein Video kann
als Anhang zu einem öffentlichen GitHub-PR-Kommentar hochgeladen und dort verlinkt
werden. Vor dem Hochladen Bild, Ton und Testdaten auf vertrauliche Inhalte prüfen.
GitHub-Anhänge sind externe Ablage; Erreichbarkeit und Zugriff nach dem Hochladen
prüfen. Keine kurzlebigen CI-Artefakte als einzige dauerhafte Kopie verwenden.

Ein Nachweis nennt:

1. PR, genaue Commit-IDs und gegebenenfalls Kombinationen oder lokale Änderungen.
2. Umgebung, Vorbereitung, verwendete Testdaten und ausführbare Schritte.
3. Testbefehl mit Ergebnis und übersprungenen Tests; bei Video den direkten Link.
4. Beobachtung und Grenzen: Was ist sichtbar, was lief außerhalb der Aufnahme,
   was wurde simuliert und was wurde nicht getestet?

Im PR auf den Nachweis verweisen und offene Punkte ausdrücklich stehen lassen.
Nach Änderungen am PR-Kopf die Aussagekraft erneut prüfen und nötigenfalls erneuern.

## Änderungen mit show-me erklären

Agenten sollen ihre gebauten Änderungen in PRs und bei Bedarf in Issues mit
[show-me von HumanLayer](https://github.com/humanlayer/skills#show-me) verständlich
zeigen und erklären. Der Skill erstellt kompakte Diagramme, Codeskizzen und
HTML-Erklärungen. Installation nach der Originalanleitung:

```bash
npx skills add humanlayer/skills --skill show-me
```

Den installierten Skill mit `/show-me` auf die konkrete Änderung anwenden: Welches
Problem löst sie, wie läuft sie ab, und was sieht oder tut der Nutzer danach?
Das Ergebnis im PR oder Issue einbetten oder dauerhaft verlinken; bei HTML eine
kurze Erklärung direkt im GitHub-Text ergänzen. Eine erzeugte Illustration ist
kein Beleg für einen ausgeführten Test. Für tatsächliche Prüfungen gelten die
Nachweisanforderungen oben. Ist der Skill nicht verfügbar, dieselbe Erklärung
mit einem Markdown-/Mermaid-Diagramm oder einer beschrifteten Abbildung liefern
und die Einschränkung nennen.
