import XCTest
import CoreGraphics
@testable import CineID

// MARK: client

private final class StubTransport: HTTPTransport {
    var status = 200
    var body = Data()
    var error: Error?
    private(set) var requests: [URLRequest] = []
    func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        requests.append(request)
        if let error { throw error }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        return (body, response)
    }
}

private func askRequest(progress: Double? = nil) -> AskRequest {
    AskRequest(question: "Who is that?", jpeg: Data([1, 2, 3]), showId: "bb", season: 3, episode: 7, progress: progress)
}

final class AskClientTests: XCTestCase {
    func testRequestMatchesTheContract() async throws {
        let transport = StubTransport()
        transport.body = Data(#"{"answer":"Gus.","safe_up_to":"S3E7","refused":false}"#.utf8)
        _ = try await AskClient(transport: transport).ask(askRequest())
        let req = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(req.url?.absoluteString, "http://127.0.0.1:8000/api/ask")
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(req.timeoutInterval, 30)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(req.httpBody)) as? [String: Any])
        XCTAssertEqual(Set(json.keys), ["question", "image_b64", "show_id", "season", "episode", "progress"])
        XCTAssertEqual(json["question"] as? String, "Who is that?")
        XCTAssertEqual(json["image_b64"] as? String, Data([1, 2, 3]).base64EncodedString())
        XCTAssertEqual(json["show_id"] as? String, "bb")
        XCTAssertEqual(json["season"] as? Int, 3)
        XCTAssertEqual(json["episode"] as? Int, 7)
        XCTAssertTrue(json["progress"] is NSNull)
    }

    func testProgressIsSentWhenKnown() throws {
        let req = try AskClient().makeURLRequest(askRequest(progress: 0.25))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(req.httpBody)) as? [String: Any])
        XCTAssertEqual(json["progress"] as? Double, 0.25)
    }

    func testParsesAnAnswer() async throws {
        let transport = StubTransport()
        transport.body = Data(#"{"answer":" He runs the chicken restaurant. ","safe_up_to":"S3E7","refused":false}"#.utf8)
        let response = try await AskClient(transport: transport).ask(askRequest())
        XCTAssertEqual(response, AskResponse(answer: "He runs the chicken restaurant.",
                                             safeUpTo: SafePosition(season: 3, episode: 7), refused: false))
    }

    func testRefusedAndMissingFieldsDefaultSensibly() async throws {
        let transport = StubTransport()
        transport.body = Data(#"{"answer":"Not yet.","refused":true}"#.utf8)
        let response = try await AskClient(transport: transport).ask(askRequest())
        XCTAssertTrue(response.refused)
        XCTAssertNil(response.safeUpTo)
    }

    func testSafeUpToAcceptsSeveralShapes() {
        let expected = SafePosition(season: 3, episode: 7)
        XCTAssertEqual(SafePosition.parse("S3E7"), expected)
        XCTAssertEqual(SafePosition.parse("s03e07"), expected)
        XCTAssertEqual(SafePosition.parse("3x7"), expected)
        XCTAssertEqual(SafePosition.parse(["season": 3, "episode": 7]), expected)
        XCTAssertEqual(SafePosition.parse([3, 7]), expected)
        XCTAssertNil(SafePosition.parse("whenever"))
        XCTAssertNil(SafePosition.parse("S0E0"))
        XCTAssertNil(SafePosition.parse(nil))
        XCTAssertEqual(expected.short, "S3E7")
    }

    func testNeedsDisclosure() async {
        let transport = StubTransport()
        transport.status = 403
        transport.body = Data(#"{"error":"needs_disclosure"}"#.utf8)
        do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail("should throw") }
        catch { XCTAssertEqual(error as? AskError, .needsDisclosure) }
    }

    func testOtherErrorsCarryStatusAndText() async {
        let transport = StubTransport()
        transport.status = 503
        transport.body = Data(#"{"error":"Claude is not set up."}"#.utf8)
        do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail("should throw") }
        catch { XCTAssertEqual(error as? AskError, .http(503, message: "Claude is not set up.")) }
    }

    func testTransportFailuresMap() async {
        let transport = StubTransport()
        transport.error = URLError(.cannotConnectToHost)
        do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail() }
        catch { XCTAssertEqual(error as? AskError, .notReachable) }
        transport.error = URLError(.timedOut)
        do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail() }
        catch { XCTAssertEqual(error as? AskError, .timedOut) }
        transport.error = URLError(.cancelled)
        do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail() }
        catch { XCTAssertTrue(error is CancellationError) }
    }

    func testMalformedOrEmptyAnswerIsADecodingError() async {
        let transport = StubTransport()
        for body in ["not json", #"{"answer":"  "}"#, #"{"nope":1}"#] {
            transport.body = Data(body.utf8)
            do { _ = try await AskClient(transport: transport).ask(askRequest()); XCTFail("should throw") }
            catch { XCTAssertEqual(error as? AskError, .decoding) }
        }
    }

    func testFriendlyTextPointsToWatchBuddySettingsAndNeverShowsCodes() {
        let text = AskFriendlyError.text(.needsDisclosure)
        XCTAssertTrue(text.contains("Settings → Watch Buddy"))
        XCTAssertEqual(AskFriendlyError.text(.http(400, message: "needs_something")), FriendlyError.text(.other),
                       "snake_case codes are not for people")
        XCTAssertEqual(AskFriendlyError.text(.http(409, message: "Add your Claude key first.")), "Add your Claude key first.")
        XCTAssertEqual(AskFriendlyError.text(.http(500, message: "Traceback (most recent call last)")), FriendlyError.text(.serverError))
        XCTAssertEqual(AskFriendlyError.text(.timedOut), FriendlyError.text(.timeout))
        XCTAssertEqual(AskFriendlyError.text(.notReachable, serverAge: 10), FriendlyError.text(.engineWarmingUp))
        XCTAssertEqual(AskFriendlyError.text(.notReachable, serverAge: 500), FriendlyError.text(.engineNotResponding))
    }
}

