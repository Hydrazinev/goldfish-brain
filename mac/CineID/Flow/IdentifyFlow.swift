import CoreGraphics
import Foundation

/// What the flow needs from the episode resolver (a protocol so tests don't need Chrome or OCR).
protocol EpisodeResolving {
    /// May block for seconds (AppleScript): the flow calls it off the main thread.
    func resolve(frame: CGImage) -> ResolvedEpisode
}
extension EpisodeResolver: EpisodeResolving {}

/// The stored viewing position, readable from any thread. The flow refreshes it from the engine
/// just before the resolver runs, so "stored" means what the engine currently has.
final class StoredPosition: @unchecked Sendable {
    private let lock = NSLock()
    private var value: (season: Int, episode: Int) = (1, 1)
    func get() -> (season: Int, episode: Int) { lock.withLock { value } }
    func set(season: Int, episode: Int) { lock.withLock { value = (season, episode) } }
}

/// Runs `operation`, but gives up with `EngineError.timedOut` after `seconds`.
/// Cancelling the caller cancels both (and surfaces as `CancellationError`).
func withDeadline<T>(_ seconds: TimeInterval, _ operation: @escaping () async throws -> T) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await operation() }
        group.addTask {
            try await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            throw EngineError.timedOut
        }
        defer { group.cancelAll() }
        guard let first = try await group.next() else { throw CancellationError() }
        return first
    }
}

/// ⌘⇧X: permission → "Identifying…" at once → engine ready → capture → black-frame check →
/// which episode → save it if detected → identify → popup. One run at a time.
@MainActor
final class IdentifyFlow {
    private let engine: EngineAPI
    private let supervisor: StatusProviding
    private let capture: ScreenCapturing
    private let resolver: EpisodeResolving
    private let model: PopupModel
    private let now: () -> Date
    private let serverStartedAt: () -> Date
    private let storedPosition: StoredPosition
    private let timeout: TimeInterval
    private let seasonLookupTimeout: TimeInterval
    private let prefetchPhotos: ([String]) async -> Void
    /// Reserved for a later progress feature (how far through the episode the viewer is). Unused for now.
    let progressProvider: (() -> Double?)?
    private var running = false
    private var requestedPermission = false

    init(engine: EngineAPI, supervisor: StatusProviding, capture: ScreenCapturing, resolver: EpisodeResolving,
         model: PopupModel, now: @escaping () -> Date = Date.init,
         storedPosition: StoredPosition = StoredPosition(),
         serverStartedAt: (() -> Date)? = nil,
         timeout: TimeInterval = 30,
         seasonLookupTimeout: TimeInterval = 5,
         prefetchPhotos: @escaping ([String]) async -> Void = { _ in },
         progressProvider: (() -> Double?)? = nil) {
        let created = now()
        self.engine = engine
        self.supervisor = supervisor
        self.capture = capture
        self.resolver = resolver
        self.model = model
        self.now = now
        self.storedPosition = storedPosition
        self.serverStartedAt = serverStartedAt ?? { created }
        self.timeout = timeout
        self.seasonLookupTimeout = seasonLookupTimeout
        self.prefetchPhotos = prefetchPhotos
        self.progressProvider = progressProvider
    }

    /// Keeps the latest analysed frame at ~/Library/Logs/CineID/last_capture.jpg (overwritten each time,
    /// never uploaded) so "it didn't recognise anyone" can be diagnosed by looking at the frame.
    private static func saveLastCapture(_ data: Data) {
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil,
              let lib = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first else { return }
        let url = lib.appendingPathComponent("Logs/CineID/last_capture.jpg")
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }

    /// True while an identify is in flight (the hotkey is ignored then).
    var isRunning: Bool { running }

    func trigger() async {
        guard !running else { return }
        running = true
        defer { running = false }
        await run()
    }

