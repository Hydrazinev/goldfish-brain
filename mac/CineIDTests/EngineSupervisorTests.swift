import XCTest
import Darwin
@testable import CineID

private final class FakeProcess: EngineProcess {
    var isRunning = false
    var startCalls = 0
    var stopCalls = 0
    var startError: Error?
    var nextPid: Int32 = 4242
    var pid: Int32? { isRunning ? currentPid : nil }
    private var currentPid: Int32 = 0

    func start() throws {
        startCalls += 1
        if let startError { throw startError }
        currentPid = nextPid
        isRunning = true
    }
    func stop() {
        stopCalls += 1
        isRunning = false
    }
}

private struct BoomError: Error {}

final class EngineSupervisorTests: XCTestCase {
    private var process: FakeProcess!
    private var healthy = false
    private var portBusy = false
    private var clock = Date(timeIntervalSince1970: 1_000_000)
    private var sup: EngineSupervisor!

    private let warmingText = "Goldfish Brain is warming up — try again in a few seconds."
    private let notRespondingText = "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain."

    override func setUp() {
        super.setUp()
        process = FakeProcess()
        healthy = false
        portBusy = false
        clock = Date(timeIntervalSince1970: 1_000_000)
        sup = EngineSupervisor(
            process: process,
            health: { [unowned self] in self.healthy },
            portIsBusy: { [unowned self] in self.portBusy },
            now: { [unowned self] in self.clock }
        )
    }

    private func advance(_ seconds: TimeInterval) { clock = clock.addingTimeInterval(seconds) }

    // MARK: start and warm-up

    func testInitialStatusIsStopped() {
        XCTAssertEqual(sup.status, .stopped)
        XCTAssertNil(sup.userMessage)
    }

    func testStartMovesToStartingAndLaunchesProcess() {
        sup.start()
        XCTAssertEqual(sup.status, .starting)
        XCTAssertEqual(process.startCalls, 1)
        XCTAssertEqual(sup.userMessage, warmingText)
    }

    func testProcessUpButHealthFalseIsWarmingUp() async {
        sup.start()
        let started = clock
        advance(5)
        await sup.tick()
        XCTAssertEqual(sup.status, .warmingUp(since: started))
        XCTAssertEqual(sup.userMessage, warmingText)
    }

    func testHealthFalseStaysWarmingUpJustBeforeWindowEnds() async {
        sup.start()
        advance(EngineSupervisor.warmUpWindow - 1)
        await sup.tick()
        XCTAssertEqual(sup.userMessage, warmingText)
        if case .warmingUp = sup.status {} else { XCTFail("expected warmingUp, got \(sup.status)") }
    }

    func testAfterWarmUpWindowWithoutHealthIsFailedWithNotRespondingMessage() async {
        sup.start()
        advance(EngineSupervisor.warmUpWindow + 1)
        await sup.tick()
        XCTAssertEqual(sup.status, .failed)
        XCTAssertEqual(sup.userMessage, notRespondingText)
        XCTAssertEqual(process.startCalls, 1, "a single failed probe must not restart the engine")
        XCTAssertEqual(process.stopCalls, 0)
    }

    func testHealthTrueIsReadyWithNoMessage() async {
        sup.start()
        healthy = true
        advance(10)
        await sup.tick()
        XCTAssertEqual(sup.status, .ready)
        XCTAssertNil(sup.userMessage)
    }

    func testLateHealthAfterFailedRecoversToReady() async {
        sup.start()
        advance(EngineSupervisor.warmUpWindow + 1)
        await sup.tick()
        XCTAssertEqual(sup.status, .failed)
        healthy = true
        await sup.tick()
        XCTAssertEqual(sup.status, .ready)
    }

    // MARK: health-failure restart rule

