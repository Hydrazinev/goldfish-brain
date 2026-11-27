import XCTest
@testable import CineID

final class MatchLabelTests: XCTestCase {
    func testPercentageIsLabelledMatchStrengthNotConfidence() {
        XCTAssertEqual(DossierMatchBar.label, "Match strength")
        XCTAssertEqual(DossierMatchBar.accessibilityText(percent: 73), "73 percent match strength")
    }
}
