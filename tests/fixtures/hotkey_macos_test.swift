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
            var detector = AltDoubleTap()
            var count = 0
            for (time, down, other) in events {
                if detector.update(down: down, otherKey: other, at: time) { count += 1 }
            }
            guard count == expected else {
                FileHandle.standardError.write(Data("Fehler: \(name), \(count) statt \(expected)\n".utf8))
                exit(1)
            }
        }
        var expired = AltDoubleTap()
        _ = expired.update(down: true, otherKey: false, at: 0)
        _ = expired.update(down: false, otherKey: false, at: 0.05)
        _ = expired.update(down: false, otherKey: false, at: 1)
        guard !expired.needsOtherKeys else { exit(1) }
        print("9 Tastenkürzel-Fälle bestanden")
    }
}
