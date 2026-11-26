import Foundation
import Darwin

enum EngineStatus: Equatable {
    case stopped
    case starting
    case warmingUp(since: Date)
    case ready
    case crashed(restarts: Int)
    case portInUse
    case failed
}

/// What other parts of the app need from the supervisor: current state and a plain-language
/// explanation when it is not `ready`.
protocol StatusProviding: AnyObject {
    var status: EngineStatus { get }
    var userMessage: String? { get }
}

/// The OS process that runs the Python engine, behind a protocol so supervision logic can be tested.
protocol EngineProcess: AnyObject {
    func start() throws
    func stop()
    var isRunning: Bool { get }
    /// Process id of the running child, nil when not running.
    var pid: Int32? { get }
}

/// Keeps the engine alive: starts it, tells "still warming up" from "hung" from "crashed", and restarts it
/// a limited number of times. Drive it by calling `tick()` every couple of seconds.
///
/// Restart rules (never restart on a single failed probe):
/// - child process not running (crash): restart, up to `maxRestarts` times, then `.failed`;
/// - process running but `/health` failing: during the first `warmUpWindow` seconds that is just
///   "warming up"; after it, `unhealthyProbesBeforeRestart` consecutive failures restart the engine
///   (counted against `maxRestarts`).
final class EngineSupervisor: StatusProviding, @unchecked Sendable {
    static let warmUpWindow: TimeInterval = 90
    static let unhealthyProbesBeforeRestart = 5
    /// After the engine has been continuously healthy this long, earlier crashes are forgotten.
    static let stableRunResetInterval: TimeInterval = 120
    static let port: UInt16 = 8000

    static let warmingUpMessage = "Goldfish Brain is warming up — try again in a few seconds."
    static let notRespondingMessage = "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain."
    static let portInUseMessage =
        "Another copy of Goldfish Brain (an older one) is already using port 8000. Quit it, then reopen Goldfish Brain. "
        + "(In Terminal: lsof -nP -iTCP:8000 -sTCP:LISTEN)"

    private let process: EngineProcess
    private let health: () async -> Bool
    private let portIsBusy: () -> Bool
    private let now: () -> Date
    private let maxRestarts: Int
    private let pidFile: URL?
    private let processInspector: (Int32) -> String?
    private let terminateOrphan: (Int32) -> Void

    private let lock = NSLock()
    private var _status: EngineStatus = .stopped
    private var restarts = 0
    private var startedAt = Date()
    private var consecutiveFailures = 0
    private var readySince: Date?
    private var gaveUp = false
    private var ticking = false

    init(process: EngineProcess,
         health: @escaping () async -> Bool,
         portIsBusy: @escaping () -> Bool,
         now: @escaping () -> Date = Date.init,
         maxRestarts: Int = 3,
         pidFile: URL? = nil,
         processInspector: @escaping (Int32) -> String? = ProcessInspector.commandLine(pid:),
         terminateOrphan: @escaping (Int32) -> Void = ProcessInspector.terminate(pid:)) {
        self.pidFile = pidFile
        self.processInspector = processInspector
        self.terminateOrphan = terminateOrphan
        self.process = process
        self.health = health
        self.portIsBusy = portIsBusy
        self.now = now
        self.maxRestarts = maxRestarts
    }

    var status: EngineStatus { lock.withLock { _status } }

    var userMessage: String? {
        switch status {
        case .stopped, .ready: return nil
        case .starting, .warmingUp, .crashed: return Self.warmingUpMessage
        case .failed: return Self.notRespondingMessage
        case .portInUse: return Self.portInUseMessage
        }
    }

    // MARK: control

    /// Launch the engine, unless something already holds the port.
    func start() {
        launch()
    }

    /// Stop the engine for good (app quit). Later ticks do nothing. The (possibly slow) process
    /// termination happens after the lock is released so status readers never wait for it.
    func stop() {
        lock.withLock {
            _status = .stopped
            gaveUp = false
            removePidFile()
        }
        process.stop()
    }

    private enum TickAction { case skip, retryLaunch, probe }

    /// One supervision step. Safe to call from a timer; overlapping calls are ignored.
    func tick() async {
        let action = lock.withLock { prepareTick() }
        defer { if action != .skip { lock.withLock { ticking = false } } }
        switch action {
        case .skip:
            return
        case .retryLaunch:
            launch()
            return
        case .probe:
            break
        }

        let running = lock.withLock { process.isRunning }
        if !running {
            lock.withLock { handleExit() }
            return
        }
        let ok = await health()
        let t = now()
        let hung = lock.withLock { apply(health: ok, at: t) }
        if hung {
            process.stop()  // slow; deliberately outside the lock
            lock.withLock { handleExit() }
        }
    }