    func testReadyEngineSingleHealthFailureDoesNotRestart() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        advance(EngineSupervisor.warmUpWindow)
        healthy = false
        await sup.tick()
        XCTAssertEqual(sup.status, .ready)
        XCTAssertEqual(process.startCalls, 1)
        XCTAssertEqual(process.stopCalls, 0)
    }

    func testConsecutiveFailuresBelowThresholdNeverRestart() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        advance(EngineSupervisor.warmUpWindow)
        healthy = false
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart - 1) {
            advance(5); await sup.tick()
        }
        XCTAssertEqual(process.startCalls, 1)
        XCTAssertEqual(process.stopCalls, 0)
    }

    func testThresholdConsecutiveFailuresAfterWarmUpRestartsEngine() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        advance(EngineSupervisor.warmUpWindow)
        healthy = false
        for _ in 0..<EngineSupervisor.unhealthyProbesBeforeRestart {
            advance(5); await sup.tick()
        }
        XCTAssertEqual(process.stopCalls, 1, "hung engine is stopped before the restart")
        XCTAssertEqual(process.startCalls, 2)
        XCTAssertEqual(sup.status, .crashed(restarts: 1))
    }

    func testFailuresDuringWarmUpAreNeverCounted() async {
        sup.start()
        // Many failed probes, all inside the warm-up window.
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart * 3) {
            advance(1); await sup.tick()
        }
        XCTAssertLessThan(clock.timeIntervalSince(Date(timeIntervalSince1970: 1_000_000)), EngineSupervisor.warmUpWindow)
        XCTAssertEqual(process.startCalls, 1)
        XCTAssertEqual(process.stopCalls, 0)
    }

    func testSuccessfulProbeResetsConsecutiveFailureCount() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        advance(EngineSupervisor.warmUpWindow)
        healthy = false
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart - 1) { advance(5); await sup.tick() }
        healthy = true
        advance(5); await sup.tick()
        healthy = false
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart - 1) { advance(5); await sup.tick() }
        XCTAssertEqual(process.stopCalls, 0)
        XCTAssertEqual(process.startCalls, 1)
    }

    func testThresholdConstants() {
        XCTAssertEqual(EngineSupervisor.warmUpWindow, 90)
        XCTAssertEqual(EngineSupervisor.unhealthyProbesBeforeRestart, 5)
    }

    // MARK: crashes

    func testProcessDeathRestartsAndReportsCrashed() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        process.isRunning = false  // child died
        advance(1); await sup.tick()
        XCTAssertEqual(sup.status, .crashed(restarts: 1))
        XCTAssertEqual(process.startCalls, 2)
        XCTAssertEqual(sup.userMessage, warmingText)
    }

    func testRestartedEngineGetsFreshWarmUpWindow() async {
        sup.start()
        healthy = true
        advance(10); await sup.tick()
        healthy = false
        process.isRunning = false
        advance(200); await sup.tick()  // restart at +210
        let restartedAt = clock
        advance(30); await sup.tick()
        XCTAssertEqual(sup.status, .warmingUp(since: restartedAt))
    }

    func testRecoversToReadyAfterRestart() async {
        sup.start()
        process.isRunning = false
        await sup.tick()
        XCTAssertEqual(sup.status, .crashed(restarts: 1))
        healthy = true
        advance(20); await sup.tick()
        XCTAssertEqual(sup.status, .ready)
    }

    func testCrashesAreRestartedUpToMaxThenFailed() async {
        sup.start()
        for n in 1...3 {
            process.isRunning = false
            await sup.tick()
            XCTAssertEqual(sup.status, .crashed(restarts: n))
            XCTAssertEqual(process.startCalls, 1 + n)
        }
        process.isRunning = false
        await sup.tick()
        XCTAssertEqual(sup.status, .failed)
        XCTAssertEqual(sup.userMessage, notRespondingText)
        XCTAssertEqual(process.startCalls, 4, "no restart after the third")
    }

    func testFailedAfterMaxRestartsIsTerminal() async {
        sup.start()
        for _ in 0..<4 { process.isRunning = false; await sup.tick() }
        XCTAssertEqual(sup.status, .failed)
        healthy = true
        await sup.tick()
        XCTAssertEqual(sup.status, .failed)
        XCTAssertEqual(process.startCalls, 4)
    }

    func testCustomMaxRestarts() async {
        let p = FakeProcess()
        let s = EngineSupervisor(process: p, health: { false }, portIsBusy: { false }, now: { self.clock }, maxRestarts: 1)
        s.start()
        p.isRunning = false
        await s.tick()
        XCTAssertEqual(s.status, .crashed(restarts: 1))
        p.isRunning = false
        await s.tick()
        XCTAssertEqual(s.status, .failed)
    }

    func testLongStableRunResetsRestartBudget() async {
        sup.start()
        process.isRunning = false
        await sup.tick()  // crashed(1)
        healthy = true
        advance(5); await sup.tick()  // ready
        advance(EngineSupervisor.stableRunResetInterval + 1); await sup.tick()
        process.isRunning = false
        await sup.tick()
        XCTAssertEqual(sup.status, .crashed(restarts: 1), "budget was reset after a long healthy run")
    }

    func testStartThrowingMovesToFailed() {
        process.startError = BoomError()
        sup.start()
        XCTAssertEqual(sup.status, .failed)
        XCTAssertEqual(sup.userMessage, notRespondingText)
    }

    func testRestartThrowingMovesToFailed() async {
        sup.start()
        process.startError = BoomError()
        process.isRunning = false
        await sup.tick()
        XCTAssertEqual(sup.status, .failed)
    }

    // MARK: port in use

    func testPortBusyBeforeStartWithForeignHealthyEngineIsPortInUse() async {
        portBusy = true
        healthy = true  // an old Goldfish Brain answers /health
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        XCTAssertEqual(process.startCalls, 0, "must not launch a second engine")
        await sup.tick()
        XCTAssertEqual(sup.status, .portInUse)
        let message = sup.userMessage ?? ""
        XCTAssertTrue(message.contains("8000"))
        XCTAssertTrue(message.lowercased().contains("older"))
        XCTAssertTrue(message.lowercased().contains("quit"))
    }

    func testPortInUseRetriesStartOnceThePortIsFree() async {
        portBusy = true
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        portBusy = false
        await sup.tick()
        XCTAssertEqual(sup.status, .starting)
        XCTAssertEqual(process.startCalls, 1)
    }

    // MARK: shutdown

    func testStopStopsProcessAndSetsStopped() {
        sup.start()
        sup.stop()
        XCTAssertEqual(process.stopCalls, 1)
        XCTAssertEqual(sup.status, .stopped)
        XCTAssertNil(sup.userMessage)
    }

    func testTickAfterStopDoesNothing() async {
        sup.start()
        sup.stop()
        healthy = true
        await sup.tick()
        XCTAssertEqual(sup.status, .stopped)
        XCTAssertEqual(process.startCalls, 1)
    }

    func testStopDuringHealthProbeIsNotOverwritten() async {
        let gate = AsyncGate()
        let p = FakeProcess()
        let s = EngineSupervisor(process: p, health: { await gate.wait(); return true }, portIsBusy: { false }, now: { self.clock })
        s.start()
        let ticking = Task { await s.tick() }
        try? await Task.sleep(nanoseconds: 50_000_000)
        s.stop()
        await gate.open()
        await ticking.value
        XCTAssertEqual(s.status, .stopped)
    }

    func testOverlappingTicksProbeOnlyOnce() async {
        let gate = AsyncGate()
        let counter = Counter()
        let s = EngineSupervisor(process: process, health: { await counter.hit(); await gate.wait(); return true },
                                 portIsBusy: { false }, now: { self.clock })
        s.start()
        let a = Task { await s.tick() }
        try? await Task.sleep(nanoseconds: 50_000_000)
        await s.tick()  // overlaps with the first, returns immediately
        await gate.open()
        await a.value
        let hits = await counter.value
        XCTAssertEqual(hits, 1)
        XCTAssertEqual(s.status, .ready)
    }

    // MARK: StatusProviding

    func testSupervisorIsAStatusProvider() {
        let provider: StatusProviding = sup
        XCTAssertEqual(provider.status, .stopped)
        XCTAssertNil(provider.userMessage)
    }
}

