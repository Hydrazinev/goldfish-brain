import XCTest
import CoreGraphics
@testable import CineID

final class ChromeDisplayTests: XCTestCase {
    private func window(owner: String, layer: Int = 0, bounds: CGRect, onscreen: Bool = true, alpha: Double = 1) -> [String: Any] {
        ["kCGWindowOwnerName": owner, "kCGWindowLayer": layer, "kCGWindowIsOnscreen": onscreen,
         "kCGWindowAlpha": alpha,
         "kCGWindowBounds": ["X": bounds.minX, "Y": bounds.minY, "Width": bounds.width, "Height": bounds.height]]
    }

    func testPicksFirstNormalChromeWindowInFrontToBackOrder() {
        let list = [
            window(owner: "Finder", bounds: CGRect(x: 0, y: 0, width: 900, height: 700)),
            window(owner: "Google Chrome", layer: 25, bounds: CGRect(x: 0, y: 0, width: 400, height: 30)),   // menu-like layer
            window(owner: "Google Chrome", bounds: CGRect(x: 10, y: 0, width: 5, height: 5)),                // tiny
            window(owner: "Google Chrome", bounds: CGRect(x: 1920, y: 0, width: 1920, height: 1080)),        // front-most real one
            window(owner: "Google Chrome", bounds: CGRect(x: 0, y: 0, width: 1500, height: 900)),
        ]
        XCTAssertEqual(ChromeDisplay.frontmostChromeWindowBounds(from: list), CGRect(x: 1920, y: 0, width: 1920, height: 1080))
    }

    func testIgnoresOffscreenAndNoChromeLists() {
        XCTAssertNil(ChromeDisplay.frontmostChromeWindowBounds(from: []))
        let list = [window(owner: "Google Chrome", bounds: CGRect(x: 0, y: 0, width: 800, height: 600), onscreen: false)]
        XCTAssertNil(ChromeDisplay.frontmostChromeWindowBounds(from: list))
        XCTAssertNil(ChromeDisplay.frontmostChromeWindowBounds(from: [["garbage": 1]]))
    }

    func testBestDisplayIsTheOneWithTheLargestOverlap() {
        let displays: [(id: CGDirectDisplayID, bounds: CGRect)] = [
            (1, CGRect(x: 0, y: 0, width: 1920, height: 1080)),
            (2, CGRect(x: 1920, y: 0, width: 1920, height: 1080)),
        ]
        // Window straddles both but mostly on display 2.
        let straddling = CGRect(x: 1700, y: 100, width: 1000, height: 600)
        XCTAssertEqual(ChromeDisplay.bestDisplay(for: straddling, in: displays), 2)
        XCTAssertEqual(ChromeDisplay.bestDisplay(for: CGRect(x: 50, y: 50, width: 400, height: 300), in: displays), 1)
    }

    func testBestDisplayIsNilWhenNothingOverlaps() {
        let displays: [(id: CGDirectDisplayID, bounds: CGRect)] = [(1, CGRect(x: 0, y: 0, width: 100, height: 100))]
        XCTAssertNil(ChromeDisplay.bestDisplay(for: CGRect(x: 500, y: 500, width: 10, height: 10), in: displays))
        XCTAssertNil(ChromeDisplay.bestDisplay(for: .zero, in: []))
    }
}
