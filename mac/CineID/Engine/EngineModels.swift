import Foundation

/// One recognised character, as returned by the engine's `POST /identify`.
/// Story fields are null when the spoiler shield or a "limited" result withholds them.
struct CharacterCard: Decodable, Identifiable, Equatable {
    let characterId: String
    let name: String
    let actor: String?
    let role: String?
    let confidence: Double
    let status: String?
    let arcType: String?
    let firstSeenSeason: Int?
    let firstSeenEpisode: Int?
    let lastSeenSeason: Int?
    let lastSeenEpisode: Int?
    let relationshipSummary: String?
    let currentArc: String?
    let lastSeenDescription: String?
    let importanceRank: Int?
    /// True when the show has no character notes yet: identify only, show no story.
    let limited: Bool?
    /// "start" when the story text describes the start of this episode (no playback progress was sent).
    var asOf: String? = nil
    /// The actor's other well-known work ("Better Call Saul (2015)"); empty or absent until the engine has cached it.
    var alsoIn: [String]? = nil

    var id: String { characterId }
}

struct IdentifyResponse: Decodable, Equatable {
    let characters: [CharacterCard]
    let totalFacesDetected: Int
    let showId: String
    /// Present (true) when faces were found but none matched confidently.
    let lowConfidence: Bool?
    /// Present when recognised characters were withheld to avoid spoilers.
    let hiddenBySpoilerShield: Int?
}

/// `GET /api/state`, trimmed to what the Mac app uses. Unknown keys are ignored.
struct EngineState: Decodable, Equatable {
    struct Setup: Decodable, Equatable {
        let tmdbKey: Bool
        let anthropicKey: Bool
    }
    struct CurrentShow: Decodable, Equatable {
        let showId: String?
        let showName: String?
        let season: Int
        let episode: Int
    }
    struct Show: Decodable, Equatable {
        let showId: String
        let showName: String
        let totalCharacters: Int?
        let posterUrl: String?
        let season: Int?
        let episode: Int?
        let current: Bool?
    }

    let setup: Setup
    let current: CurrentShow
    let shows: [Show]
    let needsFirstRun: Bool
    /// Settings → Watch Buddy "share only my Chrome window"; nil (older engine) means yes.
    var shareOnlyWindow: Bool? = nil
}

enum EngineError: Error, Equatable {
    /// Connection refused, timed out or dropped: the engine is not answering.
    case notReachable
    /// The engine accepted the connection but did not answer in time (e.g. a cold model). Not the same as down.
    case timedOut
    /// The engine answered with a non-2xx status.
    case http(Int)
    /// The engine answered 2xx but the body was not the expected shape.
    case decoding
}
