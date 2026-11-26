import XCTest
@testable import CineID

final class ChromeParsersTests: XCTestCase {
    // MARK: parseTab

    func testParseTabSplitsUrlAndTitleOnTheFirstSeparator() {
        let tab = ChromeParsers.parseTab("https://www.netflix.com/watch/70143836|||Breaking Bad|||x")
        XCTAssertEqual(tab?.url, "https://www.netflix.com/watch/70143836")
        XCTAssertEqual(tab?.title, "Breaking Bad|||x")
    }

    func testParseTabAllowsAnEmptyTitleButNotAnEmptyUrl() {
        XCTAssertEqual(ChromeParsers.parseTab("https://a.com|||")?.title, "")
        XCTAssertNil(ChromeParsers.parseTab("|||title"))
    }

    func testParseTabRejectsGarbageAndScriptErrors() {
        XCTAssertNil(ChromeParsers.parseTab(""))
        XCTAssertNil(ChromeParsers.parseTab("no separator here"))
        XCTAssertNil(ChromeParsers.parseTab("err|||Chrome got an error"))
    }

    // MARK: multi-window output

    func testParseTabsReadsOneLinePerWindow() {
        let raw = "https://www.youtube.com/watch?v=1|||Video\nhttps://www.netflix.com/watch/81|||Show\n\n"
        let tabs = ChromeParsers.parseTabs(raw)
        XCTAssertEqual(tabs.map(\.url), ["https://www.youtube.com/watch?v=1", "https://www.netflix.com/watch/81"])
        XCTAssertEqual(tabs.last?.title, "Show")
    }

    func testParseTabsSkipsBadLinesAndHandlesEmptyOutput() {
        XCTAssertTrue(ChromeParsers.parseTabs("").isEmpty)
        XCTAssertEqual(ChromeParsers.parseTabs("junk\nhttps://a.com|||A\r\n").count, 1)
    }

    func testNetflixWatchTabIsFoundInAnyWindowNotJustTheFirst() {
        let raw = "https://mail.google.com/|||Inbox\nhttps://docs.google.com/|||Doc\nhttps://www.netflix.com/watch/9|||Netflix\n"
        XCTAssertEqual(ChromeParsers.netflixWatchTab(in: ChromeParsers.parseTabs(raw))?.url, "https://www.netflix.com/watch/9")
    }

    func testNetflixWatchTabPrefersTheFrontMostWindow() {
        let raw = "https://www.netflix.com/watch/1|||front\nhttps://www.netflix.com/watch/2|||back\n"
        XCTAssertEqual(ChromeParsers.netflixWatchTab(in: ChromeParsers.parseTabs(raw))?.title, "front")
    }

    func testNetflixBrowseOrOtherSitesAreNotWatchTabs() {
        let raw = "https://www.netflix.com/browse|||Home\nhttps://example.com/?u=netflix.com/watch/5|||Sneaky\n"
        XCTAssertNil(ChromeParsers.netflixWatchTab(in: ChromeParsers.parseTabs(raw)))
    }

    // MARK: isNetflixWatch

