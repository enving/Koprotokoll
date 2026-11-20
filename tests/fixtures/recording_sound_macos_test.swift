// Wird im Test an dieselbe Quelldatei angehängt, damit die produktive Anfrage geprüft wird.
extension DesktopApp {
    static func verifySounds(port: Int) async throws {
        var sounds: [String] = []
        let app = DesktopApp(playSound: { sounds.append($0) })
        app.config = Launcher(project: "", python: "", data: "", port: port, path: "")
        _ = try await app.confirmedCapture("/start", body: [:], sound: "Pop")
        _ = try await app.confirmedCapture("/stop", body: [:], sound: "Tink")
        guard sounds == ["Pop", "Tink"] else { fatalError("Bestätigungstöne fehlen") }
        do {
            _ = try await app.confirmedCapture("/error", body: [:], sound: "Pop")
            fatalError("Fehler wurde nicht weitergegeben")
        } catch { }
        guard sounds == ["Pop", "Tink"] else { fatalError("Erfolgston trotz fehlgeschlagener Anfrage") }
        for name in sounds {
            guard NSSound(named: NSSound.Name(name)) != nil else { fatalError("Systemton fehlt") }
        }
        print("Aufnahmebestätigungen und fehlgeschlagene Anfrage geprüft")
    }
}

@main
struct RecordingSoundTest {
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        try await DesktopApp.verifySounds(port: Int(CommandLine.arguments[1])!)
    }
}
