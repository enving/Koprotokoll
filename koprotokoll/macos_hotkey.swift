import Foundation

enum ShortcutModifier: String, CaseIterable {
    case option, control, shift

    var title: String {
        switch self {
        case .option: return "Alt / Option (⌥) zweimal"
        case .control: return "Control (⌃) zweimal"
        case .shift: return "Umschalt (⇧) zweimal"
        }
    }

    var keyCodes: [UInt16] {
        switch self {
        case .option: return [58, 61]
        case .control: return [59, 62]
        case .shift: return [56, 60]
        }
    }
}

struct DesktopPreferences {
    let defaults: UserDefaults

    var sourceName: String? {
        get { defaults.string(forKey: "audioSourceName") }
        nonmutating set { defaults.set(newValue, forKey: "audioSourceName") }
    }
    var shortcutEnabled: Bool {
        get { defaults.object(forKey: "shortcutEnabled") == nil || defaults.bool(forKey: "shortcutEnabled") }
        nonmutating set { defaults.set(newValue, forKey: "shortcutEnabled") }
    }
    var modifier: ShortcutModifier {
        get { ShortcutModifier(rawValue: defaults.string(forKey: "shortcutModifier") ?? "") ?? .option }
        nonmutating set { defaults.set(newValue.rawValue, forKey: "shortcutModifier") }
    }
}

struct ModifierDoubleTap {
    static let maximumGap = 0.45
    static let maximumHold = 0.35
    private var wasDown = false
    private var pressedAt = 0.0
    private var firstRelease: Double?
    private var interrupted = false
    var needsOtherKeys: Bool { wasDown || firstRelease != nil }

    mutating func update(down: Bool, otherKey: Bool, at now: Double) -> Bool {
        var triggered = false
        if let first = firstRelease, now - first > Self.maximumGap { firstRelease = nil }
        if otherKey {
            firstRelease = nil
            interrupted = true
        }
        if down && !wasDown {
            pressedAt = now
            interrupted = otherKey
        }
        if !down && wasDown {
            if !interrupted && now - pressedAt <= Self.maximumHold {
                if let first = firstRelease, now - first <= Self.maximumGap {
                    triggered = true
                    firstRelease = nil
                } else {
                    firstRelease = now
                }
            } else {
                firstRelease = nil
            }
            interrupted = false
        }
        wasDown = down
        return triggered
    }
}

struct ConfigurableShortcut {
    private(set) var enabled = false
    private(set) var modifier = ShortcutModifier.option
    private var detector = ModifierDoubleTap()
    var needsOtherKeys: Bool { detector.needsOtherKeys }

    mutating func configure(enabled: Bool, modifier: ShortcutModifier) {
        if self.enabled != enabled || self.modifier != modifier { detector = ModifierDoubleTap() }
        self.enabled = enabled
        self.modifier = modifier
    }

    mutating func update(down: Bool, otherKey: Bool, at now: Double) -> Bool {
        enabled && detector.update(down: down, otherKey: otherKey, at: now)
    }
}
