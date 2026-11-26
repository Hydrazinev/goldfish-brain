import Foundation

/// Finds "S2:E8" / "Season 2, Episode 8" in on-screen (OCR) text.
enum EpisodeTextParser {
    static func parse(_ text: String) -> Episode? {
        if let m = ChromeParsers.matches(#"(?<![A-Za-z])[Ss](\d+)[:\s.·•×xX]*[Ee](\d+)"#, in: text),
           let s = Int(m[1]), let e = Int(m[2]) {
            return Episode(season: s, episode: e)
        }
        if let m = ChromeParsers.matches(#"[Ss]eason\s+(\d+)[,\s]+[Ee]pisode\s+(\d+)"#, in: text),
           let s = Int(m[1]), let e = Int(m[2]) {
            return Episode(season: s, episode: e)
        }
        return nil
    }
}
