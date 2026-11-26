# CineID Mac App — Stage 1: Shell + Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. The owner has custom agents in `.claude/agents/` — see "Who does what" below. **No git commits unless the owner asks.**

**Goal:** A native `CineID.app` that supervises the Python engine and, on ⌘⇧X over Netflix in Chrome (including full screen), shows the dossier popup (front/back/stack/limited/hidden/errors) without stealing focus.

**Architecture:** SwiftUI + AppKit app in `mac/` (XcodeGen). Pure logic (state machines, parsers, API client, friendly errors) is unit-tested; system APIs (panel, hotkey, ScreenCaptureKit, Chrome AppleScript, Vision) sit behind small protocols. The Python engine and its HTTP API are reused; the existing web UI stays for library/add-show until Stage 2.

**Tech Stack:** Swift 5.9+, SwiftUI, AppKit, ScreenCaptureKit, Vision, Carbon hotkey, XCTest, XcodeGen, Xcode 27.2, macOS 26 (deployment target 14.0). Python side: FastAPI/pytest (one endpoint).

**Spec:** `docs/superpowers/specs/2026-11-25-cineid-mac-app-design.md`

## Global Constraints

- App Sandbox **off**; `LSUIElement` true; `NSAppleEventsUsageDescription` set; signed with the owner's Personal Team from git-ignored `mac/Config/Local.xcconfig` (keys: `PRODUCT_BUNDLE_IDENTIFIER`, `DEVELOPMENT_TEAM`, `CINEID_REPO_ROOT`).
- Engine contract unchanged: `127.0.0.1:8000`, `GET /health`, `GET /api/state`, `POST /identify {image(base64 jpeg), show_id, season, episode}`, `POST /api/position`. `/identify` returns `characters[]` (fields: `character_id,name,actor,role,confidence,status,arc_type,first_seen_season,first_seen_episode,last_seen_season,last_seen_episode,relationship_summary,current_arc,last_seen_description,importance_rank,limited?`), `total_faces_detected`, `show_id`, and optionally `low_confidence` or `hidden_by_spoiler_shield`.
- Popup never activates the app, steals focus, pauses video, leaves full screen or switches Spaces; becomes key only on click. Auto-dismiss default 20 s.
- Never launch Chrome; only touch Chrome when it is running and the active tab URL contains `netflix.com/watch`. Do not move the mouse.
- Capture: ≤ 1920 px wide, JPEG quality 0.85; mean brightness < 8 = black frame.
- Engine warm-up window 90 s ("warming up" message); max 3 automatic engine restarts then a plain error.
- User-visible text is plain language (mirror `src/messages.py`); never raw errors, paths or exceptions.
- Style: cream paper `#F4EAD6`, dark ink `#2B2218`, red stamp `#B3382C`, serif names, monospaced labels; match the approved mockup.
- Anything needing the real screen is reported **NOT verified** with exact manual steps; never claim otherwise.
- After code changes: `graphify update .`.

## Who does what (owner's agents)

`macos-integration-expert`: Tasks 4, 8–12, 14 (system pieces). `swiftui-designer`: Tasks 6, 7. `test-runner`: after every task. `ui-verifier`: after Task 7 and Task 14. `code-reviewer`: after Tasks 8, 13 and at the end. `spoiler-auditor`: at the end (checks limited/hidden states and episode clamping). Python endpoint (Task 1) is done by the main session.

## Review Focus

- Chrome closed, Chrome open on a non-Netflix tab, several Chrome windows, Netflix paused/menus visible → no Chrome launch, falls back to OCR then stored position, popup still works.
- Hotkey pressed twice quickly or while an identify is running → one popup, no stacking, no second request.
- Engine still warming up, crashed mid-session, or port 8000 held by an old CineID → plain message, auto-restart, never a hang or silent failure.
- Multiple displays and Retina scaling → popup on the display showing Chrome; capture downscaled correctly; request size sane.
- Engine returns `limited`, `hidden_by_spoiler_shield`, `low_confidence`, zero faces, 6+ faces, very long names/arc text → the right state renders, no clipping/crash.
- Screen Recording not granted, or granted but frame is black → clear message with a button, no crash.
- OCR/Chrome returns an absurd episode (S0E0, S99E999) → rejected, stored position used (a wrong high episode would leak spoilers).

