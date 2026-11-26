import XCTest
import AppKit
import SwiftUI
@testable import CineID

final class PopupPanelTests: XCTestCase {
    // MARK: Panel flags (created, never shown)

    func testPanelIsBorderlessNonActivatingFloating() {
        let panel = PopupPanel(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100))
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel))
        XCTAssertTrue(panel.styleMask.contains(.borderless))
        XCTAssertFalse(panel.styleMask.contains(.titled))
        XCTAssertTrue(panel.isFloatingPanel)
        XCTAssertFalse(panel.hidesOnDeactivate)
        XCTAssertTrue(panel.becomesKeyOnlyIfNeeded)
        XCTAssertTrue(panel.canBecomeKey)
        XCTAssertFalse(panel.canBecomeMain)
    }

    func testPanelSitsAboveFullScreenContent() {
        let panel = PopupPanel(contentRect: .zero)
        XCTAssertGreaterThanOrEqual(panel.level.rawValue, NSWindow.Level.statusBar.rawValue)
        XCTAssertGreaterThan(panel.level.rawValue, NSWindow.Level.floating.rawValue)
    }

    func testPanelJoinsEverySpaceAndFullScreen() {
        let panel = PopupPanel(contentRect: .zero)
        for flag: NSWindow.CollectionBehavior in [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle] {
            XCTAssertTrue(panel.collectionBehavior.contains(flag), "missing \(flag)")
        }
        XCTAssertFalse(panel.collectionBehavior.contains(.moveToActiveSpace))
    }

    func testPanelIsTransparentSoTheCardDrawsItsOwnShadow() {
        let panel = PopupPanel(contentRect: .zero)
        XCTAssertFalse(panel.isOpaque)
        XCTAssertEqual(panel.backgroundColor, NSColor.clear)
        XCTAssertFalse(panel.hasShadow)
    }

    @MainActor
    func testEscapeOnTheKeyPanelRunsTheEscapeHandler() {
        let panel = PopupPanel(contentRect: .zero)
        var escaped = 0
        panel.onEscape = { escaped += 1 }
        panel.cancelOperation(nil)
        XCTAssertEqual(escaped, 1)
    }

    // MARK: Click handling without activation

    @MainActor
    func testHostingViewTakesTheFirstClickAndAsksForKeyOnClick() {
        let view = PopupHostingView(rootView: Color.clear)
        XCTAssertTrue(view.acceptsFirstMouse(for: nil), "first click must flip the card, not just focus the window")
        XCTAssertTrue(view.needsPanelToBecomeKey, "a click makes the panel key so Esc works; no click, no key")
    }

    // MARK: popupFrame

    func testPopupFrameIsTopRightWithMargin() {
        let visible = CGRect(x: 0, y: 0, width: 1440, height: 875)
        let frame = popupFrame(visibleFrame: visible, size: CGSize(width: 376, height: 520))
        XCTAssertEqual(frame, CGRect(x: 1440 - 376 - 20, y: 875 - 520 - 20, width: 376, height: 520))
    }

    func testPopupFrameOnNegativeOriginSecondaryDisplay() {
        let visible = CGRect(x: -1920, y: 200, width: 1920, height: 1055)
        let frame = popupFrame(visibleFrame: visible, size: CGSize(width: 376, height: 520), margin: 10)
        XCTAssertEqual(frame.maxX, -10)
        XCTAssertEqual(frame.maxY, 1255 - 10)
        XCTAssertTrue(visible.contains(frame))
    }

    func testPopupFrameNeverLeavesTinyScreens() {
        let visible = CGRect(x: 100, y: 50, width: 300, height: 400)
        let frame = popupFrame(visibleFrame: visible, size: CGSize(width: 376, height: 520))
        XCTAssertTrue(visible.contains(frame), "\(frame) escapes \(visible)")
        XCTAssertEqual(frame.width, 300)
        XCTAssertEqual(frame.height, 400)
    }

    func testPopupFrameWithMarginLargerThanScreenStillInside() {
        let visible = CGRect(x: 0, y: 0, width: 200, height: 200)
        let frame = popupFrame(visibleFrame: visible, size: CGSize(width: 100, height: 100), margin: 500)
        XCTAssertTrue(visible.contains(frame))
    }
}

// MARK: - Presenter

private final class FakeWindow: PopupWindowing {
    var events: [String] = []
    var lastVisibleFrame: CGRect?
    private(set) var isShown = false

    func show(visibleFrame: CGRect) {
        isShown = true
        lastVisibleFrame = visibleFrame
        events.append("show")
    }
    func refit(visibleFrame: CGRect) { lastVisibleFrame = visibleFrame; events.append("refit") }
    func hide() { isShown = false; events.append("hide") }
}

