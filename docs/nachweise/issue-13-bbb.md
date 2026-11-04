# BigBlueButton-Adapter (#13)

Stand: 4. November 2026. Geprüft durch Codex (GPT-6.1).

`python -m pytest tests/test_bbb.py -q` liefert **11 bestandene Tests**.
Sechs Linkfälle prüfen konfigurierte Hosts, Greenlight-Pfade und ähnlich benannte
fremde Hosts. Ein API-Test prüft die Übergabe der Host-Konfiguration.
Vier Tests starten echtes Chromium gegen die lokale Nachbildung
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