    func testIsNetflixWatchOnlyForWatchPlusDigits() {
        XCTAssertTrue(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/watch/70143836"))
        XCTAssertTrue(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/watch/70143836?trackId=1&tctx=0"))
        XCTAssertTrue(ChromeParsers.isNetflixWatch(url: "https://netflix.com/watch/1"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/watch/"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/watch/abc"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/browse"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: "https://www.netflix.com/title/70143836"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: "https://example.com/netflix.com/watch/1"))
        XCTAssertFalse(ChromeParsers.isNetflixWatch(url: ""))
    }

    // MARK: parseReactContext

    func testParseReactContextReadsSeasonAndEpisode() {
        XCTAssertEqual(ChromeParsers.parseReactContext("rc:S2E8"), Episode(season: 2, episode: 8))
        XCTAssertEqual(ChromeParsers.parseReactContext("rc:S10E12\n"), Episode(season: 10, episode: 12))
    }

    func testParseReactContextRejectsEverythingElse() {
        for raw in ["rc_models:a,b", "no_rc|nkeys:x", "jserr|||Allow JavaScript from Apple Events", "rc_err:oops",
                    "rc:", "rc:S2", "rc:SxE1", "", "garbage"] {
            XCTAssertNil(ChromeParsers.parseReactContext(raw), raw)
        }
    }

    func testJavaScriptDisabledErrorIsRecognised() {
        XCTAssertTrue(ChromeParsers.isJavaScriptDisabledError("jserr|||Executing JavaScript through AppleScript is turned off. Allow JavaScript from Apple Events"))
        XCTAssertFalse(ChromeParsers.isJavaScriptDisabledError("jserr|||something else"))
        XCTAssertFalse(ChromeParsers.isJavaScriptDisabledError("rc:S1E1"))
    }

    // MARK: parseTitleParts

    func testParseTitlePartsFromSeparatedChildren() {
        XCTAssertEqual(ChromeParsers.parseTitleParts("Breaking Bad|||E2|||Madrigal"), Episode(season: nil, episode: 2))
        XCTAssertEqual(ChromeParsers.parseTitleParts("Breaking Bad|||Season 4|||E11|||Crawl Space"), Episode(season: 4, episode: 11))
    }

    func testParseTitlePartsFromConcatenatedText() {
        XCTAssertEqual(ChromeParsers.parseTitleParts("Breaking BadE2Madrigal"), Episode(season: nil, episode: 2))
    }

    func testParseTitlePartsWithFullSeasonEpisodeText() {
        XCTAssertEqual(ChromeParsers.parseTitleParts("S2:E8"), Episode(season: 2, episode: 8))
        XCTAssertEqual(ChromeParsers.parseTitleParts("Season 2, Episode 8"), Episode(season: 2, episode: 8))
        XCTAssertEqual(ChromeParsers.parseTitleParts("Show|||S03E07|||Name"), Episode(season: 3, episode: 7))
    }

    func testParseTitlePartsRejectsEmptyAndGarbage() {
        for raw in ["", "empty", "Netflix", "Breaking Bad|||Madrigal", "   "] {
            XCTAssertNil(ChromeParsers.parseTitleParts(raw), raw)
        }
    }

    // MARK: parseVideoTitle

    func testVideoTitleWithEpisodeNumberOnly() {
        let t = ChromeParsers.parseVideoTitle("From|||E4|||A Rock and a Farway")
        XCTAssertEqual(t, VideoTitle(season: nil, episode: 4, title: "A Rock and a Farway"))
    }

    func testVideoTitleWithSeasonAndEpisode() {
        let t = ChromeParsers.parseVideoTitle("Show|||S2:E4|||Title")
        XCTAssertEqual(t, VideoTitle(season: 2, episode: 4, title: "Title"))
    }

    func testVideoTitleWithoutEpisodeTitle() {
        XCTAssertEqual(ChromeParsers.parseVideoTitle("Show|||E7"), VideoTitle(season: nil, episode: 7, title: ""))
    }

    func testVideoTitleGarbageIsNil() {
        for bad in ["", "   ", "NO_TITLE_EL", "empty", "Netflix", "From|||Title only", "jserr|||boom", "E|||x"] {
            XCTAssertNil(ChromeParsers.parseVideoTitle(bad), bad)
        }
    }

    // MARK: parsePauseOverlay (shown while paused: "Season 1|||A Rock and a Farway: Ep. 4")

    func testPauseOverlayGivesSeasonEpisodeAndTitle() {
        XCTAssertEqual(ChromeParsers.parsePauseOverlay("Season 1|||A Rock and a Farway: Ep. 4"),
                       VideoTitle(season: 1, episode: 4, title: "A Rock and a Farway"))
    }

    func testPauseOverlayGarbageIsNil() {
        for bad in ["", "NO_OVERLAY", "Season 1|||No episode here", "|||", "Season x|||T: Ep. 2"] {
            XCTAssertNil(ChromeParsers.parsePauseOverlay(bad), bad)
        }
    }
}
