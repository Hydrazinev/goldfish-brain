import XCTest
@testable import CineID

private struct FakeChrome: ChromeBridge {
    var reply: String?
    var delay: TimeInterval = 0
    func isRunning() -> Bool { true }
    func activeTab() -> (url: String, title: String)? { nil }
    func runJS(_ js: String) -> String? {
        if delay > 0 { Thread.sleep(forTimeInterval: delay) }
        return reply
    }
}

final class PlaybackProgressTests: XCTestCase {
    func testParseAndClamp() {
        XCTAssertEqual(PlaybackProgress.parse("0.42"), 0.42)
        XCTAssertEqual(PlaybackProgress.parse(" 0.9\n"), 0.9)
        XCTAssertEqual(PlaybackProgress.parse("1.0003"), 1)
        XCTAssertEqual(PlaybackProgress.parse("-0.2"), 0)
    }

    func testParseRejectsJunk() {
        for raw in [nil, "", "  ", "jserr|||boom", "NaN", "Infinity", "-Infinity", "abc"] as [String?] {
            XCTAssertNil(PlaybackProgress.parse(raw), "\(String(describing: raw))")
        }
    }

    func testScriptMatchesThePythonOne() {
        XCTAssertTrue(PlaybackProgress.js.contains("document.querySelector('video')"))
        XCTAssertTrue(PlaybackProgress.js.contains("v.currentTime/v.duration"))
    }

    func testReadReturnsParsedValue() async {
        let value = await PlaybackProgress.read(from: FakeChrome(reply: "0.95"))
        XCTAssertEqual(value, 0.95)
        let none = await PlaybackProgress.read(from: FakeChrome(reply: nil))
        XCTAssertNil(none)
    }

    func testSlowChromeGivesUpAfterTheTimeout() async {
        let start = Date()
        let value = await PlaybackProgress.read(from: FakeChrome(reply: "0.95", delay: 1.5), timeout: 0.2)
        XCTAssertNil(value)
        XCTAssertLessThan(Date().timeIntervalSince(start), 1.0)
    }
}

final class ChromeInstanceCheckTests: XCTestCase {
    func testOnlyMoreThanOneCounts() {
        var n = 0
        let check = ChromeInstanceCheck(count: { n })
        XCTAssertFalse(check.hasMultiple())
        n = 1; XCTAssertFalse(check.hasMultiple())
        n = 2; XCTAssertTrue(check.hasMultiple())
        n = 1; XCTAssertFalse(check.hasMultiple())
    }
}
