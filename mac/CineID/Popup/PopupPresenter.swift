import AppKit
import Combine
import SwiftUI

/// What the presenter needs from the popup window. A protocol so tests don't open real windows.
protocol PopupWindowing: AnyObject {
    /// Bring the popup up top-right inside `visibleFrame`, without activating the app.
    func show(visibleFrame: CGRect)
    /// Content changed while visible: resize and re-anchor.
    func refit(visibleFrame: CGRect)
    func hide()
    /// The panel's frame in screen coordinates (zero when never shown).
    var currentFrame: CGRect { get }
}

extension PopupWindowing {
    var currentFrame: CGRect { .zero }
}

/// The SwiftUI root inside the panel: the card plus a small ✕.
struct PopupRootView: View {
    @ObservedObject var model: PopupModel
    let photo: (String) -> NSImage?
    let position: ViewingPosition?

    var body: some View {
        ZStack(alignment: .topTrailing) {
            PopupContentView(model: model, photo: photo, position: model.viewingPosition ?? position)
            if model.phase != .hidden {
                Button(action: close) {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(Color.black.opacity(0.55)))
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                // Sits just inside the card's top-right corner (the card has a 32 pt margin).
                .padding(.top, 32 + 8)
                .padding(.trailing, 32 + 8)
                .accessibilityLabel("Close")
            }
        }
    }

    func close() { model.dismiss() }
}

/// The real window: a `PopupPanel` hosting `PopupRootView`.
@MainActor
final class PopupWindow: PopupWindowing {
    private let panel: PopupPanel
    private let hosting: PopupHostingView<PopupRootView>
    private var lastVisibleFrame: CGRect?

    init(model: PopupModel, photo: @escaping (String) -> NSImage?, position: ViewingPosition?) {
        panel = PopupPanel(contentRect: NSRect(x: 0, y: 0, width: 400, height: 560))
        hosting = PopupHostingView(rootView: PopupRootView(model: model, photo: photo, position: position))
        // The panel is sized by us (from the content), not by the hosting view.
        hosting.sizingOptions = [.intrinsicContentSize]
        panel.contentView = hosting
        panel.onEscape = { [weak model] in model?.dismiss() }
        // Follow the card whenever SwiftUI reports a new size (after it has rendered the new phase).
        hosting.onContentSizeChange = { [weak self] in self?.measureSoon() }
    }

    func show(visibleFrame: CGRect) {
        lastVisibleFrame = visibleFrame
        // Never makeKey / NSApp.activate: that would pull Chrome out of full screen or switch Space.
        // Invisible until the first placement, so the card never flashes at a stale position or size.
        panel.alphaValue = 0
        panel.orderFrontRegardless()
        measureSoon()
    }

    func refit(visibleFrame: CGRect) {
        lastVisibleFrame = visibleFrame
        measureSoon()
    }

    /// The panel's current frame (tests check that it follows the card's size).
    var currentFrame: CGRect { panel.frame }

    func hide() {
        lastVisibleFrame = nil
        panel.orderOut(nil)
    }

    private func place(in visibleFrame: CGRect) {
        let size = hosting.fittingSize
        guard size.width > 1, size.height > 1 else { return }
        // The SwiftUI content already carries a 32 pt transparent margin, so no extra window margin.
        let target = popupFrame(visibleFrame: visibleFrame, size: size, margin: 0)
        if panel.alphaValue < 1 { panel.alphaValue = 1 }
        guard target != panel.frame else { return }   // no change: avoids a resize/invalidate loop
        panel.setFrame(target, display: true)
    }

    /// Never measure synchronously: the presenter is told about a change from `$phase`'s willSet, i.e.
    /// before the model holds the new value. Forcing a layout pass there makes SwiftUI render the OLD
    /// phase and swallow the update, so the window stayed on "Identifying..." (160 pt) under a full card.
    /// Instead measure on later run-loop turns, after SwiftUI has rendered; `onContentSizeChange` does the
    /// precise follow-up, the short delays are only a safety net for slow first renders (photo decode).
    private func measureSoon() {
        for delay in [0.0, 0.05, 0.25, 0.8] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self, let frame = self.lastVisibleFrame else { return }
                self.place(in: frame)
            }
        }
    }
}

