import Foundation

/// The set of faces found in one screenshot, best match on top. Pure value type: the views
/// render it, `PopupModel` mutates it. Cards are sorted by confidence (descending, ties keep
/// the engine's order) and de-duplicated by `characterId` (the highest confidence wins).
struct CardStack: Equatable {
    private(set) var cards: [CharacterCard]
    private(set) var topIndex: Int = 0
    /// True while the top card shows its back (story notes). Reset whenever the top card changes.
    private(set) var isFlipped: Bool = false

    /// Returns nil for an empty list: there is no "top" card to show, so callers must
    /// present a message instead.
    init?(cards: [CharacterCard]) {
        var best: [String: (index: Int, card: CharacterCard)] = [:]
        for (index, card) in cards.enumerated() {
            if let existing = best[card.characterId], existing.card.confidence >= card.confidence {
                continue
            }
            // Keep the first position of this id so ties stay in the engine's order.
            best[card.characterId] = (best[card.characterId]?.index ?? index, card)
        }
        let sorted = best.values.sorted {
            if $0.card.confidence != $1.card.confidence { return $0.card.confidence > $1.card.confidence }
            return $0.index < $1.index
        }.map(\.card)
        guard !sorted.isEmpty else { return nil }
        self.cards = sorted
    }

    var count: Int { cards.count }
    var top: CharacterCard { cards[topIndex] }

    /// Cards behind the top one, in the order they will come up with `next()`.
    var behind: [CharacterCard] {
        guard count > 1 else { return [] }
        return (1..<count).map { cards[(topIndex + $0) % count] }
    }

    mutating func flip() { isFlipped.toggle() }

    mutating func next() {
        guard count > 1 else { return }
        topIndex = (topIndex + 1) % count
        isFlipped = false
    }

    mutating func previous() {
        guard count > 1 else { return }
        topIndex = (topIndex - 1 + count) % count
        isFlipped = false
    }
}
