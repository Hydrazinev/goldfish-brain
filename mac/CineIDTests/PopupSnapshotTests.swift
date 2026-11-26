import XCTest
import SwiftUI
import AppKit
@testable import CineID

@MainActor
final class PopupSnapshotTests: XCTestCase {
    private static let outputDir: URL = {
        if let path = ProcessInfo.processInfo.environment["CINEID_SNAPSHOT_DIR"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true).appendingPathComponent("cineid-snapshots")
    }()

    /// Renders on the dark desk the real popup floats over, so shadows and rotation are visible.
    private func render(_ scenario: PopupScenario) throws -> (png: Data, size: CGSize)? {
        let model = scenario.makeModel()
        let view = PopupContentView(model: model, photo: DemoPhotos.image(for:), position: PopupScenario.demoPosition)
            .frame(minWidth: 420, minHeight: 300)
            .background(DossierTheme.desk)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let cg = renderer.cgImage else { return nil }
        let rep = NSBitmapImageRep(cgImage: cg)
        let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]))
        return (png, CGSize(width: CGFloat(cg.width) / 2, height: CGFloat(cg.height) / 2))
    }

    func testEveryScenarioRendersAndWritesPNG() throws {
        try FileManager.default.createDirectory(at: Self.outputDir, withIntermediateDirectories: true)
        print("Goldfish Brain snapshots: \(Self.outputDir.path)")
        for scenario in PopupScenario.allCases where scenario != .hidden {
            let result = try XCTUnwrap(render(scenario), "\(scenario) rendered nothing")
            XCTAssertFalse(result.png.isEmpty, "\(scenario) PNG empty")
            XCTAssertGreaterThanOrEqual(result.size.width, 300, "\(scenario) width")
            XCTAssertGreaterThanOrEqual(result.size.height, 200, "\(scenario) height")
            try result.png.write(to: Self.outputDir.appendingPathComponent("\(scenario.rawValue).png"))
        }
    }

    /// The desk-coloured harness frame is still there for `hidden`, but no card may be drawn on it.
    func testHiddenScenarioDrawsNoCard() throws {
        let hidden = try XCTUnwrap(render(.hidden))
        let identifying = try XCTUnwrap(render(.identifying))
        XCTAssertNotEqual(hidden.png, identifying.png)
        XCTAssertTrue(scenarioIsHidden(.hidden))
    }

    private func scenarioIsHidden(_ s: PopupScenario) -> Bool { s.makeModel().phase == .hidden }

    func testFrontAndBackRenderDifferently() throws {
        let front = try XCTUnwrap(render(.front)), back = try XCTUnwrap(render(.back))
        XCTAssertNotEqual(front.png, back.png)
    }

    func testStackShowsDifferentTopCardAfterNext() throws {
        let model = PopupScenario.stack.makeModel()
        let before = renderData(model)
        model.next()
        XCTAssertNotEqual(before, renderData(model))
    }

    private func renderData(_ model: PopupModel) -> Data? {
        let view = PopupContentView(model: model, photo: DemoPhotos.image(for:), position: PopupScenario.demoPosition)
            .frame(minWidth: 420, minHeight: 300)
        let renderer = ImageRenderer(content: view)
        return renderer.cgImage.flatMap { NSBitmapImageRep(cgImage: $0).representation(using: .png, properties: [:]) }
    }

    func testScenarioPhases() {
        func phase(_ s: PopupScenario) -> PopupPhase { s.makeModel().phase }
        if case .cards(let s) = phase(.front) { XCTAssertFalse(s.isFlipped); XCTAssertEqual(s.count, 1) } else { XCTFail("front") }
        if case .cards(let s) = phase(.back) { XCTAssertTrue(s.isFlipped) } else { XCTFail("back") }
        if case .cards(let s) = phase(.stack) { XCTAssertEqual(s.count, 3) } else { XCTFail("stack") }
        if case .cards(let s) = phase(.limited) { XCTAssertEqual(s.top.limited, true) } else { XCTFail("limited") }
        XCTAssertEqual(phase(.hidden), .hidden)
        XCTAssertEqual(phase(.identifying), .identifying)
        XCTAssertEqual(phase(.error), .message(.failure(.blackFrame)))
        XCTAssertEqual(phase(.spoilerShield), .message(.hiddenBySpoilerShield))
    }
}

final class DemoArgsTests: XCTestCase {
    func testParsesEveryDocumentedScenario() {
        for name in ["front", "back", "stack", "limited", "hidden", "identifying", "error"] {
            XCTAssertEqual(DemoArgs.popupScenario(from: ["Goldfish Brain", "-demoPopup", name])?.rawValue, name)
        }
    }

    func testMissingOrUnknownIsNil() {
        XCTAssertNil(DemoArgs.popupScenario(from: ["Goldfish Brain"]))
        XCTAssertNil(DemoArgs.popupScenario(from: ["Goldfish Brain", "-demoPopup"]))
        XCTAssertNil(DemoArgs.popupScenario(from: ["Goldfish Brain", "-demoPopup", "bogus"]))
    }
}
