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

    func testParseReadingSplitsIdAndProgressAndSurvivesJunk() {
        XCTAssertEqual(PlaybackProgress.parseReading("81234|0.42"), PlaybackReading(watchId: "81234", progress: 0.42))
        XCTAssertEqual(PlaybackProgress.parseReading("81234|"), PlaybackReading(watchId: "81234", progress: nil))
        XCTAssertEqual(PlaybackProgress.parseReading("|0.5"), PlaybackReading(watchId: nil, progress: 0.5))
        XCTAssertEqual(PlaybackProgress.parseReading("abc|0.5"), PlaybackReading(watchId: nil, progress: 0.5))
        for raw in [nil, "", "|", "jserr|||x", "81|NaN|x", "a|b"] as [String?] {
            let r = PlaybackProgress.parseReading(raw)
            XCTAssertNil(r?.progress, "\(String(describing: raw))")
        }
    }

    func testAttachableNeedsChromeEpisodeAndTheSameWatchId() {
        let chrome = ResolvedEpisode(season: 1, episode: 2, source: .chrome, watchId: "10")
        let reading = PlaybackReading(watchId: "10", progress: 0.9)
        XCTAssertEqual(PlaybackProgress.attachable(reading, to: chrome), 0.9)
        XCTAssertNil(PlaybackProgress.attachable(PlaybackReading(watchId: "11", progress: 0.9), to: chrome), "tab changed")
        XCTAssertNil(PlaybackProgress.attachable(PlaybackReading(watchId: nil, progress: 0.9), to: chrome))
        XCTAssertNil(PlaybackProgress.attachable(nil, to: chrome))
        XCTAssertNil(PlaybackProgress.attachable(reading, to: ResolvedEpisode(season: 1, episode: 2, source: .stored, watchId: "10")), "fallback episode")
        XCTAssertNil(PlaybackProgress.attachable(reading, to: ResolvedEpisode(season: 1, episode: 2, source: .ocr)))
        XCTAssertNil(PlaybackProgress.attachable(reading, to: ResolvedEpisode(season: 1, episode: 2, source: .chrome)), "no id on the episode")
    }

    func testParseRejectsJunk() {
        for raw in [nil, "", "  ", "jserr|||boom", "NaN", "Infinity", "-Infinity", "abc"] as [String?] {
            XCTAssertNil(PlaybackProgress.parse(raw), "\(String(describing: raw))")
        }
    }

    func testScriptMatchesThePythonOne() {
        XCTAssertTrue(PlaybackProgress.js.contains("document.querySelector('video')"))
        XCTAssertTrue(PlaybackProgress.js.contains("location.pathname"))
        XCTAssertTrue(PlaybackProgress.js.contains("v.currentTime/v.duration"))
    }

    func testReadReturnsParsedValue() async {
        let value = await PlaybackProgress.read(from: FakeChrome(reply: "81234|0.95"))
        XCTAssertEqual(value, PlaybackReading(watchId: "81234", progress: 0.95))
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

final class WatchIdTests: XCTestCase {
    func testWatchIdFromUrl() {
        XCTAssertEqual(ChromeParsers.watchId(url: "https://www.netflix.com/watch/81234567?trackId=1"), "81234567")
        XCTAssertNil(ChromeParsers.watchId(url: "https://www.netflix.com/browse"))
    }
}
