import XCTest
@testable import CineID

final class ChromeWindowPickerTests: XCTestCase {
    private func win(_ id: UInt32, _ bundle: String? = "com.google.Chrome", layer: Int = 0, onScreen: Bool = true,
                     w: CGFloat = 1200, h: CGFloat = 800) -> ChromeWindowPicker.Candidate {
        .init(windowID: id, bundleID: bundle, layer: layer, onScreen: onScreen, frame: CGRect(x: 0, y: 0, width: w, height: h))
    }

    func testPicksTheFrontMostChromeWindowIgnoringOtherApps() {
        let list = [win(1, "com.apple.MobileSMS"), win(2, "com.apple.mail"), win(3), win(4)]
        XCTAssertEqual(ChromeWindowPicker.choose(from: list), 3)
    }

    func testSkipsMenusMinimisedAndTinyChromeWindows() {
        let list = [win(1, layer: 25), win(2, onScreen: false), win(3, w: 100, h: 60), win(4)]
        XCTAssertEqual(ChromeWindowPicker.choose(from: list), 4)
    }

    func testOtherBrowsersAndOurOwnWindowsNeverCount() {
        let list = [win(1, "com.apple.Safari"), win(2, "org.mozilla.firefox"), win(3, "com.google.Chrome.canary"), win(4, nil)]
        XCTAssertNil(ChromeWindowPicker.choose(from: list))
    }

    func testNoWindowsMeansNil() {
        XCTAssertNil(ChromeWindowPicker.choose(from: []))
    }
}
