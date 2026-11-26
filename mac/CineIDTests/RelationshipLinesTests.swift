import XCTest
@testable import CineID

/// "Wife of X; sister of Y" in one run-on sentence read badly on the card: one relationship per line.
final class RelationshipLinesTests: XCTestCase {
    func testSplitsOnSemicolonsAndCapitalises() {
        XCTAssertEqual(RelationshipLines.parse("Wife of Walter White; mother of Walter Jr.; sister of Marie Schrader"),
                       ["Wife of Walter White", "Mother of Walter Jr.", "Sister of Marie Schrader"])
    }

    func testAlsoSplitsOnNewlinesAndBullets() {
        XCTAssertEqual(RelationshipLines.parse("• Husband of Skyler\n• Partner of Jesse"),
                       ["Husband of Skyler", "Partner of Jesse"])
    }

    func testDropsEmptiesDuplicatesAndTrailingPeriods() {
        XCTAssertEqual(RelationshipLines.parse(" friend of Mike. ;; Friend of Mike ; ; boss of Saul "),
                       ["Friend of Mike", "Boss of Saul"])
    }

    func testCapsTheListSoTheCardStaysShort() {
        let many = (1...9).map { "Knows person \($0)" }.joined(separator: "; ")
        XCTAssertEqual(RelationshipLines.parse(many).count, 4)
        XCTAssertEqual(RelationshipLines.parse(many, limit: 2).count, 2)
    }

    func testNilAndBlankGiveNothing() {
        XCTAssertEqual(RelationshipLines.parse(nil), [])
        XCTAssertEqual(RelationshipLines.parse("  ; \n "), [])
    }

    func testKeepsAbbreviationsAndCasingInsideTheLine() {
        XCTAssertEqual(RelationshipLines.parse("ally of Dr. Jane Q. Public"), ["Ally of Dr. Jane Q. Public"])
    }
}
