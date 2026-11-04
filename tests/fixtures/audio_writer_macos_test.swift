import AVFoundation
import Foundation

@main
struct AudioWriterTests {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let system = root.appendingPathComponent("system.wav")
        let mic = root.appendingPathComponent("microphone.wav")
        let writer = try AudioWriter(systemURL: system, micURL: mic)
        writer.markStarted(at: 100)
        func buffer(rate: Double, channels: AVAudioChannelCount) -> AVAudioPCMBuffer {
            let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate,
                                       channels: channels, interleaved: false)!
            let count = AVAudioFrameCount(rate / 10)
            let result = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count)!
            result.frameLength = count
            for channel in 0..<Int(channels) {
                for index in 0..<Int(count) { result.floatChannelData![channel][index] = 0.2 }
            }
            return result
        }
        writer.receive(buffer(rate: 48_000, channels: 2), key: "system", at: 100)
        writer.receive(buffer(rate: 44_100, channels: 1), key: "microphone", at: 100.05)
        writer.receive(buffer(rate: 48_000, channels: 2), key: "system", at: 100.1)
        writer.receive(buffer(rate: 44_100, channels: 1), key: "microphone", at: 100.15)
        try writer.finish(at: 100.3)
        let original = try Data(contentsOf: system)
        do {
            _ = try AudioWriter(systemURL: system, micURL: mic)
            fatalError("Bestehende Aufnahme darf nicht überschrieben werden.")
        } catch { }
        guard try Data(contentsOf: system) == original else { fatalError("Original wurde verändert.") }
        let firstSample = try AudioWriter(systemURL: root.appendingPathComponent("first-system.wav"),
                                          micURL: root.appendingPathComponent("first-microphone.wav"))
        firstSample.markStarted()
        firstSample.receive(buffer(rate: 48_000, channels: 2), key: "system", at: 50)
        firstSample.receive(buffer(rate: 44_100, channels: 1), key: "microphone", at: 50.05)
        try firstSample.finish(at: 50.2)
        print("Audio-Konvertierung und Originalschutz geprüft")
    }
}
