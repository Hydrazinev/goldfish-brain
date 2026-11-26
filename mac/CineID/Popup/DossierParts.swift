import SwiftUI
import AppKit

/// Where in the show the viewer is; printed on the red stamp.
struct ViewingPosition: Equatable {
    let season: Int
    let episode: Int
    var stampText: String { "SEEN " + Self.code(season, episode) }
    /// "Spoiler-safe up to S3E7"
    var safeCaption: String { Self.safeCaption(season, episode) }

    static func shortCode(_ season: Int, _ episode: Int) -> String { "S\(season)E\(episode)" }
    static func safeCaption(_ season: Int, _ episode: Int) -> String { "Spoiler-safe up to " + shortCode(season, episode) }

    static func code(_ season: Int, _ episode: Int) -> String {
        String(format: "S%02dE%02d", season, episode)
    }
}

/// "1 of 3" for a stack of faces.
struct StackPosition: Equatable {
    let index: Int
    let count: Int
}

enum DossierLayout {
    static let cardWidth: CGFloat = 340
    static let cornerRadius: CGFloat = 6
    /// Side of the square picture inside the polaroid.
    static let photoSide: CGFloat = 176
    /// Slight tilts of the two faces, like paper lying on a desk.
    static let frontTilt: Double = -1.5
    static let backTilt: Double = 1.2
}

/// Small letterspaced typewriter caps, used for every section header.
struct DossierLabel: View {
    let text: String
    var color: Color = DossierTheme.label
    init(_ text: String, color: Color = DossierTheme.label) { self.text = text; self.color = color }

    var body: some View {
        Text(text.uppercased())
            .font(DossierTheme.label(10.5))
            .tracking(1.5)
            .foregroundStyle(color)
            .lineLimit(1)
            .accessibilityAddTraits(.isHeader)
    }
}

/// The card: warm cream paper with a hairline edge and a soft shadow.
struct DossierPaper: View {
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: DossierLayout.cornerRadius, style: .continuous)
        shape
            .fill(DossierTheme.paper)
            .overlay(shape.strokeBorder(Color.black.opacity(0.08), lineWidth: 1))
            .shadow(color: .black.opacity(0.45), radius: 14, x: 0, y: 10)
            .shadow(color: .black.opacity(0.25), radius: 3, x: 0, y: 1)
    }
}

/// A dashed typewriter-paper rule.
struct DossierRule: View {
    var body: some View {
        Rectangle()
            .stroke(DossierTheme.rule, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
            .frame(height: 1)
            .accessibilityHidden(true)
    }
}

/// Header line shared by both faces: a label on the left, the "1 of N" chip on the right.
struct DossierHeader: View {
    let title: String
    let stack: StackPosition?
    let onNext: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            DossierLabel(title)
            Spacer(minLength: 4)
            if let stack, stack.count > 1 {
                Button(action: onNext) {
                    Text("\(stack.index) of \(stack.count)")
                        .font(DossierTheme.label(10.5))
                        .tracking(0.6)
                        .foregroundStyle(DossierTheme.ink)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 3)
                        .background(RoundedRectangle(cornerRadius: 3).fill(DossierTheme.folder))
                        .overlay(RoundedRectangle(cornerRadius: 3).stroke(DossierTheme.rule, lineWidth: 1))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Showing person \(stack.index) of \(stack.count)")
                .accessibilityHint("Shows the next person found in the screenshot")
            }
        }
        // Leave room for the close button that sits in the top-right corner of the card.
        .padding(.trailing, 26)
    }
}

/// Small pill, filled with the photo's accent.
struct DossierPill: View {
    let text: String
    let palette: ArtworkPalette

    var body: some View {
        Text(text.uppercased())
            .font(DossierTheme.label(10))
            .tracking(1.0)
            .foregroundStyle(palette.onAccent)
            .lineLimit(1)
            .padding(.horizontal, 10)
            .padding(.vertical, 3)
            .background(Capsule().fill(palette.accent))
    }
}

/// A small ink-on-folder tag, e.g. the episode a scene happened in ("S04E03").
struct DossierTag: View {
    let text: String

    var body: some View {
        Text(text)
            .font(DossierTheme.label(10))
            .tracking(1.0)
            .foregroundStyle(DossierTheme.ink)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 3).fill(DossierTheme.folder))
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(DossierTheme.rule, lineWidth: 1))
    }
}

/// "Spoiler-safe up to S3E7": a tiny ink-grey typewriter caption in a corner of a card.
struct DossierSafeCaption: View {
    let text: String
    var body: some View {
        Text(text)
            .font(DossierTheme.typewriter(9.5))
            .foregroundStyle(DossierTheme.label)
            .lineLimit(1)
            .accessibilityLabel(text)
    }
}

