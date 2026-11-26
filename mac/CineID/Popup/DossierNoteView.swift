import SwiftUI

/// A one-note paper card for everything that is not a character: identifying, nobody found,
/// withheld by the spoiler shield, or a failure with its friendly text.
struct DossierNoteView: View {
    struct Content: Equatable {
        let label: String
        let text: String
        var stamp: String? = nil
        var working = false
        var symbol = "person.crop.circle.badge.questionmark"
    }

    let content: Content
    let palette: ArtworkPalette

    static func content(for message: PopupMessage) -> Content {
        switch message {
        case .notRecognised:
            return Content(label: "No match", text: "Face detected — not recognised", symbol: "person.fill.questionmark")
        case .noFace:
            return Content(label: "No face", text: "No face detected — try a clearer scene", symbol: "viewfinder")
        case .hiddenBySpoilerShield:
            return Content(label: "Sealed", text: "Recognised someone you haven't met yet — hidden to avoid spoilers.",
                           stamp: "NO SPOILERS", symbol: "eye.slash.fill")
        case .failure(let failure):
            return Content(label: "Heads up", text: FriendlyError.text(failure), symbol: "exclamationmark.triangle.fill")
        case .notice(let text):
            return Content(label: "Heads up", text: text, symbol: "exclamationmark.triangle.fill")
        }
    }

    static let identifying = Content(label: "One moment", text: "Looking…", working: true, symbol: "faceid")

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().fill(DossierTheme.folder)
                Circle().strokeBorder(DossierTheme.rule, style: StrokeStyle(lineWidth: 1.5, dash: [3, 3]))
                Image(systemName: content.symbol)
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(DossierTheme.ink)
            }
            .frame(width: 52, height: 52)
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    DossierLabel(content.label)
                    Spacer(minLength: 4)
                    if content.working { WorkingDots() }
                    if let stamp = content.stamp { DossierStamp(text: stamp) }
                }
                Text(content.text)
                    .font(DossierTheme.serif(18, .semibold))
                    .foregroundStyle(DossierTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.init(top: 22, leading: 18, bottom: 22, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
        .accessibilityElement(children: .combine)
    }
}

/// Three ink dots that pulse in turn. Pure SwiftUI (no AppKit spinner) so it also renders in snapshots.
struct WorkingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = 0

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { i in
                Circle().fill(DossierTheme.ink)
                    .frame(width: 7, height: 7)
                    .opacity(reduceMotion || phase % 3 == i ? 1 : 0.25)
            }
        }
        .accessibilityHidden(true)
        .task {
            guard !reduceMotion else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 350_000_000)
                phase += 1
            }
        }
    }
}
