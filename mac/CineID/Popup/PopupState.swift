import Foundation
import Combine

/// What the popup says when there is no card to show.
enum PopupMessage: Equatable {
    /// Faces were found but none matched confidently.
    case notRecognised
    /// No face in the screenshot.
    case noFace
    /// Characters were recognised but withheld to avoid spoilers.
    case hiddenBySpoilerShield
    case failure(FlowFailure)
    /// A ready-made plain-language note (e.g. the supervisor's "port 8000 is in use"), not tied to a FlowFailure.
    case notice(String)
}

enum PopupPhase: Equatable {
    case hidden
    case identifying
    case cards(CardStack)
    case message(PopupMessage)
}

/// A handle to a pending piece of scheduled work.
protocol Cancellable {
    func cancel()
}

/// Delays work. A protocol so tests can fire timers on demand.
/// The work must run on the main thread: `PopupModel` calls it under `MainActor.assumeIsolated`.
protocol Scheduler {
    func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable
}

/// Real scheduler: runs the work on the main queue.
struct MainQueueScheduler: Scheduler {
    private final class Token: Cancellable {
        let item: DispatchWorkItem
        init(_ item: DispatchWorkItem) { self.item = item }
        func cancel() { item.cancel() }
    }

    func schedule(after seconds: TimeInterval, _ work: @escaping () -> Void) -> Cancellable {
        let item = DispatchWorkItem(block: work)
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: item)
        return Token(item)
    }
}

/// What the viewer reported about a card ("Wrong?" / "Missed someone"); sent to the engine's /api/feedback.
struct FeedbackReport: Equatable {
    enum Kind: String { case wrong, missed }
    let showId: String
    /// The card that was wrong; empty for "missed" (nobody to name).
    let characterId: String
    let kind: Kind
    let season: Int
    let episode: Int
}

/// Which of the identify popup's cards are "results" (a second ⌘⇧X closes them) and which are
/// problems (a second ⌘⇧X tries again).
enum IdentifyHotkeyAction: Equatable {
    case ignore, dismiss, start

    static func decide(running: Bool, phase: PopupPhase) -> IdentifyHotkeyAction {
        if running { return .ignore }   // one identify at a time
        switch phase {
        case .hidden, .identifying: return .start
        case .cards, .message(.notRecognised), .message(.noFace), .message(.hiddenBySpoilerShield): return .dismiss
        case .message(.failure), .message(.notice): return .start   // "try again" must not just close the card
        }
    }
}

/// Holds what the popup shows. Views render `phase`; nothing else carries state.
@MainActor
final class PopupModel: ObservableObject {
    @Published private(set) var phase: PopupPhase = .hidden
    /// Where in the show the viewer is for the card on screen (set by the identify flow before presenting).
    @Published var viewingPosition: ViewingPosition?

    /// True for 1.5 s after "Wrong?" / "Missed someone" was pressed ("Thanks — noted").
    @Published private(set) var feedbackThanks = false
    /// Where reports go (set by the app; nil in previews and tests).
    var onFeedback: ((FeedbackReport) -> Void)?

    nonisolated static let defaultAutoDismiss: TimeInterval = 12
    nonisolated static let thanksSeconds: TimeInterval = 1.5

    private let scheduler: Scheduler
    private var fade: FadeTimer!
    private var thanksJob: Cancellable?
    private var currentShowId: String?

    init(scheduler: Scheduler = MainQueueScheduler(), autoDismiss: TimeInterval = PopupModel.defaultAutoDismiss) {
        self.scheduler = scheduler
        fade = FadeTimer(scheduler: scheduler, duration: autoDismiss) { [weak self] in self?.dismiss() }
    }

    /// The pointer entered or left the card: the fade-out waits while it is over the card.
    func hoverChanged(_ hovering: Bool) { fade.setHovering(hovering) }

    /// "Wrong?" (this card is the wrong person) or "Missed someone" (a face got no card).
    func reportFeedback(_ kind: FeedbackReport.Kind) {
        guard case .cards(let stack) = phase, !feedbackThanks, let showId = currentShowId else { return }
        let position = viewingPosition
        onFeedback?(FeedbackReport(showId: showId, characterId: kind == .wrong ? stack.top.characterId : "", kind: kind,
                                   season: position?.season ?? stack.top.lastSeenSeason ?? 0,
                                   episode: position?.episode ?? stack.top.lastSeenEpisode ?? 0))
        feedbackThanks = true
        thanksJob = scheduler.schedule(after: Self.thanksSeconds) { [weak self] in
            MainActor.assumeIsolated { self?.feedbackThanks = false }
        }
        restartTimer()
    }

    /// Hotkey pressed: show "Identifying…" at once, replacing whatever was there.
    func begin() {
        cancelTimer()
        resetFeedback()
        phase = .identifying
    }

    func present(_ response: IdentifyResponse) {
        currentShowId = response.showId
        resetFeedback()
        if let stack = CardStack(cards: response.characters) {
            phase = .cards(stack)
        } else if (response.hiddenBySpoilerShield ?? 0) > 0 {
            // Spoiler protection wins over "not recognised": never imply nobody was found
            // when someone was found and withheld.
            phase = .message(.hiddenBySpoilerShield)
        } else if response.lowConfidence == true {
            phase = .message(.notRecognised)
        } else {
            phase = .message(.noFace)
        }
        restartTimer()
    }

    func fail(_ failure: FlowFailure) {
        phase = .message(.failure(failure))
        restartTimer()
    }

    func notice(_ text: String) {
        phase = .message(.notice(text))
        restartTimer()
    }

    func flip() {
        guard case .cards(var stack) = phase else { return }
        stack.flip()
        phase = .cards(stack)
        restartTimer()
    }

    func next() {
        guard case .cards(var stack) = phase else { return }
        stack.next()
        phase = .cards(stack)
        restartTimer()
    }

    func previous() {
        guard case .cards(var stack) = phase else { return }
        stack.previous()
        phase = .cards(stack)
        restartTimer()
    }

    func dismiss() {
        cancelTimer()
        fade.setHovering(false)
        resetFeedback()
        phase = .hidden
    }

    /// Any hover/click on the popup: keep it open a little longer.
    func userInteracted() {
        switch phase {
        case .cards, .message: restartTimer()
        case .hidden, .identifying: break
        }
    }

    private func restartTimer() { fade.start() }

    private func cancelTimer() { fade.stop() }

    private func resetFeedback() {
        thanksJob?.cancel(); thanksJob = nil
        if feedbackThanks { feedbackThanks = false }
    }
}