---

### Task 1: Engine photo endpoint (Python)

**Files:** Modify `src/api_routes.py` (add `GET /api/characters/{character_id}/photo`; `ApiDeps.data_dir: Optional[Path] = None`), `src/server.py` (pass `data_dir=_DATA_DIR`); Test `tests/test_api.py`.
**Interfaces:** Produces: JPEG/PNG bytes (`image/jpeg` or `image/png`) of the character's best reference photo (preference order `tmdb_profile.jpg, tmdb_0.jpg, tmdb_1.jpg`, then any `*.jpg|jpeg|png`); 404 `{"ok":false,"message":"No photo."}` otherwise.

- [ ] Tests: `test_photo_served_for_known_character` (tmp `data/shows/s/raw/s-a/tmdb_profile.jpg`, character in DB → 200, `content-type` image/jpeg, bytes equal); `test_photo_prefers_tmdb_profile`; `test_photo_404_when_no_files_or_unknown_character`; `test_photo_rejects_path_traversal` (`..%2f..%2fetc`, ids not matching `[a-z0-9-]+` → 404, never reads outside `data_dir/shows`). Run → FAIL.
- [ ] Implement `photo(character_id: str)` in the router; look up `show_id` via `db.get_character`; resolve and verify the path is inside `data_dir/shows/<show_id>/raw/<character_id>/`. Run `venv/bin/pytest tests -q` → all PASS.

### Task 2: Project skeleton

**Files:** Create `mac/project.yml`, `mac/Config/CineID.xcconfig`, `mac/Config/Local.xcconfig.example`, `mac/CineID/App/CineIDApp.swift`, `mac/CineID/Support/Diag.swift`, `mac/CineIDTests/SmokeTests.swift`; Modify `.gitignore` (`mac/Config/Local.xcconfig`, `mac/*.xcodeproj`, `mac/DerivedData/`).
**Interfaces:** Produces: scheme `CineID` (app + test bundle `CineIDTests`), `Diag.log(_:)` like Venus's.

- [ ] `SmokeTests.testAppModuleLoads` asserts `Bundle(for: AppDelegate.self).bundleIdentifier != nil`. Generate project, run `xcodebuild test -scheme CineID -destination 'platform=macOS'` → FAIL (no target/class).
- [ ] Write `project.yml` (macOS app, deployment 14.0, `LSUIElement`, sandbox off, hardened runtime off, `NSAppleEventsUsageDescription`, `Local.xcconfig` via `#include?`), minimal `AppDelegate` (accessory activation policy, `Diag.log("launched")`). Run tests → PASS; `xcodebuild build` succeeds; launching the built app shows no Dock icon (manual: note it).

### Task 3: Engine models and client

**Files:** Create `mac/CineID/Engine/EngineModels.swift`, `EngineClient.swift`; Test `mac/CineIDTests/EngineClientTests.swift`.
**Interfaces:** Produces: `struct IdentifyResponse: Decodable { characters: [CharacterCard]; totalFacesDetected: Int; showId: String; lowConfidence: Bool?; hiddenBySpoilerShield: Int? }`; `struct CharacterCard: Decodable, Identifiable, Equatable` (all fields above, `limited: Bool?`, `id = characterId`); `struct EngineState: Decodable { setup, current: CurrentShow{showId?,showName?,season,episode}, shows, needsFirstRun }`; `protocol EngineAPI { func health() async throws -> Bool; func state() async throws -> EngineState; func identify(jpeg: Data, showId: String, season: Int, episode: Int) async throws -> IdentifyResponse; func setPosition(showId: String, season: Int, episode: Int) async throws; func photo(characterId: String) async throws -> Data? }`; `final class EngineClient: EngineAPI` (init `baseURL`, `URLSession`); `enum EngineError: Error { notReachable, http(Int), decoding }`.