final class FeedbackClientTests: XCTestCase {
    func testPostsTheContractBody() async throws {
        let transport = StubTransport()
        await FeedbackClient(transport: transport).send(
            FeedbackReport(showId: "bb", characterId: "saul", kind: .wrong, season: 3, episode: 7))
        let req = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(req.url?.absoluteString, "http://127.0.0.1:8000/api/feedback")
        XCTAssertEqual(req.httpMethod, "POST")
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(req.httpBody)) as? [String: Any])
        XCTAssertEqual(json["show_id"] as? String, "bb")
        XCTAssertEqual(json["character_id"] as? String, "saul")
        XCTAssertEqual(json["kind"] as? String, "wrong")
        XCTAssertEqual(json["season"] as? Int, 3)
        XCTAssertEqual(json["episode"] as? Int, 7)
    }

    func testNeverThrowsWhenTheEngineIsDown() async {
        let transport = StubTransport()
        transport.error = URLError(.cannotConnectToHost)
        await FeedbackClient(transport: transport).send(
            FeedbackReport(showId: "bb", characterId: "", kind: .missed, season: 1, episode: 1))
        XCTAssertEqual(transport.requests.count, 1)
    }
}

// MARK: model

private final class FakeScheduler: Scheduler {
    final class Job: Cancellable {
        let fireAt: TimeInterval
        let work: () -> Void
        var cancelled = false, fired = false
        init(fireAt: TimeInterval, work: @escaping () -> Void) { self.fireAt = fireAt; self.work = work }
        func cancel() { cancelled = true }
    }
    private var now: TimeInterval = 0
    private var jobs: [Job] = []
    func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable {
        let job = Job(fireAt: now + after, work: work); jobs.append(job); return job
    }
    func advance(by seconds: TimeInterval) {
        now += seconds
        for job in jobs where !job.cancelled && !job.fired && job.fireAt <= now { job.fired = true; job.work() }
    }
}

@MainActor
final class AskModelTests: XCTestCase {
    private var scheduler: FakeScheduler!
    private var model: AskModel!

    override func setUp() {
        super.setUp()
        scheduler = FakeScheduler()
        model = AskModel(scheduler: scheduler)
    }

    private func ask(_ q: String) {
        model.open(); model.draft = q; _ = model.submit()
    }

    func testEmptyEnterIsIgnored() {
        model.open()
        XCTAssertNil(model.submit())
        model.draft = "   \n "
        XCTAssertNil(model.submit())
        XCTAssertEqual(model.phase, .input)
        XCTAssertTrue(model.history.isEmpty)
    }

    func testSubmitTrimsAndMovesToThinking() {
        model.open()
        model.draft = "  Who is he? "
        XCTAssertEqual(model.submit(), "Who is he?")
        XCTAssertEqual(model.phase, .thinking(question: "Who is he?"))
        XCTAssertNil(model.submit(), "no double send while thinking")
    }

    func testSubmitNeedsAnOpenField() {
        model.draft = "x"
        XCTAssertNil(model.submit())
    }

