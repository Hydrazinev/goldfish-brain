import XCTest
import AppKit
@testable import CineID

private final class FakeScheduler: Scheduler {
    final class Job: Cancellable {
        let fireAt: TimeInterval
        let work: () -> Void
        var cancelled = false, fired = false
        init(fireAt: TimeInterval, work: @escaping () -> Void) { self.fireAt = fireAt; self.work = work }
        func cancel() { cancelled = true }
    }
    private(set) var now: TimeInterval = 0
    private(set) var jobs: [Job] = []
    var pendingCount: Int { jobs.filter { !$0.cancelled && !$0.fired }.count }
    func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable {
        let job = Job(fireAt: now + after, work: work)
        jobs.append(job)
        return job
    }
    func advance(by seconds: TimeInterval) {
        now += seconds
        for job in jobs where !job.cancelled && !job.fired && job.fireAt <= now { job.fired = true; job.work() }
    }
}

@MainActor
final class FadeTimerTests: XCTestCase {
    func testFiresAfterTheDuration() {
        let scheduler = FakeScheduler()
        var fired = 0
        let timer = FadeTimer(scheduler: scheduler, duration: 12) { fired += 1 }
        timer.start()
        scheduler.advance(by: 11.9)
        XCTAssertEqual(fired, 0)
        scheduler.advance(by: 0.2)
        XCTAssertEqual(fired, 1)
    }

    func testHoveringPausesAndLeavingRestartsAFullCountdown() {
        let scheduler = FakeScheduler()
        var fired = 0
        let timer = FadeTimer(scheduler: scheduler, duration: 12) { fired += 1 }
        timer.start()
        scheduler.advance(by: 10)
        timer.setHovering(true)
        scheduler.advance(by: 100)
        XCTAssertEqual(fired, 0, "never fades while the pointer is over the card")
        timer.setHovering(false)
        scheduler.advance(by: 11.9)
        XCTAssertEqual(fired, 0)
        scheduler.advance(by: 0.2)
        XCTAssertEqual(fired, 1)
    }

    func testStartWhileHoveringWaitsForThePointerToLeave() {
        let scheduler = FakeScheduler()
        var fired = 0
        let timer = FadeTimer(scheduler: scheduler, duration: 5) { fired += 1 }
        timer.setHovering(true)
        timer.start()
        XCTAssertEqual(scheduler.pendingCount, 0)
        timer.setHovering(false)
        scheduler.advance(by: 5)
        XCTAssertEqual(fired, 1)
    }

    func testStopDisarmsAndLeavingDoesNotRestart() {
        let scheduler = FakeScheduler()
        var fired = 0
        let timer = FadeTimer(scheduler: scheduler, duration: 5) { fired += 1 }
        timer.start()
        timer.setHovering(true)
        timer.stop()
        timer.setHovering(false)
        scheduler.advance(by: 50)
        XCTAssertEqual(fired, 0)
        XCTAssertEqual(scheduler.pendingCount, 0)
    }

    func testOnePendingJobAtATime() {
        let scheduler = FakeScheduler()
        let timer = FadeTimer(scheduler: scheduler, duration: 5) {}
        timer.start(); timer.start(); timer.setHovering(true); timer.setHovering(false)
        XCTAssertEqual(scheduler.pendingCount, 1)
    }
}

@MainActor
final class PopupFadeAndFeedbackTests: XCTestCase {
    private var scheduler: FakeScheduler!
    private var model: PopupModel!

    override func setUp() {
        super.setUp()
        scheduler = FakeScheduler()
        model = PopupModel(scheduler: scheduler)
    }

    private func response(showId: String = "bb") -> IdentifyResponse {
        let json = """
        {"characters":[{"character_id":"saul","name":"Saul","actor":"A","confidence":0.9}],"total_faces_detected":1,"show_id":"\(showId)"}
        """
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        return try! d.decode(IdentifyResponse.self, from: Data(json.utf8))
    }

