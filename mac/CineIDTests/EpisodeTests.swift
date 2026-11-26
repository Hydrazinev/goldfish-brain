import XCTest
import CoreGraphics
import CoreText
@testable import CineID

// MARK: Text parser

final class EpisodeTextParserTests: XCTestCase {
    func testParsesTheCommonOnScreenForms() {
        let cases: [(String, Episode)] = [
            ("S2:E8", Episode(season: 2, episode: 8)),
            ("S02E08", Episode(season: 2, episode: 8)),
            ("S2 E8", Episode(season: 2, episode: 8)),
            ("S2.E8", Episode(season: 2, episode: 8)),
            ("S2·E8", Episode(season: 2, episode: 8)),
            ("S2•E8", Episode(season: 2, episode: 8)),
            ("S2xE8", Episode(season: 2, episode: 8)),
            ("S2×E8", Episode(season: 2, episode: 8)),
            ("s4:e11", Episode(season: 4, episode: 11)),
            ("Breaking Bad  S4:E11 Crawl Space", Episode(season: 4, episode: 11)),
            ("Season 2, Episode 8", Episode(season: 2, episode: 8)),
            ("season 12 episode 3", Episode(season: 12, episode: 3)),
        ]
        for (text, expected) in cases {
            XCTAssertEqual(EpisodeTextParser.parse(text), expected, text)
        }
    }

    func testFindsTheEpisodeInMultiLineOCROutput() {
        XCTAssertEqual(EpisodeTextParser.parse("Breaking Bad\nS3:E7 One Minute\n12:04"), Episode(season: 3, episode: 7))
    }

    func testReturnsNilForTextWithoutAnEpisode() {
        for text in ["", "   ", "Netflix", "Mass 3", "Season 2", "Episode 8", "E8", "S:E", "12:04 / 47:00"] {
            XCTAssertNil(EpisodeTextParser.parse(text), text)
        }
    }

    func testHugeNumbersDoNotCrash() {
        XCTAssertNil(EpisodeTextParser.parse("S99999999999999999999E1"))
    }
}

// MARK: OCR (real Vision, on a generated image)

final class VisionOCRTests: XCTestCase {
    func testPixelRectConvertsNormalizedTopLeftRegions() {
        let rect = VisionOCR.pixelRect(for: CGRect(x: 0, y: 0.75, width: 1, height: 0.25), imageSize: CGSize(width: 1000, height: 400))
        XCTAssertEqual(rect, CGRect(x: 0, y: 300, width: 1000, height: 100))
        XCTAssertEqual(VisionOCR.pixelRect(for: CGRect(x: -1, y: -1, width: 5, height: 5), imageSize: CGSize(width: 10, height: 10)),
                       CGRect(x: 0, y: 0, width: 10, height: 10), "clamped to the image")
    }

    func testDefaultRegionsAreTopFifteenAndBottomTwentyFivePercent() {
        XCTAssertEqual(VisionOCR.defaultRegions, [CGRect(x: 0, y: 0, width: 1, height: 0.15),
                                                   CGRect(x: 0, y: 0.75, width: 1, height: 0.25)])
    }

    func testReadsEpisodeTextFromTheBottomBand() throws {
        let image = Self.frame(text: "S2:E8", atBottom: true)
        let text = VisionOCR().readText(in: image, regions: VisionOCR.defaultRegions)
        XCTAssertEqual(EpisodeTextParser.parse(text), Episode(season: 2, episode: 8), "OCR read: \(text)")
    }

    func testIgnoresTextOutsideTheScannedBands() {
        let image = Self.frame(text: "S2:E8", atBottom: false, middle: true)
        let text = VisionOCR().readText(in: image, regions: VisionOCR.defaultRegions)
        XCTAssertNil(EpisodeTextParser.parse(text), "OCR read: \(text)")
    }

