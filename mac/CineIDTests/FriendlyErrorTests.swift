import XCTest
@testable import CineID

final class FriendlyErrorTests: XCTestCase {
    func testBlackFrame() {
        XCTAssertEqual(FriendlyError.text(.blackFrame),
            "Netflix video is hidden from screenshots.\nIn Chrome: Settings → System → turn off “Use graphics acceleration when available”, then reopen Chrome.")
    }

    func testNoScreenPermission() {
        XCTAssertEqual(FriendlyError.text(.noScreenPermission),
            "Goldfish Brain needs Screen Recording permission. Open System Settings → Privacy & Security → Screen Recording and turn on Goldfish Brain.")
    }

    func testEngineWarmingUp() {
        XCTAssertEqual(FriendlyError.text(.engineWarmingUp),
            "Goldfish Brain is warming up — try again in a few seconds.")
    }

    func testEngineNotResponding() {
        XCTAssertEqual(FriendlyError.text(.engineNotResponding),
            "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain.")
    }

    func testShowNotSetUpWithName() {
        XCTAssertEqual(FriendlyError.text(.showNotSetUp("Severance")),
            "Severance hasn't been set up yet — open Goldfish Brain → Add a Show.")
    }

    func testShowNotSetUpWithoutName() {
        XCTAssertEqual(FriendlyError.text(.showNotSetUp(nil)),
            "This show hasn't been set up yet — open Goldfish Brain → Add a Show.")
    }

    func testShowNotSetUpWithEmptyNameFallsBack() {
        XCTAssertEqual(FriendlyError.text(.showNotSetUp("")),
            "This show hasn't been set up yet — open Goldfish Brain → Add a Show.")
    }

    func testServerError() {
        XCTAssertEqual(FriendlyError.text(.serverError), "Something went wrong on our side. Try again.")
    }

    func testTimeout() {
        XCTAssertEqual(FriendlyError.text(.timeout), "Taking too long — try again.")
    }

    func testOther() {
        XCTAssertEqual(FriendlyError.text(.other), "Something went wrong. Try again.")
    }

    func testNoCaseContainsRawErrorMarkers() {
        let all: [FlowFailure] = [.blackFrame, .noScreenPermission, .engineWarmingUp, .engineNotResponding,
                                  .noChromeWindow, .showNotSetUp(nil), .showNotSetUp("X"), .serverError, .timeout, .other]
        for f in all {
            let t = FriendlyError.text(f)
            for marker in ["Error:", "Exception", "Traceback"] {
                XCTAssertFalse(t.contains(marker), "\(f) contains \(marker)")
            }
        }
    }

    // MARK: EngineError mapping

    func testNotReachableYoungServerIsWarmingUp() {
        XCTAssertEqual(FlowFailure.from(.notReachable, serverAge: 0, showName: nil), .engineWarmingUp)
        XCTAssertEqual(FlowFailure.from(.notReachable, serverAge: 89.9, showName: nil), .engineWarmingUp)
    }

    func testNotReachableOldServerIsNotResponding() {
        XCTAssertEqual(FlowFailure.from(.notReachable, serverAge: 90, showName: nil), .engineNotResponding)
        XCTAssertEqual(FlowFailure.from(.notReachable, serverAge: 600, showName: nil), .engineNotResponding)
    }

    func testTimedOutIsTimeout() {
        XCTAssertEqual(FlowFailure.from(.timedOut, serverAge: 5, showName: nil), .timeout)
        XCTAssertEqual(FlowFailure.from(.timedOut, serverAge: 500, showName: nil), .timeout)
    }

    func test404IsShowNotSetUp() {
        XCTAssertEqual(FlowFailure.from(.http(404), serverAge: 5, showName: "Severance"), .showNotSetUp("Severance"))
        XCTAssertEqual(FlowFailure.from(.http(404), serverAge: 5, showName: nil), .showNotSetUp(nil))
    }

    func test5xxIsServerError() {
        XCTAssertEqual(FlowFailure.from(.http(500), serverAge: 5, showName: nil), .serverError)
        XCTAssertEqual(FlowFailure.from(.http(503), serverAge: 5, showName: nil), .serverError)
    }

    func testOtherHTTPAndDecodingAreOther() {
        XCTAssertEqual(FlowFailure.from(.http(400), serverAge: 5, showName: nil), .other)
        XCTAssertEqual(FlowFailure.from(.http(422), serverAge: 5, showName: nil), .other)
        XCTAssertEqual(FlowFailure.from(.decoding, serverAge: 5, showName: nil), .other)
    }
}
