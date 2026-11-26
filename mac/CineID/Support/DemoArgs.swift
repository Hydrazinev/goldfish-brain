import AppKit

/// A canned popup state for `-demoPopup <name>` previews and snapshot tests. Built by driving a
/// real `PopupModel`, so what you preview is exactly what the app can reach.
enum PopupScenario: String, CaseIterable {
    // The documented `-demoPopup` values.
    case front, back, stack, limited, hidden, identifying, error
    // Extra states: also previewable, and rendered by the snapshot tests.
    case longName, longText, spoilerShield, notRecognised, noFace, thanks

    /// The viewing position shown on the red stamp in demos.
    static let demoPosition = ViewingPosition(season: 4, episode: 4)

    @MainActor
    func makeModel() -> PopupModel {
        let model = PopupModel(scheduler: NeverScheduler())
        switch self {
        case .front: model.present(Self.response([Sample.saul]))
        case .back:
            model.present(Self.response([Sample.saul]))
            model.flip()
        case .stack: model.present(Self.response([Sample.skyler, Sample.saul, Sample.mike]))
        case .limited: model.present(Self.response([Sample.limited]))
        case .hidden:
            model.present(Self.response([Sample.saul]))
            model.dismiss()
        case .identifying: model.begin()
        case .error: model.fail(.blackFrame)
        case .longName: model.present(Self.response([Sample.longText]))
        case .longText:
            model.present(Self.response([Sample.longText]))
            model.flip()
        case .spoilerShield: model.present(Self.response([], faces: 1, hidden: 1))
        case .notRecognised: model.present(Self.response([], faces: 2, lowConfidence: true))
        case .noFace: model.present(Self.response([], faces: 0))
        case .thanks:
            model.present(Self.response([Sample.saul]))
            model.reportFeedback(.wrong)
        }
        return model
    }

    private static func response(_ cards: [CharacterCard], faces: Int? = nil, lowConfidence: Bool? = nil,
                                 hidden: Int? = nil) -> IdentifyResponse {
        IdentifyResponse(characters: cards, totalFacesDetected: faces ?? cards.count, showId: "demo",
                         lowConfidence: lowConfidence, hiddenBySpoilerShield: hidden)
    }
}

/// A canned Ask card state for `-demoAsk <name>` previews and snapshot tests.
enum AskScenario: String, CaseIterable {
    case input, thinking, answer, refused, longAnswer, needsDisclosure, blackFrame

    @MainActor
    func makeModel() -> AskModel {
        let model = AskModel(scheduler: NeverScheduler())
        model.open()
        func ask(_ question: String) { model.draft = question; _ = model.submit() }
        switch self {
        case .input: break
        case .thinking: ask("Why is she so afraid of him?")
        case .answer:
            ask("Who is the man in the grey suit?")
            model.showAnswer(question: "Who is the man in the grey suit?",
                             response: AskResponse(answer: "That's Gus Fring, the owner of Los Pollos Hermanos. So far you have only seen him as a polite businessman.",
                                                   safeUpTo: SafePosition(season: 4, episode: 4), refused: false))
        case .refused:
            ask("Does he die?")
            model.showAnswer(question: "Does he die?",
                             response: AskResponse(answer: "I'd rather not say: that goes beyond where you are in the story. Ask me again after you have watched a bit more.",
                                                   safeUpTo: SafePosition(season: 4, episode: 4), refused: true))
        case .longAnswer:
            let q = "Why did the lawyer take the money from the harbour master and what does the burned ledger have to do with the audit?"
            ask(q)
            model.showAnswer(question: q, response: AskResponse(
                answer: "Up to this point the money is a retainer: the lawyer is paid to keep the harbour master's name off the warehouse leases. The ledger she burns on the pier is the only record that links the two of them, and the audit that is coming would have found it. That is why she is destroying it one page at a time rather than handing it over.",
                safeUpTo: SafePosition(season: 4, episode: 4), refused: false))
        case .needsDisclosure: model.showMessage(AskFriendlyError.needsDisclosure)
        case .blackFrame: model.showMessage(FriendlyError.text(.blackFrame))
        }
        return model
    }
}

/// Demo popups never time out: a preview that vanishes after 20 s is no use.
struct NeverScheduler: Scheduler {
    private struct Nothing: Cancellable { func cancel() {} }
    func schedule(after: TimeInterval, _ work: @escaping () -> Void) -> Cancellable { Nothing() }
}

enum DemoArgs {
    /// `-demoPopup front|back|stack|limited|hidden|identifying|error` (nil when absent or unknown).
    static var popupScenario: PopupScenario? { popupScenario(from: CommandLine.arguments) }

