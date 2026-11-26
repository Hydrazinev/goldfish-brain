import AppKit
import XCTest
@testable import CineID

/// Goldfish Brain must be quittable like any Mac app (privacy: a quit app must not keep listening for ⌘⇧X).
@MainActor
final class AppMenuTests: XCTestCase {
    func testAppMenuHasQuitWithCommandQ() throws {
        let menu = EditMenu.makeMainMenu()
        let appMenu = try XCTUnwrap(menu.items.first?.submenu, "first main-menu item must be the app menu")
        let quit = try XCTUnwrap(appMenu.items.first { $0.title == "Quit Goldfish Brain" }, "app menu needs 'Quit Goldfish Brain'")
        XCTAssertEqual(quit.keyEquivalent, "q")
        XCTAssertTrue(quit.keyEquivalentModifierMask.contains(.command))
        XCTAssertEqual(quit.action, #selector(NSApplication.terminate(_:)))
        XCTAssertNil(quit.target, "goes to NSApp through the responder chain")
    }

    func testAppMenuHasHide() throws {
        let appMenu = try XCTUnwrap(EditMenu.makeMainMenu().items.first?.submenu)
        XCTAssertNotNil(appMenu.items.first { $0.title == "Hide Goldfish Brain" })
    }

    func testEditMenuStillThere() throws {
        let titles = EditMenu.makeMainMenu().items.compactMap { $0.submenu?.title }
        XCTAssertTrue(titles.contains("Edit"))
    }

    func testAppIsARegularDockApp() throws {
        let info = Bundle(for: AppDelegate.self).infoDictionary
        XCTAssertNotEqual(info?["LSUIElement"] as? Bool, true, "a menu-bar-only app has no Dock icon and no way to quit")
    }
}
