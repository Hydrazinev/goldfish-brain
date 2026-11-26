import SwiftUI
import AppKit

/// The popup's content. Renders `model.phase` and forwards taps to the model; owns no state.
/// Includes the margin the rotation and shadow need, so the hosting window should fit to it.
struct PopupContentView: View {
    @ObservedObject var model: PopupModel
    let photo: (String) -> NSImage?
    /// Current viewing position for the "seen" pill; when nil the stamp uses the card's last-seen episode.
    var position: ViewingPosition?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(model: PopupModel, photo: @escaping (String) -> NSImage?, position: ViewingPosition? = nil) {
        self.model = model
        self.photo = photo
        self.position = position
    }

    var body: some View {
        switch model.phase {
        case .hidden:
            EmptyView()
        case .identifying:
            framed(DossierNoteView(content: DossierNoteView.identifying, palette: .fallback), tilt: DossierLayout.frontTilt)
        case .message(let message):
            framed(DossierNoteView(content: DossierNoteView.content(for: message), palette: .fallback), tilt: DossierLayout.frontTilt)
        case .cards(let stack):
            framed(cardView(stack), tilt: stack.isFlipped && stack.top.limited != true ? DossierLayout.backTilt : DossierLayout.frontTilt)
        }
    }

    private func framed<V: View>(_ view: V, tilt: Double) -> some View {
        view
            .rotationEffect(.degrees(tilt))
            // The tilt follows the flip, so the card settles at the other angle as it turns over.
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.5), value: tilt)
            .padding(32)
            .onHover { if $0 { model.userInteracted() } }   // hover also pauses the fade (see DismissWatcher)
    }

    private func cardView(_ stack: CardStack) -> some View {
        let card = stack.top
        let image = photo(card.characterId)
        let palette = ArtworkPalette.from(image)
        let chip = stack.count > 1 ? StackPosition(index: stack.topIndex + 1, count: stack.count) : nil
        let stamp = position?.stampText ?? lastSeenStamp(card)
        let canFlip = card.limited != true

        return FlipCard(
            angle: stack.isFlipped && canFlip ? 180 : 0,
            front: DossierFrontView(card: card, photo: image, palette: palette, stampText: stamp, stack: chip,
                                    safeCaption: (model.viewingPosition ?? position)?.safeCaption,
                                    feedbackThanks: model.feedbackThanks,
                                    onWrong: { model.reportFeedback(.wrong) }, onMissed: { model.reportFeedback(.missed) },
                                    onNext: model.next, onFlip: model.flip),
            back: DossierBackView(card: card, palette: palette, stack: chip, feedbackThanks: model.feedbackThanks,
                                  onWrong: { model.reportFeedback(.wrong) }, onMissed: { model.reportFeedback(.missed) },
                                  onNext: model.next, onFlip: model.flip)
        )
        // Reduce Motion: swap faces instantly instead of turning.
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.5), value: stack.isFlipped)
        .id(card.characterId)
    }

    private func lastSeenStamp(_ card: CharacterCard) -> String? {
        guard let s = card.lastSeenSeason, let e = card.lastSeenEpisode else { return nil }
        return ViewingPosition(season: s, episode: e).stampText
    }
}
