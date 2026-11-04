import AppKit
import CoreGraphics
import Foundation

struct Launcher: Decodable {
    let project: String
    let python: String
    let data: String
    let port: Int
    let path: String
    var baseURL: String { "http://127.0.0.1:\(port)" }
}

struct AudioSource: Decodable {
    let id: String
    let name: String
}

struct SourceList: Decodable { let devices: [AudioSource] }

@MainActor
final class DesktopApp: NSObject, NSApplicationDelegate {
    private var config: Launcher!
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let stateItem = NSMenuItem(title: "Koprotokoll startet …", action: nil, keyEquivalent: "")
    private let toggleItem = NSMenuItem(title: "Aufnahme starten · ⌥ zweimal", action: #selector(toggleAction), keyEquivalent: "")
    private let sourcesMenu = NSMenu()
    private var sources: [AudioSource] = []
    private var selectedName = UserDefaults.standard.string(forKey: "audioSourceName")
    private var active = false
    private var startedAt = 0.0
    private var busy = true
    private var refreshing = false
    private var listening = false
    private var server: Process?
    private var timer: Timer?
    private var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let url = Bundle.main.url(forResource: "launcher", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode(Launcher.self, from: data) else {
            fail("Die Startkonfiguration fehlt. Bitte Koprotokoll.command erneut öffnen.")
            NSApp.terminate(nil)
            return
        }
        config = decoded
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "Ko"
        item.button?.toolTip = "Koprotokoll: Alt zweimal startet oder stoppt die Aufnahme"
        item.menu = menu
        menu.autoenablesItems = false
        sourcesMenu.autoenablesItems = false
        stateItem.isEnabled = false
        toggleItem.isEnabled = false
        toggleItem.target = self
        menu.addItem(stateItem)
        menu.addItem(toggleItem)
        let sourceItem = NSMenuItem(title: "Audioquelle", action: nil, keyEquivalent: "")
        sourceItem.submenu = sourcesMenu
        menu.addItem(sourceItem)
        addItem("Audioquellen aktualisieren", action: #selector(refreshSourcesAction))
        menu.addItem(.separator())
        addItem("Oberfläche öffnen", action: #selector(openUI))
        addItem("Tastenkürzel prüfen", action: #selector(checkShortcut))
        addItem("Tastenkürzel beenden", action: #selector(quit))
        Task { await prepare() }
    }

    private func addItem(_ title: String, action: Selector) {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
        entry.target = self
        menu.addItem(entry)
    }

    private func request(_ path: String, body: [String: Any]? = nil) async throws -> Data {
        var request = URLRequest(url: URL(string: config.baseURL + path)!)
        request.timeoutInterval = body != nil || path == "/api/capture/devices" ? 75 : 5
        if let body = body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let detail = object?["detail"] as? String ?? "Der lokale Server antwortet nicht wie erwartet."
            throw NSError(domain: "Koprotokoll", code: 1, userInfo: [NSLocalizedDescriptionKey: detail])
        }
        return data
    }

    private func serverReady() async -> Bool {
        guard let data = try? await request("/openapi.json"),
              let schema = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let info = schema["info"] as? [String: Any] else { return false }
        return info["title"] as? String == "Koprotokoll API"
    }

    private func prepare() async {
        defer { busy = false }
        if !(await serverReady()) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: config.python)
            process.arguments = ["-m", "koprotokoll", "serve", "--port", String(config.port)]
            process.currentDirectoryURL = URL(fileURLWithPath: config.project)
            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = config.path
            environment["KOPROTOKOLL_DATA"] = config.data
            process.environment = environment
            let logURL = URL(fileURLWithPath: config.data).appendingPathComponent("desktop/server.log")
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil)
            }
            if let log = try? FileHandle(forWritingTo: logURL) {
                log.seekToEndOfFile()
                process.standardOutput = log
                process.standardError = log
            }
            do { try process.run(); server = process }
            catch { fail("Der lokale Server konnte nicht starten: \(error.localizedDescription)"); return }
            var ready = false
            for _ in 0..<40 {
                if await serverReady() { ready = true; break }
                if !process.isRunning { break }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            guard ready else {
                fail("Koprotokoll konnte nicht starten. Prüfe den Port und data/desktop/server.log.")
                return
            }
        }
        busy = false
        toggleItem.isEnabled = true
        await refreshSources()
        await refreshState()
        if selectedName == nil && !sources.isEmpty { chooseSource() }
        enableShortcut()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refreshState() }
        }
    }

    @objc private func refreshSourcesAction() { Task { await refreshSources() } }

    private func refreshSources() async {
        do {
            sources = try JSONDecoder().decode(SourceList.self, from: await request("/api/capture/devices")).devices
            sourcesMenu.removeAllItems()
            for source in sources {
                let entry = NSMenuItem(title: source.name, action: #selector(selectSource(_:)), keyEquivalent: "")
                entry.representedObject = source.name
                entry.target = self
                entry.state = source.name == selectedName ? .on : .off
                sourcesMenu.addItem(entry)
            }
            if let selected = selectedName, !sources.contains(where: { $0.name == selected }) {
                stateItem.title = "Gewählte Audioquelle ist nicht verfügbar"
            }
        } catch { fail("Audioquellen konnten nicht geladen werden: \(error.localizedDescription)") }
    }

    private func chooseSource() {
        let alert = NSAlert()
        alert.messageText = "Audioquelle für Koprotokoll wählen"
        alert.informativeText = "Wähle ein Mikrofon für ein Raumgespräch oder Systemton plus Mikrofon für ein Online-Meeting. Die Aufnahme beginnt erst nach Alt zweimal oder über das Menü."
        alert.addButton(withTitle: "Übernehmen")
        alert.addButton(withTitle: "Abbrechen")
        let popup = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 390, height: 28))
        popup.addItems(withTitles: sources.map(\.name))
        alert.accessoryView = popup
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn, let name = popup.titleOfSelectedItem {
            rememberSource(name)
        }
    }

    private func rememberSource(_ name: String) {
        selectedName = name
        UserDefaults.standard.set(name, forKey: "audioSourceName")
        for entry in sourcesMenu.items { entry.state = entry.title == name ? .on : .off }
    }

    @objc private func selectSource(_ sender: NSMenuItem) {
        guard !active && !busy, let name = sender.representedObject as? String else { return }
        rememberSource(name)
    }

    private func refreshState() async {
        guard !refreshing && !busy else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            let data = try await request("/api/capture")
            let state = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            active = state["active"] as? Bool ?? false
            startedAt = state["started_at"] as? Double ?? 0
            if active {
                let elapsed = max(0, Int(Date().timeIntervalSince1970 - startedAt))
                let time = String(format: "%02d:%02d", elapsed / 60, elapsed % 60)
                item.button?.title = "● \(time)"
                let level = Int(state["level_db"] as? Double ?? -120)
                stateItem.title = "Aufnahme läuft · Pegel \(level) dB"
                toggleItem.title = "Stopp und auswerten · ⌥ zweimal"
            } else {
                item.button?.title = "Ko"
                stateItem.title = listening ? "Bereit · Alt zweimal" : "Tastenkürzel benötigt Eingabeüberwachung"
                toggleItem.title = "Aufnahme starten · ⌥ zweimal"
            }
            for entry in sourcesMenu.items { entry.isEnabled = !active }
        } catch {
            stateItem.title = "Server nicht erreichbar; Aufnahmezustand unbekannt"
            item.button?.title = "Ko !"
        }
    }

    @objc private func toggleAction() { Task { await toggle() } }

    private func toggle() async {
        guard !busy else { return }
        busy = true
        toggleItem.isEnabled = false
        defer { busy = false; toggleItem.isEnabled = true; Task { await refreshState() } }
        do {
            let data = try await request("/api/capture")
            let state = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            if state["active"] as? Bool == true {
                stateItem.title = "Aufnahme wird gesichert …"
                let result = try await request("/api/capture/stop", body: [:])
                let meeting = try JSONSerialization.jsonObject(with: result) as? [String: Any] ?? [:]
                active = false
                if let id = meeting["id"] as? Int {
                    NSWorkspace.shared.open(URL(string: config.baseURL + "/#meeting=\(id)")!)
                }
            } else {
                guard let name = selectedName else { chooseSource(); return }
                let available = try JSONDecoder().decode(SourceList.self, from: await request("/api/capture/devices")).devices
                let matches = available.filter { $0.name == name }
                guard matches.count == 1, let source = matches.first else {
                    fail("Die gewählte Audioquelle ist nicht eindeutig verfügbar. Bitte im Menü neu auswählen.")
                    return
                }
                stateItem.title = "Aufnahme wird gestartet …"
                _ = try await request("/api/capture/start", body: ["device": source.id, "title": ""])
                active = true
                item.button?.title = "● Aufnahme"
            }
        } catch { fail("Aufnahme konnte nicht umgeschaltet werden: \(error.localizedDescription)") }
    }

    @objc private func checkShortcut() {
        enableShortcut()
        fail(listening
             ? "Alt zweimal ist aktiviert. Die Aufnahme lässt sich auch im Ko-Menü starten und stoppen."
             : "Bitte Koprotokoll unter Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung freigeben. Danach erneut prüfen oder die App neu öffnen. Start und Stopp im Ko-Menü funktionieren auch ohne Tastenkürzel.")
    }

    private func enableShortcut() {
        guard !listening else { return }
        guard CGPreflightListenEventAccess() || CGRequestListenEventAccess() else {
            stateItem.title = "Eingabeüberwachung für Koprotokoll freigeben"
            return
        }
        listening = true
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiatedAllowingIdleSystemSleep],
                                                        reason: "Koprotokoll Alt-Doppeltipp")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var detector = AltDoubleTap()
            let optionKeys: Set<CGKeyCode> = [58, 61]
            let otherKeys = (0..<128).map { CGKeyCode($0) }.filter { !optionKeys.contains($0) && $0 != 57 }
            while true {
                let down = optionKeys.contains { CGEventSource.keyState(.hidSystemState, key: $0) }
                let other = (down || detector.needsOtherKeys)
                    && otherKeys.contains { CGEventSource.keyState(.hidSystemState, key: $0) }
                if detector.update(down: down, otherKey: other, at: ProcessInfo.processInfo.systemUptime) {
                    Task { @MainActor in await self?.toggle() }
                }
                Thread.sleep(forTimeInterval: 0.01)
            }
        }
    }

    @objc private func openUI() { NSWorkspace.shared.open(URL(string: config.baseURL)!) }

    @objc private func quit() {
        guard !active && !busy else { fail("Bitte zuerst die Aufnahme stoppen und sichern."); return }
        // Der Server beendet die Auswertung auch nach dem Schließen der Menüleiste.
        NSApp.terminate(nil)
    }

    private func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Koprotokoll"
        alert.informativeText = message
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}

@main
struct DesktopMain {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = DesktopApp()
        app.delegate = delegate
        app.run()
        _ = delegate
    }
}
