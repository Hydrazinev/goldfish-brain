import SwiftUI

/// The Ask card, in the same dossier paper as the identify card.
struct AskCardView: View {
    @ObservedObject var model: AskModel
    var onSubmit: () -> Void = {}
    var onCancel: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let placeholder = "Ask about this scene…"
    static let thinkingText = "Thinking…"

    var body: some View {
        Group {
            switch model.phase {
            case .hidden:
                EmptyView()
            case .input:
                inputCard
            case .thinking(let question):
                thinkingCard(question)
            case .reacting:
                reactingCard
            case .answer(let answer):
                answerCard(answer)
            case .message(let text):
                DossierNoteView(content: .init(label: "Heads up", text: text, symbol: "exclamationmark.triangle.fill"),
                                palette: .fallback)
            }
        }
        // No tilt while typing: a rotated text field misplaces the caret.
        .rotationEffect(.degrees(model.phase == .input || model.phase == .hidden ? 0 : DossierLayout.frontTilt))
        .padding(AskLayout.margin)
    }

    private var inputCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            DossierLabel("Ask about this scene")
            DossierRule()
            HStack(spacing: 8) {
                Image(systemName: "questionmark.bubble").font(.system(size: 15)).foregroundStyle(DossierTheme.label)
                    .accessibilityHidden(true)
                AskTextField(text: $model.draft, placeholder: Self.placeholder, onSubmit: onSubmit, onCancel: onCancel,
                             onUp: model.recallOlder, onDown: model.recallNewer)
                    .frame(height: 22)
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 3).fill(DossierTheme.polaroid))
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(DossierTheme.rule, lineWidth: 1))
            HStack {
                DossierSafeCaption(text: "↩ send   esc close   ↑ last question")
                Spacer()
            }
        }
        .padding(.init(top: 18, leading: 18, bottom: 14, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
    }

    private func thinkingCard(_ question: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                DossierLabel("You asked")
                Spacer(minLength: 4)
                WorkingDots()
            }
            questionText(question)
            DossierRule()
            Text(Self.thinkingText)
                .font(DossierTheme.serif(18, .semibold))
                .foregroundStyle(DossierTheme.ink)
        }
        .padding(.init(top: 18, leading: 18, bottom: 18, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
        .accessibilityElement(children: .combine)
    }

    private var reactingCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                DossierLabel(AskModel.defaultBuddyName)
                Spacer(minLength: 4)
                WorkingDots()
            }
            DossierRule()
            Text(Self.thinkingText)
                .font(DossierTheme.serif(18, .semibold))
                .foregroundStyle(DossierTheme.ink)
        }
        .padding(.init(top: 18, leading: 18, bottom: 18, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
        .accessibilityElement(children: .combine)
    }

    private func answerCard(_ answer: AskAnswer) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                DossierLabel(answer.buddyName ?? (answer.refused ? "Holding back" : "Answer"))
                Spacer(minLength: 4)
                if answer.refused { DossierStamp(text: "NO SPOILERS") }
            }
            if answer.buddyName == nil { questionText(answer.question) }
            DossierRule()
            Text(answer.text)
                .font(DossierTheme.typewriter(13))
                .lineSpacing(3)
                .foregroundStyle(DossierTheme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                if let safe = answer.safeUpTo { DossierSafeCaption(text: "Spoiler-safe up to \(safe)") }
                Spacer()
            }
        }
        .padding(.init(top: 18, leading: 18, bottom: 14, trailing: 18))
        .frame(width: DossierLayout.cardWidth)
        .background(DossierPaper())
        .accessibilityElement(children: .combine)
    }

    private func questionText(_ question: String) -> some View {
        Text("“\(question)”")
            .font(DossierTheme.serif(14, .regular))
            .italic()
            .foregroundStyle(DossierTheme.label)
            .lineLimit(3)
            .fixedSize(horizontal: false, vertical: true)
    }
}

enum AskLayout {
    /// Transparent margin around the card for its tilt and shadow (same as the identify popup).
    static let margin: CGFloat = 32
}
