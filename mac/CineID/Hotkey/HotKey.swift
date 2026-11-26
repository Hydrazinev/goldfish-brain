import Carbon.HIToolbox
import Foundation

struct HotKeyCombo: Equatable, Hashable {
    var keyCode: UInt32
    /// Carbon modifier mask (`cmdKey`, `shiftKey`, …), not NSEvent flags.
    var modifiers: UInt32

    /// ⌘⇧X: identify who is on screen.
    static let identify = HotKeyCombo(keyCode: UInt32(kVK_ANSI_X), modifiers: UInt32(cmdKey | shiftKey))
    /// ⌘⇧E: ask a question about what is on screen.
    static let ask = HotKeyCombo(keyCode: UInt32(kVK_ANSI_E), modifiers: UInt32(cmdKey | shiftKey))
    /// Esc on its own. Registered only while a card is on screen (see `DismissWatcher`).
    static let escape = HotKeyCombo(keyCode: UInt32(kVK_Escape), modifiers: 0)
}

enum HotKeyError: Error, Equatable {
    /// Someone (this app or another) already holds this combination.
    case alreadyRegistered
    /// Carbon refused for another reason; carries its OSStatus for logs only.
    case registrationFailed(Int32)
}

/// The system seam. Contract: `register` throws `.alreadyRegistered` when the combo is held;
/// the returned token is later passed to `unregister`.
protocol HotKeyBackend: AnyObject {
    func register(_ combo: HotKeyCombo, handler: @escaping () -> Void) throws -> UInt32
    func unregister(_ token: UInt32)
}

/// A global hotkey. Registered while this object lives; released on deinit.
/// Uses Carbon `RegisterEventHotKey`, which needs no Accessibility or Input Monitoring permission.
final class HotKey {
    private let backend: HotKeyBackend
    private let token: UInt32

    init(combo: HotKeyCombo, backend: HotKeyBackend = CarbonHotKeyBackend.shared,
         handler: @escaping () -> Void) throws {
        self.backend = backend
        self.token = try backend.register(combo, handler: handler)
    }

    deinit { backend.unregister(token) }
}

/// Carbon implementation. Handlers run on the main thread (application event target).
final class CarbonHotKeyBackend: HotKeyBackend {
    static let shared = CarbonHotKeyBackend()

    private struct Entry {
        let combo: HotKeyCombo
        let ref: EventHotKeyRef
        let handler: () -> Void
    }

    /// 'CNID'
    private static let signature: OSType = 0x434E_4944
    private static var counter: UInt32 = 0

    private var entries: [UInt32: Entry] = [:]
    private var eventHandler: EventHandlerRef?
    /// The id handed out by the most recent successful `register` (tests use it to simulate a press).
    private(set) var lastRegisteredID: UInt32 = 0

    init() {}

    deinit {
        for entry in entries.values { UnregisterEventHotKey(entry.ref) }
        if let eventHandler { RemoveEventHandler(eventHandler) }
    }

    func register(_ combo: HotKeyCombo, handler: @escaping () -> Void) throws -> UInt32 {
        if entries.values.contains(where: { $0.combo == combo }) { throw HotKeyError.alreadyRegistered }
        try installEventHandlerIfNeeded()

        Self.counter += 1
        let id = Self.counter
        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(combo.keyCode, combo.modifiers,
                                         EventHotKeyID(signature: Self.signature, id: id),
                                         GetApplicationEventTarget(), 0, &ref)
        if status == OSStatus(eventHotKeyExistsErr) { throw HotKeyError.alreadyRegistered }
        guard status == noErr, let ref else { throw HotKeyError.registrationFailed(status) }

        entries[id] = Entry(combo: combo, ref: ref, handler: handler)
        lastRegisteredID = id
        return id
    }

    func unregister(_ token: UInt32) {
        guard let entry = entries.removeValue(forKey: token) else { return }
        UnregisterEventHotKey(entry.ref)
    }

    /// Runs the handler for a pressed hotkey id. Returns false when the id is not ours.
    @discardableResult
    func dispatch(id: UInt32) -> Bool {
        guard let entry = entries[id] else { return false }
        entry.handler()
        return true
    }

    private func installEventHandlerIfNeeded() throws {
        guard eventHandler == nil else { return }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return OSStatus(eventNotHandledErr) }
            var hotKeyID = EventHotKeyID()
            let got = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                        nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            guard got == noErr, hotKeyID.signature == CarbonHotKeyBackend.signature else {
                return OSStatus(eventNotHandledErr)
            }
            let backend = Unmanaged<CarbonHotKeyBackend>.fromOpaque(userData).takeUnretainedValue()
            return backend.dispatch(id: hotKeyID.id) ? noErr : OSStatus(eventNotHandledErr)
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &eventHandler)
        guard status == noErr else { throw HotKeyError.registrationFailed(status) }
    }
}
