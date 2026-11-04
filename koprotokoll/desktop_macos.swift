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

final class ShortcutMonitor: @unchecked Sendable {
    private let lock = NSLock()
    private var detector = ConfigurableShortcut()

    func configure(enabled: Bool, modifier: ShortcutModifier) {
        lock.lock()
        defer { lock.unlock() }
        detector.configure(enabled: enabled, modifier: modifier)
    }

    func start(_ triggered: @escaping @Sendable () -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let monitor = self {
                if monitor.sample() { triggered() }
                Thread.sleep(forTimeInterval: 0.01)
            }
        }
    }

    private func sample() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard detector.enabled else { return false }
        let keys = detector.modifier.keyCodes
        let down = keys.contains { CGEventSource.keyState(.hidSystemState, key: $0) }
        let other = (down || detector.needsOtherKeys) && (UInt16(0)..<128).contains {
            !keys.contains($0) && $0 != 57 && CGEventSource.keyState(.hidSystemState, key: $0)
        }
        return detector.update(down: down, otherKey: other, at: ProcessInfo.processInfo.systemUptime)
    }
}

@MainActor
final class DesktopApp: NSObject, NSApplicationDelegate {
    private var config: Launcher!
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let stateItem = NSMenuItem(title: "Koprotokoll startet …", action: nil, keyEquivalent: "")
    private let toggleItem = NSMenuItem(title: "Aufnahme starten · ⌥ zweimal", action: #selector(toggleAction), keyEquivalent: "")
    private let sourcesMenu = NSMenu()
    private var sources: [AudioSource] = []
    private let preferences = DesktopPreferences(defaults: .standard)
    private var selectedName: String? { preferences.sourceName }
    private var active = false
    private var startedAt = 0.0
    private var busy = true
    private var refreshing = false
    private var sourcesLoading = false
    private var listening = false
    private var server: Process?
    private var timer: Timer?
    private var activity: NSObjectProtocol?
    private var monitor: ShortcutMonitor?
    private var settingsWindow: NSWindow?
    private let sourcePicker = NSPopUpButton()
    private let modifierPicker = NSPopUpButton()
    private let shortcutToggle = NSButton(checkboxWithTitle: "Globales Tastenkürzel aktivieren", target: nil, action: nil)
    private let permissionLabel = NSTextField(wrappingLabelWithString: "")
    private let recordingLabel = NSTextField(wrappingLabelWithString: "Koprotokoll startet …")
    private let recordButton = NSButton(title: "Aufnahme starten", target: nil, action: nil)
    private let refreshButton = NSButton(title: "Audioquellen aktualisieren", target: nil, action: nil)

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
        addItem("Einstellungen …", action: #selector(showSettings))
        addItem("Oberfläche öffnen", action: #selector(openUI))
        addItem("Tastenkürzel prüfen", action: #selector(checkShortcut))
        addItem("Tastenkürzel beenden", action: #selector(quit))
        showSettings()
        Task { await prepare() }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSettings()
        return true
    }

    @objc private func showSettings() {
        if settingsWindow == nil { buildSettingsWindow() }
        updateSettingsControls()
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildSettingsWindow() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 570, height: 560),
                              styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Koprotokoll – Einstellungen"
        window.isReleasedWhenClosed = false
        window.center()
        settingsWindow = window
        guard let content = window.contentView else { return }
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -24),
        ])
        func label(_ text: String, heading: Bool = false) {
            let field = NSTextField(wrappingLabelWithString: text)
            if heading { field.font = .systemFont(ofSize: 15, weight: .semibold) }
            stack.addArrangedSubview(field)
            field.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        func button(_ button: NSButton, action: Selector) {
            button.target = self
            button.action = action
            button.bezelStyle = .rounded
        }
        label("Audioquelle", heading: true)
        sourcePicker.target = self
        sourcePicker.action = #selector(selectSettingsSource)
        sourcePicker.setAccessibilityLabel("Audioquelle")
        stack.addArrangedSubview(sourcePicker)
        sourcePicker.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        button(refreshButton, action: #selector(refreshSourcesAction))
        stack.addArrangedSubview(refreshButton)
        label("Für ein Raumgespräch ein Mikrofon wählen. Für ein Online-Meeting müssen Systemton und Mikrofon enthalten sein.")
        label("Systemton benötigt unter macOS ‚Nur Aufnahme von Systemaudio‘ und die Mikrofonfreigabe. Bildschirminhalte werden nicht aufgenommen.")
        label("Tastenkürzel", heading: true)
        shortcutToggle.target = self
        shortcutToggle.action = #selector(changeShortcut)
        stack.addArrangedSubview(shortcutToggle)
        modifierPicker.addItems(withTitles: ShortcutModifier.allCases.map(\.title))
        modifierPicker.target = self
        modifierPicker.action = #selector(changeShortcut)
        modifierPicker.setAccessibilityLabel("Doppeltipp-Taste")
        stack.addArrangedSubview(modifierPicker)
        modifierPicker.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        label("Die gewählte Taste zweimal kurz drücken und loslassen: starten; erneut zweimal: stoppen und auswerten.")
        stack.addArrangedSubview(permissionLabel)
        permissionLabel.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        let permissionRow = NSStackView()
        permissionRow.spacing = 8
        let check = NSButton(title: "Freigabe prüfen", target: nil, action: nil)
        let system = NSButton(title: "macOS-Einstellungen öffnen", target: nil, action: nil)
        button(check, action: #selector(checkShortcut))
        button(system, action: #selector(openSystemSettings))
        permissionRow.addArrangedSubview(check)
        permissionRow.addArrangedSubview(system)
        stack.addArrangedSubview(permissionRow)
        label("Unter Datenschutz & Sicherheit → Eingabeüberwachung muss Koprotokoll freigegeben sein. Änderungen hier werden sofort gespeichert.")
        label("Aufnahme", heading: true)
        stack.addArrangedSubview(recordingLabel)
        recordingLabel.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        let actions = NSStackView()
        actions.spacing = 8
        button(recordButton, action: #selector(toggleAction))
        let meetings = NSButton(title: "Meetings öffnen", target: nil, action: nil)
        button(meetings, action: #selector(openUI))
        actions.addArrangedSubview(recordButton)
        actions.addArrangedSubview(meetings)
        stack.addArrangedSubview(actions)
        content.layoutSubtreeIfNeeded()
        window.setContentSize(NSSize(width: 570, height: max(560, stack.fittingSize.height + 48)))
        window.center()
    }

    private func updateSettingsControls() {
        guard settingsWindow != nil else { return }
        var titles = ["Bitte Audioquelle wählen …"] + sources.map(\.name)
        let selected = selectedName.flatMap { name in sources.firstIndex { $0.name == name } }
        if let name = selectedName, selected == nil { titles.append("Nicht verfügbar: \(name)") }
        if sourcePicker.itemTitles != titles {
            sourcePicker.removeAllItems()
            sourcePicker.addItems(withTitles: titles)
            for (index, source) in sources.enumerated() {
                sourcePicker.item(at: index + 1)?.representedObject = source.name
            }
        }
        let selectedIndex = selected.map { $0 + 1 } ?? (selectedName == nil ? 0 : titles.count - 1)
        if sourcePicker.indexOfSelectedItem != selectedIndex { sourcePicker.selectItem(at: selectedIndex) }
        sourcePicker.isEnabled = !active && !busy && !sourcesLoading && !sources.isEmpty
        refreshButton.isEnabled = !busy && !sourcesLoading
        shortcutToggle.state = preferences.shortcutEnabled ? .on : .off
        shortcutToggle.isEnabled = !active && !busy
        let modifierIndex = ShortcutModifier.allCases.firstIndex(of: preferences.modifier) ?? 0
        if modifierPicker.indexOfSelectedItem != modifierIndex { modifierPicker.selectItem(at: modifierIndex) }
        modifierPicker.isEnabled = preferences.shortcutEnabled && !active && !busy
        let granted = CGPreflightListenEventAccess()
        permissionLabel.stringValue = granted
            ? (preferences.shortcutEnabled ? "Eingabeüberwachung freigegeben. Tastenkürzel aktiv." : "Eingabeüberwachung freigegeben. Tastenkürzel ausgeschaltet.")
            : "Eingabeüberwachung fehlt. Start und Stopp über die Schaltfläche sind verfügbar."
        recordingLabel.stringValue = stateItem.title
        recordButton.title = active ? "Stopp und auswerten" : "Aufnahme starten"
        recordButton.isEnabled = !busy
    }

    @objc private func selectSettingsSource() {
        guard !active && !busy, let name = sourcePicker.selectedItem?.representedObject as? String else { return }
        rememberSource(name)
    }

    @objc private func changeShortcut() {
        guard !active && !busy else { return }
        preferences.shortcutEnabled = shortcutToggle.state == .on
        preferences.modifier = ShortcutModifier.allCases[modifierPicker.indexOfSelectedItem]
        enableShortcut()
        updateSettingsControls()
        Task { await refreshState() }
    }

    @objc private func openSystemSettings() {
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.systempreferences") {
            NSWorkspace.shared.openApplication(at: url, configuration: .init())
        }
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
        defer { busy = false; updateSettingsControls() }
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
        enableShortcut()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refreshState() }
        }
    }

    @objc private func refreshSourcesAction() { Task { await refreshSources() } }

    private func refreshSources() async {
        guard !sourcesLoading else { return }
        sourcesLoading = true
        refreshButton.isEnabled = false
        defer { sourcesLoading = false; updateSettingsControls() }
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
        showSettings()
        settingsWindow?.makeFirstResponder(sourcePicker)
    }

    private func rememberSource(_ name: String) {
        preferences.sourceName = name
        for entry in sourcesMenu.items { entry.state = entry.title == name ? .on : .off }
        updateSettingsControls()
    }

    @objc private func selectSource(_ sender: NSMenuItem) {
        guard !active && !busy, let name = sender.representedObject as? String else { return }
        rememberSource(name)
    }

    private func refreshState() async {
        guard !refreshing && !busy else { return }
        refreshing = true
        defer { refreshing = false; updateSettingsControls() }
        enableShortcut()
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
                toggleItem.title = "Stopp und auswerten"
            } else {
                item.button?.title = "Ko"
                stateItem.title = listening ? "Bereit · \(preferences.modifier.title)" : "Bereit · Start über die Schaltfläche"
                toggleItem.title = "Aufnahme starten"
            }
            for entry in sourcesMenu.items { entry.isEnabled = !active }
            if let error = state["error"] as? String {
                stateItem.title = "Aufnahmefehler: \(error)"
                item.button?.title = "Ko !"
            }
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
        updateSettingsControls()
        defer { busy = false; toggleItem.isEnabled = true; updateSettingsControls(); Task { await refreshState() } }
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
        if preferences.shortcutEnabled && !CGPreflightListenEventAccess() { _ = CGRequestListenEventAccess() }
        enableShortcut()
        showSettings()
    }

    private func enableShortcut() {
        listening = preferences.shortcutEnabled && CGPreflightListenEventAccess()
        if listening && monitor == nil {
            let sampler = ShortcutMonitor()
            monitor = sampler
            sampler.start { [weak self] in
                Task { @MainActor in
                    guard let self = self, self.listening else { return }
                    await self.toggle()
                }
            }
        }
        monitor?.configure(enabled: listening, modifier: preferences.modifier)
        if listening && activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiatedAllowingIdleSystemSleep],
                                                            reason: "Koprotokoll Tastenkürzel")
        } else if !listening, let previous = activity {
            ProcessInfo.processInfo.endActivity(previous)
            activity = nil
        }
        item.button?.toolTip = "Koprotokoll: \(preferences.modifier.title) startet oder stoppt die Aufnahme"
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
        if CommandLine.arguments.count == 4, CommandLine.arguments[1] == "--capture-audio" {
            SystemAudioCapture.run(systemPath: CommandLine.arguments[2], microphonePath: CommandLine.arguments[3])
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = DesktopApp()
        app.delegate = delegate
        app.run()
        _ = delegate
    }
}