@MainActor
final class PopupPresenterTests: XCTestCase {
    private func makePresenter(_ model: PopupModel, window: FakeWindow,
                               visible: CGRect = CGRect(x: 0, y: 0, width: 1000, height: 800)) -> PopupPresenter {
        PopupPresenter(model: model, screenProvider: { nil }, fallbackVisibleFrame: visible, window: window)
    }

    func testStaysHiddenForAHiddenModel() {
        let window = FakeWindow()
        let presenter = makePresenter(PopupModel(), window: window)
        withExtendedLifetime(presenter) {}
        XCTAssertFalse(window.isShown)
    }

    func testShowsOnIdentifyingAndHidesOnHidden() {
        let model = PopupModel()
        let window = FakeWindow()
        let presenter = makePresenter(model, window: window)
        withExtendedLifetime(presenter) {
            model.begin()
            XCTAssertTrue(window.isShown)
            model.dismiss()
            XCTAssertFalse(window.isShown)
        }
    }

    func testStaysShownWhileThePhaseChangesBetweenVisibleStates() {
        let model = PopupModel()
        let window = FakeWindow()
        let presenter = makePresenter(model, window: window)
        withExtendedLifetime(presenter) {
            model.begin()
            model.fail(.timeout)
            XCTAssertTrue(window.isShown)
            XCTAssertEqual(window.events, ["show", "refit"], "second visible phase refits, never re-shows")
        }
    }

    func testPicksTheScreenFromTheProviderEachTimeItShows() {
        let model = PopupModel()
        let window = FakeWindow()
        var calls = 0
        let presenter = PopupPresenter(model: model, screenProvider: { calls += 1; return nil },
                                       fallbackVisibleFrame: CGRect(x: 5, y: 6, width: 700, height: 500), window: window)
        withExtendedLifetime(presenter) {
            model.begin(); model.dismiss(); model.begin()
            XCTAssertEqual(calls, 2)
            XCTAssertEqual(window.lastVisibleFrame, CGRect(x: 5, y: 6, width: 700, height: 500))
        }
    }

    func testShowsImmediatelyWhenTheModelIsAlreadyVisibleAtInit() {
        let model = PopupModel()
        model.begin()
        let window = FakeWindow()
        let presenter = makePresenter(model, window: window)
        withExtendedLifetime(presenter) { XCTAssertTrue(window.isShown) }
    }

    func testHostingViewSizesToTheCardContent() {
        let model = PopupScenario.front.makeModel()
        let view = PopupHostingView(rootView: PopupRootView(model: model, photo: { _ in nil }, position: nil))
        view.sizingOptions = [.intrinsicContentSize]
        view.layoutSubtreeIfNeeded()
        let size = view.fittingSize
        XCTAssertGreaterThan(size.width, 250)
        XCTAssertLessThan(size.width, 600)
        XCTAssertGreaterThan(size.height, 250)
        XCTAssertLessThan(size.height, 1200)
    }

    func testCloseButtonActionDismissesTheModel() {
        let model = PopupModel()
        model.begin()
        let root = PopupRootView(model: model, photo: { _ in nil }, position: nil)
        root.close()
        XCTAssertEqual(model.phase, .hidden)
    }
}

@MainActor
final class DemoLauncherTests: XCTestCase {
    private func fakePresenter(_ model: PopupModel) -> PopupPresenter {
        PopupPresenter(model: model, screenProvider: { nil }, fallbackVisibleFrame: CGRect(x: 0, y: 0, width: 800, height: 600),
                       window: FakeWindow())
    }

    func testNeverRunsUnderXCTest() {
        let result = DemoLauncher.runIfRequested(arguments: ["Goldfish Brain", "-demoPopup", "front"],
                                                 environment: ["XCTestConfigurationFilePath": "/tmp/x"],
                                                 makePresenter: fakePresenter)
        XCTAssertNil(result)
    }

    func testDoesNothingWithoutTheFlagOrWithAnUnknownScenario() {
        XCTAssertNil(DemoLauncher.runIfRequested(arguments: ["Goldfish Brain"], environment: [:], makePresenter: fakePresenter))
        XCTAssertNil(DemoLauncher.runIfRequested(arguments: ["Goldfish Brain", "-demoPopup", "nope"], environment: [:],
                                                 makePresenter: fakePresenter))
    }

    func testStartsTheRequestedScenario() {
        var seen: PopupModel?
        let result = DemoLauncher.runIfRequested(arguments: ["Goldfish Brain", "-demoPopup", "stack"], environment: [:]) { model in
            seen = model
            return self.fakePresenter(model)
        }
        XCTAssertNotNil(result)
        guard case .cards(let stack)? = seen?.phase else { return XCTFail("expected cards, got \(String(describing: seen?.phase))") }
        XCTAssertEqual(stack.count, 3)
    }
}
