import SwiftUI
import AppKit

/// Front of a character card (dossier page): polaroid, name, "played by", status pill, red "seen" stamp and match bar.
/// Pure rendering of a `CharacterCard`; every action comes in as a closure.
struct DossierFrontView: View {
    static let limitedHint = "Story notes aren't available for this show yet."

    let card: CharacterCard
    let photo: NSImage?
    let palette: ArtworkPalette
    let stampText: String?
    let stack: StackPosition?
    /// "Spoiler-safe up to S3E7" (nil when the viewing position is not known).
    var safeCaption: String? = nil
    var feedbackThanks = false
    var onWrong: () -> Void = {}
    var onMissed: () -> Void = {}
    let onNext: () -> Void
    let onFlip: () -> Void

    private var isLimited: Bool { card.limited == true }
    private var percent: Int { Int((min(max(card.confidence, 0), 1) * 100).rounded()) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            DossierHeader(title: "Character file", stack: stack, onNext: onNext)
            DossierRule()

            VStack(spacing: 12) {
                DossierPhoto(name: card.name, photo: photo, palette: palette)
                    .frame(maxWidth: .infinity)
                    .overlay(alignment: .topTrailing) {
                        if !isLimited, let stampText {
                            DossierStamp(text: stampText).offset(x: 6, y: 16)
                        }
                    }
                    .padding(.top, 6)

                VStack(spacing: 5) {
                    Text(card.name)
                        .font(DossierTheme.serif(25))
                        .foregroundStyle(DossierTheme.ink)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                    if let actor = card.actor, !actor.isEmpty {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            DossierLabel("Played by")
                            Text(actor)
                                .font(DossierTheme.typewriter(13, .bold))
                                .foregroundStyle(DossierTheme.ink)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .frame(maxWidth: .infinity)

                if isLimited {
                    Text(Self.limitedHint)
                        .font(DossierTheme.typewriter(12.5))
                        .foregroundStyle(DossierTheme.ink)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    if let status = card.status, !status.isEmpty {
                        DossierPill(text: status, palette: palette)
                    }
                    DossierMatchBar(percent: percent)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { if !isLimited { onFlip() } }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityDescription)
            .accessibilityAddTraits(isLimited ? [] : .isButton)
            .accessibilityHint(isLimited ? "" : "Turns the card over to show story notes")
            .accessibilityAction { if !isLimited { onFlip() } }

            HStack(alignment: .firstTextBaseline) {
                if let safeCaption { DossierSafeCaption(text: safeCaption) }
                Spacer(minLength: 8)
                if !isLimited {
                    DossierLabel("Click to turn over").accessibilityHidden(true)
                }
            }
            DossierFeedbackRow(thanks: feedbackThanks, onWrong: onWrong, onMissed: onMissed)
        }
        .padding(.init(top: 18, leading: 18, bottom: 14, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
    }

    private var accessibilityDescription: String {
        var parts = [card.name]
        if let actor = card.actor, !actor.isEmpty { parts.append("played by \(actor)") }
        if isLimited { parts.append(Self.limitedHint) } else {
            parts.append("\(percent) percent match")
            if let status = card.status, !status.isEmpty { parts.append("status \(status)") }
            if let stampText { parts.append(stampText.lowercased()) }
        }
        return parts.joined(separator: ", ")
    }
}
