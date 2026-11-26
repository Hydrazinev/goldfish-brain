import XCTest
import Carbon.HIToolbox
@testable import CineID

/// Stands in for Carbon. Follows the same contract as `CarbonHotKeyBackend`.
private final class FakeBackend: HotKeyBackend {
    var registered: [UInt32: (combo: HotKeyCombo, handler: () -> Void)] = [:]
    var unregisterCalls = 0
    var nextID: UInt32 = 1
    var failWith: HotKeyError?

    func register(_ combo: HotKeyCombo, handler: @escaping () -> Void) throws -> UInt32 {
        if let failWith { throw failWith }
        if registered.values.contains(where: { $0.combo == combo }) { throw HotKeyError.alreadyRegistered }
        defer { nextID += 1 }
        registered[nextID] = (combo, handler)
        return nextID
    }

    func unregister(_ token: UInt32) {
        unregisterCalls += 1
        registered[token] = nil
    }

    func press(_ combo: HotKeyCombo) { registered.values.filter { $0.combo == combo }.forEach { $0.handler() } }
}

final class HotKeyTests: XCTestCase {
    func testIdentifyComboIsCommandShiftX() {
        XCTAssertEqual(HotKeyCombo.identify.keyCode, UInt32(kVK_ANSI_X))
        XCTAssertEqual(HotKeyCombo.identify.modifiers, UInt32(cmdKey | shiftKey))
    }

    func testRegisteringTheSameComboTwiceThrowsAlreadyRegistered() throws {
        let backend = FakeBackend()
        let first = try HotKey(combo: .identify, backend: backend) {}
        XCTAssertThrowsError(try HotKey(combo: .identify, backend: backend) {}) { error in
            XCTAssertEqual(error as? HotKeyError, .alreadyRegistered)
        }
        XCTAssertEqual(backend.registered.count, 1)
        withExtendedLifetime(first) {}
    }

    func testHandlerRunsWhenTheComboIsPressed() throws {
        let backend = FakeBackend()
        var presses = 0
        let key = try HotKey(combo: .identify, backend: backend) { presses += 1 }
        backend.press(.identify)
        backend.press(.identify)
        XCTAssertEqual(presses, 2)
        withExtendedLifetime(key) {}
    }

    func testDeinitUnregistersSoTheComboCanBeRegisteredAgain() throws {
        let backend = FakeBackend()
        var key: HotKey? = try HotKey(combo: .identify, backend: backend) {}
        XCTAssertEqual(backend.registered.count, 1)
        key = nil
        XCTAssertEqual(backend.unregisterCalls, 1)
        XCTAssertTrue(backend.registered.isEmpty)
        let again = try HotKey(combo: .identify, backend: backend) {}
        withExtendedLifetime((key, again)) {}
    }

    func testFailedRegistrationDoesNotUnregisterAnything() {
        let backend = FakeBackend()
        backend.failWith = .registrationFailed(-1)
        XCTAssertThrowsError(try HotKey(combo: .identify, backend: backend) {}) { error in
            XCTAssertEqual(error as? HotKeyError, .registrationFailed(-1))
        }
        XCTAssertEqual(backend.unregisterCalls, 0)
    }

    // MARK: Real Carbon (registration only; no key press, no event loop needed)

    /// Control-Option-Command-F19: a combo no one presses, so registering it here is harmless.
    private let probe = HotKeyCombo(keyCode: UInt32(kVK_F19), modifiers: UInt32(controlKey | optionKey | cmdKey))

    func testCarbonBackendRegistersRejectsDuplicatesAndReleases() throws {
        let backend = CarbonHotKeyBackend()
        let first = try HotKey(combo: probe, backend: backend) {}
        XCTAssertThrowsError(try HotKey(combo: probe, backend: backend) {}) { error in
            XCTAssertEqual(error as? HotKeyError, .alreadyRegistered)
        }
        withExtendedLifetime(first) {}
    }

    func testSystemRejectsTheSameComboFromAnotherBackendInstance() throws {
        let first = try HotKey(combo: probe, backend: CarbonHotKeyBackend()) {}
        XCTAssertThrowsError(try HotKey(combo: probe, backend: CarbonHotKeyBackend()) {}) { error in
            XCTAssertEqual(error as? HotKeyError, .alreadyRegistered, "OS-level duplicate must map to alreadyRegistered")
        }
        withExtendedLifetime(first) {}
    }

    func testCarbonBackendReleasesOnDeinit() throws {
        let backend = CarbonHotKeyBackend()
        var key: HotKey? = try HotKey(combo: probe, backend: backend) {}
        key = nil
        let again = try HotKey(combo: probe, backend: backend) {}
        withExtendedLifetime((key, again)) {}
    }

    func testCarbonDispatchRunsTheRegisteredHandlerOnly() throws {
        let backend = CarbonHotKeyBackend()
        var fired = 0
        let key = try HotKey(combo: probe, backend: backend) { fired += 1 }
        XCTAssertFalse(backend.dispatch(id: 999_999), "unknown ids are not handled")
        XCTAssertEqual(fired, 0)
        XCTAssertTrue(backend.dispatch(id: backend.lastRegisteredID))
        XCTAssertEqual(fired, 1)
        withExtendedLifetime(key) {}
    }
}