    static func askScenario(from arguments: [String]) -> AskScenario? {
        guard let flag = arguments.firstIndex(of: "-demoAsk"), flag + 1 < arguments.count else { return nil }
        return AskScenario(rawValue: arguments[flag + 1])
    }

    static func popupScenario(from arguments: [String]) -> PopupScenario? {
        guard let flag = arguments.firstIndex(of: "-demoPopup"), flag + 1 < arguments.count else { return nil }
        return PopupScenario(rawValue: arguments[flag + 1])
    }
}

/// Made-up characters for previews and snapshots (not engine data).
private enum Sample {
    private static func card(_ id: String, _ name: String, _ actor: String?, _ confidence: Double,
                             status: String? = nil, arcType: String? = nil, first: (Int, Int)? = nil,
                             last: (Int, Int)? = nil, knows: String? = nil, arc: String? = nil,
                             scene: String? = nil, limited: Bool? = nil, alsoIn: [String]? = nil) -> CharacterCard {
        CharacterCard(characterId: id, name: name, actor: actor, role: nil, confidence: confidence, status: status,
                      arcType: arcType, firstSeenSeason: first?.0, firstSeenEpisode: first?.1,
                      lastSeenSeason: last?.0, lastSeenEpisode: last?.1, relationshipSummary: knows,
                      currentArc: arc, lastSeenDescription: scene, importanceRank: nil, limited: limited, alsoIn: alsoIn)
    }

    static let saul = card("saul", "Saul Goodman", "Bob Odenkirk", 0.91, status: "alive", arcType: "deception",
                           first: (2, 8), last: (4, 3),
                           knows: "Walter's criminal lawyer. Works with Mike and Jesse, and knows more than he lets on.",
                           arc: "Laundering Walt's money through a chain of nail salons while staying one step ahead of the DEA.",
                           scene: "In his office, rehearsing a pitch for a new client while Huell watches the door.",
                           alsoIn: ["Better Call Saul (2015)", "Little Miss Sunshine (2006)", "Nebraska (2013)", "Fargo (2014)"])
    static let skyler = card("skyler", "Skyler White", "Anna Gunn", 0.74, status: "alive", arcType: "conflict",
                             first: (1, 1), last: (4, 2),
                             knows: "Walter's wife. Knows about the business and is hiding it from her sister.",
                             arc: "Looking for a way to explain the money.", scene: "At the car wash, going over the books.",
                             alsoIn: ["Gotham (2014)"])
    static let mike = card("mike", "Mike Ehrmantraut", "Jonathan Banks", 0.58, status: "alive", arcType: "danger",
                           first: (1, 7), last: (4, 3), knows: "Fixer for Gus Fring.",
                           arc: "Watching Walt closely.", scene: "Outside the laundry, in his car.")
    static let limited = card("limited-actor", "Hank Schrader", "Dean Norris", 0.83, limited: true)
    static let longText = card("long", "Dr. Alexandria Montgomery-Featherstonehaugh III", "Maximilian Bartholomew-Hargreaves-Okonkwo",
                               0.67, status: "presumed missing", arcType: "turning_point", first: (1, 1), last: (4, 3),
                               knows: "Sister to the late governor, estranged business partner of the harbour master, and the only person who has read both halves of the missing ledger that three families are looking for.",
                               arc: "Has quietly bought up every warehouse on the waterfront through shell companies, and is now deciding whether to burn the evidence or hand it to the one detective who has never taken a bribe. Time is running out before the audit.",
                               scene: "Alone on the pier at dusk, burning a stack of ledgers one page at a time while a car idles at the far end of the dock with its headlights off.")
}

/// Generated stand-in photos so demos show real palettes without shipping artwork.
enum DemoPhotos {
    static func image(for characterId: String) -> NSImage? {
        let background: (CGFloat, CGFloat, CGFloat)
        switch characterId {
        case "saul": background = (0.80, 0.38, 0.14)
        case "skyler": background = (0.15, 0.40, 0.75)
        case "mike": background = (0.35, 0.55, 0.25)
        case "long": background = (0.50, 0.25, 0.65)
        default: return nil   // also exercises the initial-letter fallback
        }
        return NSImage(size: NSSize(width: 160, height: 200), flipped: false) { rect in
            NSColor(red: background.0, green: background.1, blue: background.2, alpha: 1).setFill()
            rect.fill()
            NSColor(white: 0.12, alpha: 0.85).setFill()
            NSBezierPath(ovalIn: NSRect(x: 50, y: 95, width: 60, height: 70)).fill()
            NSBezierPath(ovalIn: NSRect(x: 15, y: -70, width: 130, height: 150)).fill()
            return true
        }
    }
}