/// Shows the popup panel whenever the model has something to show, hides it when it doesn't.
@MainActor
final class PopupPresenter {
    private let screenProvider: () -> NSScreen?
    private let fallbackVisibleFrame: CGRect?
    private let window: PopupWindowing
    private var visible = false
    private var subscription: AnyCancellable?
    private var watcher: DismissWatcher?

    /// The visible card's rectangle (without the transparent margin the tilt and shadow need), in screen
    /// coordinates; nil while hidden. What counts as "inside the card" for clicks and hover.
    var cardRect: CGRect? {
        visible ? window.currentFrame.insetBy(dx: PopupPresenter.cardMargin, dy: PopupPresenter.cardMargin) : nil
    }
    static let cardMargin: CGFloat = 32

    /// Esc / click-outside / hover handling while the card is visible.
    func attach(_ watcher: DismissWatcher) {
        self.watcher = watcher
        watcher.setActive(visible)
    }

    /// - Parameters:
    ///   - screenProvider: the display to use each time the popup appears (default: the one showing Chrome).
    ///   - fallbackVisibleFrame: used when `screenProvider` gives nothing (default: the main screen).
    init(model: PopupModel,
         screenProvider: @escaping () -> NSScreen? = { ChromeDisplay.screenShowingChrome() ?? NSScreen.main },
         fallbackVisibleFrame: CGRect? = nil,
         photo: @escaping (String) -> NSImage? = { _ in nil },
         position: ViewingPosition? = nil,
         window: PopupWindowing? = nil) {
        self.screenProvider = screenProvider
        self.fallbackVisibleFrame = fallbackVisibleFrame
        self.window = window ?? PopupWindow(model: model, photo: photo, position: position)
        // `$phase` publishes before the property changes, so use the value it delivers.
        subscription = model.$phase.sink { [weak self] phase in
            MainActor.assumeIsolated { self?.apply(phase) }
        }
    }

    func show() {
        if !visible { visible = true; window.show(visibleFrame: currentVisibleFrame()); watcher?.setActive(true) }
    }
    func hide() {
        if visible { visible = false; window.hide() }
        watcher?.setActive(false)
    }

    private func apply(_ phase: PopupPhase) {
        if phase == .hidden {
            hide()
        } else if visible {
            window.refit(visibleFrame: currentVisibleFrame())
        } else {
            show()
        }
    }

    private func currentVisibleFrame() -> CGRect {
        screenProvider()?.visibleFrame
            ?? fallbackVisibleFrame
            ?? NSScreen.main?.visibleFrame
            ?? CGRect(x: 0, y: 0, width: 1440, height: 900)
    }
}

/// `-demoPopup <scenario>`: shows that canned state in the real panel (manual checks only).
@MainActor
enum DemoLauncher {
    private static var retained: [AnyObject] = []

    /// Returns the presenter when a demo was started. Never runs under XCTest.
    @discardableResult
    static func runIfRequested(
        arguments: [String] = CommandLine.arguments,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        makePresenter: @MainActor (PopupModel) -> PopupPresenter = { model in
            PopupPresenter(model: model, photo: { DemoPhotos.image(for: $0) }, position: PopupScenario.demoPosition)
        }
    ) -> PopupPresenter? {
        guard environment["XCTestConfigurationFilePath"] == nil else { return nil }
        if let ask = DemoArgs.askScenario(from: arguments) {
            let model = ask.makeModel()
            let window = AskWindow(model: model, onSubmit: {}, onClose: { [weak model] in model?.dismiss() })
            let presenter = AskPresenter(model: model, window: window)
            retained = [model, presenter, window]
            Diag.log("demo ask: \(ask.rawValue)")
            return nil
        }
        guard let scenario = DemoArgs.popupScenario(from: arguments) else { return nil }
        let model = scenario.makeModel()
        let presenter = makePresenter(model)
        retained = [model, presenter]
        Diag.log("demo popup: \(scenario.rawValue)")
        return presenter
    }
}