    func testUpArrowRecallsTheLastQuestionAndDownReturnsToEmpty() {
        ask("one"); ask("two"); ask("three")
        model.open()
        model.recallOlder(); XCTAssertEqual(model.draft, "three")
        model.recallOlder(); XCTAssertEqual(model.draft, "two")
        model.recallOlder(); model.recallOlder(); XCTAssertEqual(model.draft, "one", "stops at the oldest")
        model.recallNewer(); XCTAssertEqual(model.draft, "two")
        model.recallNewer(); model.recallNewer(); XCTAssertEqual(model.draft, "")
    }

    func testRecallNeverOverwritesWhatWasTyped() {
        ask("old")
        model.open()
        model.draft = "typing something"
        model.recallOlder()
        XCTAssertEqual(model.draft, "typing something")
    }

    func testOnlyTheLastFewAreKeptAndRepeatsAreCollapsed() {
        for n in 1...8 { ask("q\(n)") }
        XCTAssertEqual(model.history, ["q4", "q5", "q6", "q7", "q8"])
        ask("q8")
        XCTAssertEqual(model.history.count, 5)
    }

    func testAnswerFadesAfterTwentySecondsUnlessHovered() {
        ask("q")
        model.showAnswer(question: "q", response: AskResponse(answer: "a", safeUpTo: SafePosition(season: 3, episode: 7), refused: false))
        XCTAssertEqual(model.phase, .answer(AskAnswer(question: "q", text: "a", refused: false, safeUpTo: "S3E7")))
        scheduler.advance(by: 19)
        model.hoverChanged(true)
        scheduler.advance(by: 100)
        XCTAssertNotEqual(model.phase, .hidden)
        model.hoverChanged(false)
        scheduler.advance(by: 20.1)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testInputAndThinkingNeverAutoFade() {
        model.open()
        scheduler.advance(by: 500)
        XCTAssertEqual(model.phase, .input)
        model.draft = "q"; _ = model.submit()
        scheduler.advance(by: 500)
        XCTAssertEqual(model.phase, .thinking(question: "q"))
    }

    func testMessagesFadeAndDismissClearsTheDraft() {
        model.showMessage("hello")
        scheduler.advance(by: 20.1)
        XCTAssertEqual(model.phase, .hidden)
        model.open(); model.draft = "abc"; model.dismiss()
        XCTAssertEqual(model.draft, "")
    }

    func testHotkeyDecision() {
        XCTAssertEqual(AskHotkeyAction.decide(phase: .hidden), .start)
        for phase in [AskPhase.input, .thinking(question: "q"), .message("m"),
                      .answer(AskAnswer(question: "q", text: "a", refused: false, safeUpTo: nil))] {
            XCTAssertEqual(AskHotkeyAction.decide(phase: phase), .dismiss)
        }
    }
}

// MARK: flow

private final class FakeEngine: EngineAPI {
    var showId: String? = "bb"
    func health() async throws -> Bool { true }
    func state() async throws -> EngineState {
        EngineState(setup: .init(tmdbKey: true, anthropicKey: true),
                    current: .init(showId: showId, showName: "Breaking Bad", season: 4, episode: 4), shows: [], needsFirstRun: false)
    }
    func identify(jpeg: Data, showId: String, season: Int, episode: Int) async throws -> IdentifyResponse { fatalError() }
    func setPosition(showId: String, season: Int, episode: Int) async throws {}
    func selectShow(showId: String) async throws {}
    func lookupSeason(showId: String, episode: Int, title: String) async throws -> Int? { nil }
    func photo(characterId: String) async throws -> Data? { nil }
}

private final class FakeStatus: StatusProviding {
    var status: EngineStatus = .ready
    var userMessage: String?
}

private final class FakeCapture: ScreenCapturing {
    var permission = true
    var gray: UInt8 = 128
    private(set) var captures = 0
    private(set) var permissionRequests = 0
    func hasPermission() -> Bool { permission }
    func requestPermission() -> Bool { permissionRequests += 1; return permission }
    func capture() async throws -> CGImage {
        captures += 1
        let w = 64, h = 36
        let provider = CGDataProvider(data: Data([UInt8](repeating: gray, count: w * h * 4)) as CFData)!
        return CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: w * 4,
                       space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                       provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
    }
}

private struct FakeResolver: EpisodeResolving {
    func resolve(frame: CGImage) -> ResolvedEpisode { ResolvedEpisode(season: 3, episode: 7, source: .chrome) }
}

private final class FakeAsk: AskAPI {
    var result: Result<AskResponse, Error> = .success(AskResponse(answer: "Gus.", safeUpTo: SafePosition(season: 3, episode: 7), refused: false))
    var delay: TimeInterval = 0
    private(set) var requests: [AskRequest] = []
    func ask(_ request: AskRequest) async throws -> AskResponse {
        requests.append(request)
        if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
        return try result.get()
    }
}

@MainActor
final class AskFlowTests: XCTestCase {
    private var client = FakeAsk()
    private var capture = FakeCapture()
    private var status = FakeStatus()
    private var engine = FakeEngine()
    private var model = AskModel(scheduler: FakeScheduler())
    private lazy var flow = AskFlow(client: client, engine: engine, supervisor: status, capture: capture,
                                    resolver: FakeResolver(), model: model, timeout: 2)