private actor AsyncGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func open() {
        isOpen = true
        waiters.forEach { $0.resume() }
        waiters.removeAll()
    }
}

private actor Counter {
    private(set) var value = 0
    func hit() { value += 1 }
}


// MARK: - Fix round 1: lock-free stop, pidfile and orphan recovery

private final class SlowStopProcess: EngineProcess {
    var isRunning = true
    var pid: Int32? { 99 }
    let stopEntered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    func start() throws { isRunning = true }
    func stop() {
        stopEntered.signal()
        release.wait()
        isRunning = false
    }
}

final class EngineSupervisorStopLockTests: XCTestCase {
    private var clock = Date(timeIntervalSince1970: 1_000_000)

    /// Reads `status` and `userMessage` from another thread and returns whether they came back promptly.
    private func readsPromptly(_ sup: EngineSupervisor) -> Bool {
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global().async { _ = sup.status; _ = sup.userMessage; done.signal() }
        return done.wait(timeout: .now() + 1) == .success
    }

    func testStatusReadableWhileSlowStopInProgress() {
        let p = SlowStopProcess()
        let sup = EngineSupervisor(process: p, health: { true }, portIsBusy: { false }, now: { self.clock })
        sup.start()
        DispatchQueue.global().async { sup.stop() }
        XCTAssertEqual(p.stopEntered.wait(timeout: .now() + 2), .success)
        let prompt = readsPromptly(sup)
        p.release.signal()  // always unblock, so a regression fails instead of deadlocking
        XCTAssertTrue(prompt, "status must not block behind a slow process.stop()")
        XCTAssertEqual(sup.status, .stopped)
    }

