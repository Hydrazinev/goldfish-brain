import AppKit
import Foundation

/// What the Netflix tab said at one moment: which title (`/watch/<id>`) and how far through it (0...1).
struct PlaybackReading: Equatable, Sendable {
    var watchId: String?
    var progress: Double?
}

/// How far through the video the viewer is (0...1), read from the Netflix `<video>` via Chrome.
/// It is only a number: nothing else leaves the Mac because of it.
enum PlaybackProgress {
    /// "watchId|progress", e.g. "81234567|0.42" (either part may be empty).
    static let js = "(function(){var m=location.pathname.match(/^\\/watch\\/(\\d+)/);var v=document.querySelector('video');"
        + "return (m?m[1]:'')+'|'+((v&&v.duration)?String(v.currentTime/v.duration):'')})()"

    /// "0.42" or "id|0.42" -> 0.42, clamped to 0...1. Nil for empty, "jserr|||…", NaN/Infinity or anything non-numeric.
    static func parse(_ raw: String?) -> Double? { parseReading(raw)?.progress }

    /// "81234567|0.42" -> (id, 0.42); a bare "0.42" is progress with no id. Nil for junk and script errors.
    static func parseReading(_ raw: String?) -> PlaybackReading? {
        guard let text = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty, !text.hasPrefix("jserr") else { return nil }
        let parts = text.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        let idPart = parts.count == 2 ? parts[0] : ""
        let numberPart = parts.count == 2 ? parts[1] : parts[0]
        let id = idPart.isEmpty || !idPart.allSatisfy(\.isNumber) ? nil : idPart
        var progress: Double?
        if let value = Double(numberPart.trimmingCharacters(in: .whitespaces)), value.isFinite {
            progress = min(1, max(0, value))
        }
        if id == nil && progress == nil { return nil }
        return PlaybackReading(watchId: id, progress: progress)
    }

    /// The progress to send with a capture: only when the episode came from Chrome detection and the tab's
    /// watch id when progress was read is the one the episode was detected on. Anything else is nil.
    static func attachable(_ reading: PlaybackReading?, to episode: ResolvedEpisode) -> Double? {
        guard episode.source == .chrome, let id = episode.watchId, !id.isEmpty,
              let reading, reading.watchId == id else { return nil }
        return reading.progress
    }

    /// Reads the tab off the main thread (AppleScript can block for seconds) and gives up
    /// after `timeout`, returning nil, so a slow Chrome never delays the card.
    static func read(from chrome: ChromeBridge, timeout: TimeInterval = 1) async -> PlaybackReading? {
        await withCheckedContinuation { (continuation: CheckedContinuation<PlaybackReading?, Never>) in
            let once = Once(continuation)
            DispatchQueue.global(qos: .userInitiated).async {
                once.resume(parseReading(chrome.runJS(js)))
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { once.resume(nil) }
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<PlaybackReading?, Never>?
        init(_ continuation: CheckedContinuation<PlaybackReading?, Never>) { self.continuation = continuation }
        func resume(_ value: PlaybackReading?) {
            let c = lock.withLock { () -> CheckedContinuation<PlaybackReading?, Never>? in
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