- [ ] Tests with a `URLProtocol` stub: decode a full success body (use a real captured shape), a `limited` body, `low_confidence`, `hidden_by_spoiler_shield`; `identify` sends JSON with base64 image and the four fields; 404 → `.http(404)`; connection refused → `.notReachable`; photo 404 → `nil`. Run → FAIL. Implement with `JSONDecoder` snake_case; run → PASS.

### Task 4: Engine supervisor

**Files:** Create `mac/CineID/Engine/EngineSupervisor.swift`; Test `EngineSupervisorTests.swift`.
**Interfaces:** Produces: `enum EngineStatus: Equatable { case stopped, starting, warmingUp(since: Date), ready, crashed(restarts: Int), portInUse, failed }`; `protocol EngineProcess { func start() throws; func stop(); var isRunning: Bool { get } }`; `final class EngineSupervisor { init(process:, health: @escaping () async -> Bool, portIsBusy: @escaping () -> Bool, now: @escaping () -> Date, maxRestarts: Int = 3); func tick() async; var status: EngineStatus; var userMessage: String? }` plus `ProcessEngine: EngineProcess` launching `<CINEID_REPO_ROOT>/venv/bin/python src/server.py` (cwd repo, stdout/stderr to `~/Library/Logs/CineID/engine.log`).

- [ ] Tests (fakes): start → `starting`; process up but health false → `warmingUp` and `userMessage == "CineID is warming up — try again in a few seconds."` until 90 s, then `failed`/"CineID's engine isn't responding…"; health true → `ready`; process dies → restart, `crashed(restarts: 1)` … after 3 → `failed`; port busy before start and health true from a foreign process → `portInUse` with message naming the old instance; `stop()` called on shutdown. Run → FAIL → implement → PASS.

### Task 5: Friendly error text

**Files:** Create `mac/CineID/Flow/FriendlyError.swift`; Test `FriendlyErrorTests.swift`.
**Interfaces:** Produces: `enum FlowFailure: Equatable { case blackFrame, noScreenPermission, engineWarmingUp, engineNotResponding, showNotSetUp(String?), serverError, timeout, other }`; `FriendlyError.text(_: FlowFailure) -> String`. Strings identical to `src/messages.py`: black frame → "Netflix video is hidden from screenshots.\nIn Chrome: Settings → System → turn off “Use graphics acceleration when available”, then reopen Chrome."; permission → "CineID needs Screen Recording permission. Open System Settings → Privacy & Security → Screen Recording and turn on CineID."; warming → "CineID is warming up — try again in a few seconds."; not responding → "CineID's engine isn't responding. Quit and reopen CineID."; not set up → "{show|This show} hasn't been set up yet — open CineID → Add a Show."; server → "Something went wrong on our side. Try again."; timeout → "Taking too long — try again."; other → "Something went wrong. Try again."

- [ ] One test per case asserting the exact string; `testNoCaseContainsRawErrorMarkers` (no "Error:", "Exception", "Traceback"). FAIL → implement → PASS.

### Task 6: Popup state model

**Files:** Create `mac/CineID/Popup/PopupState.swift`, `CardStack.swift`; Test `PopupStateTests.swift`.
**Interfaces:** Produces: `struct CardStack: Equatable { init(cards: [CharacterCard]); var top: CharacterCard; var isFlipped: Bool; mutating func flip(); mutating func next(); mutating func previous(); var count: Int }` (sorted by confidence desc, dedup by `characterId`); `enum PopupPhase: Equatable { case hidden, identifying, cards(CardStack), message(PopupMessage) }`; `enum PopupMessage: Equatable { case notRecognised, noFace, hiddenBySpoilerShield, failure(FlowFailure) }`; `protocol Scheduler { func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable }`; `final class PopupModel: ObservableObject { @Published var phase; init(scheduler:, autoDismiss: TimeInterval = 20); func begin(); func present(_ r: IdentifyResponse); func fail(_ f: FlowFailure); func flip(); func next(); func previous(); func dismiss(); func userInteracted() }`.