    func testStatusReadableWhileHungRestartStopsProcess() async {
        let p = SlowStopProcess()
        var healthy = true
        let sup = EngineSupervisor(process: p, health: { healthy }, portIsBusy: { false }, now: { self.clock })
        sup.start()
        clock = clock.addingTimeInterval(10)
        await sup.tick()
        clock = clock.addingTimeInterval(EngineSupervisor.warmUpWindow)
        healthy = false
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart - 1) {
            clock = clock.addingTimeInterval(5)
            await sup.tick()
        }
        clock = clock.addingTimeInterval(5)
        let ticking = Task.detached { await sup.tick() }  // the 5th failure: blocks inside process.stop()
        XCTAssertEqual(p.stopEntered.wait(timeout: .now() + 2), .success)
        let prompt = readsPromptly(sup)
        p.release.signal()
        XCTAssertTrue(prompt, "status must not block behind a slow process.stop()")
        await ticking.value
        XCTAssertEqual(sup.status, .crashed(restarts: 1))
    }

    func testStopDuringHungRestartDoesNotResurrectEngine() async {
        let p = SlowStopProcess()
        var healthy = true
        let sup = EngineSupervisor(process: p, health: { healthy }, portIsBusy: { false }, now: { self.clock })
        sup.start()
        clock = clock.addingTimeInterval(10)
        await sup.tick()
        clock = clock.addingTimeInterval(EngineSupervisor.warmUpWindow)
        healthy = false
        for _ in 0..<(EngineSupervisor.unhealthyProbesBeforeRestart - 1) {
            clock = clock.addingTimeInterval(5)
            await sup.tick()
        }
        clock = clock.addingTimeInterval(5)
        let ticking = Task.detached { await sup.tick() }
        XCTAssertEqual(p.stopEntered.wait(timeout: .now() + 2), .success)
        DispatchQueue.global().async { sup.stop() }  // quit while the hung engine is being stopped
        XCTAssertEqual(p.stopEntered.wait(timeout: .now() + 2), .success)
        p.release.signal()
        p.release.signal()
        await ticking.value
        XCTAssertEqual(sup.status, .stopped)
    }
}

