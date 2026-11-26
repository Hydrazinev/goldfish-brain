import Foundation

/// Turns the engine's one-line relationship summary ("Wife of X; sister of Y") into separate,
/// tidy lines for the card ("Wife of X", "Sister of Y").
enum RelationshipLines {
    static func parse(_ summary: String?, limit: Int = 4) -> [String] {
        guard let summary else { return [] }
        let parts = summary.components(separatedBy: CharacterSet(charactersIn: ";\n•"))
        var seen = Set<String>()
        var lines: [String] = []
        for part in parts {
            var line = part.trimmingCharacters(in: .whitespacesAndNewlines)
            // "Walter Jr." keeps its dot; only a sentence-final dot after a long-enough word goes.
            if line.hasSuffix("."), let last = line.split(separator: " ").last, last.count > 4 { line.removeLast() }
            guard let first = line.first else { continue }
            line = first.uppercased() + line.dropFirst()
            guard seen.insert(line.lowercased()).inserted else { continue }
            lines.append(line)
            if lines.count == limit { break }
        }
        return lines
    }
}
