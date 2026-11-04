import Foundation

struct AltDoubleTap {
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
