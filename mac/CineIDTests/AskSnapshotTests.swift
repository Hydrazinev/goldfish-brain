import XCTest
import SwiftUI
import AppKit
@testable import CineID

@MainActor
final class AskSnapshotTests: XCTestCase {
    private static let outputDir: URL = {
        if let path = ProcessInfo.processInfo.environment["CINEID_SNAPSHOT_DIR"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true).appendingPathComponent("cineid-snapshots")
    }()

    private func render(_ scenario: AskScenario) throws -> (png: Data, size: CGSize) {
        let view = AskCardView(model: scenario.makeModel())
            .frame(minWidth: 420, minHeight: 200)
            .background(DossierTheme.desk)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let cg = try XCTUnwrap(renderer.cgImage, "\(scenario) rendered nothing")
        let png = try XCTUnwrap(NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]))
        return (png, CGSize(width: CGFloat(cg.width) / 2, height: CGFloat(cg.height) / 2))
    }

    func testEveryAskScenarioRendersAndWritesPNG() throws {
        try FileManager.default.createDirectory(at: Self.outputDir, withIntermediateDirectories: true)
        var seen = Set<Data>()
        for scenario in AskScenario.allCases {
            let result = try render(scenario)
            XCTAssertGreaterThanOrEqual(result.size.height, 120, "\(scenario)")
            try result.png.write(to: Self.outputDir.appendingPathComponent("ask-\(scenario.rawValue).png"))
            seen.insert(result.png)
        }
        XCTAssertEqual(seen.count, AskScenario.allCases.count, "every state must look different")
    }

    /// ImageRenderer cannot draw the AppKit text field (it paints a placeholder), so draw the real
    /// view hierarchy of the input state, with a typed question, through AppKit.
    func testInputStateWithTextFieldDrawsThroughAppKit() throws {
        try FileManager.default.createDirectory(at: Self.outputDir, withIntermediateDirectories: true)
        let model = AskScenario.input.makeModel()
        model.draft = "Why does she look so worried?"
        let hosting = NSHostingView(rootView: AskCardView(model: model).background(DossierTheme.desk))
        hosting.frame = NSRect(origin: .zero, size: hosting.fittingSize)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = hosting
        hosting.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        let rep = try XCTUnwrap(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
        hosting.cacheDisplay(in: hosting.bounds, to: rep)
        let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]))
        try png.write(to: Self.outputDir.appendingPathComponent("ask-input-typed.png"))
        XCTAssertGreaterThan(rep.pixelsWide, 300)
    }

    func testThanksStateRendersAndDiffersFromFront() throws {
        func png(_ s: PopupScenario) throws -> Data {
            let view = PopupContentView(model: s.makeModel(), photo: DemoPhotos.image(for:), position: PopupScenario.demoPosition)
                .frame(minWidth: 420, minHeight: 300).background(DossierTheme.desk)
            let r = ImageRenderer(content: view); r.scale = 2
            return try XCTUnwrap(r.cgImage.flatMap { NSBitmapImageRep(cgImage: $0).representation(using: .png, properties: [:]) })
        }
        let thanks = try png(.thanks)
        XCTAssertNotEqual(thanks, try png(.front))
        try thanks.write(to: Self.outputDir.appendingPathComponent("thanks.png"))
    }

    func testScenarioPhases() {
        XCTAssertEqual(AskScenario.input.makeModel().phase, .input)
        XCTAssertEqual(AskScenario.thinking.makeModel().phase, .thinking(question: "Why is she so afraid of him?"))
        if case .answer(let a) = AskScenario.refused.makeModel().phase { XCTAssertTrue(a.refused) } else { XCTFail() }
        XCTAssertEqual(DemoArgs.askScenario(from: ["x", "-demoAsk", "answer"]), .answer)
        XCTAssertNil(DemoArgs.askScenario(from: ["x", "-demoAsk", "nope"]))
    }

    func testSafeCaptionWording() {
        XCTAssertEqual(ViewingPosition(season: 3, episode: 7).safeCaption, "Spoiler-safe up to S3E7")
    }
}

@MainActor
final class AskPanelTests: XCTestCase {
    func testPanelCanTypeWithoutActivatingTheApp() {
        let panel = AskPanel(contentRect: NSRect(x: 0, y: 0, width: 300, height: 100))
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
        XCTAssertTrue(panel.canBecomeKey)
        XCTAssertFalse(panel.canBecomeMain)
        XCTAssertFalse(panel.hidesOnDeactivate)
        XCTAssertGreaterThanOrEqual(panel.level.rawValue, NSWindow.Level.popUpMenu.rawValue, "above full-screen content")
        for flag: NSWindow.CollectionBehavior in [.canJoinAllSpaces, .fullScreenAuxiliary, .transient] {
            XCTAssertTrue(panel.collectionBehavior.contains(flag))
        }
    }

    func testEscapeInThePanelCloses() {
        let panel = AskPanel(contentRect: NSRect(x: 0, y: 0, width: 300, height: 100))
        var closed = false
        panel.onEscape = { closed = true }
        panel.cancelOperation(nil)
        XCTAssertTrue(closed)
    }

    private final class Window: AskWindowing {
        var frame = CGRect(x: 0, y: 0, width: 400, height: 300)
        var currentFrame: CGRect { frame }
        var events: [String] = []
        func show(visibleFrame: CGRect) { events.append("show") }
        func refit(visibleFrame: CGRect) { events.append("refit") }
        func hide() { events.append("hide") }
    }

    func testPresenterShowsRefitsAndHidesWithThePhase() {
        let model = AskModel(scheduler: NeverScheduler())
        let window = Window()
        let presenter = AskPresenter(model: model, window: window, screenProvider: { nil },
                                     fallbackVisibleFrame: CGRect(x: 0, y: 0, width: 1000, height: 800))
        withExtendedLifetime(presenter) {
            model.open(); model.draft = "q"; _ = model.submit()
            model.dismiss()
            XCTAssertEqual(window.events, ["show", "refit", "hide"])
        }
    }
}
