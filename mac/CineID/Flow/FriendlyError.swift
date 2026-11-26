import Foundation

/// Everything that can go wrong between the hotkey and the popup, reduced to cases that
/// each have one plain-language message. Never carries a raw error, path or traceback.
enum FlowFailure: Equatable {
    case blackFrame
    case noScreenPermission
    case engineWarmingUp
    case engineNotResponding
    case showNotSetUp(String?)
    case serverError
    case timeout
    case other
}

extension FlowFailure {
    /// The engine loads its face model for a while after launch; before this many seconds a
    /// refused connection means "still starting", after it means "stuck".
    static let warmupSeconds: TimeInterval = 90

    /// Maps an engine client error to a user-facing failure case.
    static func from(_ error: EngineError, serverAge: TimeInterval, showName: String?) -> FlowFailure {
        switch error {
        case .notReachable:
            return serverAge < warmupSeconds ? .engineWarmingUp : .engineNotResponding
        case .timedOut:
            return .timeout
        case .http(let code):
            if code == 404 { return .showNotSetUp(showName) }
            if code >= 500 { return .serverError }
            return .other
        case .decoding:
            return .other
        }
    }
}

enum FriendlyError {
    /// Plain-language text; mirrors src/messages.py (the Swift strings are authoritative where they differ).
    static func text(_ failure: FlowFailure) -> String {
        switch failure {
        case .blackFrame:
            return "Netflix video is hidden from screenshots.\nIn Chrome: Settings → System → turn off “Use graphics acceleration when available”, then reopen Chrome."
        case .noScreenPermission:
            return "Goldfish Brain needs Screen Recording permission. Open System Settings → Privacy & Security → Screen Recording and turn on Goldfish Brain."
        case .engineWarmingUp:
            return "Goldfish Brain is warming up — try again in a few seconds."
        case .engineNotResponding:
            return "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain."
        case .showNotSetUp(let name):
            let label: String
            if let name, !name.isEmpty { label = name } else { label = "This show" }
            return "\(label) hasn't been set up yet — open Goldfish Brain → Add a Show."
        case .serverError:
            return "Something went wrong on our side. Try again."
        case .timeout:
            return "Taking too long — try again."
        case .other:
            return "Something went wrong. Try again."
        }
    }
}