    // MARK: launch (lock is NOT held across orphan termination)

    private func launch() {
        lock.withLock {
            restarts = 0
            consecutiveFailures = 0
            readySince = nil
            gaveUp = false
        }
        var busy = portIsBusy()
        if busy, let orphan = lock.withLock({ takeOrphanPid() }) {
            terminateOrphan(orphan)  // SIGTERM then SIGKILL; may take seconds
            busy = portIsBusy()
        }
        lock.withLock {
            if busy {
                _status = .portInUse
                return
            }
            do {
                try process.start()
                writePidFile()
                startedAt = now()
                _status = .starting
            } catch {
                Diag.log("engine start failed: \(error)")
                giveUp()
            }
        }
    }

    /// Reads the pidfile. Returns a pid only when it names a live process that looks like our engine.
    /// A stale or foreign pidfile is deleted and nil returned; a foreign process is never touched.
    private func takeOrphanPid() -> Int32? {
        guard let pidFile, let text = try? String(contentsOf: pidFile, encoding: .utf8) else { return nil }
        let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
        guard let pid, pid > 1, pid != getpid(),
              let command = processInspector(pid), ProcessInspector.looksLikeEngine(command) else {
            removePidFile()
            return nil
        }
        removePidFile()
        return pid
    }

    private func writePidFile() {
        guard let pidFile, let pid = process.pid else { return }
        try? FileManager.default.createDirectory(at: pidFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? "\(pid)\n".write(to: pidFile, atomically: true, encoding: .utf8)
    }

    private func removePidFile() {
        guard let pidFile else { return }
        try? FileManager.default.removeItem(at: pidFile)
    }

    // MARK: state machine (all called with the lock held)

    private func prepareTick() -> TickAction {
        if ticking || gaveUp { return .skip }
        switch _status {
        case .stopped:
            return .skip
        case .portInUse:
            ticking = true
            return .retryLaunch
        default:
            ticking = true
            return .probe
        }
    }

    private func giveUp() {
        process.stop()  // the child is already dead or never started, so this returns immediately
        removePidFile()
        gaveUp = true
        _status = .failed
    }

    /// The process died, or was judged hung and stopped: restart it if the budget allows.
    private func handleExit() {
        if _status == .stopped { return }  // quit raced with a restart: stay stopped
        restarts += 1
        consecutiveFailures = 0
        readySince = nil
        if restarts > maxRestarts {
            giveUp()
            return
        }
        do {
            try process.start()
            writePidFile()
            startedAt = now()
            _status = .crashed(restarts: restarts)
        } catch {
            Diag.log("engine restart failed: \(error)")
            giveUp()
        }
    }

    /// Returns true when the engine is judged hung and must be stopped and restarted (by the caller, off the lock).
    private func apply(health ok: Bool, at t: Date) -> Bool {
        if _status == .stopped { return false }  // stopped while the probe was in flight
        if ok {
            consecutiveFailures = 0
            if readySince == nil { readySince = t }
            _status = .ready
            if restarts > 0, let since = readySince, t.timeIntervalSince(since) >= Self.stableRunResetInterval {
                restarts = 0
            }
            return false
        }
        readySince = nil
        if t.timeIntervalSince(startedAt) < Self.warmUpWindow {
            // Still within the warm-up window: not a failure. A ready engine stays ready.
            consecutiveFailures = 0
            if _status != .ready { _status = .warmingUp(since: startedAt) }
            return false
        }
        consecutiveFailures += 1
        if consecutiveFailures >= Self.unhealthyProbesBeforeRestart {
            return true
        }
        if _status != .ready { _status = .failed }
        return false
    }
}

// MARK: - Real wiring helpers

/// `/health` probe with a short timeout so a slow or hung engine is noticed quickly.
enum EngineHealthProbe {
    static func make(baseURL: URL = EngineClient.defaultBaseURL,
                     timeout: TimeInterval = 3,
                     protocolClasses: [AnyClass]? = nil) -> () async -> Bool {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout
        if let protocolClasses { config.protocolClasses = protocolClasses }
        let session = URLSession(configuration: config)
        let base = baseURL.absoluteString.hasSuffix("/") ? String(baseURL.absoluteString.dropLast()) : baseURL.absoluteString
        let url = URL(string: base + "/health") ?? baseURL
        return {
            var req = URLRequest(url: url)
            req.timeoutInterval = timeout
            guard let (_, response) = try? await session.data(for: req),
                  let http = response as? HTTPURLResponse else { return false }
            return (200..<300).contains(http.statusCode)
        }
    }
}

/// "Is something already listening on this port?" Connecting (not binding) avoids false positives from
/// sockets lingering in TIME_WAIT after our own engine was stopped.
enum PortProbe {
    static func make(port: UInt16 = EngineSupervisor.port,
                     canConnect: @escaping (UInt16) -> Bool = PortProbe.posixCanConnect(port:)) -> () -> Bool {
        { canConnect(port) }
    }