    private func settle(until condition: () -> Bool) async {
        for _ in 0..<200 where !condition() { try? await Task.sleep(nanoseconds: 10_000_000) }
    }

    private func isAnswer() -> Bool { if case .answer = model.phase { return true } else { return false } }
    private func isMessage() -> Bool { if case .message = model.phase { return true } else { return false } }

    func testHotkeyOpensTheFieldAndCapturesInTheBackgroundBeforeAnyQuestion() async {
        flow.hotKeyPressed()
        XCTAssertEqual(model.phase, .input, "the card is up at once")
        await settle { self.capture.captures == 1 }
        XCTAssertEqual(capture.captures, 1)
    }

    func testFullRoundTripSendsQuestionJpegAndDetectedEpisode() async throws {
        flow.hotKeyPressed()
        model.draft = "Who is he?"
        flow.submit()
        XCTAssertEqual(model.phase, .thinking(question: "Who is he?"))
        await settle { self.isAnswer() }
        guard case .answer(let answer) = model.phase else { return XCTFail("got \(model.phase)") }
        XCTAssertEqual(answer.text, "Gus.")
        XCTAssertEqual(answer.safeUpTo, "S3E7")
        let sent = try XCTUnwrap(client.requests.first)
        XCTAssertEqual(sent.question, "Who is he?")
        XCTAssertEqual(sent.showId, "bb")
        XCTAssertEqual([sent.season, sent.episode], [3, 7])
        XCTAssertGreaterThan(sent.jpeg.count, 100)
        XCTAssertEqual(capture.captures, 1, "one screenshot, taken when the card opened")
    }

    func testEmptyEnterSendsNothing() async {
        flow.hotKeyPressed()
        flow.submit()
        try? await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(model.phase, .input)
        XCTAssertTrue(client.requests.isEmpty)
    }

    func testNeedsDisclosureShowsTheSettingsPointer() async {
        client.result = .failure(AskError.needsDisclosure)
        flow.hotKeyPressed(); model.draft = "q"; flow.submit()
        await settle { self.isMessage() }
        XCTAssertEqual(model.phase, .message(AskFriendlyError.needsDisclosure))
    }

    func testBlackFrameIsReportedAsSoonAsItIsKnown() async {
        capture.gray = 0
        flow.hotKeyPressed()
        await settle { self.isMessage() }
        XCTAssertEqual(model.phase, .message(FriendlyError.text(.blackFrame)))
        XCTAssertTrue(client.requests.isEmpty)
    }

    func testNoScreenPermissionAsksOnceAndExplains() {
        capture.permission = false
        flow.hotKeyPressed()
        XCTAssertEqual(model.phase, .message(FriendlyError.text(.noScreenPermission)))
        XCTAssertEqual(capture.permissionRequests, 1)
        flow.dismiss(); flow.hotKeyPressed()
        XCTAssertEqual(capture.permissionRequests, 1)
        XCTAssertEqual(capture.captures, 0)
    }

    func testEngineNotReadyExplainsWithoutCapturing() {
        status.status = .warmingUp(since: Date())
        flow.hotKeyPressed()
        XCTAssertEqual(model.phase, .message(FriendlyError.text(.engineWarmingUp)))
        XCTAssertEqual(capture.captures, 0)
    }

    func testNoShowSetUp() async {
        engine.showId = nil
        flow.hotKeyPressed()
        await settle { self.isMessage() }
        XCTAssertEqual(model.phase, .message(FriendlyError.text(.showNotSetUp(nil))))
    }

    func testHotkeyAgainClosesAndALateAnswerDoesNotReopenTheCard() async {
        client.delay = 0.2
        flow.hotKeyPressed(); model.draft = "q"; flow.submit()
        flow.hotKeyPressed()   // dismiss while thinking
        XCTAssertEqual(model.phase, .hidden)
        try? await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertEqual(model.phase, .hidden)
    }

    func testEngineTimeoutBecomesAPlainMessage() async {
        client.delay = 5
        let quick = AskFlow(client: client, engine: engine, supervisor: status, capture: capture,
                            resolver: FakeResolver(), model: model, timeout: 0.2)
        quick.hotKeyPressed(); model.draft = "q"; quick.submit()
        await settle { self.isMessage() }
        XCTAssertEqual(model.phase, .message(FriendlyError.text(.timeout)))
    }
}
