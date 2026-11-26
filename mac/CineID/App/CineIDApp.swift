import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var environment: AppEnvironment?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A normal Mac app: Dock icon with a running dot, an app menu, ⌘Q to quit. (It used to be
        // menu-bar-only, which left no reachable way to quit it.)
        NSApp.setActivationPolicy(.regular)
        Diag.log("launched")
        // Under XCTest (app-hosted) nothing real starts: no engine, no hotkey, no windows.
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return }
        if DemoLauncher.runIfRequested() != nil || DemoArgs.askScenario(from: CommandLine.arguments) != nil { return }   // `-demoPopup <state>`: preview only
        EditMenu.install()   // app menu (Quit ⌘Q) + Edit menu, from the first moment
        let env = AppEnvironment()
        environment = env
        env.start()
        if CommandLine.arguments.contains("-identifyNow") { env.runDiagnosticIdentify() }
        env.openUIWhenReady()   // opening the app shows its window once the engine is up
    }

    /// Clicking the Dock icon / re-opening the app while it runs shows the window.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        environment?.openUI()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        environment?.shutdown()
    }
}

@main
enum CineIDMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        // NSApplication.delegate is weak; keep the delegate alive for the whole run (matters in -O builds).
        withExtendedLifetime(delegate) { app.run() }
    }
}