    func testIdentifyCardFadesAfterTwelveSecondsByDefault() {
        model.present(response())
        scheduler.advance(by: 11.9)
        XCTAssertNotEqual(model.phase, .hidden)
        scheduler.advance(by: 0.2)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testHoverPausesTheFadeOfTheIdentifyCard() {
        model.present(response())
        scheduler.advance(by: 6)
        model.hoverChanged(true)
        scheduler.advance(by: 60)
        XCTAssertNotEqual(model.phase, .hidden)
        model.hoverChanged(false)
        scheduler.advance(by: 12.1)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testDismissClearsStaleHoverSoTheNextCardFades() {
        model.present(response())
        model.hoverChanged(true)
        model.dismiss()
        model.present(response())
        scheduler.advance(by: 12.1)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testWrongSendsTheTopCardAndShowsThanksForOneAndAHalfSeconds() {
        var sent: [FeedbackReport] = []
        model.onFeedback = { sent.append($0) }
        model.viewingPosition = ViewingPosition(season: 3, episode: 7)
        model.present(response(showId: "bb"))
        model.reportFeedback(.wrong)
        XCTAssertEqual(sent, [FeedbackReport(showId: "bb", characterId: "saul", kind: .wrong, season: 3, episode: 7)])
        XCTAssertTrue(model.feedbackThanks)
        scheduler.advance(by: 1.4)
        XCTAssertTrue(model.feedbackThanks)
        scheduler.advance(by: 0.2)
        XCTAssertFalse(model.feedbackThanks)
    }

    func testMissedSendsNoCharacterId() {
        var sent: [FeedbackReport] = []
        model.onFeedback = { sent.append($0) }
        model.viewingPosition = ViewingPosition(season: 1, episode: 2)
        model.present(response())
        model.reportFeedback(.missed)
        XCTAssertEqual(sent.first?.kind, .missed)
        XCTAssertEqual(sent.first?.characterId, "")
    }

    func testSecondTapDuringThanksIsIgnoredAndOtherPhasesIgnoreFeedback() {
        var count = 0
        model.onFeedback = { _ in count += 1 }
        model.reportFeedback(.wrong)            // hidden
        model.begin(); model.reportFeedback(.wrong)   // identifying
        XCTAssertEqual(count, 0)
        model.present(response())
        model.reportFeedback(.wrong); model.reportFeedback(.missed)
        XCTAssertEqual(count, 1)
    }

    func testThanksClearsWhenTheCardIsReplacedOrDismissed() {
        model.present(response())
        model.reportFeedback(.wrong)
        model.dismiss()
        XCTAssertFalse(model.feedbackThanks)
        model.present(response())
        model.reportFeedback(.wrong)
        model.begin()
        XCTAssertFalse(model.feedbackThanks)
    }

    func testHotkeyDecision() {
        typealias A = IdentifyHotkeyAction
        XCTAssertEqual(A.decide(running: true, phase: .identifying), .ignore)
        XCTAssertEqual(A.decide(running: true, phase: .hidden), .ignore)
        XCTAssertEqual(A.decide(running: false, phase: .hidden), .start)
        let card = PopupScenario.front.makeModel().phase
        XCTAssertEqual(A.decide(running: false, phase: card), .dismiss)
        XCTAssertEqual(A.decide(running: false, phase: .message(.noFace)), .dismiss)
        XCTAssertEqual(A.decide(running: false, phase: .message(.hiddenBySpoilerShield)), .dismiss)
        XCTAssertEqual(A.decide(running: false, phase: .message(.failure(.engineWarmingUp))), .start, "retry, not close")
        XCTAssertEqual(A.decide(running: false, phase: .message(.notice("x"))), .start)
    }
}

// MARK: dismiss rules

private final class FakeEscape: EscapeKeying {
    var armed = false
    var claimable = true
    var handler: (() -> Void)?
    func arm(_ handler: @escaping () -> Void) -> Bool {
        guard claimable else { return false }
        armed = true; self.handler = handler; return true
    }
    func disarm() { armed = false; handler = nil }
}

private final class FakeClicks: ClickMonitoring {
    var running = false
    var handler: ((CGPoint) -> Void)?
    func start(_ handler: @escaping (CGPoint) -> Void) { running = true; self.handler = handler }
    func stop() { running = false; handler = nil }
}

@MainActor
final class DismissWatcherTests: XCTestCase {
    private let card = CGRect(x: 100, y: 100, width: 300, height: 400)
    private var escape: FakeEscape!
    private var clicks: FakeClicks!
    private var mouse = CGPoint.zero
    private var dismissed = 0
    private var hovers: [Bool] = []
    private var watcher: DismissWatcher!

    override func setUp() {
        super.setUp()
        escape = FakeEscape(); clicks = FakeClicks(); mouse = .zero; dismissed = 0; hovers = []
        watcher = DismissWatcher(escape: escape, clicks: clicks, mouseLocation: { [unowned self] in mouse },
                                 cardRect: { [unowned self] in card }, pollInterval: nil,
                                 onDismiss: { [unowned self] in dismissed += 1 },
                                 onHover: { [unowned self] in hovers.append($0) })
    }

    func testListensOnlyWhileActive() {
        XCTAssertFalse(escape.armed); XCTAssertFalse(clicks.running)
        watcher.setActive(true)
        XCTAssertTrue(escape.armed); XCTAssertTrue(clicks.running)
        watcher.setActive(false)
        XCTAssertFalse(escape.armed); XCTAssertFalse(clicks.running)
    }

    func testEscapeDismissesOnlyWhileActive() {
        watcher.escapePressed()
        XCTAssertEqual(dismissed, 0)
        watcher.setActive(true)
        escape.handler?()
        XCTAssertEqual(dismissed, 1)
    }

    func testClickOutsideDismissesClickInsideDoesNot() {
        watcher.setActive(true)
        clicks.handler?(CGPoint(x: 250, y: 300))
        XCTAssertEqual(dismissed, 0)
        clicks.handler?(CGPoint(x: 50, y: 300))
        XCTAssertEqual(dismissed, 1)
        clicks.handler?(CGPoint(x: 100, y: 100))   // the card's own corner counts as inside
        XCTAssertEqual(dismissed, 1)
    }

    func testClickWhenNotActiveIsIgnored() {
        watcher.handleClick(at: CGPoint(x: 0, y: 0))
        XCTAssertEqual(dismissed, 0)
    }

    func testHoverIsReportedOnChangeOnlyAndClearedOnDeactivate() {
        watcher.setActive(true)
        watcher.tick(); XCTAssertEqual(hovers, [])
        mouse = CGPoint(x: 200, y: 200)
        watcher.tick(); watcher.tick()
        XCTAssertEqual(hovers, [true])
        mouse = CGPoint(x: 0, y: 0)
        watcher.tick()
        XCTAssertEqual(hovers, [true, false])
        mouse = CGPoint(x: 200, y: 200)
        watcher.tick()
        watcher.setActive(false)
        XCTAssertEqual(hovers, [true, false, true, false], "hover is released when the card goes away")
    }

    func testAnEscapeThatCannotBeClaimedDoesNotBreakTheRest() {
        escape.claimable = false
        watcher.setActive(true)
        XCTAssertFalse(escape.armed)
        XCTAssertTrue(clicks.running)
    }
}

private final class FakeBackend: HotKeyBackend {
    var registered: [UInt32: HotKeyCombo] = [:]
    var next: UInt32 = 1
    func register(_ combo: HotKeyCombo, handler: @escaping () -> Void) throws -> UInt32 {
        if registered.values.contains(combo) { throw HotKeyError.alreadyRegistered }
        defer { next += 1 }
        registered[next] = combo
        return next
    }
    func unregister(_ token: UInt32) { registered[token] = nil }
}

final class CarbonEscapeTests: XCTestCase {
    func testArmRegistersBareEscapeAndDisarmReleasesIt() {
        let backend = FakeBackend()
        let escape = CarbonEscape(backend: backend)
        XCTAssertTrue(escape.arm {})
        XCTAssertEqual(Array(backend.registered.values), [HotKeyCombo.escape])
        XCTAssertEqual(HotKeyCombo.escape.modifiers, 0)
        escape.disarm()
        XCTAssertTrue(backend.registered.isEmpty)
    }

    func testReportsFailureWhenAnotherHolderHasEscape() {
        let backend = FakeBackend()
        _ = try? backend.register(.escape) {}
        XCTAssertFalse(CarbonEscape(backend: backend).arm {})
    }

    func testAskComboIsCommandShiftE() {
        XCTAssertNotEqual(HotKeyCombo.ask, HotKeyCombo.identify)
        XCTAssertEqual(HotKeyCombo.ask.modifiers, HotKeyCombo.identify.modifiers)
    }
}

@MainActor
final class PopupPresenterWatcherTests: XCTestCase {
    private final class Window: PopupWindowing {
        var frame = CGRect(x: 0, y: 0, width: 400, height: 500)
        var currentFrame: CGRect { frame }
        func show(visibleFrame: CGRect) {}
        func refit(visibleFrame: CGRect) {}
        func hide() {}
    }

    func testWatcherIsActiveExactlyWhileTheCardIsVisibleAndCardRectExcludesTheMargin() {
        let model = PopupModel(scheduler: FakeScheduler())
        let window = Window()
        let presenter = PopupPresenter(model: model, screenProvider: { nil },
                                       fallbackVisibleFrame: CGRect(x: 0, y: 0, width: 1000, height: 800), window: window)
        let watcher = DismissWatcher(escape: FakeEscape(), clicks: FakeClicks(), cardRect: { presenter.cardRect },
                                     pollInterval: nil, onDismiss: {}, onHover: { _ in })
        presenter.attach(watcher)
        XCTAssertFalse(watcher.isActive)
        XCTAssertNil(presenter.cardRect)
        model.begin()
        XCTAssertTrue(watcher.isActive)
        XCTAssertEqual(presenter.cardRect, CGRect(x: 32, y: 32, width: 336, height: 436))
        model.dismiss()
        XCTAssertFalse(watcher.isActive)
    }
}
