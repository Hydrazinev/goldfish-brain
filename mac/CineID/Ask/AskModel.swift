import Foundation
import Combine

struct AskAnswer: Equatable {
    let question: String
    let text: String
    let refused: Bool
    /// "S3E7" when the engine said how far the answer is safe to go.
    let safeUpTo: String?
    /// Set for a Watch Buddy reaction (there is no question then): the card's header.
    var buddyName: String? = nil
}

enum AskPhase: Equatable {
    case hidden
    /// The text field is open; the screenshot is being taken in the background.
    case input
    case thinking(question: String)
    /// ⌘⇧R: the Watch Buddy is looking at the scene.
    case reacting
    case answer(AskAnswer)
    /// A plain-language problem (no permission, engine warming up, privacy box not ticked, ...).
    case message(String)
}

/// What the Ask card shows. The questions asked are remembered in memory only (never written to disk).
@MainActor
final class AskModel: ObservableObject {
    nonisolated static let historyLimit = 5
    nonisolated static let defaultBuddyName = "Watch Buddy"
    nonisolated static let defaultAnswerFade: TimeInterval = 20

    @Published private(set) var phase: AskPhase = .hidden
    /// The text field's content.
    @Published var draft = ""

    private(set) var history: [String] = []
    private var recallIndex: Int?
    private var fade: FadeTimer!

    init(scheduler: Scheduler = MainQueueScheduler(), answerFade: TimeInterval = AskModel.defaultAnswerFade) {
        fade = FadeTimer(scheduler: scheduler, duration: answerFade) { [weak self] in self?.dismiss() }
    }

    func hoverChanged(_ hovering: Bool) { fade.setHovering(hovering) }

    /// ⌘⇧E: open the question field, empty.
    func open() {
        fade.stop()
        draft = ""
        recallIndex = nil
        phase = .input
    }

    /// Enter: returns the question to send, or nil (and stays put) when the field is empty or not open.
    func submit() -> String? {
        guard case .input = phase else { return nil }
        let question = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return nil }
        if history.last != question { history.append(question) }
        if history.count > Self.historyLimit { history.removeFirst(history.count - Self.historyLimit) }
        recallIndex = nil
        phase = .thinking(question: question)
        return question
    }

    /// ⌘⇧R: straight to "Thinking…", no text field.
    func openReacting() {
        fade.stop()
        draft = ""
        recallIndex = nil
        phase = .reacting
    }

    func showReaction(response: AskResponse) {
        phase = .answer(AskAnswer(question: "", text: response.answer, refused: response.refused,
                                  safeUpTo: response.safeUpTo?.short,
                                  buddyName: response.buddyName ?? Self.defaultBuddyName))
        fade.duration = Self.defaultAnswerFade
        fade.start()
    }

    func showAnswer(question: String, response: AskResponse) {
        phase = .answer(AskAnswer(question: question, text: response.answer, refused: response.refused,
                                  safeUpTo: response.safeUpTo?.short))
        fade.duration = Self.defaultAnswerFade
        fade.start()
    }

    func showMessage(_ text: String) {
        phase = .message(text)
        fade.start()
    }

    func dismiss() {
        fade.stop()
        fade.setHovering(false)
        draft = ""
        recallIndex = nil
        phase = .hidden
    }

    /// ↑: the previous question. Never overwrites text the viewer typed themselves.
    func recallOlder() {
        guard case .input = phase, !history.isEmpty else { return }
        if recallIndex == nil && !draft.isEmpty { return }
        let index = max(0, (recallIndex ?? history.count) - 1)
        recallIndex = index
        draft = history[index]
    }

    /// ↓: back toward the newest; past it, an empty field again.
    func recallNewer() {
        guard case .input = phase, let current = recallIndex else { return }
        if current + 1 < history.count {
            recallIndex = current + 1
            draft = history[current + 1]
        } else {
            recallIndex = nil
            draft = ""
        }
    }
}

enum AskHotkeyAction: Equatable {
    case start, dismiss
    /// Same key again closes the card, whatever it is showing; otherwise it opens.
    static func decide(phase: AskPhase) -> AskHotkeyAction { phase == .hidden ? .start : .dismiss }
}
