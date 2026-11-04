import AVFoundation
import CoreAudio
import Darwin
import Foundation

enum AudioCaptureError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

func requireAudioSuccess(_ status: OSStatus, _ operation: String) throws {
    guard status == noErr else {
        throw AudioCaptureError.message("\(operation) fehlgeschlagen (Core Audio \(status)). Bitte die Freigabe unter ‚Nur Aufnahme von Systemaudio‘ prüfen.")
    }
}

private func reportAudio(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object) {
        FileHandle.standardOutput.write(data + Data("\n".utf8))
    }
}

final class AudioWriter {
    private let lock = NSLock()
    private var files: [String: AVAudioFile] = [:]
    private var converters: [String: AVAudioConverter] = [:]
    private var levels: [String: Double] = [:]
    private var received: Set<String> = []
    private var lastReport = 0.0
    private var startedAt: Double?
    private var failure: String?
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48_000,
                                             channels: 2, interleaved: true)!

    init(systemURL: URL, micURL: URL) throws {
        for (key, url) in ["system": systemURL, "microphone": micURL] {
            guard !FileManager.default.fileExists(atPath: url.path) else {
                throw AudioCaptureError.message("Die Audiodatei existiert bereits: \(url.lastPathComponent)")
            }
            files[key] = try AVAudioFile(forWriting: url, settings: outputFormat.settings,
                                         commonFormat: .pcmFormatInt16, interleaved: true)
        }
    }

    func markStarted(at time: Double? = nil) {
        lock.lock()
        startedAt = time
        lock.unlock()
    }

    func receive(_ buffer: AVAudioPCMBuffer, key: String, at time: Double) {
        lock.lock()
        defer { lock.unlock() }
        guard failure == nil, buffer.frameLength > 0, let file = files[key] else { return }
        if startedAt == nil { startedAt = time }
        do {
            if converters[key] == nil { converters[key] = AVAudioConverter(from: buffer.format, to: outputFormat) }
            guard let converter = converters[key] else {
                throw AudioCaptureError.message("Audioformat für \(key) lässt sich nicht umwandeln.")
            }
            let ratio = outputFormat.sampleRate / buffer.format.sampleRate
            let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * ratio)) + 32
            guard let converted = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
                throw AudioCaptureError.message("Audiopuffer ließ sich nicht anlegen.")
            }
            var provided = false
            var error: NSError?
            let result = converter.convert(to: converted, error: &error) { _, status in
                if provided { status.pointee = .noDataNow; return nil }
                provided = true
                status.pointee = .haveData
                return buffer
            }
            if result == .error {
                throw error ?? NSError(domain: "Koprotokoll", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Audioformat ließ sich nicht umwandeln."])
            }
            let targetFrame = Int64(max(0, time - (startedAt ?? time)) * outputFormat.sampleRate)
            if targetFrame > file.framePosition {
                try writeSilence(frames: targetFrame - file.framePosition, to: file)
            }
            try file.write(from: converted)
            received.insert(key)
            var peak = 0.0
            for audioBuffer in UnsafeMutableAudioBufferListPointer(converted.mutableAudioBufferList) {
                guard let data = audioBuffer.mData else { continue }
                let samples = data.assumingMemoryBound(to: Int16.self)
                for index in 0..<(Int(audioBuffer.mDataByteSize) / MemoryLayout<Int16>.size) {
                    peak = max(peak, abs(Double(samples[index])) / 32768)
                }
            }
            levels[key] = peak > 0 ? max(-120, 20 * log10(peak)) : -120
            if received.count == 2 && time - lastReport >= 0.25 {
                lastReport = time
                reportAudio(["ready": true, "level_db": levels.values.max() ?? -120])
            }
        } catch {
            failure = error.localizedDescription
            reportAudio(["error": error.localizedDescription])
        }
    }

    func finish(at time: Double = AVAudioTime.seconds(forHostTime: mach_absolute_time())) throws {
        lock.lock()
        defer { files.removeAll(); lock.unlock() }
        if let failure = failure { throw AudioCaptureError.message(failure) }
        guard received.count == 2 else {
            throw AudioCaptureError.message("Systemton oder Mikrofon lieferte keine Samples. Audio-Freigaben und Quelle prüfen.")
        }
        let targetFrame = Int64(max(0, time - (startedAt ?? time)) * outputFormat.sampleRate)
        for file in files.values {
            if targetFrame > file.framePosition { try writeSilence(frames: targetFrame - file.framePosition, to: file) }
        }
    }

    private func writeSilence(frames: Int64, to file: AVAudioFile) throws {
        var remaining = frames
        while remaining > 0 {
            let count = AVAudioFrameCount(min(remaining, Int64(outputFormat.sampleRate)))
            guard let silence = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: count) else {
                throw AudioCaptureError.message("Stillepuffer ließ sich nicht anlegen.")
            }
            silence.frameLength = count
            for buffer in UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList) {
                if let data = buffer.mData { memset(data, 0, Int(buffer.mDataByteSize)) }
            }
            try file.write(from: silence)
            remaining -= Int64(count)
        }
    }
}

