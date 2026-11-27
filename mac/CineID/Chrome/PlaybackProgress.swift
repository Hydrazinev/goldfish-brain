import AppKit
import Foundation

/// How far through the video the viewer is (0...1), read from the Netflix `<video>` via Chrome.
/// It is only a number: nothing else leaves the Mac because of it.
enum PlaybackProgress {
    /// Mirrors PROGRESS_JS in src/chrome_scripts.py.
    static let js = "(function(){var v=document.querySelector('video');return (v&&v.duration)?String(v.currentTime/v.duration):''})()"

    /// "0.42" -> 0.42, clamped to 0...1. Nil for empty, "jserr|||…", NaN/Infinity or anything non-numeric.
    static func parse(_ raw: String?) -> Double? {
        guard let text = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty, !text.hasPrefix("jserr"),
              let value = Double(text), value.isFinite else { return nil }
        return min(1, max(0, value))
    }

    /// Reads the progress off the main thread (AppleScript can block for seconds) and gives up
    /// after `timeout`, returning nil, so a slow Chrome never delays the card.
    static func read(from chrome: ChromeBridge, timeout: TimeInterval = 1) async -> Double? {
        await withCheckedContinuation { (continuation: CheckedContinuation<Double?, Never>) in
            let once = Once(continuation)
            DispatchQueue.global(qos: .userInitiated).async {
                once.resume(parse(chrome.runJS(js)))
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { once.resume(nil) }
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<Double?, Never>?
        init(_ continuation: CheckedContinuation<Double?, Never>) { self.continuation = continuation }
        func resume(_ value: Double?) {
            let c = lock.withLock { () -> CheckedContinuation<Double?, Never>? in
                defer { continuation = nil }
                return continuation
            }
            c?.resume(returning: value)
        }
    }
}

/// More than one running Chrome (e.g. an automation copy with no windows): AppleScript may talk to the
/// wrong one, so the episode silently falls back to the stored position.
final class ChromeInstanceCheck {
    static let note = "More than one Chrome is running — episode detection may be wrong. Quit the extra copy."
    private let count: () -> Int
    private var lastCount = 0

    init(count: @escaping () -> Int = {
        NSRunningApplication.runningApplications(withBundleIdentifier: AppleScriptChrome.bundleIdentifier).count
    }) { self.count = count }

    /// True when several Chromes run. Logs when the number changes.
    func hasMultiple() -> Bool {
        let n = count()
        if n != lastCount {
            lastCount = n
            if n > 1 { Diag.log("warning: \(n) Chrome instances are running; episode detection may talk to the wrong one") }
        }
        return n > 1
    }
}
