import AppKit
import SwiftUI

/// The popup window. It must show over full-screen Chrome without ever activating Goldfish Brain,
/// so Netflix keeps playing, stays full screen and no Space switch happens.
///
/// - `.nonactivatingPanel` + `.borderless`: showing or clicking it never activates the app.
/// - `canBecomeKey` is true but `becomesKeyOnlyIfNeeded` is on: it only becomes key when the
///   hosted view asks (see `PopupHostingView.needsPanelToBecomeKey`), i.e. on a click. Until
///   then the keyboard stays with Chrome, so Space still pauses Netflix.
/// - `canJoinAllSpaces` + `fullScreenAuxiliary` + `stationary`: visible in full-screen Spaces.
final class PopupPanel: NSPanel {
    /// Esc, once the panel has been clicked and so is key.
    var onEscape: (@MainActor () -> Void)?

    init(contentRect: NSRect) {
        super.init(contentRect: contentRect, styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: true)
        isFloatingPanel = true
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        // Above the menu bar and everything a full-screen app draws.
        level = .popUpMenu
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false          // the card draws its own shadow
        isReleasedWhenClosed = false
        isMovableByWindowBackground = false
        animationBehavior = .none
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) {
        MainActor.assumeIsolated { onEscape?() }
    }
}

/// Hosts the SwiftUI card in the non-activating panel.
final class PopupHostingView<Content: View>: NSHostingView<Content> {
    /// The very first click on the card (when Chrome is the active app) must act on the card,
    /// not be swallowed as a focus click.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    /// A click asks the panel to become key (so Esc works). Without a click it never does.
    override var needsPanelToBecomeKey: Bool { true }

    /// Called whenever SwiftUI's content size changes (e.g. "Identifying…" note -> full card), so the
    /// window can follow. Measuring only a moment after the model changes was too early.
    var onContentSizeChange: (() -> Void)?
    override func invalidateIntrinsicContentSize() {
        super.invalidateIntrinsicContentSize()
        onContentSizeChange?()
    }
}

/// Top-right placement inside `visibleFrame` (which already excludes the menu bar and Dock).
/// The result always lies inside `visibleFrame`, shrinking the size if the screen is too small.
func popupFrame(visibleFrame: CGRect, size: CGSize, margin: CGFloat = 20) -> CGRect {
    let width = min(size.width, visibleFrame.width)
    let height = min(size.height, visibleFrame.height)
    let x = max(visibleFrame.minX, visibleFrame.maxX - width - margin)
    let y = max(visibleFrame.minY, visibleFrame.maxY - height - margin)
    return CGRect(x: x, y: y, width: width, height: height)
}
