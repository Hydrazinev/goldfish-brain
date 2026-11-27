import XCTest
import AppKit
@testable import CineID

final class StatusMenuModelTests: XCTestCase {
    private func state(shows: [(String, String)], current: (String, Int, Int)?) -> EngineState {
        EngineState(setup: .init(tmdbKey: true, anthropicKey: true),
                    current: .init(showId: current?.0, showName: shows.first { $0.0 == current?.0 }?.1,
                                   season: current?.1 ?? 1, episode: current?.2 ?? 1),
                    shows: shows.map { .init(showId: $0.0, showName: $0.1, totalCharacters: nil, posterUrl: nil,
                                             season: nil, episode: nil, current: $0.0 == current?.0) },
                    needsFirstRun: false)
    }

    func testStampFormatting() {
        XCTAssertEqual(StatusMenuModel.stamp(season: 2, episode: 8), "S02E08")
        XCTAssertEqual(StatusMenuModel.stamp(season: 12, episode: 104), "S12E104")
    }

    func testFirstLineForEachEngineStatus() {
        func line(_ s: EngineStatus, message: String? = nil) -> String {
            StatusMenuModel(status: s, supervisorMessage: message, state: nil).firstLine
        }
        XCTAssertEqual(line(.ready), "Ready")
        XCTAssertEqual(line(.starting), "Warming up…")
        XCTAssertEqual(line(.warmingUp(since: Date())), "Warming up…")
        XCTAssertEqual(line(.crashed(restarts: 1)), "Restarting…")
        XCTAssertEqual(line(.failed), "Engine not responding")
        XCTAssertEqual(line(.stopped), "Engine stopped")
        XCTAssertEqual(line(.portInUse, message: "Port busy."), "Port busy.")
    }

    func testChromeNoteReplacesReadyButNeverAnEngineProblem() {
        let note = "More than one Chrome is running — episode detection may be wrong. Quit the extra copy."
        XCTAssertEqual(StatusMenuModel(status: .ready, supervisorMessage: nil, state: nil, chromeNote: note).firstLine, note)
        XCTAssertEqual(StatusMenuModel(status: .failed, supervisorMessage: nil, state: nil, chromeNote: note).firstLine,
                       "Engine not responding")
    }

    func testFirstLineIncludesShowAndStamp() {
        let s = state(shows: [("bb", "Breaking Bad")], current: ("bb", 4, 4))
        XCTAssertEqual(StatusMenuModel(status: .ready, supervisorMessage: nil, state: s).firstLine,
                       "Ready · Breaking Bad · S04E04")
        XCTAssertEqual(StatusMenuModel(status: .starting, supervisorMessage: nil, state: s).firstLine,
                       "Warming up… · Breaking Bad · S04E04")
    }

    func testStartupProblemReplacesTheFirstLine() {
        let m = StatusMenuModel(status: .ready, supervisorMessage: nil, state: nil, problem: "Goldfish Brain can't find its engine.")
        XCTAssertEqual(m.firstLine, "Goldfish Brain can't find its engine.")
    }

    func testItemsWithNoShow() {
        let m = StatusMenuModel(status: .ready, supervisorMessage: nil, state: state(shows: [], current: nil))
        XCTAssertEqual(m.showChoices, [])
        XCTAssertEqual(m.switchShowTitle, "Switch show (none set up yet)")
        XCTAssertEqual(m.topLevelTitles, ["Ready — no show yet", "Identify who's on screen", "Ask a question", "React to this scene", "Open Goldfish Brain…", "Add a Show…", "Switch show (none set up yet)", "Quit Goldfish Brain"])
    }

    func testItemsWithOneShow() {
        let m = StatusMenuModel(status: .ready, supervisorMessage: nil, state: state(shows: [("bb", "Breaking Bad")], current: ("bb", 1, 1)))
        XCTAssertEqual(m.showChoices, [.init(showId: "bb", title: "Breaking Bad", isCurrent: true)])
        XCTAssertEqual(m.switchShowTitle, "Switch show")
        XCTAssertFalse(m.canSwitchShow)
    }

    func testItemsWithSeveralShows() {
        let m = StatusMenuModel(status: .ready, supervisorMessage: nil,
                                state: state(shows: [("bb", "Breaking Bad"), ("sev", "Severance")], current: ("sev", 1, 3)))
        XCTAssertEqual(m.showChoices.map(\.title), ["Breaking Bad", "Severance"])
        XCTAssertEqual(m.showChoices.map(\.isCurrent), [false, true])
        XCTAssertTrue(m.canSwitchShow)
    }

    func testEditMenuHasPasteCutCopySelectAll() throws {
        let menu = EditMenu.makeMainMenu()
        let edit = try XCTUnwrap(menu.items.compactMap(\.submenu).first { $0.title == "Edit" })
        func key(_ title: String) -> (String, Selector?)? {
            edit.items.first { $0.title == title }.map { ($0.keyEquivalent, $0.action) }
        }
        XCTAssertEqual(key("Paste")?.0, "v"); XCTAssertEqual(key("Paste")?.1, #selector(NSText.paste(_:)))
        XCTAssertEqual(key("Copy")?.0, "c"); XCTAssertEqual(key("Cut")?.0, "x"); XCTAssertEqual(key("Select All")?.0, "a")
    }

    func testWebUIURLs() {
        XCTAssertEqual(WebUIWindow.url(route: "").absoluteString, "http://127.0.0.1:8000/ui/")
        XCTAssertEqual(WebUIWindow.url(route: "add").absoluteString, "http://127.0.0.1:8000/ui/#/add")
    }
}
