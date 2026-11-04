# Prüfung der direkt umsetzbaren Issues, November 2026

Auftrag: alle direkt umsetzbaren offenen Aufgaben abarbeiten. Ausgang:
`75679f72884d2b63e97e4c2785353f4b12855c37`, Anwendungsstand dieser Prüfung:
`4525dde` (4. November 2026, Codex / GPT-6.1, GitHub-Konto `enving`).
Nach der ersten Schlussprüfung wurde zusätzlich der Fall einer langsamen
Microsoft-Codeprüfung abgesichert: Pro Anmeldegeneration läuft höchstens eine
Provider-Anfrage; auch nach Ablauf des Intervalls kann sie nicht überholt werden.
Dieser Nachtrag und der Regressionstest gehören zum selben Folgecommit.

## Umsetzungen und Grenzen

| Issue | Umsetzung | Prüfungen und noch offene manuelle Fälle |
|---|---|---|
| #20 | Deutsche Aufgaben, Namen, Fristen und Satztrennung | [52 feste Beispiele und weitere Satzgrenzen](issue-20-aufgaben.md); kein Benchmark echter Meetings |
| #2 | Primeline als Standard, v3 bleibt wählbar | [Echte Engine-Auswertung](issue-2-primeline.md), feste Modellrevision, Token-Dauern, Chunk-/Retry-Tests |
| #7 | SMTP mit TLS, ein gewählter Mailweg und Testmail | [Lokaler SMTP-Server und vier Browservarianten](issue-7-smtp.md); keine externen Mails |
| #8 | EML sowie native Outlook-/Thunderbird-Entwürfe | [MIME, Chromium, Prozess-/COM-Nachbildung](issue-8-entwuerfe.md); echte Programme noch zu prüfen |
| #9 | Klassischer Outlook-Kalender per COM | UTC, Windows-Locale, Serien und Zeitfenster mit nachgebildetem COM geprüft; echter Windows-Test offen |
| #10 | Gerätecode, Kalender und Versand über Graph | Provider-Nachbildung, Tokenwechsel, Abbruch und parallele Anfragen, Pagination und echte UI; echtes Konto offen |
| #11 | VTODO, CalDAV-, Outlook- und To-Do-Aufgabenexport | ICS geparst, echter lokaler CalDAV-Server, COM/Graph nachgebildet, Browser mit Quellzeitsprung; echte Zielsysteme offen |
| #13 | BigBlueButton-Gastadapter mit „Nur zuhören“ | [16 Link-/API-/Chromium-Tests](issue-13-bbb.md); echter BBB-Beitritt samt Audio offen |
| #16 | Namen aus zeitgleichen Meet-Hinweisen | [Browser, Roh-Diarisierungszeiten, Mehrdeutigkeit und manuelle Reserven](issue-16-sprechernamen.md); echtes Meet offen |
| #19 | Docker, Compose mit Ollama und Build-Workflow | [Image gebaut, echte Erkennung mit Diarisierung und Neustart](issue-19-docker.md); AMD64/GPU und generatives LLM nicht geprüft |

#14 war durch den bereits gemergten PR #26 implementiert. Das Issue wurde nach
Abgleich geschlossen; es wurde kein neuer echter Teams-Beitritt durchgeführt.
Der GitHub-Fahrplan #21 wurde an die schon erledigten Aufgaben angeglichen.

#35 bleibt bei Bogotrax. #31 braucht eine menschlich geprüfte Hörreferenz und
weitere reale Nachweise. #4 und #15 sind Diskussionen; #17 und #18 sind Epics
ohne `agent-ready`-Label. Diese Aufgaben wurden nicht als erledigt ausgegeben.

## Gesamttests und Review

`python -m pytest -q -ra`: **269 bestanden**, keine übersprungenen Tests.
Die neuen Oberflächenabläufe wurden in echtem Chromium gegen die lokale API
geprüft; SMTP bei 1280/390 Pixeln und jeweils hell/dunkel. Der mechanische
Impeccable-Detektor für die drei Frontend-Dateien lieferte keine Befunde.
`git diff --check` ohne Befund.

Unabhängige Agenten prüften die Implementierungen mehrfach. Reproduzierte
Befunde wurden mit Gegenbeispielen behoben: Satzgrenzen und Aufgabenbesitzer,
Primeline-Wortenden, getrennte Mail-Einstellungen, Geheimnisse in 422-Antworten,
Outlook-Zeitzonen, Microsoft-Abbruch/Abfrageintervall, Mailbox-Adressen,
Aufgabenkennungen, fremde Browser-Origins, BBB-Originaltexte und sichere
Sprecherzuordnung anhand unveränderter Diarisierungszeiten. Die abschließende
Freigabe bezieht sich auf den danach unveränderten Commit und wird im PR angegeben.

Es wurden noch keine neuen Branches gepusht und keine neuen PRs erstellt.
Die GitHub-CI und die noch offenen echten Plattform-/Clientprüfungen sind damit
nicht durch die lokalen grünen Tests ersetzt.
