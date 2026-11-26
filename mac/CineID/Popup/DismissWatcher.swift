import AppKit

/// Esc without Accessibility permission: a Carbon hotkey for Esc, held only while a card is showing.
protocol EscapeKeying: AnyObject {
    /// Returns false when Esc could not be claimed (another app holds it): the card then just lacks Esc.
    func arm(_ handler: @escaping () -> Void) -> Bool
    func disarm()
}

/// Mouse-down anywhere (in any app) while a card is showing. Needs no permission.
protocol ClickMonitoring: AnyObject {
    func start(_ handler: @escaping (CGPoint) -> Void)
    func stop()
}

/// Real Esc: Carbon `RegisterEventHotKey` with no modifiers.
final class CarbonEscape: EscapeKeying {
    private let backend: HotKeyBackend
    private var hotKey: HotKey?
    init(backend: HotKeyBackend = CarbonHotKeyBackend.shared) { self.backend = backend }

    func arm(_ handler: @escaping () -> Void) -> Bool {
        hotKey = nil
        do {
            hotKey = try HotKey(combo: .escape, backend: backend, handler: handler)
            return true
        } catch {
            Diag.log("Esc could not be claimed; the card closes by click or timeout")
            return false
        }
    }

    func disarm() { hotKey = nil }
}

/// Real click monitor: global (clicks in other apps, e.g. Chrome) plus local (clicks in Goldfish Brain's other windows).
final class NSEventClickMonitor: ClickMonitoring {
    private var global: Any?
    private var local: Any?
    private static let mask: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown, .otherMouseDown]

    func start(_ handler: @escaping (CGPoint) -> Void) {
        stop()
        global = NSEvent.addGlobalMonitorForEvents(matching: Self.mask) { _ in handler(NSEvent.mouseLocation) }
        local = NSEvent.addLocalMonitorForEvents(matching: Self.mask) { event in
            handler(NSEvent.mouseLocation)
            return event
        }
    }

    func stop() {
        if let global { NSEvent.removeMonitor(global) }
        if let local { NSEvent.removeMonitor(local) }
        global = nil; local = nil
    }
}

/// While a card is on screen: Esc dismisses, a click outside the card dismisses (clicks inside do not),
/// and the pointer being over the card is reported so the auto-fade can wait.
/// Hover is polled (4-5 times a second) because a non-activating panel gets no reliable enter/exit events.
@MainActor
final class DismissWatcher {
    private let escape: EscapeKeying
    private let clicks: ClickMonitoring
    private let mouseLocation: () -> CGPoint
    private let cardRect: () -> CGRect?
    private let onDismiss: () -> Void
    private let onHover: (Bool) -> Void
    private let pollInterval: TimeInterval?
    private var poller: Timer?
    private var hovering = false
    private(set) var isActive = false

    /// - Parameters:
    ///   - cardRect: the visible card's rectangle in screen coordinates (nil when not on screen).
    ///   - pollInterval: nil disables the hover timer (tests call `tick()`).
    init(escape: EscapeKeying, clicks: ClickMonitoring, mouseLocation: @escaping () -> CGPoint = { NSEvent.mouseLocation },
         cardRect: @escaping () -> CGRect?, pollInterval: TimeInterval? = 0.2,
         onDismiss: @escaping () -> Void, onHover: @escaping (Bool) -> Void) {
        self.escape = escape
        self.clicks = clicks
        self.mouseLocation = mouseLocation
        self.cardRect = cardRect
        self.pollInterval = pollInterval
        self.onDismiss = onDismiss
        self.onHover = onHover
    }

    func setActive(_ active: Bool) {
        guard active != isActive else { return }
        isActive = active
        if active {
            _ = escape.arm { [weak self] in MainActor.assumeIsolated { self?.escapePressed() } }
            clicks.start { [weak self] point in MainActor.assumeIsolated { self?.handleClick(at: point) } }
            if let pollInterval {
                let timer = Timer(timeInterval: pollInterval, repeats: true) { [weak self] _ in
                    MainActor.assumeIsolated { self?.tick() }
                }
                RunLoop.main.add(timer, forMode: .common)
                poller = timer
            }
        } else {
            escape.disarm()
            clicks.stop()
            poller?.invalidate(); poller = nil
            if hovering { hovering = false; onHover(false) }
        }
    }

    func escapePressed() { if isActive { onDismiss() } }

    /// A mouse-down at `point` (screen coordinates): outside the card dismisses; inside never does.
    func handleClick(at point: CGPoint) {
        guard isActive else { return }
        if let rect = cardRect(), rect.contains(point) { return }
        onDismiss()
    }

    func tick() {
        guard isActive else { return }
        let inside = cardRect()?.contains(mouseLocation()) ?? false
        if inside != hovering { hovering = inside; onHover(inside) }
    }
}
