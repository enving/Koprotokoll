# BigBlueButton-Adapter (#13)

Stand: 4. November 2026. Geprüft durch Codex (GPT-6.1).

`python -m pytest tests/test_bbb.py -q` liefert **16 bestandene Tests**.
Sechs Linkfälle prüfen konfigurierte Hosts, Greenlight-Pfade und ähnlich benannte
fremde Hosts. Ein API-Test prüft die Übergabe der Host-Konfiguration.
Sechs Tests starten echtes Chromium gegen die lokale Nachbildung
`tests/fixtures/bbb_stub.html`: Gastname, Wartebereich, Ablehnung, fehlendes
„Nur zuhören“ und regulärer Beitritt mit anschließendem Verlassen. Die Nachbildung
enthält absichtlich auch „Microphone“ und „End meeting“; beide dürfen nie gewählt werden.

Die Selektoren orientieren sich an den öffentlich verfügbaren Komponenten
[AudioModal](https://github.com/bigbluebutton/bigbluebutton/blob/develop/bigbluebutton-html5/imports/ui/components/audio/audio-modal/component.jsx),
[LeaveMeetingButton](https://github.com/bigbluebutton/bigbluebutton/blob/develop/bigbluebutton-html5/imports/ui/components/nav-bar/leave-meeting-button/component.jsx)
und [Greenlight](https://github.com/bigbluebutton/greenlight).

**Offen:** Eine reale Instanz wurde nicht betreten. Die Tests beweisen weder
Kompatibilität mit allen BBB-/Greenlight-Versionen noch eine funktionierende
WebRTC-Tonaufnahme dort. Vor Schließen von #13 einen freigegebenen Gast-Raum
betreten, Ton mit Zustimmung aufnehmen, stoppen, Verarbeitung und Verlassen prüfen.

Nach unabhängigem Review wurden die Originaltexte aus den
[Greenlight-Sprachdateien](https://github.com/bigbluebutton/greenlight/tree/master/app/assets/locales)
und den [BBB-Sprachdateien](https://github.com/bigbluebutton/bigbluebutton/tree/develop/bigbluebutton-html5/public/locales)
ergänzt: „Konferenz beitreten“, die Pflichtzustimmung zur möglichen Aufzeichnung
in beiden Sprachen und drei tatsächliche Endmeldungen. Diese Fälle haben eigene
Chromium-Prüfungen. Die Nachbildung ist dadurch näher am Original, bleibt aber
kein Ersatz für den ausstehenden Live-Beitritt.