final class EngineSupervisorOrphanTests: XCTestCase {
    private let ours = "/Users/x/Projects/cineid/venv/bin/python src/server.py"
    private var dir: URL!
    private var pidFile: URL!
    private var process: FakeProcess!
    private var portBusy = false
    private var inspected: [Int32] = []
    private var terminated: [Int32] = []
    private var commands: [Int32: String] = [:]
    private var terminateFreesPort = true
    private var clock = Date(timeIntervalSince1970: 1_000_000)

    override func setUpWithError() throws {
        try super.setUpWithError()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("cineid-pid-\(UUID().uuidString)")
        pidFile = dir.appendingPathComponent("sub/engine.pid")  // parent does not exist yet
        process = FakeProcess()
        portBusy = false
        inspected = []; terminated = []; commands = [:]
        terminateFreesPort = true
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func makeSupervisor() -> EngineSupervisor {
        EngineSupervisor(
            process: process,
            health: { true },
            portIsBusy: { [unowned self] in self.portBusy },
            now: { [unowned self] in self.clock },
            pidFile: pidFile,
            processInspector: { [unowned self] pid in self.inspected.append(pid); return self.commands[pid] },
            terminateOrphan: { [unowned self] pid in
                self.terminated.append(pid)
                if self.terminateFreesPort { self.portBusy = false }
            }
        )
    }

    private func writePidFile(_ text: String) throws {
        try FileManager.default.createDirectory(at: pidFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: pidFile, atomically: true, encoding: .utf8)
    }

    private var pidFileText: String? {
        (try? String(contentsOf: pidFile, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func testPidFileWrittenOnStartAndRemovedOnStop() {
        let sup = makeSupervisor()
        process.nextPid = 31337
        sup.start()
        XCTAssertEqual(pidFileText, "31337")
        sup.stop()
        XCTAssertNil(pidFileText)
        XCTAssertFalse(FileManager.default.fileExists(atPath: pidFile.path))
    }

    func testPidFileUpdatedWhenEngineIsRestarted() async {
        let sup = makeSupervisor()
        process.nextPid = 100
        sup.start()
        process.nextPid = 101
        process.isRunning = false
        await sup.tick()
        XCTAssertEqual(pidFileText, "101")
    }

    func testPidFileRemovedWhenSupervisorGivesUp() async {
        let sup = makeSupervisor()
        sup.start()
        XCTAssertNotNil(pidFileText)
        for _ in 0..<4 { process.isRunning = false; await sup.tick() }
        XCTAssertEqual(sup.status, .failed)
        XCTAssertNil(pidFileText)
    }

    func testPidFileRemovedWhenInitialStartFails() {
        process.startError = NSError(domain: "x", code: 1)
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(sup.status, .failed)
        XCTAssertNil(pidFileText)
    }

    func testOrphanWithMatchingCommandIsKilledAndFreshEngineStarts() throws {
        try writePidFile("777\n")
        commands[777] = ours
        portBusy = true
        let sup = makeSupervisor()
        process.nextPid = 888
        sup.start()
        XCTAssertEqual(terminated, [777])
        XCTAssertEqual(sup.status, .starting)
        XCTAssertEqual(process.startCalls, 1)
        XCTAssertEqual(pidFileText, "888", "pidfile now names the new engine")
    }

    func testStalePidIsIgnoredAndPortStaysInUse() throws {
        try writePidFile("777")
        commands[777] = nil  // no such process
        portBusy = true
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        XCTAssertTrue(terminated.isEmpty)
        XCTAssertEqual(process.startCalls, 0)
        XCTAssertNil(pidFileText, "stale pidfile is cleaned up")
    }

    func testReusedPidRunningUnrelatedCommandIsNeverKilled() throws {
        try writePidFile("777")
        commands[777] = "/usr/bin/vim notes.txt"
        portBusy = true
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        XCTAssertTrue(terminated.isEmpty, "must never kill a process that is not our engine")
        XCTAssertEqual(process.startCalls, 0)
    }

    func testMissingPidFileMeansPortInUseAndNothingInspected() {
        portBusy = true
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        XCTAssertTrue(inspected.isEmpty)
        XCTAssertTrue(terminated.isEmpty)
    }

    func testOrphanLogicNotConsultedWhenPortIsFree() throws {
        try writePidFile("777")
        commands[777] = ours
        portBusy = false
        let sup = makeSupervisor()
        sup.start()
        XCTAssertTrue(inspected.isEmpty)
        XCTAssertTrue(terminated.isEmpty)
        XCTAssertEqual(sup.status, .starting)
    }

    func testOrphanKilledButPortStillBusyIsPortInUse() throws {
        try writePidFile("777")
        commands[777] = ours
        portBusy = true
        terminateFreesPort = false  // something else also holds the port
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(terminated, [777])
        XCTAssertEqual(sup.status, .portInUse)
        XCTAssertEqual(process.startCalls, 0)
    }

    func testGarbageAndUnsafePidsAreNeverKilled() throws {
        for text in ["", "abc", "0", "1", "-5", String(getpid())] {
            try writePidFile(text)
            commands = [0: ours, 1: ours, -5: ours, getpid(): ours]
            portBusy = true
            terminated = []
            let sup = makeSupervisor()
            sup.start()
            XCTAssertTrue(terminated.isEmpty, "pidfile text \(text.debugDescription) must not be killed")
            XCTAssertEqual(sup.status, .portInUse)
        }
    }

    func testTickFromPortInUseReclaimsOrphanLater() async throws {
        portBusy = true
        let sup = makeSupervisor()
        sup.start()
        XCTAssertEqual(sup.status, .portInUse)
        try writePidFile("777")
        commands[777] = ours
        await sup.tick()
        XCTAssertEqual(terminated, [777])
        XCTAssertEqual(sup.status, .starting)
    }

    func testPortInUseMessageMentionsHowToFindTheHolder() {
        XCTAssertTrue(EngineSupervisor.portInUseMessage.contains("lsof -nP -iTCP:8000 -sTCP:LISTEN"))
    }
}

final class ProcessInspectorTests: XCTestCase {
    func testCommandLineOfThisProcessAndOfAMissingPid() {
        XCTAssertNotNil(ProcessInspector.commandLine(pid: getpid()))
        XCTAssertNil(ProcessInspector.commandLine(pid: 2_000_000_000))
    }

    func testTerminateKillsARealOrphanedSleepProcess() throws {
        // `sleep` is started by a shell that exits at once, so it is re-parented like a real orphan.
        let sh = Process()
        sh.executableURL = URL(fileURLWithPath: "/bin/sh")
        sh.arguments = ["-c", "sleep 60 >/dev/null 2>&1 & echo $!"]
        let out = Pipe()
        sh.standardOutput = out
        try sh.run()
        let text = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        sh.waitUntilExit()
        let pid = try XCTUnwrap(Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)))
        XCTAssertEqual(kill(pid, 0), 0, "sleep should be alive")
        XCTAssertEqual(ProcessInspector.commandLine(pid: pid)?.contains("sleep 60"), true)
        ProcessInspector.terminate(pid: pid)
        XCTAssertNotEqual(kill(pid, 0), 0, "sleep should be gone")
    }

    func testEngineCommandMatcher() {
        XCTAssertTrue(ProcessInspector.looksLikeEngine("/a/venv/bin/python src/server.py"))
        XCTAssertFalse(ProcessInspector.looksLikeEngine("/usr/bin/vim server.py"))
    }
}

// MARK: - ProcessEngine launch spec

final class ProcessEngineTests: XCTestCase {
    func testLaunchSpecBuildsPaths() {
        let repo = URL(fileURLWithPath: "/Users/someone/Projects/cineid")
        let home = URL(fileURLWithPath: "/Users/someone")
        let spec = ProcessEngine.launchSpec(repoRoot: repo, home: home)
        XCTAssertEqual(spec.executable.path, "/Users/someone/Projects/cineid/venv/bin/python")
        XCTAssertEqual(spec.arguments, ["src/server.py"])
        XCTAssertEqual(spec.cwd.path, "/Users/someone/Projects/cineid")
        XCTAssertEqual(spec.logFile.path, "/Users/someone/Library/Logs/CineID/engine.log")
    }

    func testRepoRootIsStandardized() {
        let root = ProcessEngine.repoRoot(infoDictionary: ["CineIDRepoRoot": "/Users/x/Projects/cineid/mac/.."])
        XCTAssertEqual(root?.path, "/Users/x/Projects/cineid")
    }

    func testRepoRootMissingOrUnexpandedIsNil() {
        XCTAssertNil(ProcessEngine.repoRoot(infoDictionary: [:]))
        XCTAssertNil(ProcessEngine.repoRoot(infoDictionary: ["CineIDRepoRoot": ""]))
        XCTAssertNil(ProcessEngine.repoRoot(infoDictionary: ["CineIDRepoRoot": "$(CINEID_REPO_ROOT)"]))
        XCTAssertNil(ProcessEngine.repoRoot(infoDictionary: ["CineIDRepoRoot": 5]))
    }

    func testStartWithMissingPythonThrowsWithoutLaunching() {
        let repo = FileManager.default.temporaryDirectory.appendingPathComponent("cineid-none-\(UUID().uuidString)")
        let engine = ProcessEngine(repoRoot: repo, home: repo)
        XCTAssertThrowsError(try engine.start())
        XCTAssertFalse(engine.isRunning)
        engine.stop()  // harmless when never started
    }
}

// MARK: - Port probe

final class PortProbeTests: XCTestCase {
    func testBusyWhenConnectSucceeds() {
        var asked: UInt16?
        let isBusy = PortProbe.make(port: 8000, canConnect: { asked = $0; return true })
        XCTAssertTrue(isBusy())
        XCTAssertEqual(asked, 8000)
    }

    func testFreeWhenConnectFails() {
        XCTAssertFalse(PortProbe.make(port: 8000, canConnect: { _ in false })())
    }

    func testRealConnectSeesAListeningSocketAndAFreePort() throws {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        XCTAssertGreaterThanOrEqual(fd, 0)
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        addr.sin_addr = in_addr(s_addr: UInt32(0x7f000001).bigEndian)
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        XCTAssertEqual(bound, 0)
        XCTAssertEqual(listen(fd, 1), 0)
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let got = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) }
        }
        XCTAssertEqual(got, 0)
        let port = UInt16(bigEndian: addr.sin_port)
        XCTAssertTrue(PortProbe.posixCanConnect(port: port))

