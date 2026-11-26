import XCTest
@testable import CineID

final class SmokeTests: XCTestCase {
    func testAppModuleLoads() {
        XCTAssertNotNil(Bundle(for: AppDelegate.self).bundleIdentifier)
    }
}
