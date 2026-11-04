import Foundation

@main
struct HotkeyTests {
    static func main() {
        let cases: [(String, [(Double, Bool, Bool)], Int)] = [
            ("Zwei kurze Alt-Tipps", [(0, true, false), (0.05, false, false),
                                      (0.20, true, false), (0.25, false, false)], 1),
            ("Halten ist kein Doppeltipp", [(0, true, false), (0.6, false, false),
                                           (0.7, true, false), (0.75, false, false)], 0),
            ("Zu großer Abstand", [(0, true, false), (0.05, false, false),
                                   (1, true, false), (1.05, false, false)], 0),
            ("Alt-Kombination unterbricht", [(0, true, false), (0.01, true, true),
                                            (0.05, false, false), (0.2, true, false),
                                            (0.25, false, false)], 0),
            ("Andere Taste zwischen Tipps", [(0, true, false), (0.05, false, false),
                                            (0.1, false, true), (0.2, true, false),
                                            (0.25, false, false)], 0),
            ("Gehaltene weitere Taste", [(0, true, true), (0.05, false, false),
                                        (0.2, true, false), (0.25, false, false)], 0),
            ("Wiederholter Zustand zählt nicht", [(0, true, false), (0.01, true, false),
                                                 (0.02, true, false), (0.05, false, false)], 0),
            ("Zwei Doppeltipps schalten zweimal", [(0, true, false), (0.05, false, false),
                                                  (0.2, true, false), (0.25, false, false),
                                                  (0.4, true, false), (0.45, false, false),
                                                  (0.6, true, false), (0.65, false, false)], 2),
        ]
        for (name, events, expected) in cases {
            var detector = ModifierDoubleTap()
            var count = 0
            for (time, down, other) in events {
                if detector.update(down: down, otherKey: other, at: time) { count += 1 }
            }
            guard count == expected else {
                FileHandle.standardError.write(Data("Fehler: \(name), \(count) statt \(expected)\n".utf8))
                exit(1)
            }
        }
        var expired = ModifierDoubleTap()
        _ = expired.update(down: true, otherKey: false, at: 0)
        _ = expired.update(down: false, otherKey: false, at: 0.05)
        _ = expired.update(down: false, otherKey: false, at: 1)
        guard !expired.needsOtherKeys else { exit(1) }
        let suite = "koprotokoll-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = DesktopPreferences(defaults: defaults)
        guard preferences.shortcutEnabled, preferences.modifier == .option, preferences.sourceName == nil else { exit(1) }
        defaults.set("Vorhandenes Mikrofon", forKey: "audioSourceName")
        preferences.modifier = .control
        preferences.shortcutEnabled = false
        let reopened = DesktopPreferences(defaults: UserDefaults(suiteName: suite)!)
        guard reopened.sourceName == "Vorhandenes Mikrofon", reopened.modifier == .control,
              !reopened.shortcutEnabled else { exit(1) }
        defaults.set("unbekannte-taste", forKey: "shortcutModifier")
        guard preferences.modifier == .option else { exit(1) }

        var configurable = ConfigurableShortcut()
        configurable.configure(enabled: true, modifier: .option)
        _ = configurable.update(down: true, otherKey: false, at: 0)
        _ = configurable.update(down: false, otherKey: false, at: 0.05)
        configurable.configure(enabled: true, modifier: .control)
        _ = configurable.update(down: true, otherKey: false, at: 0.20)
        guard !configurable.update(down: false, otherKey: false, at: 0.25) else { exit(1) }
        _ = configurable.update(down: true, otherKey: false, at: 0.40)
        guard configurable.update(down: false, otherKey: false, at: 0.45) else { exit(1) }

        _ = configurable.update(down: true, otherKey: false, at: 1)
        _ = configurable.update(down: false, otherKey: false, at: 1.05)
        configurable.configure(enabled: false, modifier: .control)
        _ = configurable.update(down: true, otherKey: false, at: 1.20)
        guard !configurable.update(down: false, otherKey: false, at: 1.25) else { exit(1) }
        configurable.configure(enabled: true, modifier: .control)
        _ = configurable.update(down: true, otherKey: false, at: 1.30)
        guard !configurable.update(down: false, otherKey: false, at: 1.35) else { exit(1) }
        print("14 Tastenkürzel- und Einstellungsfälle bestanden")
    }
}
