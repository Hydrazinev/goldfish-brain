import XCTest
import AppKit
@testable import CineID

/// Regression: in real use the engine returned a full card but the popup window stayed at the size of
/// the small "Identifying..." note (404x160), so the card was squeezed into a tiny strip. This goes
/// through the real `PopupPresenter` `$phase` sink and the real `PopupWindow`, like the app does.
@MainActor
final class PopupWindowResizeTests: XCTestCase {
    private let screen = CGRect(x: 0, y: 0, width: 1512, height: 950)

    private func spin(_ seconds: TimeInterval) {
        RunLoop.current.run(until: Date().addingTimeInterval(seconds))
    }

    private func response(_ json: String) throws -> IdentifyResponse {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(IdentifyResponse.self, from: Data(json.utf8))
    }

    /// A 600x800 photo like the ones the engine serves (not a tiny stand-in).
    private func bigPhoto() -> NSImage {
        let image = NSImage(size: NSSize(width: 600, height: 800))
        image.lockFocus()
        NSColor(red: 0.2, green: 0.45, blue: 0.7, alpha: 1).setFill()
        NSRect(x: 0, y: 0, width: 600, height: 800).fill()
        NSColor(red: 0.85, green: 0.5, blue: 0.2, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 150, y: 300, width: 300, height: 400)).fill()
        image.unlockFocus()
        return image
    }

    private func fullCard() throws -> IdentifyResponse {
        try response("""
        {"characters":[{"character_id":"s-a","name":"Tabitha Matthews","actor":"Catalina Sandino Moreno",
          "confidence":0.62,"current_arc":"Is caught up in the search for missing residents while the forest threatens the town's edges.",
          "last_seen_description":"Joins the search party and warns the group to keep moving before sundown.",
          "relationship_summary":"Husband of Boyd Matthews; mother of Julie Matthews","status":"alive",
          "last_seen_season":1,"last_seen_episode":4}],
         "total_faces_detected":1,"show_id":"from","as_of":"start"}
        """)
    }

    func testWindowGrowsFromTheIdentifyingNoteToAFullCardThroughThePresenter() throws {
        let photo = bigPhoto()
        let model = PopupModel(scheduler: MainQueueScheduler(), autoDismiss: 60)
        let window = PopupWindow(model: model, photo: { _ in photo }, position: nil)
        let presenter = PopupPresenter(model: model, fallbackVisibleFrame: screen, photo: { _ in photo }, window: window)
        defer { model.dismiss(); withExtendedLifetime(presenter) {} }

        model.begin()
        spin(0.6)
        let noteHeight = window.currentFrame.height
        XCTAssertGreaterThan(noteHeight, 100, "the note must have been measured")
        XCTAssertLessThan(noteHeight, 260)

        // A later run-loop turn, as when the engine's answer arrives, with the position set first like IdentifyFlow.
        let arrived = expectation(description: "answer arrives")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            MainActor.assumeIsolated {
                model.viewingPosition = ViewingPosition(season: 1, episode: 4)
                model.present(try! self.fullCard())
                arrived.fulfill()
            }
        }
        wait(for: [arrived], timeout: 3)
        spin(2)

        let frame = window.currentFrame
        XCTAssertGreaterThan(frame.height, noteHeight + 150,
                             "the window must follow the card: note \(noteHeight) pt, now \(frame.height) pt")
        XCTAssertLessThanOrEqual(frame.maxY, screen.maxY)
        XCTAssertGreaterThanOrEqual(frame.minY, screen.minY)
    }

    func testWindowKeepsFollowingWhenTheCardFlips() throws {
        let photo = bigPhoto()
        let model = PopupModel(scheduler: MainQueueScheduler(), autoDismiss: 60)
        let window = PopupWindow(model: model, photo: { _ in photo }, position: nil)
        let presenter = PopupPresenter(model: model, fallbackVisibleFrame: screen, photo: { _ in photo }, window: window)
        defer { model.dismiss(); withExtendedLifetime(presenter) {} }

        model.begin()
        spin(0.3)
        model.present(try fullCard())
        spin(1.5)
        let front = window.currentFrame.height
        model.flip()
        spin(1.5)
        XCTAssertGreaterThan(front, 300)
        XCTAssertGreaterThan(window.currentFrame.height, 300, "back face is sized too")
        XCTAssertLessThanOrEqual(window.currentFrame.maxY, screen.maxY)
    }
}
