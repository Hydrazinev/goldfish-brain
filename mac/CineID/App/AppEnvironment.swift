import AppKit

/// Remembers when the engine last (re)started, for "still warming up" vs "stuck" judgements.
final class EngineStartTracker: @unchecked Sendable {
    private let lock = NSLock()
    private var started = Date()
    var date: Date { lock.withLock { started } }
    func note(_ date: Date) { lock.withLock { started = date } }
}

/// Reference photos already fetched from the engine (the popup asks for them synchronously).
final class PhotoStore: @unchecked Sendable {
    private let lock = NSLock()
    private var images: [String: NSImage] = [:]
    private let engine: EngineAPI
    init(engine: EngineAPI) { self.engine = engine }

    func image(for id: String) -> NSImage? { lock.withLock { images[id] } }

    /// Fetch what is missing (each with a short deadline); failures just mean no photo.
    func prefetch(_ ids: [String]) async {
        let missing = ids.filter { image(for: $0) == nil }
        await withTaskGroup(of: Void.self) { group in
            for id in missing {
                group.addTask { [engine] in
                    guard let data = try? await withDeadline(3, { try await engine.photo(characterId: id) }),
                          let image = NSImage(data: data) else { return }
                    self.lock.withLock { self.images[id] = image }
                }
            }
        }
    }
}

/// Builds and owns everything the running app needs: engine supervision, hotkey, popup, menu bar.
/// Never created under XCTest.
@MainActor
final class AppEnvironment {
    static let pidFile = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/CineID/engine.pid")

    private let engine = EngineClient()
    private let model = PopupModel()
    private let startTracker = EngineStartTracker()
    private let webUI = WebUIWindow()
    private var supervisor: EngineSupervisor?
    private var presenter: PopupPresenter?
    private var flow: IdentifyFlow?
    private var hotKey: HotKey?
    private var askHotKey: HotKey?
    private var reactHotKey: HotKey?
    private let askModel = AskModel()
    private var askFlow: AskFlow?
    private var askPresenter: AskPresenter?
    private var identifyTask: Task<Void, Never>?
    private let feedback = FeedbackClient()
    private var statusMenu: StatusMenuController?
    private var tickTask: Task<Void, Never>?
    private var signalSources: [DispatchSourceSignal] = []
    private var engineState: EngineState?
    private var lastStatus: EngineStatus = .stopped
    private var startupProblem: String?
    private var hotKeyProblem: String?
    private var askHotKeyProblem: String?
    private var reactHotKeyProblem: String?
    private var stopped = false
    private let chromeCheck = ChromeInstanceCheck()

    /// Open the Goldfish Brain window (library / add a show / settings).
    func openUI(route: String = "") {
        Task { @MainActor [weak self] in self?.webUI.open(route: route) }
    }

