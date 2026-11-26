import SwiftUI

/// Back of a character card: story notes, then the quiet "Wrong?" row.
struct DossierBackView: View {
    let card: CharacterCard
    let palette: ArtworkPalette
    let stack: StackPosition?
    var feedbackThanks = false
    var onWrong: () -> Void = {}
    var onMissed: () -> Void = {}
    let onNext: () -> Void
    let onFlip: () -> Void

    private func clean(_ text: String?) -> String? {
        guard let t = text?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        return t
    }

    private var arcTypeLabel: String? {
        clean(card.arcType)?.replacingOccurrences(of: "_", with: " ")
    }

    private var lastSeen: String? {
        guard let s = card.lastSeenSeason, let e = card.lastSeenEpisode else { return nil }
        return ViewingPosition.code(s, e)
    }

    private var alsoIn: [String] {
        (card.alsoIn ?? []).compactMap { clean($0) }
    }

    private var firstSeen: String? {
        guard let s = card.firstSeenSeason, let e = card.firstSeenEpisode else { return nil }
        return ViewingPosition.code(s, e)
    }

    /// Without playback progress the engine describes the start of the episode, not "right now".
    static func sectionTitle(asOf: String?) -> String { asOf == "start" ? "As of start of episode" : "Right now" }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            DossierHeader(title: "File notes", stack: stack, onNext: onNext)

            VStack(alignment: .leading, spacing: 14) {
                Text(card.name)
                    .font(DossierTheme.serif(20))
                    .foregroundStyle(DossierTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                DossierRule()

                if let arc = clean(card.currentArc) {
                    DossierSection(title: Self.sectionTitle(asOf: card.asOf), text: arc, accent: palette.accent) {
                        if let arcTypeLabel { DossierPill(text: arcTypeLabel, palette: palette) }
                    }
                    DossierRule()
                }
                if let scene = clean(card.lastSeenDescription) {
                    DossierSection(title: "Last scene", text: scene, accent: nil) {
                        if let lastSeen { DossierTag(text: lastSeen) }
                    }
                    DossierRule()
                }
                if case let knows = RelationshipLines.parse(card.relationshipSummary), !knows.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        DossierLabel("Knows")
                        ForEach(knows, id: \.self) { line in
                            HStack(alignment: .firstTextBaseline, spacing: 7) {
                                Circle().fill(DossierTheme.stampRed).frame(width: 5, height: 5)
                                Text(line)
                                    .font(DossierTheme.typewriter(12.5))
                                    .foregroundStyle(DossierTheme.ink)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    .accessibilityElement(children: .combine)
                    DossierRule()
                }
                if !alsoIn.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        DossierLabel("Also in")
                        ForEach(alsoIn, id: \.self) { title in
                            HStack(alignment: .firstTextBaseline, spacing: 7) {
                                Circle().fill(DossierTheme.stampRed).frame(width: 5, height: 5)
                                Text(title)
                                    .font(DossierTheme.typewriter(12.5))
                                    .foregroundStyle(DossierTheme.ink)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    .accessibilityElement(children: .combine)
                    DossierRule()
                }
                if let firstSeen {
                    HStack(spacing: 8) {
                        DossierLabel("First seen")
                        DossierTag(text: firstSeen)
                    }
                    .accessibilityElement(children: .combine)
                }
                if !hasAnyNotes {
                    Text("No story notes for this moment yet.")
                        .font(DossierTheme.typewriter(12.5))
                        .foregroundStyle(DossierTheme.label)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture(perform: onFlip)
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("Turns the card back over")
            .accessibilityAction(named: "Turn over", onFlip)

            DossierFeedbackRow(thanks: feedbackThanks, onWrong: onWrong, onMissed: onMissed)
        }
        .padding(.init(top: 18, leading: 18, bottom: 14, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
    }

    private var hasAnyNotes: Bool {
        clean(card.currentArc) != nil || clean(card.lastSeenDescription) != nil
            || clean(card.relationshipSummary) != nil || firstSeen != nil
    }
}
