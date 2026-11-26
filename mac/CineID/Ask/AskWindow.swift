import AppKit
import Combine
import SwiftUI

/// The Ask card's window. Unlike the identify popup it must take keyboard focus (to type) without
/// activating Goldfish Brain, so Chrome stays the active app, stays full screen and the video keeps playing:
/// `.nonactivatingPanel` + `canBecomeKey`, above full-screen content, on every Space, never activating.
final class AskPanel: NSPanel {
    var onEscape: (@MainActor () -> Void)?

    init(contentRect: NSRect) {
        super.init(contentRect: contentRect, styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: true)
        isFloatingPanel = true
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = false
        level = .popUpMenu
        collectionBehavior = AskPanel.behaviour
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        isReleasedWhenClosed = false
        animationBehavior = .none
    }

    static let behaviour: NSWindow.CollectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) { MainActor.assumeIsolated { onEscape?() } }
}

struct AskRootView: View {
    @ObservedObject var model: AskModel
    var onSubmit: () -> Void
    var onClose: () -> Void

    var body: some View {
        ZStack(alignment: .topTrailing) {
            AskCardView(model: model, onSubmit: onSubmit, onCancel: onClose)
            if model.phase != .hidden {
                Button(action: onClose) {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(Color.black.opacity(0.55)))
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .padding(.top, AskLayout.margin + 8)
                .padding(.trailing, AskLayout.margin + 8)
                .accessibilityLabel("Close")
            }
        }
    }
}

final class AskHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var needsPanelToBecomeKey: Bool { true }
    var onContentSizeChange: (() -> Void)?
    override func invalidateIntrinsicContentSize() {
        super.invalidateIntrinsicContentSize()
        onContentSizeChange?()
    }
}

@MainActor
protocol AskWindowing: AnyObject {
    func show(visibleFrame: CGRect)
    func refit(visibleFrame: CGRect)
    func hide()
    var currentFrame: CGRect { get }
}

@MainActor
final class AskWindow: AskWindowing {
    private let panel: AskPanel
    private let hosting: AskHostingView<AskRootView>
    private var lastVisibleFrame: CGRect?

    init(model: AskModel, onSubmit: @escaping () -> Void, onClose: @escaping () -> Void) {
        panel = AskPanel(contentRect: NSRect(x: 0, y: 0, width: 400, height: 200))
        hosting = AskHostingView(rootView: AskRootView(model: model, onSubmit: onSubmit, onClose: onClose))
        hosting.sizingOptions = [.intrinsicContentSize]
        panel.contentView = hosting
        panel.onEscape = onClose
        hosting.onContentSizeChange = { [weak self] in self?.measureSoon() }
    }

    var currentFrame: CGRect { panel.frame }

    func show(visibleFrame: CGRect) {
        lastVisibleFrame = visibleFrame
        panel.alphaValue = 0          // invisible until placed: no flash at a stale position
        // Never NSApp.activate: that would pull Chrome out of full screen. makeKey on a non-activating
        // panel gives it the keyboard without making Goldfish Brain the active app.
        panel.orderFrontRegardless()
        panel.makeKey()
        measureSoon()
    }

    func refit(visibleFrame: CGRect) {
        lastVisibleFrame = visibleFrame
        measureSoon()
    }

    func hide() {
        lastVisibleFrame = nil
        panel.orderOut(nil)
    }

    private func place(in visibleFrame: CGRect) {
        let size = hosting.fittingSize
        guard size.width > 1, size.height > 1 else { return }
        let target = popupFrame(visibleFrame: visibleFrame, size: size, margin: 0)
        if target != panel.frame { panel.setFrame(target, display: true) }
        if panel.alphaValue < 1 { panel.alphaValue = 1 }
    }

    private func measureSoon() {
        for delay in [0.0, 0.05, 0.25, 0.8] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self, let frame = self.lastVisibleFrame else { return }
                self.place(in: frame)
            }
        }
    }
}

/// Shows the Ask window whenever the model has something to show.
@MainActor
final class AskPresenter {
    private let screenProvider: () -> NSScreen?
    private let fallbackVisibleFrame: CGRect?
    private let window: AskWindowing
    private var visible = false
    private var subscription: AnyCancellable?
    private var watcher: DismissWatcher?

    init(model: AskModel, window: AskWindowing,
         screenProvider: @escaping () -> NSScreen? = { ChromeDisplay.screenShowingChrome() ?? NSScreen.main },
         fallbackVisibleFrame: CGRect? = nil) {
        self.window = window
        self.screenProvider = screenProvider
        self.fallbackVisibleFrame = fallbackVisibleFrame
        subscription = model.$phase.sink { [weak self] phase in
            MainActor.assumeIsolated { self?.apply(phase) }
        }
    }

    /// The card's rectangle (without the transparent margin) in screen coordinates; nil when hidden.
    var cardRect: CGRect? {
        visible ? window.currentFrame.insetBy(dx: AskLayout.margin, dy: AskLayout.margin) : nil
    }

    func attach(_ watcher: DismissWatcher) {
        self.watcher = watcher
        watcher.setActive(visible)
    }

    private func apply(_ phase: AskPhase) {
        if phase == .hidden {
            if visible { visible = false; window.hide() }
            watcher?.setActive(false)
        } else if visible {
            window.refit(visibleFrame: currentVisibleFrame())
        } else {
            visible = true
            window.show(visibleFrame: currentVisibleFrame())
            watcher?.setActive(true)
        }
    }

    private func currentVisibleFrame() -> CGRect {
        screenProvider()?.visibleFrame ?? fallbackVisibleFrame ?? NSScreen.main?.visibleFrame
            ?? CGRect(x: 0, y: 0, width: 1440, height: 900)
    }
}