    static func posixCanConnect(port: UInt16) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr = in_addr(s_addr: UInt32(0x7f00_0001).bigEndian)
        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }
}

/// Runs `<repo>/venv/bin/python src/server.py` with the repo as working directory, logging to
/// `~/Library/Logs/CineID/engine.log`.
final class ProcessEngine: EngineProcess, @unchecked Sendable {
    struct LaunchSpec: Equatable {
        let executable: URL
        let arguments: [String]
        let cwd: URL
        let logFile: URL
    }

    enum LaunchError: Error { case pythonMissing(String), logUnavailable }

    static func launchSpec(repoRoot: URL,
                           home: URL = FileManager.default.homeDirectoryForCurrentUser) -> LaunchSpec {
        LaunchSpec(
            executable: repoRoot.appendingPathComponent("venv/bin/python"),
            arguments: ["src/server.py"],
            cwd: repoRoot,
            logFile: home.appendingPathComponent("Library/Logs/CineID/engine.log")
        )
    }

    /// The repo root baked into Info.plist (`CineIDRepoRoot`), standardized (collapses `mac/..`).
    /// Nil when missing, empty or an unexpanded build variable.
    static func repoRoot(infoDictionary: [String: Any]? = Bundle.main.infoDictionary) -> URL? {
        guard let raw = infoDictionary?["CineIDRepoRoot"] as? String else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.contains("$(") else { return nil }
        return URL(fileURLWithPath: trimmed).standardizedFileURL
    }

    private let spec: LaunchSpec
    private let lock = NSLock()
    private var process: Process?

    init(repoRoot: URL, home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        self.spec = Self.launchSpec(repoRoot: repoRoot.standardizedFileURL, home: home)
    }

    var isRunning: Bool { lock.withLock { process?.isRunning ?? false } }

    var pid: Int32? {
        lock.withLock { process?.isRunning == true ? process?.processIdentifier : nil }
    }

    func start() throws {
        try lock.withLock {
            if process?.isRunning == true { return }
            guard FileManager.default.isExecutableFile(atPath: spec.executable.path) else {
                throw LaunchError.pythonMissing(spec.executable.path)
            }
            let fm = FileManager.default
            try fm.createDirectory(at: spec.logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !fm.fileExists(atPath: spec.logFile.path) {
                fm.createFile(atPath: spec.logFile.path, contents: nil)
            }
            guard let log = try? FileHandle(forWritingTo: spec.logFile) else { throw LaunchError.logUnavailable }
            _ = try? log.seekToEnd()

            let p = Process()
            p.executableURL = spec.executable
            p.arguments = spec.arguments
            p.currentDirectoryURL = spec.cwd
            p.standardOutput = log
            p.standardError = log
            var env = ProcessInfo.processInfo.environment
            env["PYTHONUNBUFFERED"] = "1"
            p.environment = env
            do {
                try p.run()
            } catch {
                try? log.close()
                throw error
            }
            try? log.close()  // the child holds its own copy of the descriptor
            process = p
        }
    }

    /// Terminate the child (SIGTERM, then SIGKILL after a short grace period).
    func stop() {
        let p = lock.withLock { () -> Process? in
            defer { process = nil }
            return process
        }
        guard let p, p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }
}

/// Looks at, and terminates, other processes by pid (used to clean up an engine orphaned by a crash).
enum ProcessInspector {
    /// The full command line of `pid` (`ps -p <pid> -o command=`), or nil when no such process.
    static func commandLine(pid: Int32) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-p", String(pid), "-o", "command="]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    static func looksLikeEngine(_ command: String) -> Bool {
        command.contains("src/server.py")
    }

    /// SIGTERM, then SIGKILL if still alive after about 3 seconds.
    static func terminate(pid: Int32) {
        guard pid > 1, pid != getpid() else { return }
        kill(pid, SIGTERM)
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline {
            if kill(pid, 0) != 0 { return }  // gone
            Thread.sleep(forTimeInterval: 0.05)
        }
        kill(pid, SIGKILL)
        for _ in 0..<20 {
            if kill(pid, 0) != 0 { return }
            Thread.sleep(forTimeInterval: 0.05)
        }
    }
}