        // Same port after the listener closes is free again (find a fresh closed port).
        let closedFD = socket(AF_INET, SOCK_STREAM, 0)
        var a2 = addr; a2.sin_port = 0
        _ = withUnsafePointer(to: &a2) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(closedFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        var l2 = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &a2) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(closedFD, $0, &l2) } }
        let freePort = UInt16(bigEndian: a2.sin_port)
        close(closedFD)  // bound but never listening, now closed
        XCTAssertFalse(PortProbe.posixCanConnect(port: freePort))
    }
}

// MARK: - Health probe

final class EngineHealthProbeTests: XCTestCase {
    private let base = URL(string: "http://127.0.0.1:8000")!

    override func setUp() {
        super.setUp()
        StubURLProtocol.handler = nil
    }

    private func probe() -> () async -> Bool {
        EngineHealthProbe.make(baseURL: base, timeout: 3, protocolClasses: [StubURLProtocol.self])
    }

    func testTrueOn200AndHitsHealthPathWithShortTimeout() async {
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data("{}".utf8))
        }
        let ok = await probe()()
        XCTAssertTrue(ok)
        XCTAssertEqual(seen?.url?.path, "/health")
        XCTAssertEqual(seen?.timeoutInterval, 3)
    }

    func testFalseOn503() async {
        StubURLProtocol.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!, Data())
        }
        let ok = await probe()()
        XCTAssertFalse(ok)
    }

    func testFalseOnTimeoutAndOnRefused() async {
        StubURLProtocol.handler = { _ in throw URLError(.timedOut) }
        var ok = await probe()()
        XCTAssertFalse(ok)
        StubURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        ok = await probe()()
        XCTAssertFalse(ok)
    }
}
