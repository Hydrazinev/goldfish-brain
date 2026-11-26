import XCTest
@testable import CineID

private final class FakeScheduler: Scheduler {
    final class Job: Cancellable {
        let fireAt: TimeInterval
        let work: () -> Void
        var cancelled = false
        var fired = false
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
        for job in jobs where !job.cancelled && !job.fired && job.fireAt <= now {
            job.fired = true
            job.work()
        }
    }
}

private func card(_ id: String, _ confidence: Double, name: String? = nil) -> CharacterCard {
    CharacterCard(characterId: id, name: name ?? id.capitalized, actor: "Actor", role: nil,
                  confidence: confidence, status: nil, arcType: nil,
                  firstSeenSeason: nil, firstSeenEpisode: nil, lastSeenSeason: nil, lastSeenEpisode: nil,
                  relationshipSummary: nil, currentArc: nil, lastSeenDescription: nil,
                  importanceRank: nil, limited: nil)
}

private func response(_ cards: [CharacterCard], faces: Int? = nil,
                      lowConfidence: Bool? = nil, hidden: Int? = nil) -> IdentifyResponse {
    IdentifyResponse(characters: cards, totalFacesDetected: faces ?? cards.count, showId: "show",
                     lowConfidence: lowConfidence, hiddenBySpoilerShield: hidden)
}

final class CardStackTests: XCTestCase {
    func testEmptyIsNil() {
        XCTAssertNil(CardStack(cards: []))
    }

    func testSortsByConfidenceDescending() throws {
        let s = try XCTUnwrap(CardStack(cards: [card("a", 0.6), card("b", 0.9), card("c", 0.7)]))
        XCTAssertEqual(s.top.characterId, "b")
        XCTAssertEqual(s.cards.map(\.characterId), ["b", "c", "a"])
        XCTAssertEqual(s.count, 3)
    }

    func testTiesKeepEngineOrder() throws {
        let s = try XCTUnwrap(CardStack(cards: [card("x", 0.8), card("y", 0.8), card("z", 0.8), card("w", 0.9)]))
        XCTAssertEqual(s.cards.map(\.characterId), ["w", "x", "y", "z"])
    }

    func testDedupKeepsHighestConfidence() throws {
        let s = try XCTUnwrap(CardStack(cards: [card("a", 0.5, name: "low"), card("b", 0.6), card("a", 0.9, name: "high")]))
        XCTAssertEqual(s.count, 2)
        XCTAssertEqual(s.top.characterId, "a")
        XCTAssertEqual(s.top.name, "high")
    }

    func testDedupKeepsHighestConfidenceWhenItComesFirst() throws {
        let s = try XCTUnwrap(CardStack(cards: [card("a", 0.9, name: "high"), card("b", 0.6), card("a", 0.5, name: "low")]))
        XCTAssertEqual(s.count, 2)
        XCTAssertEqual(s.top.name, "high", "a keep-last implementation would keep the 0.5 duplicate")
    }

    func testDedupEqualConfidenceKeepsFirst() throws {
        let s = try XCTUnwrap(CardStack(cards: [card("a", 0.7, name: "first"), card("a", 0.7, name: "second")]))
        XCTAssertEqual(s.count, 1)
        XCTAssertEqual(s.top.name, "first")
    }

    func testFlipToggles() throws {
        var s = try XCTUnwrap(CardStack(cards: [card("a", 0.9)]))
        XCTAssertFalse(s.isFlipped)
        s.flip()
        XCTAssertTrue(s.isFlipped)
        s.flip()
        XCTAssertFalse(s.isFlipped)
    }

    func testNextAndPreviousWrap() throws {
        var s = try XCTUnwrap(CardStack(cards: [card("a", 0.9), card("b", 0.8), card("c", 0.7)]))
        s.next(); XCTAssertEqual(s.top.characterId, "b")
        s.next(); XCTAssertEqual(s.top.characterId, "c")
        s.next(); XCTAssertEqual(s.top.characterId, "a")
        s.previous(); XCTAssertEqual(s.top.characterId, "c")
        s.previous(); XCTAssertEqual(s.top.characterId, "b")
    }

    func testChangingTopResetsFlip() throws {
        var s = try XCTUnwrap(CardStack(cards: [card("a", 0.9), card("b", 0.8)]))
        s.flip(); s.next()
        XCTAssertFalse(s.isFlipped)
        s.flip(); s.previous()
        XCTAssertFalse(s.isFlipped)
    }

    func testSingleCardNextPreviousAreNoOps() throws {
        var s = try XCTUnwrap(CardStack(cards: [card("a", 0.9)]))
        s.flip()
        s.next(); s.previous()
        XCTAssertEqual(s.top.characterId, "a")
        XCTAssertTrue(s.isFlipped, "no-op navigation must not unflip")
        XCTAssertTrue(s.behind.isEmpty)
    }

