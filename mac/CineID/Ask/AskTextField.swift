import SwiftUI
import AppKit

/// One-line text field for the question. Enter sends, Esc cancels, ↑/↓ recall earlier questions.
/// Takes keyboard focus as soon as it is in a key window (the Ask panel makes itself key without
/// activating the app).
struct AskTextField: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var onSubmit: () -> Void
    var onCancel: () -> Void
    var onUp: () -> Void
    var onDown: () -> Void

    func makeNSView(context: Context) -> NSTextField {
        let field = NSTextField()
        field.delegate = context.coordinator
        field.isBordered = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.font = NSFont(name: "Courier New", size: 14) ?? .monospacedSystemFont(ofSize: 14, weight: .regular)
        field.textColor = NSColor(srgbRed: 0x2B / 255, green: 0x22 / 255, blue: 0x18 / 255, alpha: 1)
        field.placeholderAttributedString = NSAttributedString(string: placeholder, attributes: [
            .font: field.font as Any,
            .foregroundColor: NSColor(srgbRed: 0x6B / 255, green: 0x5A / 255, blue: 0x3C / 255, alpha: 0.8),
        ])
        field.lineBreakMode = .byTruncatingHead
        field.cell?.usesSingleLineMode = true
        field.cell?.isScrollable = true
        field.setAccessibilityLabel("Your question")
        context.coordinator.focusSoon(field)
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        context.coordinator.parent = self
        if field.stringValue != text { field.stringValue = text }
        context.coordinator.focusSoon(field)
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: AskTextField
        private var focused = false
        init(_ parent: AskTextField) { self.parent = parent }

        /// Retries a few times: the window may not be key on the first layout pass.
        func focusSoon(_ field: NSTextField, attempt: Int = 0) {
            guard !focused, attempt < 8 else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + (attempt == 0 ? 0 : 0.05)) { [weak self, weak field] in
                guard let self, let field, !self.focused else { return }
                if let window = field.window, window.isKeyWindow, window.makeFirstResponder(field) {
                    self.focused = true
                } else {
                    self.focusSoon(field, attempt: attempt + 1)
                }
            }
        }

        func controlTextDidChange(_ obj: Notification) {
            guard let field = obj.object as? NSTextField else { return }
            parent.text = field.stringValue
        }

        func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            switch selector {
            case #selector(NSResponder.insertNewline(_:)): parent.onSubmit(); return true
            case #selector(NSResponder.cancelOperation(_:)): parent.onCancel(); return true
            case #selector(NSResponder.moveUp(_:)): parent.onUp(); return true
            case #selector(NSResponder.moveDown(_:)): parent.onDown(); return true
            default: return false
            }
        }
    }
}
