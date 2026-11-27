import Foundation

struct Episode: Equatable {
    /// Nil when the source only knew the episode number (the caller fills in the stored season).
    var season: Int?
    var episode: Int
}

/// Pure parsers for what Chrome and its page scripts hand back. Never trusts the text: anything
/// unexpected gives nil so the caller falls through to the next source.
/// What the player's title element shows: season only when Netflix prints it ("S2:E4").
struct VideoTitle: Equatable {
    var season: Int?
    var episode: Int
    var title: String
}

enum ChromeParsers {
    typealias Tab = (url: String, title: String)

    private static let separator = "|||"

    /// "URL|||title" (the title may itself contain the separator). Nil for empty, malformed or `err|||…`.
    static func parseTab(_ raw: String) -> Tab? {
        guard let range = raw.range(of: separator) else { return nil }
        let url = raw[..<range.lowerBound].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, url != "err" else { return nil }
        let title = raw[range.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
        return (url, title)
    }

    /// One "URL|||title" line per Chrome window (front-most first); bad lines are skipped.
    static func parseTabs(_ raw: String) -> [Tab] {
        raw.split(whereSeparator: \.isNewline).compactMap { parseTab(String($0)) }
    }

    /// The Netflix watch tab among the windows' active tabs; the first (front-most) one wins.
    static func netflixWatchTab(in tabs: [Tab]) -> Tab? {
        tabs.first { isNetflixWatch(url: $0.url) }
    }

    /// True only for `http(s)://[www.]netflix.com/watch/<digits>`.
    static func isNetflixWatch(url: String) -> Bool {
        matches(#"^https?://([a-z0-9-]+\.)?netflix\.com/watch/\d+"#, in: url, options: .caseInsensitive) != nil
    }

    /// The `<digits>` of a Netflix `/watch/<digits>` URL, else nil.
    static func watchId(url: String) -> String? {
        matches(#"^https?://([a-z0-9-]+\.)?netflix\.com/watch/(\d+)"#, in: url, options: .caseInsensitive)?[2]
    }

    /// "rc:S2E8" from the page's React context; "rc_models:…", "no_rc|…", "jserr|||…" and others give nil.
    static func parseReactContext(_ raw: String) -> Episode? {
        guard let m = matches(#"^rc:S(\d+)E(\d+)$"#, in: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              let s = Int(m[1]), let e = Int(m[2]) else { return nil }
        return Episode(season: s, episode: e)
    }

    /// True for the script error Chrome gives while "Allow JavaScript from Apple Events" is off.
    static func isJavaScriptDisabledError(_ raw: String) -> Bool {
        guard raw.hasPrefix("jserr") else { return false }
        let lower = raw.lowercased()
        return lower.contains("allow javascript from apple events") || lower.contains("turned off")
    }

    /// The player title's children joined by "|||": "From|||E4|||Title" or "Show|||S2:E4|||Title".
    /// Anything without an episode marker ("NO_TITLE_EL", "Netflix", errors) gives nil.
    static func parseVideoTitle(_ raw: String) -> VideoTitle? {
        let parts = raw.components(separatedBy: separator).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        for (i, part) in parts.enumerated() {
            var season: Int?
            var episode: Int?
            if let m = matches(#"^[Ss](\d{1,3})\s*[:.·•xX]?\s*[Ee](\d{1,3})$"#, in: part) {
                season = Int(m[1]); episode = Int(m[2])
            } else if let m = matches(#"^[Ee](\d{1,3})$"#, in: part) {
                episode = Int(m[1])
            }
            guard let episode else { continue }
            let title = i + 1 < parts.count ? parts[i + 1] : ""
            return VideoTitle(season: season, episode: episode, title: title)
        }
        return nil
    }

    /// The overlay Netflix shows while paused: "Season 1|||A Rock and a Farway: Ep. 4".
    static func parsePauseOverlay(_ raw: String) -> VideoTitle? {
        let parts = raw.components(separatedBy: separator).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard parts.count >= 2, let sm = matches(#"^[Ss]eason\s+(\d{1,3})$"#, in: parts[0]), let season = Int(sm[1]),
              let em = matches(#"^(.*?)[:\s]*Ep\.?\s*(\d{1,3})$"#, in: parts[1]), let episode = Int(em[2]) else { return nil }
        return VideoTitle(season: season, episode: episode, title: em[1].trimmingCharacters(in: .whitespaces))
    }

    /// Player-title text: "Show|||E2|||Title", "ShowE2Title", "S2:E8", "Season 2, Episode 8".
    static func parseTitleParts(_ raw: String) -> Episode? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }

        if let m = matches(#"(?<![A-Za-z])[Ss](\d+)[:\s.·•xX]*[Ee](\d+)"#, in: text), let s = Int(m[1]), let e = Int(m[2]) {
            return Episode(season: s, episode: e)
        }
        if let m = matches(#"[Ss]eason\s+(\d+)[,\s]+[Ee]pisode\s+(\d+)"#, in: text), let s = Int(m[1]), let e = Int(m[2]) {
            return Episode(season: s, episode: e)
        }

        var season: Int?
        var episode: Int?
        for part in text.components(separatedBy: separator).map({ $0.trimmingCharacters(in: .whitespaces) }) {
            if let m = matches(#"^[Ee](\d{1,3})$"#, in: part), let e = Int(m[1]) { episode = e }
            if let m = matches(#"^[Ss]eason\s+(\d+)$"#, in: part), let s = Int(m[1]) { season = s }
        }
        // Concatenated children ("Breaking BadE2Madrigal"): Netflix always writes an uppercase E.
        if episode == nil, let m = matches(#"E(\d{1,2})(?!\d)"#, in: text), let e = Int(m[1]) { episode = e }

        guard let episode else { return nil }
        return Episode(season: season, episode: episode)
    }

    /// First match's capture groups (index 0 is the whole match).
    static func matches(_ pattern: String, in text: String,
                                options: NSRegularExpression.Options = []) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return nil }
        let whole = NSRange(text.startIndex..., in: text)
        guard let match = regex.firstMatch(in: text, range: whole) else { return nil }
        return (0..<match.numberOfRanges).map { i in
            Range(match.range(at: i), in: text).map { String(text[$0]) } ?? ""
        }
    }
}
