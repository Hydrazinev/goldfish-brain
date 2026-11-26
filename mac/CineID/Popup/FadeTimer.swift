import Foundation

/// Auto-dismiss timer that waits while the mouse is over the card: it counts down only while the
/// pointer is away, and starts a full countdown again once the pointer leaves.
@MainActor
final class FadeTimer {
    private let scheduler: Scheduler
    private let onFire: () -> Void
    var duration: TimeInterval
    private var pending: Cancellable?
    /// True between `start()` and `stop()` / firing.
    private(set) var isArmed = false
    private(set) var isHovering = false

    init(scheduler: Scheduler, duration: TimeInterval, onFire: @escaping () -> Void) {
        self.scheduler = scheduler
        self.duration = duration
        self.onFire = onFire
    }

    /// (Re)start a full countdown. While hovering it is held until the pointer leaves.
    func start() {
        pending?.cancel(); pending = nil
        isArmed = true
        if !isHovering { schedule() }
    }

    func stop() {
        isArmed = false
        pending?.cancel(); pending = nil
    }

    func setHovering(_ hovering: Bool) {
        guard hovering != isHovering else { return }
        isHovering = hovering
        if hovering {
            pending?.cancel(); pending = nil
        } else if isArmed {
            schedule()
        }
    }

    private func schedule() {
        pending = scheduler.schedule(after: duration) { [weak self] in
            MainActor.assumeIsolated { self?.fire() }
        }
    }

    private func fire() {
        pending = nil
        guard isArmed else { return }
        isArmed = false
        onFire()
    }
}