    private func run() async {
        var sw = Stopwatch("identify")
        defer { sw.lap("finished") }
        if !capture.hasPermission() {
            // Preflight alone never prompts or lists the app in Settings: ask once per app run.
            if !requestedPermission {
                requestedPermission = true
                capture.requestPermission()
            }
            return model.fail(.noScreenPermission)
        }
        model.begin()
        sw.lap("popup shown (Looking…)")

        switch supervisor.status {
        case .ready: break
        case .starting, .warmingUp, .crashed: return model.fail(.engineWarmingUp)
        case .failed, .stopped: return model.fail(.engineNotResponding)
        case .portInUse:
            return model.notice(supervisor.userMessage ?? EngineSupervisor.portInUseMessage)
        }

        var showName: String?
        do {
            let state = try await withDeadline(timeout) { [engine] in try await engine.state() }
            showName = state.current.showName
            guard let showId = state.current.showId, !showId.isEmpty else { return model.fail(.showNotSetUp(nil)) }
            storedPosition.set(season: state.current.season, episode: state.current.episode)
            sw.lap("engine state")

            let image = try await withDeadline(timeout) { [capture] in try await capture.capture() }
            sw.lap("screen capture")
            let prepared = await offMain { FrameCheck.prepare(image) }
            sw.lap("resize + jpeg")
            let jpeg: Data
            switch prepared {
            case .black: return model.fail(.blackFrame)
            case .unencodable: return model.fail(.other)
            case .jpeg(let data):
                jpeg = data
                Self.saveLastCapture(data)   // local debug copy: what the app actually sees
            }

            let episode = try await EpisodeDetection.resolve(
                frame: image, showId: showId, resolver: resolver, engine: engine,
                timeout: timeout, seasonLookupTimeout: seasonLookupTimeout)
            sw.lap("episode detect (\(episode.source))")

            let response = try await withDeadline(timeout) { [engine] in
                try await engine.identify(jpeg: jpeg, showId: showId, season: episode.season, episode: episode.episode)
            }
            sw.lap("engine identify")
            Diag.log("result: faces=\(response.totalFacesDetected) cards=\(response.characters.count) low=\(response.lowConfidence ?? false) hidden=\(response.hiddenBySpoilerShield ?? 0)")
            await prefetchPhotos(response.characters.map(\.characterId))
            sw.lap("photos")
            try Task.checkCancellation()
            model.viewingPosition = ViewingPosition(season: episode.season, episode: episode.episode)
            model.present(response)
        } catch is CancellationError {
            model.dismiss()   // cancelled on purpose: no error popup
        } catch where Task.isCancelled {
            model.dismiss()   // e.g. the capture API reporting a cancel as its own error
        } catch let error as EngineError {
            let age = now().timeIntervalSince(serverStartedAt())
            model.fail(FlowFailure.from(error, serverAge: age, showName: showName))
        } catch let error as CaptureError {
            model.fail(error == .noPermission ? .noScreenPermission : .other)
        } catch {
            model.fail(.other)
        }
    }
}

/// Runs blocking work on a background queue and resumes when done.
func offMain<T>(_ work: @escaping () -> T) async -> T {
    await withCheckedContinuation { continuation in
        DispatchQueue.global(qos: .userInitiated).async { continuation.resume(returning: work()) }
    }
}

/// Which episode is on screen, shared by identify and ask: the resolver (Chrome, OCR, stored), then an
/// engine season lookup when only the number is known, then a best-effort save of what was detected.
enum EpisodeDetection {
    static func resolve(frame: CGImage, showId: String, resolver: EpisodeResolving, engine: EngineAPI,
                        timeout: TimeInterval, seasonLookupTimeout: TimeInterval) async throws -> ResolvedEpisode {
        var episode = await offMain { resolver.resolve(frame: frame) }
        if !episode.seasonKnown, let title = episode.title, !title.isEmpty {
            // Best effort: Netflix's page gives no season, so ask the engine (TMDB). Never blocks the caller.
            let number = episode.episode
            do {
                let found = try await withDeadline(seasonLookupTimeout) {
                    try await engine.lookupSeason(showId: showId, episode: number, title: title)
                }
                if let found, EpisodeResolver.seasonRange.contains(found) {
                    episode.season = found
                    episode.seasonKnown = true
                } else {
                    Diag.log("season lookup found nothing; using the stored season")
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                Diag.log("season lookup failed; using the stored season")
            }
        }
        Diag.log(String(format: "episode S%02dE%02d via %@", episode.season, episode.episode,
                        "\(episode.source)" + (episode.seasonKnown ? "" : " (season guessed)")))
        if episode.source != .stored {
            // Best effort: a failed save must not stop the caller.
            let (season, number) = (episode.season, episode.episode)
            do {
                try await withDeadline(timeout) { try await engine.setPosition(showId: showId, season: season, episode: number) }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                Diag.log("could not save the detected episode")
            }
        }
        return episode
    }
}
