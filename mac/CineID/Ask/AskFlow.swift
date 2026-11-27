import CoreGraphics
import Foundation

/// ⌘⇧E: open the question card at once, take the screenshot and work out the episode in the background
/// while the viewer types, then send question + screenshot to the engine on Enter.
/// The question and the image are never logged.
@MainActor
final class AskFlow {
    /// Everything captured while the viewer was typing.
    struct Prepared {
        let jpeg: Data
        let showId: String
        let season: Int
        let episode: Int
        /// Read at the same moment as the screenshot; nil unless it provably belongs to this episode.
        let progress: Double?
    }

    private enum Prep {
        case ready(Prepared)
        case failed(String)
    }

    private let client: AskAPI
    private let engine: EngineAPI
    private let supervisor: StatusProviding
    private let capture: ScreenCapturing
    private let resolver: EpisodeResolving
    private let model: AskModel
    private let storedPosition: StoredPosition
    private let timeout: TimeInterval
    private let seasonLookupTimeout: TimeInterval
    private let serverStartedAt: () -> Date
    private let progressProvider: (() async -> PlaybackReading?)?
    private var prep: Task<Prep, Never>?
    private var asking: Task<Void, Never>?
    private var generation = 0
    private var requestedPermission = false

    init(client: AskAPI, engine: EngineAPI, supervisor: StatusProviding, capture: ScreenCapturing,
         resolver: EpisodeResolving, model: AskModel, storedPosition: StoredPosition = StoredPosition(),
         serverStartedAt: @escaping () -> Date = Date.init, timeout: TimeInterval = AskClient.timeout,
         seasonLookupTimeout: TimeInterval = 5, progressProvider: (() async -> PlaybackReading?)? = nil) {
        self.client = client
        self.engine = engine
        self.supervisor = supervisor
        self.capture = capture
        self.resolver = resolver
        self.model = model
        self.storedPosition = storedPosition
        self.serverStartedAt = serverStartedAt
        self.timeout = timeout
        self.seasonLookupTimeout = seasonLookupTimeout
        self.progressProvider = progressProvider
    }

    /// The hotkey: same key again closes whatever the card shows.
    func hotKeyPressed() {
        switch AskHotkeyAction.decide(phase: model.phase) {
        case .dismiss: dismiss()
        case .start: begin()
        }
    }

    /// ⌘⇧R: same key again closes whatever the card shows; otherwise the Watch Buddy reacts, no typing.
    func reactHotKeyPressed() {
        switch AskHotkeyAction.decide(phase: model.phase) {
        case .dismiss: dismiss()
        case .start: begin(react: true)
        }
    }

    func dismiss() {
        generation += 1
        prep?.cancel(); prep = nil
        asking?.cancel(); asking = nil
        model.dismiss()
    }

    func begin(react: Bool = false) {
        generation += 1
        prep?.cancel(); asking?.cancel()
        let mine = generation

        if !capture.hasPermission() {
            if !requestedPermission {
                requestedPermission = true
                capture.requestPermission()
            }
            return model.showMessage(FriendlyError.text(.noScreenPermission))
        }
        switch supervisor.status {
        case .ready: break
        case .starting, .warmingUp, .crashed: return model.showMessage(FriendlyError.text(.engineWarmingUp))
        case .failed, .stopped: return model.showMessage(FriendlyError.text(.engineNotResponding))
        case .portInUse: return model.showMessage(supervisor.userMessage ?? EngineSupervisor.portInUseMessage)
        }

        if react { model.openReacting() } else { model.open() }   // the card is up before any capture or engine work
        // The screenshot is taken now, not when the question is sent: it must show the scene being asked about.
        let task = Task { [weak self] in await self?.prepare() ?? .failed(FriendlyError.text(.other)) }
        prep = task
        if react {   // no question to wait for: send as soon as the screenshot is ready
            asking = Task { [weak self] in await self?.run("", task, mine, mode: .react) }
            return
        }
        Task { [weak self] in
            let result = await task.value
            guard let self, self.generation == mine, case .failed(let text) = result, case .input = self.model.phase else { return }
            self.model.showMessage(text)   // e.g. the black-frame guide: no point letting them type
        }
    }