    func testManyCardsCycleFullCircle() throws {
        let cards = (0..<8).map { card("c\($0)", 1.0 - Double($0) * 0.05) }
        var s = try XCTUnwrap(CardStack(cards: cards))
        XCTAssertEqual(s.count, 8)
        for _ in 0..<8 { s.next() }
        XCTAssertEqual(s.top.characterId, "c0")
        for _ in 0..<8 { s.previous() }
        XCTAssertEqual(s.top.characterId, "c0")
        s.previous()
        XCTAssertEqual(s.top.characterId, "c7")
    }

    func testBehindListsUpcomingCards() throws {
        var s = try XCTUnwrap(CardStack(cards: [card("a", 0.9), card("b", 0.8), card("c", 0.7)]))
        XCTAssertEqual(s.behind.map(\.characterId), ["b", "c"])
        s.next()
        XCTAssertEqual(s.behind.map(\.characterId), ["c", "a"])
    }
}

@MainActor
final class PopupModelTests: XCTestCase {
    private var scheduler: FakeScheduler!
    private var model: PopupModel!

    override func setUp() {
        super.setUp()
        scheduler = FakeScheduler()
        model = PopupModel(scheduler: scheduler, autoDismiss: 20)
    }

    private func stack() -> CardStack? {
        if case .cards(let s) = model.phase { return s }
        return nil
    }

    func testStartsHidden() {
        XCTAssertEqual(model.phase, .hidden)
    }

    func testBeginShowsIdentifying() {
        model.begin()
        XCTAssertEqual(model.phase, .identifying)
    }

    func testPresentEmptyLowConfidenceIsNotRecognised() {
        model.present(response([], faces: 1, lowConfidence: true))
        XCTAssertEqual(model.phase, .message(.notRecognised))
    }

    func testPresentEmptyHiddenBySpoilerShield() {
        model.present(response([], faces: 1, hidden: 2))
        XCTAssertEqual(model.phase, .message(.hiddenBySpoilerShield))
    }

    func testSpoilerShieldWinsOverLowConfidence() {
        model.present(response([], faces: 1, lowConfidence: true, hidden: 1))
        XCTAssertEqual(model.phase, .message(.hiddenBySpoilerShield))
    }

    func testPresentEmptyOtherwiseIsNoFace() {
        model.present(response([], faces: 0))
        XCTAssertEqual(model.phase, .message(.noFace))
    }

    func testPresentEmptyWithZeroHiddenIsNoFace() {
        model.present(response([], faces: 0, hidden: 0))
        XCTAssertEqual(model.phase, .message(.noFace))
    }

    func testPresentCardsSortedAndDeduped() throws {
        model.present(response([card("a", 0.5), card("b", 0.9), card("a", 0.7)]))
        let s = try XCTUnwrap(stack())
        XCTAssertEqual(s.cards.map(\.characterId), ["b", "a"])
        XCTAssertEqual(s.cards.last?.confidence, 0.7)
    }

    func testFlipTogglesWhenShowingCards() throws {
        model.present(response([card("a", 0.9)]))
        model.flip()
        XCTAssertEqual(try XCTUnwrap(stack()).isFlipped, true)
        model.flip()
        XCTAssertEqual(try XCTUnwrap(stack()).isFlipped, false)
    }

    func testFlipIgnoredWithoutCards() {
        model.flip()
        XCTAssertEqual(model.phase, .hidden)
        model.begin(); model.flip()
        XCTAssertEqual(model.phase, .identifying)
        model.fail(.timeout); model.flip()
        XCTAssertEqual(model.phase, .message(.failure(.timeout)))
    }

    func testNextPreviousWrapThroughModelAndResetFlip() throws {
        model.present(response([card("a", 0.9), card("b", 0.8)]))
        model.flip()
        model.next()
        XCTAssertEqual(try XCTUnwrap(stack()).top.characterId, "b")
        XCTAssertEqual(try XCTUnwrap(stack()).isFlipped, false)
        model.next()
        XCTAssertEqual(try XCTUnwrap(stack()).top.characterId, "a")
        model.previous()
        XCTAssertEqual(try XCTUnwrap(stack()).top.characterId, "b")
    }

    func testNextPreviousIgnoredWithoutCards() {
        model.next(); model.previous()
        XCTAssertEqual(model.phase, .hidden)
    }

    func testSingleCardNextPreviousNoOp() throws {
        model.present(response([card("a", 0.9)]))
        model.next(); model.previous()
        XCTAssertEqual(try XCTUnwrap(stack()).top.characterId, "a")
    }