/// "Wrong?" and "Missed someone" as quiet text buttons; for a moment after a tap it says "Thanks — noted".
struct DossierFeedbackRow: View {
    static let thanksText = "Thanks — noted"
    let thanks: Bool
    let onWrong: () -> Void
    let onMissed: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            if thanks {
                Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(DossierTheme.stampRed)
                Text(Self.thanksText).font(DossierTheme.typewriter(11)).foregroundStyle(DossierTheme.ink)
            } else {
                link("Wrong?", color: DossierTheme.ink, hint: "Tell Goldfish Brain this match is wrong", action: onWrong)
                Text("·").font(DossierTheme.typewriter(11)).foregroundStyle(DossierTheme.rule).accessibilityHidden(true)
                link("Missed someone", color: DossierTheme.label, hint: "Tell Goldfish Brain a face got no card", action: onMissed)
            }
            Spacer(minLength: 0)
        }
        .frame(height: 16)
    }

    private func link(_ title: String, color: Color, hint: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(DossierTheme.typewriter(11))
                .underline()
                .foregroundStyle(color)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(hint)
    }
}

/// "SEEN S04E04": a red rubber stamp with a bordered box, tilted.
struct DossierStamp: View {
    let text: String
    var angle: Double = -5

    var body: some View {
        Text(text)
            .font(DossierTheme.label(11))
            .tracking(1.4)
            .foregroundStyle(DossierTheme.stampRed)
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(DossierTheme.paper.opacity(0.8))
            .overlay(Rectangle().stroke(DossierTheme.stampRed, lineWidth: 2))
            .rotationEffect(.degrees(angle))
            .accessibilityLabel(text.capitalized)
    }
}

/// The match as a bar (rust-red fill on a paper-coloured track) with the percentage beside it.
struct DossierMatchBar: View {
    let percent: Int

    var body: some View {
        HStack(spacing: 10) {
            DossierLabel("Match")
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(DossierTheme.track)
                    Capsule().fill(DossierTheme.stampRed)
                        .frame(width: max(7, geo.size.width * CGFloat(min(max(percent, 0), 100)) / 100))
                }
            }
            .frame(height: 7)
            Text("\(percent)%")
                .font(DossierTheme.typewriter(13, .bold))
                .monospacedDigit()
                .foregroundStyle(DossierTheme.ink)
                .accessibilityLabel("\(percent) percent match")
        }
        .accessibilityElement(children: .combine)
    }
}

/// Polaroid: the actor's photo (or the character's initial on the accent) in a white frame with a
/// thicker bottom edge, a slight tilt, and a strip of tape in the photo's accent colour.
struct DossierPhoto: View {
    let name: String
    let photo: NSImage?
    let palette: ArtworkPalette
    var side: CGFloat = DossierLayout.photoSide
    var tilt: Double = 2

    var body: some View {
        let border: CGFloat = 9
        VStack(spacing: 0) {
            picture
                .frame(width: side, height: side)
                .clipShape(RoundedRectangle(cornerRadius: 2))
                .overlay(RoundedRectangle(cornerRadius: 2).stroke(Color.black.opacity(0.12), lineWidth: 1))
            Color.clear.frame(width: side, height: 26)
        }
        .padding(.init(top: border, leading: border, bottom: 0, trailing: border))
        .background(Rectangle().fill(DossierTheme.polaroid))
        .overlay(Rectangle().stroke(Color.black.opacity(0.08), lineWidth: 1))
        .shadow(color: .black.opacity(0.28), radius: 6, x: 0, y: 4)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(palette.accent.opacity(0.75))
                .frame(width: 58, height: 16)
                .rotationEffect(.degrees(-5))
                .offset(y: -8)
        }
        .rotationEffect(.degrees(tilt))
        .accessibilityHidden(true)
    }

    @ViewBuilder private var picture: some View {
        if let photo {
            Image(nsImage: photo).resizable().scaledToFill()
        } else {
            ZStack {
                LinearGradient(colors: [palette.accent, palette.accentRGB.darkened(0.5).color],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                Text(String(name.trimmingCharacters(in: .whitespaces).prefix(1)).uppercased())
                    .font(DossierTheme.serif(side * 0.4))
                    .foregroundStyle(.white)
            }
        }
    }
}

/// Section on the back of a card: label, then text.
struct DossierSection<Trailing: View>: View {
    let title: String
    let text: String
    let accent: Color?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if let accent {
                Capsule().fill(accent).frame(width: 3)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    DossierLabel(title)
                    trailing()
                }
                Text(text)
                    .font(DossierTheme.typewriter(12.5))
                    .lineSpacing(2)
                    .foregroundStyle(DossierTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Flips between two faces with a 3D turn. The angle is the animated value, so the face that is
/// showing switches at the half-way point of the turn rather than at the start.
struct FlipCard<Front: View, Back: View>: View, Animatable {
    var angle: Double
    let front: Front
    let back: Back

    var animatableData: Double {
        get { angle }
        set { angle = newValue }
    }

    var body: some View {
        // Only the face that is showing takes part in layout, so each face keeps its own height.
        Group {
            if angle < 90 {
                front
            } else {
                back.rotation3DEffect(.degrees(180), axis: (x: 0, y: 1, z: 0))
            }
        }
        .rotation3DEffect(.degrees(angle), axis: (x: 0, y: 1, z: 0), perspective: 0.35)
    }
}
