import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

@available(macOS 15.0, *)
final class AudioWriter: NSObject, SCStreamOutput, SCStreamDelegate {
    private let systemURL: URL
    private let micURL: URL
    private let lock = NSLock()
    private var files: [String: AVAudioFile] = [:]
    private var firstTimes: [String: CMTime] = [:]
    private var converters: [String: AVAudioConverter] = [:]
    private var levels: [String: Double] = ["system": -120, "microphone": -120]
    private var lastReport = Date.distantPast
    private var startedAt = Date.distantPast
    private var captureStart = CMTime.invalid
    private var failureReported = false
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48_000,
                                             channels: 2, interleaved: true)!

    init(systemURL: URL, micURL: URL) {
        self.systemURL = systemURL
        self.micURL = micURL
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard sampleBuffer.isValid,
              type == .audio || type == .microphone,
              let description = sampleBuffer.formatDescription?.audioStreamBasicDescription,
              let format = withUnsafePointer(to: description, { AVAudioFormat(streamDescription: $0) }) else { return }
        let key = type == .audio ? "system" : "microphone"
        do {
            try sampleBuffer.withAudioBufferList { audioBufferList, _ in
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                    bufferListNoCopy: audioBufferList.unsafePointer) else { return }
                lock.lock()
                defer { lock.unlock() }
                let url = key == "system" ? systemURL : micURL
                if files[key] == nil {
                    files[key] = try AVAudioFile(forWriting: url, settings: outputFormat.settings,
                                                 commonFormat: .pcmFormatInt16, interleaved: true)
                    converters[key] = AVAudioConverter(from: format, to: outputFormat)
                    firstTimes[key] = captureStart.isValid ? captureStart : CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
                }
                guard let converter = converters[key],
                      let converted = convert(buffer, with: converter) else { return }
                let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
                let start = firstTimes[key] ?? timestamp
                let elapsed = CMTimeSubtract(timestamp, start)
                let targetFrame = CMTimeConvertScale(elapsed, timescale: Int32(outputFormat.sampleRate),
                                                     method: .roundHalfAwayFromZero).value
                if let file = files[key], targetFrame > file.framePosition {
                    try writeSilence(frames: targetFrame - file.framePosition, format: outputFormat, to: file)
                }
                try files[key]?.write(from: converted)
                levels[key] = peakLevel(converted)
                let now = Date()
                if now.timeIntervalSince(lastReport) >= 0.25 {
                    lastReport = now
                    let level = max(levels["system"] ?? -120, levels["microphone"] ?? -120)
                    if let data = try? JSONSerialization.data(withJSONObject: ["ready": true, "level_db": level]),
                       let line = String(data: data, encoding: .utf8) {
                        FileHandle.standardOutput.write(Data((line + "\n").utf8))
                    }
                }
            }
        } catch {
            if !failureReported {
                failureReported = true
                FileHandle.standardError.write(Data("Audio-Schreiben fehlgeschlagen: \(error)\n".utf8))
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("ScreenCaptureKit beendet: \(error)\n".utf8))
    }

    func finish() {
        lock.lock()
        let elapsed = Date().timeIntervalSince(startedAt)
        for file in files.values {
            let targetFrame = Int64(elapsed * file.processingFormat.sampleRate)
            if targetFrame > file.framePosition {
                try? writeSilence(frames: targetFrame - file.framePosition,
                                  format: file.processingFormat, to: file)
            }
        }
        files.removeAll()
        lock.unlock()
    }

    func markStarted() {
        lock.lock()
        startedAt = Date()
        captureStart = CMClockGetTime(CMClockGetHostTimeClock())
        lock.unlock()
    }

    private func peakLevel(_ buffer: AVAudioPCMBuffer) -> Double {
        var peak = 0.0
        let count = Int(buffer.frameLength)
        if let samples = buffer.floatChannelData {
            for channel in 0..<Int(buffer.format.channelCount) {
                for index in 0..<count { peak = max(peak, abs(Double(samples[channel][index]))) }
            }
        } else if let samples = buffer.int16ChannelData {
            for channel in 0..<Int(buffer.format.channelCount) {
                for index in 0..<count { peak = max(peak, abs(Double(samples[channel][index])) / 32768) }
            }
        }
        return peak > 0 ? max(-120, 20 * log10(peak)) : -120
    }

    private func convert(_ input: AVAudioPCMBuffer, with converter: AVAudioConverter) -> AVAudioPCMBuffer? {
        let ratio = outputFormat.sampleRate / input.format.sampleRate
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * ratio)) + 32
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return nil }
        var provided = false
        var conversionError: NSError?
        let result = converter.convert(to: output, error: &conversionError) { _, status in
            if provided {
                status.pointee = .noDataNow
                return nil
            }
            provided = true
            status.pointee = .haveData
            return input
        }
        guard result != .error else {
            if !failureReported {
                failureReported = true
                FileHandle.standardError.write(Data("Audioformat ließ sich nicht umwandeln: \(String(describing: conversionError))\n".utf8))
            }
            return nil
        }
        return output.frameLength > 0 ? output : nil
    }

    private func writeSilence(frames: Int64, format: AVAudioFormat, to file: AVAudioFile) throws {
        var remaining = frames
        while remaining > 0 {
            let count = AVAudioFrameCount(min(remaining, Int64(format.sampleRate)))
            guard let silence = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count) else { return }
            silence.frameLength = count
            for audioBuffer in UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList) {
                if let data = audioBuffer.mData { memset(data, 0, Int(audioBuffer.mDataByteSize)) }
            }
            try file.write(from: silence)
            remaining -= Int64(count)
        }
    }
}

@main
struct SystemAudioCapture {
    @MainActor
    static func main() async {
        guard CommandLine.arguments.count == 3 else {
            fatalError("Aufruf: capture_macos.swift SYSTEM.wav MIKROFON.wav")
        }
        guard #available(macOS 15.0, *) else {
            fatalError("Systemton und Mikrofonaufnahme erfordern macOS 15 oder neuer.")
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first else { fatalError("Kein Bildschirm für Systemaudio gefunden.") }
            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let configuration = SCStreamConfiguration()
            configuration.width = 2
            configuration.height = 2
            configuration.capturesAudio = true
            configuration.captureMicrophone = true
            configuration.sampleRate = 48_000
            configuration.channelCount = 2
            let writer = AudioWriter(systemURL: URL(fileURLWithPath: CommandLine.arguments[1]),
                                     micURL: URL(fileURLWithPath: CommandLine.arguments[2]))
            let stream = SCStream(filter: filter, configuration: configuration, delegate: writer)
            try stream.addStreamOutput(writer, type: .audio, sampleHandlerQueue: DispatchQueue(label: "koprotokoll.system-audio"))
            try stream.addStreamOutput(writer, type: .microphone, sampleHandlerQueue: DispatchQueue(label: "koprotokoll.microphone"))
            try await stream.startCapture()
            writer.markStarted()

            signal(SIGINT, SIG_IGN)
            let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                interrupt.setEventHandler {
                    Task { @MainActor in
                        try? await stream.stopCapture()
                        writer.finish()
                        continuation.resume()
                    }
                }
                interrupt.resume()
            }
        } catch {
            FileHandle.standardError.write(Data("Systemaudio ließ sich nicht starten: \(error)\n".utf8))
            exit(1)
        }
    }
}