    /// Enter in the text field.
    func submit() {
        guard let question = model.submit() else { return }
        let mine = generation
        let preparing = prep
        asking?.cancel()
        asking = Task { [weak self] in await self?.run(question, preparing, mine) }
    }

    // MARK: steps

    private func prepare() async -> Prep {
        do {
            let state = try await withDeadline(timeout) { [engine] in try await engine.state() }
            guard let showId = state.current.showId, !showId.isEmpty else { return .failed(FriendlyError.text(.showNotSetUp(nil))) }
            storedPosition.set(season: state.current.season, episode: state.current.episode)

            // Read the playback position now, alongside the capture (not at submit, when the tab may have changed).
            let progressTask = Task { await progressProvider?() }
            defer { progressTask.cancel() }

            // The screenshot goes to Anthropic: only the Chrome window, unless the owner turned that off.
            let windowOnly = state.shareOnlyWindow ?? true
            let image = try await withDeadline(timeout) { [capture] in
                windowOnly ? try await capture.captureChromeWindow() : try await capture.capture()
            }
            switch await offMain({ FrameCheck.prepare(image) }) {
            case .black: return .failed(FriendlyError.text(.blackFrame))
            case .unencodable: return .failed(FriendlyError.text(.other))
            case .jpeg(let jpeg):
                let episode = try await EpisodeDetection.resolve(
                    frame: image, showId: showId, resolver: resolver, engine: engine,
                    timeout: timeout, seasonLookupTimeout: seasonLookupTimeout)
                let progress = PlaybackProgress.attachable(await progressTask.value, to: episode)
                return .ready(Prepared(jpeg: jpeg, showId: showId, season: episode.season, episode: episode.episode,
                                       progress: progress))
            }
        } catch let error as EngineError {
            return .failed(FriendlyError.text(FlowFailure.from(error, serverAge: Date().timeIntervalSince(serverStartedAt()), showName: nil)))
        } catch let error as CaptureError {
            switch error {
            case .noPermission: return .failed(FriendlyError.text(.noScreenPermission))
            case .noChromeWindow: return .failed(FriendlyError.text(.noChromeWindow))
            default: return .failed(FriendlyError.text(.other))
            }
        } catch {
            return .failed(FriendlyError.text(.other))
        }
    }

    private func run(_ question: String, _ preparing: Task<Prep, Never>?, _ mine: Int, mode: AskMode = .ask) async {
        guard let preparing else { return }
        let prepared: Prepared
        switch await preparing.value {
        case .failed(let text):
            guard generation == mine, !Task.isCancelled else { return }
            return model.showMessage(text)
        case .ready(let value): prepared = value
        }
        guard generation == mine, !Task.isCancelled else { return }

        let request = AskRequest(question: question, jpeg: prepared.jpeg, showId: prepared.showId,
                                 season: prepared.season, episode: prepared.episode, progress: prepared.progress,
                                 mode: mode)
        do {
            let response = try await withDeadline(timeout) { [client] in try await client.ask(request) }
            guard generation == mine, !Task.isCancelled else { return }
            Diag.log("ask answered (refused=\(response.refused))")
            if mode == .react { model.showReaction(response: response) } else { model.showAnswer(question: question, response: response) }
        } catch is CancellationError {
            return   // dismissed while thinking
        } catch let error as AskError {
            guard generation == mine else { return }
            switch error {   // never the engine's text: it could echo the question
            case .http(let code, _): Diag.log("ask failed (HTTP \(code))")
            default: Diag.log("ask failed (\(error))")
            }
            model.showMessage(AskFriendlyError.text(error, serverAge: Date().timeIntervalSince(serverStartedAt())))
        } catch let error as EngineError {   // withDeadline's own timeout
            guard generation == mine else { return }
            model.showMessage(AskFriendlyError.text(error == .timedOut ? .timedOut : .decoding))
        } catch {
            guard generation == mine else { return }
            model.showMessage(AskFriendlyError.text(.decoding))
        }
    }
}
