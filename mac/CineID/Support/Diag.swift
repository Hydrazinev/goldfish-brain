import Foundation

/// One-line diagnostics: printed to stdout and appended to ~/Library/Logs/CineID/app.log
/// (so a Finder-launched app can still be inspected).
/// Never include screenshots, API keys, file paths or personal data: only counts, flags, states and timings.
enum Diag {
    private static let start = Date()
    private static let queue = DispatchQueue(label: "cineid.diag")
    private static let logURL: URL? = {
        guard let lib = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first else { return nil }
        let dir = lib.appendingPathComponent("Logs/CineID", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("app.log")
    }()

    static func log(_ message: @autoclosure () -> String) {
        let line = String(format: "[cineid +%6.1fs] ", Date().timeIntervalSince(start)) + message()
        print(line)
        guard let url = logURL, ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return }
        queue.async {
            let data = Data((line + "\n").utf8)
            if let handle = try? FileHandle(forWritingTo: url) {
                handle.seekToEndOfFile(); handle.write(data); try? handle.close()
            } else {
                try? data.write(to: url)
            }
        }
    }
}

/// Logs how long each step of a flow took (milliseconds since the previous lap).
struct Stopwatch {
    private let name: String
    private var last = Date()
    private let begin = Date()
    init(_ name: String) { self.name = name }
    mutating func lap(_ step: String) {
        let now = Date()
        Diag.log("\(name): \(step) \(Int(now.timeIntervalSince(last) * 1000)) ms (total \(Int(now.timeIntervalSince(begin) * 1000)) ms)")
        last = now
    }
}
