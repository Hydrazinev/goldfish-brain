import XCTest
import CoreGraphics
@testable import CineID

// MARK: - Fakes

private final class FakeEngine: EngineAPI {
    var stateResult: Result<EngineState, Error> = .success(makeState())
    var identifyHandler: () async throws -> IdentifyResponse = { identifyResponse([card()]) }
    private(set) var identifyCalls = 0
    private(set) var identifyProgress: Double?
    private(set) var identifyArgs: (show: String, season: Int, episode: Int)?
    private(set) var setPositionCalls: [(show: String, season: Int, episode: Int)] = []

    func health() async throws -> Bool { true }
    func state() async throws -> EngineState { try stateResult.get() }
    func identify(jpeg: Data, showId: String, season: Int, episode: Int, progress: Double?) async throws -> IdentifyResponse {
        identifyCalls += 1
        identifyArgs = (showId, season, episode)
        identifyProgress = progress
        return try await identifyHandler()
    }
    func setPosition(showId: String, season: Int, episode: Int) async throws {
        setPositionCalls.append((showId, season, episode))
    }
    func photo(characterId: String) async throws -> Data? { nil }
    var lookupResult: Result<Int?, Error> = .success(nil)
    private(set) var lookupArgs: [(show: String, episode: Int, title: String)] = []
    var lookupDelay: TimeInterval = 0
    func lookupSeason(showId: String, episode: Int, title: String) async throws -> Int? {
        lookupArgs.append((showId, episode, title))
        if lookupDelay > 0 { try await Task.sleep(nanoseconds: UInt64(lookupDelay * 1_000_000_000)) }
        return try lookupResult.get()
    }
    func selectShow(showId: String) async throws {}
}

private final class FakeStatus: StatusProviding {
    var status: EngineStatus = .ready
    var userMessage: String?
}

private final class FakeCapture: ScreenCapturing {
    var permission = true
    private(set) var permissionRequests = 0
    func requestPermission() -> Bool { permissionRequests += 1; return permission }
    var image: CGImage = grayImage(128)
    private(set) var captures = 0
    func hasPermission() -> Bool { permission }
    func capture() async throws -> CGImage { captures += 1; return image }
    func captureChromeWindow() async throws -> CGImage { fatalError("⌘⇧X keeps the whole-display capture") }
}

private struct FakeResolver: EpisodeResolving {
    var result = ResolvedEpisode(season: 4, episode: 4, source: .stored)
    func resolve(frame: CGImage) -> ResolvedEpisode { result }
}

private struct NoScheduler: Scheduler {
    private struct Nothing: Cancellable { func cancel() {} }
    func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable { Nothing() }
}

private func grayImage(_ gray: UInt8) -> CGImage {
    let w = 64, h = 36
    let pixels = [UInt8](repeating: gray, count: w * h * 4)
    let provider = CGDataProvider(data: Data(pixels) as CFData)!
    return CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: w * 4,
                   space: CGColorSpaceCreateDeviceRGB(),
                   bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                   provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
}

private func makeState(show: String? = "breaking-bad", name: String? = "Breaking Bad",
                       season: Int = 4, episode: Int = 4) -> EngineState {
    EngineState(setup: .init(tmdbKey: true, anthropicKey: true),
                current: .init(showId: show, showName: name, season: season, episode: episode),
                shows: [], needsFirstRun: false)
}

private func card(limited: Bool? = nil) -> CharacterCard {
    CharacterCard(characterId: "saul", name: "Saul Goodman", actor: "Bob Odenkirk", role: nil, confidence: 0.9,
                  status: "alive", arcType: nil, firstSeenSeason: 2, firstSeenEpisode: 8, lastSeenSeason: 4,
                  lastSeenEpisode: 3, relationshipSummary: "Lawyer", currentArc: "Laundering", lastSeenDescription: "Office",
                  importanceRank: 1, limited: limited)
}

