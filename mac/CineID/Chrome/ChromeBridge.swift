import AppKit
import Foundation

protocol ChromeBridge {
    func isRunning() -> Bool
    /// The Netflix watch tab among all Chrome windows' active tabs (front-most first), else nil.
    func activeTab() -> (url: String, title: String)?
    /// Runs JavaScript in that Netflix watch tab. Nil on any problem (Chrome closed, no Netflix tab,
    /// "Allow JavaScript from Apple Events" off, Automation denied, script error).
    func runJS(_ js: String) -> String?
}

/// Runs AppleScript source and returns its text result; nil on any error.
protocol AppleScriptRunning {
    func run(_ source: String) -> String?
}

/// Real runner: `NSAppleScript` on a background queue (it is not safe on the main thread and can
/// block for seconds), waiting up to `timeout`. Call from a background task, not the main thread.
final class NSAppleScriptRunner: AppleScriptRunning {
    private let queue = DispatchQueue(label: "cineid.applescript")
    private let timeout: TimeInterval

    init(timeout: TimeInterval = 10) { self.timeout = timeout }

    func run(_ source: String) -> String? {
        let done = DispatchSemaphore(value: 0)
        var result: String?
        queue.async {
            var info: NSDictionary?
            if let script = NSAppleScript(source: source) {
                let descriptor = script.executeAndReturnError(&info)
                if info == nil { result = descriptor.stringValue }
            }
            done.signal()
        }
        guard done.wait(timeout: .now() + timeout) == .success else { return nil }
        return result
    }
}

final class AppleScriptChrome: ChromeBridge {
    static let bundleIdentifier = "com.google.Chrome"

    private let isChromeRunning: () -> Bool
    private let runner: AppleScriptRunning

    init(isChromeRunning: @escaping () -> Bool = {
            !NSRunningApplication.runningApplications(withBundleIdentifier: AppleScriptChrome.bundleIdentifier).isEmpty
         },
         runner: AppleScriptRunning = NSAppleScriptRunner()) {
        self.isChromeRunning = isChromeRunning
        self.runner = runner
    }

    func isRunning() -> Bool { isChromeRunning() }

    func activeTab() -> (url: String, title: String)? {
        // Checked first: even `tell application "Google Chrome"` would launch it if closed.
        guard isChromeRunning(), let raw = runner.run(Self.tabsScript) else { return nil }
        return ChromeParsers.netflixWatchTab(in: ChromeParsers.parseTabs(raw))
    }

    func runJS(_ js: String) -> String? {
        guard isChromeRunning(), let raw = runner.run(Self.javaScriptScript(for: js)) else { return nil }
        let result = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if result.isEmpty || result.hasPrefix("jserr") { return nil }
        return result
    }

    // MARK: Scripts

    /// One "URL|||title" line per window's active tab, front-most window first.
    /// `if application … is running` does not launch Chrome. Deliberately no `front window`:
    /// the Netflix tab may be in a window behind the one the user is looking at.
    static let tabsScript = """
    if application "Google Chrome" is running then
        tell application "Google Chrome"
            set out to ""
            repeat with w in windows
                try
                    set t to active tab of w
                    set out to out & (URL of t) & "|||" & (title of t) & linefeed
                end try
            end repeat
            return out
        end tell
    end if
    return ""
    """

    /// Runs `js` in the active tab of the first (front-most) window whose active tab is a Netflix watch page.
    static func javaScriptScript(for js: String) -> String {
        """
        if application "Google Chrome" is running then
            tell application "Google Chrome"
                repeat with w in windows
                    try
                        set t to active tab of w
                        if (URL of t) contains "netflix.com/watch" then
                            try
                                return execute t javascript "\(escapeForAppleScript(js))"
                            on error em
                                return "jserr|||" & em
                            end try
                        end if
                    end try
                end repeat
            end tell
        end if
        return ""
        """
    }

    /// For use inside an AppleScript string literal: escape `\` and `"`, flatten newlines.
    static func escapeForAppleScript(_ text: String) -> String {
        text.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r\n", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
    }
}
