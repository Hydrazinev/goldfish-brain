import Foundation

/// What the menu bar menu says, as plain values (the NSMenu is built from this).
struct StatusMenuModel: Equatable {
    struct ShowChoice: Equatable {
        let showId: String
        let title: String
        let isCurrent: Bool
    }

    var status: EngineStatus
    /// The supervisor's own explanation (used verbatim for "port in use").
    var supervisorMessage: String?
    var state: EngineState?
    /// A problem found at launch (engine not found, hotkey taken): replaces the status line.
    var problem: String? = nil
    /// More than one Chrome is running: shown instead of "Ready…" (never hides a real engine problem).
    var chromeNote: String? = nil

    /// `S02E08`
    static func stamp(season: Int, episode: Int) -> String { ViewingPosition.code(season, episode) }

    var currentShowLine: String? {
        guard let current = state?.current, let name = current.showName, !name.isEmpty else { return nil }
        return "\(name) · \(Self.stamp(season: current.season, episode: current.episode))"
    }

    var firstLine: String {
        if let problem { return problem }
        let base: String
        switch status {
        case .ready:
            if let chromeNote { return chromeNote }
            base = "Ready"
        case .starting, .warmingUp: base = "Warming up…"
        case .crashed: base = "Restarting…"
        case .failed: base = "Engine not responding"
        case .stopped: base = "Engine stopped"
        case .portInUse: return supervisorMessage ?? EngineSupervisor.portInUseMessage
        }
        if let show = currentShowLine { return "\(base) · \(show)" }
        return state != nil && status == .ready ? "Ready — no show yet" : base
    }

    var showChoices: [ShowChoice] {
        (state?.shows ?? []).map { ShowChoice(showId: $0.showId, title: $0.showName, isCurrent: $0.showId == state?.current.showId) }
    }

    var switchShowTitle: String { showChoices.isEmpty ? "Switch show (none set up yet)" : "Switch show" }
    /// Only worth offering when there is a different show to switch to.
    var canSwitchShow: Bool { showChoices.count > 1 }

    /// Titles of the top-level items, in order (the first is the status line).
    static let identifyTitle = "Identify who's on screen"
    static let askTitle = "Ask a question"
    static let reactTitle = "React to this scene"

    var topLevelTitles: [String] { [firstLine, Self.identifyTitle, Self.askTitle, Self.reactTitle, "Open Goldfish Brain…", "Add a Show…", switchShowTitle, "Quit Goldfish Brain"] }
}