- [ ] Tests: `present` maps `characters.isEmpty && lowConfidence` → `.notRecognised`; empty + `hiddenBySpoilerShield` → `.hiddenBySpoilerShield`; empty otherwise → `.noFace`; ordering/dedup; `flip` toggles; `next/previous` wrap; `dismiss` → `.hidden`; auto-dismiss fires after 20 s of a fake scheduler and is cancelled/restarted by `userInteracted`; `begin()` while cards shown replaces them; `fail(.blackFrame)` → `.message(.failure(.blackFrame))`. FAIL → implement → PASS.

### Task 7: Dossier views, palette, demo mode, snapshots

**Files:** Create `mac/CineID/Popup/ArtworkPalette.swift`, `DossierFrontView.swift`, `DossierBackView.swift`, `DossierStackView.swift`, `PopupContentView.swift`, `mac/CineID/Support/DemoArgs.swift`; Test `PaletteTests.swift`, `PopupSnapshotTests.swift`.
**Interfaces:** Consumes Task 6 models. Produces: `struct ArtworkPalette { accent: Color; shadow: Color; static func from(_ image: NSImage?) -> ArtworkPalette }` (port of Venus's idea, falls back to warm amber for nil); `PopupContentView(model: PopupModel, photo: (String) -> NSImage?)`; `DemoArgs.popupScenario: PopupScenario?` from `-demoPopup front|back|stack|limited|hidden|identifying|error`.

- [ ] `PaletteTests`: solid red image → accent hue red-ish and luminance within a readable range; nil image → fallback amber; ink-on-paper contrast ratio ≥ 7. Snapshot tests render each scenario with `ImageRenderer` to PNG in `CINEID_SNAPSHOT_DIR` (default temp dir printed to the log) and assert non-empty and size ≥ 300×200. FAIL → implement the views to match the mockup (front: photo, serif name, "played by", rotated red stamp `SEEN S{ss}E{ee}`, match bar, status pill; back: RIGHT NOW / LAST SCENE / KNOWS / FIRST SEEN + 👍/👎 buttons (disabled in Stage 1, labelled "soon"); limited: name/actor + hint "Story notes aren't available for this show yet."; Reduce Motion respected for the flip) → PASS. Run `ui-verifier` on the PNGs.

### Task 8: Popup panel and presenter

**Files:** Create `mac/CineID/Popup/PopupPanel.swift`, `PopupPresenter.swift`; Test `PopupPanelTests.swift`.
**Interfaces:** Produces: `final class PopupPanel: NSPanel` (borderless, `.nonactivatingPanel`, `isFloatingPanel`, `hidesOnDeactivate = false`, `becomesKeyOnlyIfNeeded = true`, `canBecomeKey` true, level above full-screen content, `collectionBehavior ⊇ [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]`); `func popupFrame(visibleFrame: CGRect, size: CGSize, margin: CGFloat = 20) -> CGRect` (top-right); `final class PopupPresenter { init(model: PopupModel, screenProvider: () -> NSScreen?); func show(); func hide() }` reacting to `model.phase`.

- [ ] Tests: panel flags/level/behaviour assertions on a created (not shown) panel; `popupFrame` for several visible frames incl. negative-origin secondary displays and tiny screens (never off-screen); presenter shows on `.identifying` and hides on `.hidden`. FAIL → implement → PASS. **NOT verifiable here — manual checklist (owner):** full-screen Chrome Netflix: popup appears, Netflix keeps playing and full screen, no Space switch, Space still pauses video until the card is clicked, Esc closes after click, ✕ closes, popup lands on the correct display.

### Task 9: Global hotkey

**Files:** Create `mac/CineID/Hotkey/HotKey.swift`; Test `HotKeyTests.swift`.
**Interfaces:** Produces: `struct HotKeyCombo: Equatable { keyCode: UInt32; modifiers: UInt32; static let identify: HotKeyCombo /* ⌘⇧X */ }`; `final class HotKey { init(combo:, handler: @escaping () -> Void) throws; deinit unregisters }` via `RegisterEventHotKey` (no Accessibility permission).

- [ ] Tests: `HotKeyCombo.identify` has `kVK_ANSI_X` and `cmdKey|shiftKey`; registering two instances of the same combo throws `.alreadyRegistered` (registration seam injected so the test doesn't need a real event loop). FAIL → implement → PASS. Manual: pressing ⌘⇧X inside Terminal/Chrome fires the handler.

### Task 10: Screen capture and frame checks

**Files:** Create `mac/CineID/Capture/FrameCheck.swift`, `ScreenCapture.swift`; Test `FrameCheckTests.swift`.
**Interfaces:** Produces: `enum FrameCheck { static func targetSize(for: CGSize, maxWidth: CGFloat = 1920) -> CGSize; static func isBlack(meanBrightness: Double) -> Bool /* < 8 of 255 */; static func meanBrightness(of: CGImage) -> Double; static func jpeg(_: CGImage, quality: CGFloat = 0.85) -> Data? }`; `protocol ScreenCapturing { func hasPermission() -> Bool; func capture() async throws -> CGImage }`; `final class ScreenCaptureKitCapture: ScreenCapturing` (display containing the frontmost Chrome window, else main display).

- [ ] Tests: `targetSize` keeps aspect and never upsizes; black image → brightness ≈ 0 → `isBlack`; mid-grey → not black; `jpeg` of a generated image decodes back to the same size. FAIL → implement → PASS. Manual: without Screen Recording permission `hasPermission()` is false and the flow shows the permission message.

### Task 11: Chrome bridge and parsers

**Files:** Create `mac/CineID/Chrome/ChromeParsers.swift`, `ChromeBridge.swift`; Test `ChromeParsersTests.swift`, `ChromeBridgeTests.swift`.
**Interfaces:** Produces: `enum ChromeParsers { static func parseTab(_ raw: String) -> (url: String, title: String)? /* "URL|||title" */; static func isNetflixWatch(url: String) -> Bool; static func parseReactContext(_ raw: String) -> Episode? /* "rc:S2E8" */; static func parseTitleParts(_ raw: String) -> Episode? /* "Show|||E2|||Title", "ShowE2Title", "S2:E8" */ }`; `struct Episode: Equatable { season: Int?; episode: Int }`; `protocol ChromeBridge { func isRunning() -> Bool; func activeTab() -> (url: String, title: String)?; func runJS(_ js: String) -> String? }`; `final class AppleScriptChrome: ChromeBridge` (checks `NSRunningApplication` first; `NSAppleScript` execution off the main thread; returns nil on any error).

- [ ] Parser tests use the vectors the Python app already handled: `rc:S2E8`, `rc_models:a,b`, `no_rc|nkeys:x`, `jserr|||Allow JavaScript from Apple Events`, `Breaking Bad|||E2|||Madrigal`, `Breaking BadE2Madrigal`, `S2:E8`, `Season 2, Episode 8`, empty/garbage → nil; `isNetflixWatch` true only for `netflix.com/watch/<digits>`. Bridge test with a fake runner: not running → no script executed (never launches Chrome). FAIL → implement → PASS. Manual: with Netflix playing, `activeTab()` returns the watch URL.

### Task 12: OCR and episode resolver

**Files:** Create `mac/CineID/Episode/EpisodeTextParser.swift`, `OCRReader.swift`, `EpisodeResolver.swift`; Test `EpisodeTests.swift`.
**Interfaces:** Produces: `EpisodeTextParser.parse(_ text: String) -> Episode?` (patterns `S(\d+)[:\s.·•×xX]*E(\d+)` and `Season N, Episode M`); `protocol OCRReading { func readText(in: CGImage, regions: [CGRect]) -> String }` with `VisionOCR` (top 15 % + bottom 25 %, fast mode); `struct ResolvedEpisode: Equatable { season: Int; episode: Int; source: Source /* .chrome, .ocr, .stored */ }`; `final class EpisodeResolver { init(chrome: ChromeBridge, ocr: OCRReading, stored: () -> (season: Int, episode: Int)); func resolve(frame: CGImage) -> ResolvedEpisode }`.

- [ ] Tests: Chrome JS result wins; Chrome unavailable/not Netflix → OCR; OCR empty → stored; Chrome gives episode without season → season from stored; **sanity**: season outside 1…50 or episode outside 1…500 (e.g. S0E0, S99E999) rejected → next source; parser vectors incl. `S2:E8`, `S02E08`, "Season 2, Episode 8". FAIL → implement → PASS.

### Task 13: Identify flow

**Files:** Create `mac/CineID/Flow/IdentifyFlow.swift`; Test `IdentifyFlowTests.swift`.
**Interfaces:** Consumes Tasks 3–6, 10–12. Produces: `final class IdentifyFlow { init(engine: EngineAPI, supervisor: StatusProviding, capture: ScreenCapturing, resolver: EpisodeResolver, model: PopupModel, now: () -> Date); func trigger() async }` — ignores re-entry while running; order: permission check → `model.begin()` (instant) → engine ready check → capture → black-frame check → resolve episode → `setPosition` if source ≠ stored → `identify` → `model.present`; HTTP 404 → `.showNotSetUp(name)`; engine errors map to `FlowFailure`.

- [ ] Tests with fakes: success; `limited`; hidden; low confidence; zero faces; no permission → `.noScreenPermission` and no capture attempted; black frame → `.blackFrame` and no identify call; engine warming → `.engineWarmingUp`; unreachable long after launch → `.engineNotResponding`; 404 → `.showNotSetUp`; second `trigger()` while one is running → exactly one engine call and one popup; `setPosition` not called when the episode came from the stored position. FAIL → implement → PASS. Run `code-reviewer`.

### Task 14: Menu bar, wiring, launch script, docs

**Files:** Create `mac/CineID/Menu/StatusMenu.swift`, `mac/CineID/App/AppEnvironment.swift`, `mac/CineID/App/WebUIWindow.swift`, `scripts/run-mac.sh`, `docs/HOW_TO_TRY_MAC.md`; Modify `AppDelegate` to build the environment (supervisor tick every 2 s, hotkey → `IdentifyFlow.trigger()`), `CLAUDE.md`.
**Interfaces:** Produces: status item showing current show + `S02E08` (from `/api/state`, refreshed on open), items *Open CineID…* / *Add a Show…* (open `http://127.0.0.1:8000/ui/#/…` in a `WKWebView` window with an Edit menu installed so ⌘V works), *Switch show* (`POST /api/shows/select`), *Quit* (stops the engine). `run-mac.sh`: `xcodegen && xcodebuild … build && open` the built app; fails clearly if `Local.xcconfig` is missing.

- [ ] Test `StatusMenuModelTests`: menu item titles for no show / one show / several; stamp formatting `S02E08`; engine statuses map to a first menu line ("Warming up…", "Engine not responding", "Ready"). FAIL → implement → PASS.
- [ ] Build and launch with `scripts/run-mac.sh`; confirm the engine starts, `/health` answers, status item appears, and `-demoPopup front|back|stack|limited|hidden|identifying|error` shows each state. Run `ui-verifier`, `code-reviewer`, `spoiler-auditor`.
- [ ] Write `docs/HOW_TO_TRY_MAC.md` (build steps, permissions to grant to CineID, the manual full-screen checklist, what is NOT verified), update `CLAUDE.md`, run `graphify update .`. Final: `venv/bin/pytest tests -q` and `xcodebuild test` both green; report counts.

## Self-review notes

- Spec coverage (Stage 1): supervision (T4), hotkey (T9), capture + black-frame guide (T10, T5), Chrome/episode (T11, T12), popup front/back/stack/limited/hidden/errors/instant loading/auto-dismiss/focus rules (T6–T8, T13), menu + web-UI hosting (T14), photo for the card (T1). Deferred by design to later plans: SwiftUI library/first-run/add-show/settings (Stage 2), 👍/👎 feedback (Stage 3, buttons present but disabled), Watch Buddy (Stage 4).
- Names are consistent across tasks (`CharacterCard`, `IdentifyResponse`, `PopupModel`, `FlowFailure`, `EngineStatus`, `Episode`, `ResolvedEpisode`).