@available(macOS 15.0, *)
final class SystemAudioTap {
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var deviceID = AudioObjectID(kAudioObjectUnknown)
    private var ioProc: AudioDeviceIOProcID?

    func start(writer: AudioWriter) throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.name = "Koprotokoll Systemaudio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try requireAudioSuccess(AudioHardwareCreateProcessTap(description, &tapID), "Systemaudio-Zugriff")

        var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat,
                                                mScope: kAudioObjectPropertyScopeGlobal,
                                                mElement: kAudioObjectPropertyElementMain)
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try requireAudioSuccess(AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &asbd), "Systemaudio-Format")
        guard let format = AVAudioFormat(streamDescription: &asbd), format.sampleRate > 0 else {
            throw AudioCaptureError.message("Systemaudio meldet kein verwendbares Audioformat.")
        }
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Koprotokoll Audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString,
                                             kAudioSubTapDriftCompensationKey: true]],
        ]
        try requireAudioSuccess(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &deviceID), "Audiogerät")
        try requireAudioSuccess(AudioDeviceCreateIOProcIDWithBlock(&ioProc, deviceID,
            DispatchQueue(label: "koprotokoll.systemaudio")) { _, input, inputTime, _, _ in
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: input) else { return }
                let host = inputTime.pointee.mHostTime
                writer.receive(buffer, key: "system",
                               at: AVAudioTime.seconds(forHostTime: host == 0 ? mach_absolute_time() : host))
            }, "Systemaudio-Callback")
        try requireAudioSuccess(AudioDeviceStart(deviceID, ioProc), "Systemaudio-Aufnahme")
    }

    func stop() {
        if let proc = ioProc {
            AudioDeviceStop(deviceID, proc)
            AudioDeviceDestroyIOProcID(deviceID, proc)
            ioProc = nil
        }
        if deviceID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(deviceID); deviceID = 0 }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID); tapID = 0 }
    }

    deinit { stop() }
}

enum SystemAudioCapture {
    static func run(systemPath: String, microphonePath: String) {
        guard #available(macOS 15.0, *) else {
            reportAudio(["error": "Systemton und Mikrofonaufnahme erfordern macOS 15 oder neuer."])
            exit(1)
        }
        let stopped = DispatchSemaphore(value: 0)
        signal(SIGINT, SIG_IGN)
        let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
        interrupt.setEventHandler { stopped.signal() }
        interrupt.resume()
        do {
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                let answered = DispatchSemaphore(value: 0)
                AVCaptureDevice.requestAccess(for: .audio) { _ in answered.signal() }
                guard answered.wait(timeout: .now() + 50) == .success else {
                    throw AudioCaptureError.message("Mikrofonfreigabe wurde nicht rechtzeitig bestätigt.")
                }
            }
            guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
                throw AudioCaptureError.message("Mikrofonfreigabe fehlt. Koprotokoll unter Datenschutz & Sicherheit → Mikrofon freigeben.")
            }
            let writer = try AudioWriter(systemURL: URL(fileURLWithPath: systemPath),
                                         micURL: URL(fileURLWithPath: microphonePath))
            let engine = AVAudioEngine()
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0 && format.channelCount > 0 else {
                throw AudioCaptureError.message("Das Standardmikrofon meldet kein Audioformat.")
            }
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, time in
                writer.receive(buffer, key: "microphone",
                               at: AVAudioTime.seconds(forHostTime: time.hostTime == 0 ? mach_absolute_time() : time.hostTime))
            }
            let tap = SystemAudioTap()
            defer { tap.stop(); engine.stop(); input.removeTap(onBus: 0) }
            writer.markStarted()
            try tap.start(writer: writer)
            try engine.start()
            stopped.wait()
            tap.stop()
            engine.stop()
            try writer.finish()
        } catch {
            reportAudio(["error": error.localizedDescription])
            FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
            exit(1)
        }
        interrupt.cancel()
    }
}
