import XCTest
@testable import CineID

private final class FakeRunner: AppleScriptRunning {
    var scripts: [String] = []
    var replies: [String?] = []
    func run(_ source: String) -> String? {
        scripts.append(source)
        return replies.isEmpty ? nil : replies.removeFirst()
    }
}

final class ChromeBridgeTests: XCTestCase {
    private func bridge(running: Bool, _ runner: FakeRunner) -> AppleScriptChrome {
        AppleScriptChrome(isChromeRunning: { running }, runner: runner)
    }

    // MARK: never launch Chrome

    func testNothingRunsWhenChromeIsNotRunning() {
        let runner = FakeRunner()
        let chrome = bridge(running: false, runner)
        XCTAssertFalse(chrome.isRunning())
        XCTAssertNil(chrome.activeTab())
        XCTAssertNil(chrome.runJS("1+1"))
        XCTAssertTrue(runner.scripts.isEmpty, "a script would launch Chrome")
    }

    func testScriptsGuardOnIsRunningAndNeverLaunchOrActivate() {
        for script in [AppleScriptChrome.tabsScript, AppleScriptChrome.javaScriptScript(for: "1")] {
            XCTAssertTrue(script.contains("if application \"Google Chrome\" is running"))
            let lower = script.lowercased()
            XCTAssertFalse(lower.contains("launch"))
            XCTAssertFalse(lower.contains("activate"))
            XCTAssertFalse(lower.contains("front window"), "must not depend on the front window")
            XCTAssertFalse(lower.contains("set index"))
        }
    }

    func testScriptsLoopOverEveryWindow() {
        XCTAssertTrue(AppleScriptChrome.tabsScript.contains("repeat with w in windows"))
        let js = AppleScriptChrome.javaScriptScript(for: "1")
        XCTAssertTrue(js.contains("repeat with w in windows"))
        XCTAssertTrue(js.contains("netflix.com/watch"))
        XCTAssertTrue(js.contains("execute t javascript"), "JS runs in the matching tab, not the front one")
    }

    // MARK: activeTab

    func testActiveTabFindsTheNetflixTabBehindAnotherWindow() {
        let runner = FakeRunner()
        runner.replies = ["https://docs.google.com/|||Doc\nhttps://www.netflix.com/watch/81|||Netflix\n"]
        let tab = bridge(running: true, runner).activeTab()
        XCTAssertEqual(tab?.url, "https://www.netflix.com/watch/81")
        XCTAssertEqual(runner.scripts.count, 1)
    }

    func testActiveTabIsNilWithoutANetflixWatchTabOrOnScriptFailure() {
        let runner = FakeRunner()
        runner.replies = ["https://docs.google.com/|||Doc\n", nil, ""]
        let chrome = bridge(running: true, runner)
        XCTAssertNil(chrome.activeTab())
        XCTAssertNil(chrome.activeTab())
        XCTAssertNil(chrome.activeTab())
    }

    // MARK: runJS

    func testRunJSReturnsTheScriptResult() {
        let runner = FakeRunner()
        runner.replies = ["rc:S2E8"]
        XCTAssertEqual(bridge(running: true, runner).runJS("return 1"), "rc:S2E8")
        XCTAssertTrue(runner.scripts[0].contains("return 1"))
    }

    func testRunJSTurnsErrorsAndEmptyResultsIntoNil() {
        let runner = FakeRunner()
        runner.replies = ["jserr|||Allow JavaScript from Apple Events", "", nil]
        let chrome = bridge(running: true, runner)
        XCTAssertNil(chrome.runJS("x"))
        XCTAssertNil(chrome.runJS("x"))
        XCTAssertNil(chrome.runJS("x"))
    }

    func testJavaScriptIsEscapedForAppleScriptStrings() {
        let script = AppleScriptChrome.javaScriptScript(for: "var s=\"a\\b\";\nreturn s")
        XCTAssertTrue(script.contains("var s=\\\"a\\\\b\\\";"), script)
        XCTAssertFalse(script.contains("\"a\\b\""))
        XCTAssertEqual(AppleScriptChrome.escapeForAppleScript("a\"b\\c\nd"), "a\\\"b\\\\c d")
    }
}