    /// Black 1600x900 frame with large white text in the bottom band (or the middle).
    private static func frame(text: String, atBottom: Bool, middle: Bool = false) -> CGImage {
        let w = 1600, h = 900
        let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        ctx.setFillColor(gray: 0, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        let font = CTFontCreateWithName("Helvetica-Bold" as CFString, 72, nil)
        let attributed = NSAttributedString(string: text, attributes: [
            .font: font, .foregroundColor: CGColor(gray: 1, alpha: 1)])
        let line = CTLineCreateWithAttributedString(attributed)
        // CGContext origin is bottom-left: bottom band is low y.
        ctx.textPosition = CGPoint(x: 80, y: middle ? 430 : (atBottom ? 80 : 800))
        CTLineDraw(line, ctx)
        return ctx.makeImage()!
    }
}

// MARK: Resolver

private final class FakeChrome: ChromeBridge {
    var running = true
    var tab: (url: String, title: String)? = ("https://www.netflix.com/watch/1", "Netflix")
    var reactContext: String?
    var titleParts: String?
    var overlay: String?
    var jsCalls: [String] = []

    func isRunning() -> Bool { running }
    func activeTab() -> (url: String, title: String)? { tab }
    func runJS(_ js: String) -> String? {
        jsCalls.append(js)
        if js == EpisodeResolver.reactContextJS { return reactContext }
        if js == EpisodeResolver.videoTitleJS { return titleParts }
        if js == EpisodeResolver.pauseOverlayJS { return overlay }
        return nil
    }
}

private final class FakeOCR: OCRReading {
    var text = ""
    var calls = 0
    func readText(in image: CGImage, regions: [CGRect]) -> String { calls += 1; return text }
}

final class EpisodeResolverTests: XCTestCase {
    private let frame: CGImage = {
        let ctx = CGContext(data: nil, width: 4, height: 4, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        return ctx.makeImage()!
    }()

    private func resolver(_ chrome: FakeChrome, _ ocr: FakeOCR, stored: (Int, Int) = (1, 3)) -> EpisodeResolver {
        EpisodeResolver(chrome: chrome, ocr: ocr, stored: { (season: stored.0, episode: stored.1) })
    }

    func testChromeJavaScriptResultWins() {
        let chrome = FakeChrome(); chrome.reactContext = "rc:S2E8"
        let ocr = FakeOCR(); ocr.text = "S5:E5"
        let result = resolver(chrome, ocr).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 2, episode: 8, source: .chrome))
        XCTAssertEqual(ocr.calls, 0, "no OCR when Chrome answered")
    }

    func testTitleTextIsUsedWhenTheReactContextHasNothing() {
        let chrome = FakeChrome(); chrome.reactContext = "rc_models:a,b"; chrome.titleParts = "Show|||S3:E4|||Name"
        XCTAssertEqual(resolver(chrome, FakeOCR()).resolve(frame: frame), ResolvedEpisode(season: 3, episode: 4, source: .chrome, title: "Name"))
    }