    /// Show the Goldfish Brain window as soon as the engine answers, so opening the app visibly does something
    /// (it is otherwise menu-bar only). Gives up quietly if the engine fails or the port is taken —
    /// the menu's first line explains those.
    func openUIWhenReady() {
        Task { @MainActor [weak self] in
            for _ in 0..<180 {                                   // ~90 s
                guard let self, !self.stopped else { return }
                switch self.supervisor?.status {
                case .ready?: self.webUI.open(route: ""); return
                case .failed?, .portInUse?: return
                default: break
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
    }

    /// Diagnostic: `open CineID.app --args -identifyNow` runs one identify as soon as the engine is ready
    /// and logs per-step timings to ~/Library/Logs/CineID/app.log.
    func runDiagnosticIdentify() {
        Task { @MainActor [weak self] in
            for _ in 0..<180 {
                guard let self else { return }
                if self.supervisor?.status == .ready { break }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            await self?.flow?.trigger()
            Diag.log("diagnostic identify finished")
        }
    }

    func start() {
        let photos = PhotoStore(engine: engine)
        model.onFeedback = { [feedback] report in Task { await feedback.send(report) } }
        let popup = PopupPresenter(model: model, photo: { photos.image(for: $0) })
        popup.attach(DismissWatcher(escape: CarbonEscape(), clicks: NSEventClickMonitor(),
                                    cardRect: { [weak popup] in popup?.cardRect },
                                    onDismiss: { [weak self] in self?.dismissIdentify() },
                                    onHover: { [model] in model.hoverChanged($0) }))
        presenter = popup

        let askWindow = AskWindow(model: askModel, onSubmit: { [weak self] in self?.askFlow?.submit() },
                                  onClose: { [weak self] in self?.dismissAsk() })
        let ask = AskPresenter(model: askModel, window: askWindow)
        ask.attach(DismissWatcher(escape: CarbonEscape(), clicks: NSEventClickMonitor(),
                                  cardRect: { [weak ask] in ask?.cardRect },
                                  onDismiss: { [weak self] in self?.dismissAsk() },
                                  onHover: { [askModel] in askModel.hoverChanged($0) }))
        askPresenter = ask

        if let repoRoot = ProcessEngine.repoRoot() {
            let sup = EngineSupervisor(process: ProcessEngine(repoRoot: repoRoot),
                                       health: EngineHealthProbe.make(),
                                       portIsBusy: PortProbe.make(),
                                       pidFile: Self.pidFile)
            supervisor = sup
            let stored = StoredPosition()
            let progressChrome = AppleScriptChrome()   // its own AppleScript queue: never waits behind episode detection
            flow = IdentifyFlow(engine: engine, supervisor: sup, capture: ScreenCaptureKitCapture(),
                                resolver: EpisodeResolver(chrome: AppleScriptChrome(), ocr: VisionOCR(),
                                                          stored: { stored.get() }),
                                model: model, storedPosition: stored,
                                serverStartedAt: { [startTracker] in startTracker.date },
                                prefetchPhotos: { await photos.prefetch($0) },
                                progressProvider: { await PlaybackProgress.read(from: progressChrome) })
            let capture = ScreenCaptureKitCapture()
            askFlow = AskFlow(client: AskClient(), engine: engine, supervisor: sup, capture: capture,
                              resolver: EpisodeResolver(chrome: AppleScriptChrome(), ocr: VisionOCR(),
                                                        stored: { stored.get() }),
                              model: askModel, storedPosition: stored,
                              serverStartedAt: { [startTracker] in startTracker.date },
                              progressProvider: { await PlaybackProgress.read(from: progressChrome) })
        } else {
            startupProblem = "Goldfish Brain can't find its engine. Rebuild the app with scripts/run-mac.sh."
        }

        installHotKey()
        installMenu()
        installSignalHandlers()
        startEngineAndTicking()
    }

    /// Stops the engine. Safe to call more than once (quit menu, termination, SIGTERM/SIGINT).
    func shutdown() {
        guard !stopped else { return }
        stopped = true
        tickTask?.cancel()
        supervisor?.stop()
    }

    // MARK: engine

    private func startEngineAndTicking() {
        guard let supervisor else { return }
        let tracker = startTracker
        tickTask = Task { [weak self] in
            tracker.note(Date())
            // start() may probe ports and reclaim an orphan engine: keep it off the main thread.
            await Task.detached { supervisor.start() }.value
            while !Task.isCancelled {
                await supervisor.tick()
                if case .warmingUp(let since) = supervisor.status { tracker.note(since) }
                await self?.engineTicked()
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    private func engineTicked() async {
        guard let supervisor else { return }
        let status = supervisor.status
        let changed = status != lastStatus
        lastStatus = status
        if status == .ready, changed || engineState == nil { await refreshState() }
        else if changed { refreshMenu() }
    }

    private func refreshState() async {
        if let state = try? await withDeadline(3, { [engine] in try await engine.state() }) { engineState = state }
        refreshMenu()
    }

    // MARK: hotkey

    private func installHotKey() {
        do {
            hotKey = try HotKey(combo: .identify) { [weak self] in
                Task { @MainActor in self?.identifyHotKeyPressed() }
            }
        } catch HotKeyError.alreadyRegistered {
            hotKeyProblem = "⌘⇧X is already used by another app, so Goldfish Brain can't listen for it."
        } catch {
            hotKeyProblem = "Goldfish Brain couldn't register ⌘⇧X."
        }
        do {
            askHotKey = try HotKey(combo: .ask) { [weak self] in
                Task { @MainActor in self?.askHotKeyPressed() }
            }
        } catch HotKeyError.alreadyRegistered {
            askHotKeyProblem = "⌘⇧E is already used by another app, so Goldfish Brain can't listen for it."
        } catch {
            askHotKeyProblem = "Goldfish Brain couldn't register ⌘⇧E."
        }
        do {
            reactHotKey = try HotKey(combo: .react) { [weak self] in
                Task { @MainActor in self?.reactHotKeyPressed() }
            }
        } catch HotKeyError.alreadyRegistered {
            reactHotKeyProblem = "⌘⇧R is already used by another app, so Goldfish Brain can't listen for it."
        } catch {
            reactHotKeyProblem = "Goldfish Brain couldn't register ⌘⇧R."
        }
    }

    /// ⌘⇧X: ignored while an identify runs; closes a result card; otherwise starts an identify.
    private func identifyHotKeyPressed() {
        switch IdentifyHotkeyAction.decide(running: flow?.isRunning ?? false, phase: model.phase) {
        case .ignore: return
        case .dismiss: model.dismiss()
        case .start:
            _ = chromeCheck.hasMultiple()   // logs when several Chromes run
            if let startupProblem { return model.notice(startupProblem) }
            dismissAsk()   // one card at a time
            guard let flow else { return }
            identifyTask = Task { @MainActor in await flow.trigger() }
        }
    }

    /// ⌘⇧E: opens the question card; the same key again closes it.
    private func askHotKeyPressed() {
        if askModel.phase == .hidden {
            if let startupProblem { dismissIdentify(); return askModel.showMessage(startupProblem) }
            dismissIdentify()
        }
        askFlow?.hotKeyPressed()
    }

    /// ⌘⇧R: the Watch Buddy reacts to the scene; the same key again closes the card.
    private func reactHotKeyPressed() {
        if askModel.phase == .hidden {
            if let startupProblem { dismissIdentify(); return askModel.showMessage(startupProblem) }
            dismissIdentify()
        }
        askFlow?.reactHotKeyPressed()
    }

    /// Esc, a click outside, or the hotkey: close the identify card (and stop an identify still running).
    private func dismissIdentify() {
        if model.phase == .identifying { identifyTask?.cancel() }
        model.dismiss()
    }

    private func dismissAsk() {
        if let askFlow { askFlow.dismiss() } else { askModel.dismiss() }
    }

    // MARK: menu

    private func installMenu() {
        let actions = StatusMenuController.Actions(
            identify: { [weak self] in self?.identifyHotKeyPressed() },
            ask: { [weak self] in self?.askHotKeyPressed() },
            react: { [weak self] in self?.reactHotKeyPressed() },
            openUI: { [weak self] route in self?.webUI.open(route: route) },
            selectShow: { [weak self] id in self?.selectShow(id) },
            quit: { NSApp.terminate(nil) },
            refresh: { [weak self] in Task { @MainActor in await self?.refreshState() } })
        statusMenu = StatusMenuController(model: menuModel(), actions: actions)
    }

    private func menuModel() -> StatusMenuModel {
        StatusMenuModel(status: supervisor?.status ?? .stopped, supervisorMessage: supervisor?.userMessage,
                        state: engineState, problem: startupProblem ?? hotKeyProblem ?? askHotKeyProblem ?? reactHotKeyProblem,
                        chromeNote: chromeCheck.hasMultiple() ? ChromeInstanceCheck.note : nil)
    }

    private func refreshMenu() { statusMenu?.update(menuModel()) }

    private func selectShow(_ id: String) {
        Task { @MainActor [engine] in
            do { try await engine.selectShow(showId: id) } catch { Diag.log("could not switch show") }
            await self.refreshState()
        }
    }

    // MARK: signals

    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)   // let the dispatch source see it instead of the default kill
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in
                self?.shutdown()
                NSApp.terminate(nil)
            }
            source.resume()
            signalSources.append(source)
        }
    }
}