    func testSixPlusCards() throws {
        model.present(response((0..<7).map { card("c\($0)", 0.3 + Double($0) * 0.1) }))
        let s = try XCTUnwrap(stack())
        XCTAssertEqual(s.count, 7)
        XCTAssertEqual(s.top.characterId, "c6")
    }

    func testDismissHides() {
        model.present(response([card("a", 0.9)]))
        model.dismiss()
        XCTAssertEqual(model.phase, .hidden)
    }

    func testDismissWhenAlreadyHiddenIsHarmless() {
        model.dismiss()
        XCTAssertEqual(model.phase, .hidden)
        XCTAssertEqual(scheduler.pendingCount, 0)
    }

    func testFailBlackFrame() {
        model.fail(.blackFrame)
        XCTAssertEqual(model.phase, .message(.failure(.blackFrame)))
    }

    func testFailAfterCardsReplacesThem() {
        model.present(response([card("a", 0.9)]))
        model.fail(.serverError)
        XCTAssertEqual(model.phase, .message(.failure(.serverError)))
    }

    func testBeginWhileShowingCardsReplacesThem() {
        model.present(response([card("a", 0.9)]))
        model.begin()
        XCTAssertEqual(model.phase, .identifying)
    }

    func testPresentWhileShowingCardsReplaces() throws {
        model.present(response([card("a", 0.9)]))
        model.flip()
        model.present(response([card("b", 0.8), card("c", 0.7)]))
        let s = try XCTUnwrap(stack())
        XCTAssertEqual(s.cards.map(\.characterId), ["b", "c"])
        XCTAssertFalse(s.isFlipped)
    }

    func testPresentAfterIdentifying() {
        model.begin()
        model.present(response([card("a", 0.9)]))
        XCTAssertNotNil(stack())
    }

    // MARK: auto-dismiss

    func testAutoDismissAfter20Seconds() {
        model.present(response([card("a", 0.9)]))
        scheduler.advance(by: 19.9)
        XCTAssertNotNil(stack())
        scheduler.advance(by: 0.2)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testMessagesAutoDismissToo() {
        model.fail(.timeout)
        scheduler.advance(by: 20)
        XCTAssertEqual(model.phase, .hidden)
        model.present(response([], lowConfidence: true))
        scheduler.advance(by: 20)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testIdentifyingDoesNotAutoDismiss() {
        model.begin()
        scheduler.advance(by: 120)
        XCTAssertEqual(model.phase, .identifying)
    }

    func testBeginCancelsPendingAutoDismiss() {
        model.present(response([card("a", 0.9)]))
        scheduler.advance(by: 10)
        model.begin()
        scheduler.advance(by: 30)
        XCTAssertEqual(model.phase, .identifying)
    }

    func testUserInteractedRestartsTimer() {
        model.present(response([card("a", 0.9)]))
        scheduler.advance(by: 15)
        model.userInteracted()
        scheduler.advance(by: 15)
        XCTAssertNotNil(stack(), "30 s in, but only 15 s since interaction")
        scheduler.advance(by: 5)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testFlipNextPreviousRestartTimer() {
        model.present(response([card("a", 0.9), card("b", 0.8)]))
        for action in [model.flip, model.next, model.previous] {
            scheduler.advance(by: 15)
            action()
        }
        scheduler.advance(by: 15)
        XCTAssertNotNil(stack())
        scheduler.advance(by: 5)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testDismissCancelsTimerAndLateFireIsHarmless() {
        model.present(response([card("a", 0.9)]))
        model.dismiss()
        XCTAssertEqual(scheduler.pendingCount, 0)
        model.begin()
        scheduler.advance(by: 25)
        XCTAssertEqual(model.phase, .identifying, "old timer must not hide the new identify")
    }

    func testUserInteractedIgnoredWhenHiddenOrIdentifying() {
        model.userInteracted()
        model.begin()
        model.userInteracted()
        XCTAssertEqual(scheduler.pendingCount, 0)
    }

    func testOnlyOneTimerPending() {
        model.present(response([card("a", 0.9)]))
        model.userInteracted(); model.userInteracted()
        XCTAssertEqual(scheduler.pendingCount, 1)
    }

    func testCustomAutoDismissInterval() {
        let custom = PopupModel(scheduler: scheduler, autoDismiss: 5)
        custom.fail(.other)
        scheduler.advance(by: 5)
        XCTAssertEqual(custom.phase, .hidden)
    }
}


final class DossierSectionTitleTests: XCTestCase {
    func testSectionTitleFollowsAsOf() {
        XCTAssertEqual(DossierBackView.sectionTitle(asOf: "start"), "As of start of episode")
        XCTAssertEqual(DossierBackView.sectionTitle(asOf: nil), "Right now")
        XCTAssertEqual(DossierBackView.sectionTitle(asOf: "progress"), "Right now")
    }
}