private func identifyResponse(_ cards: [CharacterCard], faces: Int? = nil, low: Bool? = nil, hidden: Int? = nil) -> IdentifyResponse {
    IdentifyResponse(characters: cards, totalFacesDetected: faces ?? cards.count, showId: "breaking-bad",
                     lowConfidence: low, hiddenBySpoilerShield: hidden)
}

// MARK: - Tests

@MainActor
final class IdentifyFlowTests: XCTestCase {
    private var engine: FakeEngine!
    private var status: FakeStatus!
    private var capture: FakeCapture!
    private var model: PopupModel!
    private var resolver = FakeResolver()
    private var launch = Date(timeIntervalSince1970: 1_000_000)
    private var clock = Date(timeIntervalSince1970: 1_000_000)

    override func setUp() async throws {
        engine = FakeEngine(); status = FakeStatus(); capture = FakeCapture()
        model = PopupModel(scheduler: NoScheduler())
        resolver = FakeResolver()
        launch = Date(timeIntervalSince1970: 1_000_000); clock = launch
    }

    private func makeFlow(timeout: TimeInterval = 5) -> IdentifyFlow {
        IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                     now: { [unowned self] in clock }, serverStartedAt: { [unowned self] in launch }, timeout: timeout)
    }

    private func failure(_ f: FlowFailure, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(model.phase, .message(.failure(f)), file: file, line: line)
    }

    func testProgressIsSentToTheEngine() async {
        resolver.result = ResolvedEpisode(season: 1, episode: 2, source: .chrome, watchId: "77")
        let flow = IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                                timeout: 5, progressProvider: { PlaybackReading(watchId: "77", progress: 0.93) })
        await flow.trigger()
        XCTAssertEqual(engine.identifyProgress, 0.93)
    }

    func testProgressFromAnotherTabIsNotSent() async {
        resolver.result = ResolvedEpisode(season: 1, episode: 2, source: .chrome, watchId: "77")
        let flow = IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                                timeout: 5, progressProvider: { PlaybackReading(watchId: "88", progress: 0.93) })
        await flow.trigger()
        XCTAssertNil(engine.identifyProgress)
    }

    func testProgressWithAFallbackEpisodeIsNotSent() async {
        resolver.result = ResolvedEpisode(season: 1, episode: 2, source: .stored, watchId: "77")
        let flow = IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                                timeout: 5, progressProvider: { PlaybackReading(watchId: "77", progress: 0.93) })
        await flow.trigger()
        XCTAssertNil(engine.identifyProgress)
    }

    func testNoProviderOrUnknownProgressSendsNil() async {
        await makeFlow().trigger()
        XCTAssertNil(engine.identifyProgress)
        let flow = IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                                timeout: 5, progressProvider: { nil })
        await flow.trigger()
        XCTAssertNil(engine.identifyProgress)
    }

    func testSuccessShowsCardsWithTheRealPosition() async {
        resolver.result = ResolvedEpisode(season: 2, episode: 8, source: .chrome)
        await makeFlow().trigger()
        guard case .cards(let stack) = model.phase else { return XCTFail("expected cards, got \(model.phase)") }
        XCTAssertEqual(stack.top.name, "Saul Goodman")
        XCTAssertEqual(model.viewingPosition, ViewingPosition(season: 2, episode: 8))
        XCTAssertEqual(engine.identifyArgs?.show, "breaking-bad")
        XCTAssertEqual(engine.identifyArgs?.season, 2)
        XCTAssertEqual(engine.identifyArgs?.episode, 8)
    }

    func testLimitedCardIsStillPresented() async {
        engine.identifyHandler = { identifyResponse([card(limited: true)]) }
        await makeFlow().trigger()
        guard case .cards(let stack) = model.phase else { return XCTFail("expected cards") }
        XCTAssertEqual(stack.top.limited, true)
    }

    func testHiddenBySpoilerShield() async {
        engine.identifyHandler = { identifyResponse([], faces: 1, hidden: 1) }
        await makeFlow().trigger()
        XCTAssertEqual(model.phase, .message(.hiddenBySpoilerShield))
    }

    func testLowConfidence() async {
        engine.identifyHandler = { identifyResponse([], faces: 2, low: true) }
        await makeFlow().trigger()
        XCTAssertEqual(model.phase, .message(.notRecognised))
    }

    func testZeroFaces() async {
        engine.identifyHandler = { identifyResponse([], faces: 0) }
        await makeFlow().trigger()
        XCTAssertEqual(model.phase, .message(.noFace))
    }

    func testNoScreenPermissionSkipsCapture() async {
        capture.permission = false
        await makeFlow().trigger()
        failure(.noScreenPermission)
        XCTAssertEqual(capture.captures, 0)
        XCTAssertEqual(engine.identifyCalls, 0)
    }

    func testPermissionIsRequestedOncePerRunNotOnEveryPress() async {
        capture.permission = false
        let flow = makeFlow()
        await flow.trigger()
        failure(.noScreenPermission)
        XCTAssertEqual(capture.permissionRequests, 1)
        await flow.trigger()
        failure(.noScreenPermission)
        XCTAssertEqual(capture.permissionRequests, 1, "second press must not prompt again")
    }

    func testPermissionNeverRequestedWhenGranted() async {
        await makeFlow().trigger()
        XCTAssertEqual(capture.permissionRequests, 0)
    }

    func testBlackFrameNeverReachesTheEngine() async {
        capture.image = grayImage(0)
        await makeFlow().trigger()
        failure(.blackFrame)
        XCTAssertEqual(engine.identifyCalls, 0)
    }

    func testEngineWarmingUpSkipsCapture() async {
        status.status = .warmingUp(since: launch)
        await makeFlow().trigger()
        failure(.engineWarmingUp)
        XCTAssertEqual(capture.captures, 0)
    }

    func testGaveUpEngineIsNotResponding() async {
        status.status = .failed
        await makeFlow().trigger()
        failure(.engineNotResponding)
    }

    func testPortInUseShowsTheSupervisorsOwnMessage() async {
        status.status = .portInUse
        status.userMessage = "Port 8000 is taken."
        await makeFlow().trigger()
        XCTAssertEqual(model.phase, .message(.notice("Port 8000 is taken.")))
    }

    func testUnreachableLongAfterLaunchIsNotResponding() async {
        engine.stateResult = .failure(EngineError.notReachable)
        clock = launch.addingTimeInterval(300)
        await makeFlow().trigger()
        failure(.engineNotResponding)
    }

    func testUnreachableRightAfterLaunchIsWarmingUp() async {
        engine.stateResult = .failure(EngineError.notReachable)
        clock = launch.addingTimeInterval(10)
        await makeFlow().trigger()
        failure(.engineWarmingUp)
    }

    func testNotFoundMeansShowNotSetUpAndNamesTheShow() async {
        engine.identifyHandler = { throw EngineError.http(404) }
        await makeFlow().trigger()
        failure(.showNotSetUp("Breaking Bad"))
    }

    func testNoCurrentShowMeansShowNotSetUp() async {
        engine.stateResult = .success(makeState(show: nil, name: nil))
        await makeFlow().trigger()
        failure(.showNotSetUp(nil))
        XCTAssertEqual(engine.identifyCalls, 0)
    }

    func testSecondTriggerWhileRunningIsIgnored() async {
        var releases: [CheckedContinuation<Void, Never>] = []
        engine.identifyHandler = {
            await withCheckedContinuation { releases.append($0) }
            return identifyResponse([card()])
        }
        let flow = makeFlow()
        let first = Task { await flow.trigger() }
        for _ in 0..<200 where releases.isEmpty { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertEqual(releases.count, 1, "first identify never reached the engine")
        XCTAssertEqual(model.phase, .identifying)
        let second = Task { await flow.trigger() }
        try? await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertEqual(engine.identifyCalls, 1, "a second press must not start a second request")
        releases.forEach { $0.resume() }   // (releases a duplicate too, so a failing run ends instead of hanging)
        await first.value
        await second.value
        guard case .cards = model.phase else { return XCTFail("expected cards") }
        // Once finished, a new press works again.
        engine.identifyHandler = { identifyResponse([card()]) }
        let before = engine.identifyCalls
        await flow.trigger()
        XCTAssertEqual(engine.identifyCalls, before + 1)
    }

    func testStoredPositionIsNotWrittenBack() async {
        resolver.result = ResolvedEpisode(season: 4, episode: 4, source: .stored)
        await makeFlow().trigger()
        XCTAssertTrue(engine.setPositionCalls.isEmpty)
    }

    func testDetectedPositionIsSavedBeforeIdentifying() async {
        resolver.result = ResolvedEpisode(season: 2, episode: 8, source: .ocr)
        await makeFlow().trigger()
        XCTAssertEqual(engine.setPositionCalls.count, 1)
        XCTAssertEqual(engine.setPositionCalls.first?.show, "breaking-bad")
        XCTAssertEqual(engine.setPositionCalls.first?.season, 2)
        XCTAssertEqual(engine.setPositionCalls.first?.episode, 8)
    }

    private func unknownSeason() {
        resolver.result = ResolvedEpisode(season: 4, episode: 4, source: .chrome, title: "A Rock", seasonKnown: false)
    }

    func testUnknownSeasonIsLookedUpAndUsed() async {
        unknownSeason(); engine.lookupResult = .success(2)
        await makeFlow().trigger()
        XCTAssertEqual(engine.lookupArgs.first?.title, "A Rock")
        XCTAssertEqual(engine.identifyArgs?.season, 2)
        XCTAssertEqual(engine.setPositionCalls.first?.season, 2)
    }

    func testUnknownSeasonNotFoundKeepsTheStoredSeason() async {
        unknownSeason(); engine.lookupResult = .success(nil)
        await makeFlow().trigger()
        XCTAssertEqual(engine.identifyArgs?.season, 4)
        XCTAssertEqual(engine.identifyArgs?.episode, 4)
        XCTAssertEqual(model.viewingPosition, ViewingPosition(season: 4, episode: 4))
    }

    func testSeasonLookupThrowingNeverFailsTheIdentify() async {
        unknownSeason(); engine.lookupResult = .failure(EngineError.http(500))
        await makeFlow().trigger()
        XCTAssertEqual(engine.identifyArgs?.season, 4)
        XCTAssertEqual(engine.identifyCalls, 1)
    }

    func testSlowSeasonLookupIsAbandonedAfterTheDeadline() async {
        unknownSeason(); engine.lookupDelay = 5
        let flow = IdentifyFlow(engine: engine, supervisor: status, capture: capture, resolver: resolver, model: model,
                                now: { [unowned self] in clock }, serverStartedAt: { [unowned self] in launch },
                                timeout: 5, seasonLookupTimeout: 0.05)
        await flow.trigger()
        XCTAssertEqual(engine.identifyArgs?.season, 4)
    }

    func testKnownSeasonOrNoTitleSkipsTheLookup() async {
        resolver.result = ResolvedEpisode(season: 2, episode: 8, source: .chrome, title: "T", seasonKnown: true)
        await makeFlow().trigger()
        resolver.result = ResolvedEpisode(season: 4, episode: 4, source: .chrome, title: nil, seasonKnown: false)
        await makeFlow().trigger()
        XCTAssertTrue(engine.lookupArgs.isEmpty)
    }

    func testEngineTimeoutEndsTheIdentifyingPopup() async {
        engine.identifyHandler = {
            try await Task.sleep(nanoseconds: 60_000_000_000)
            return identifyResponse([card()])
        }
        await makeFlow(timeout: 0.1).trigger()
        failure(.timeout)
    }

    func testCancellationShowsNoError() async {
        engine.identifyHandler = {
            try await Task.sleep(nanoseconds: 60_000_000_000)
            return identifyResponse([card()])
        }
        let flow = makeFlow()
        let task = Task { await flow.trigger() }
        for _ in 0..<200 where engine.identifyCalls == 0 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) }
        task.cancel()
        await task.value
        XCTAssertEqual(model.phase, .hidden)
    }
}