    func testChromeEpisodeWithoutSeasonTakesTheStoredSeason() {
        let chrome = FakeChrome(); chrome.reactContext = "no_rc|nkeys:x"; chrome.titleParts = "Breaking Bad|||E5|||Name"
        let result = resolver(chrome, FakeOCR(), stored: (4, 1)).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 4, episode: 5, source: .chrome, title: "Name", seasonKnown: false))
    }

    func testDomTitleGivesEpisodeAndTitleWithProvisionalSeasonWhenReactContextIsGone() {
        let chrome = FakeChrome(); chrome.reactContext = "no_rc|"; chrome.titleParts = "From|||E4|||A Rock and a Farway"
        let result = resolver(chrome, FakeOCR(), stored: (1, 1)).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 1, episode: 4, source: .chrome, title: "A Rock and a Farway", seasonKnown: false))
    }

    func testPauseOverlayIsUsedWhenThePlayerTitleIsHidden() {
        let chrome = FakeChrome(); chrome.titleParts = "NO_TITLE_EL"; chrome.overlay = "Season 1|||A Rock and a Farway: Ep. 4"
        let result = resolver(chrome, FakeOCR(), stored: (3, 3)).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 1, episode: 4, source: .chrome, title: "A Rock and a Farway", seasonKnown: true))
    }

    func testDomTitleWithSeasonIsKnown() {
        let chrome = FakeChrome(); chrome.titleParts = "Show|||S3:E4|||Name"
        let result = resolver(chrome, FakeOCR()).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 3, episode: 4, source: .chrome, title: "Name", seasonKnown: true))
    }

    func testReactContextResultHasKnownSeasonAndNoTitle() {
        let chrome = FakeChrome(); chrome.reactContext = "rc:S2E8"
        let r = resolver(chrome, FakeOCR()).resolve(frame: frame)
        XCTAssertTrue(r.seasonKnown); XCTAssertNil(r.title)
    }

    func testChromeNotRunningFallsToOCRWithoutAskingChromeAnything() {
        let chrome = FakeChrome(); chrome.running = false
        let ocr = FakeOCR(); ocr.text = "S2:E8"
        XCTAssertEqual(resolver(chrome, ocr).resolve(frame: frame), ResolvedEpisode(season: 2, episode: 8, source: .ocr))
        XCTAssertTrue(chrome.jsCalls.isEmpty)
    }

    func testNoNetflixTabFallsToOCR() {
        let chrome = FakeChrome(); chrome.tab = nil
        let ocr = FakeOCR(); ocr.text = "Season 6, Episode 1"
        XCTAssertEqual(resolver(chrome, ocr).resolve(frame: frame), ResolvedEpisode(season: 6, episode: 1, source: .ocr))
        XCTAssertTrue(chrome.jsCalls.isEmpty)
    }

    func testNonNetflixTabFallsToOCR() {
        let chrome = FakeChrome(); chrome.tab = ("https://www.netflix.com/browse", "Home")
        let ocr = FakeOCR(); ocr.text = "S1:E2"
        XCTAssertEqual(resolver(chrome, ocr).resolve(frame: frame).source, .ocr)
    }

    func testJavaScriptBlockedFallsToOCR() {
        let chrome = FakeChrome()   // runJS returns nil for everything, as when Apple Events JS is off
        let ocr = FakeOCR(); ocr.text = "S2:E8"
        XCTAssertEqual(resolver(chrome, ocr).resolve(frame: frame), ResolvedEpisode(season: 2, episode: 8, source: .ocr))
    }

    func testEmptyOCRFallsToStoredPosition() {
        let result = resolver(FakeChrome(), FakeOCR(), stored: (3, 9)).resolve(frame: frame)
        XCTAssertEqual(result, ResolvedEpisode(season: 3, episode: 9, source: .stored))
    }

    // MARK: sanity bounds

    func testAbsurdChromeValuesAreRejectedAndTheNextSourceIsUsed() {
        for bad in ["rc:S0E0", "rc:S99E999", "rc:S51E1", "rc:S1E501", "rc:S0E5", "rc:S2E0"] {
            let chrome = FakeChrome(); chrome.reactContext = bad
            let ocr = FakeOCR(); ocr.text = "S2:E8"
            XCTAssertEqual(resolver(chrome, ocr).resolve(frame: frame).source, .ocr, bad)
        }
    }

    func testAbsurdOCRValuesAreRejectedAndTheStoredPositionIsUsed() {
        for bad in ["S0E0", "S99E999", "S51:E1", "S1:E501", "Season 99, Episode 1"] {
            let ocr = FakeOCR(); ocr.text = bad
            XCTAssertEqual(resolver(FakeChrome(), ocr, stored: (2, 2)).resolve(frame: frame),
                           ResolvedEpisode(season: 2, episode: 2, source: .stored), bad)
        }
    }

    func testBoundariesAreInclusive() {
        let ocr = FakeOCR(); ocr.text = "S50:E500"
        XCTAssertEqual(resolver(FakeChrome(), ocr).resolve(frame: frame), ResolvedEpisode(season: 50, episode: 500, source: .ocr))
        ocr.text = "S1:E1"
        XCTAssertEqual(resolver(FakeChrome(), ocr).resolve(frame: frame), ResolvedEpisode(season: 1, episode: 1, source: .ocr))
    }

    func testChromeEpisodeOutsideBoundsIsRejectedEvenWithoutSeason() {
        let chrome = FakeChrome(); chrome.titleParts = "Show|||E999|||Name"
        XCTAssertEqual(resolver(chrome, FakeOCR()).resolve(frame: frame).source, .stored)
    }

    func testJavaScriptSourcesLookLikeThePythonOnes() {
        XCTAssertTrue(EpisodeResolver.reactContextJS.contains("reactContext"))
        XCTAssertTrue(EpisodeResolver.videoTitleJS.contains("video-title"))
    }
}
