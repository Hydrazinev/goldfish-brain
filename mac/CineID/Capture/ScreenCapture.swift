import AppKit
import CoreGraphics
import ScreenCaptureKit

protocol ScreenCapturing {
    /// True when Screen Recording is allowed. Never shows a prompt.
    func hasPermission() -> Bool
    /// Asks macOS to show its Screen Recording prompt (and list the app in Settings). Returns whether access is granted now.
    @discardableResult func requestPermission() -> Bool
    func capture() async throws -> CGImage
}

enum CaptureError: Error, Equatable {
    /// Screen Recording is off (or was declined) for Goldfish Brain.
    case noPermission
    case noDisplay
    case failed
}

/// Captures the display that shows Chrome (else the main display) with ScreenCaptureKit,
/// leaving Goldfish Brain's own windows (the popup) out of the picture.
final class ScreenCaptureKitCapture: ScreenCapturing {
    static let settingsURL = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!

    func hasPermission() -> Bool { CGPreflightScreenCaptureAccess() }

    /// Asks macOS to show its Screen Recording prompt (first time only). Returns whether access is granted now.
    @discardableResult
    func requestPermission() -> Bool { CGRequestScreenCaptureAccess() }

    static func openScreenRecordingSettings() { NSWorkspace.shared.open(settingsURL) }

    func capture() async throws -> CGImage { try await captureDisplayContainingChrome() }

    /// The display showing the front-most Chrome window, else the main display.
    func captureDisplayContainingChrome() async throws -> CGImage {
        guard hasPermission() else { throw CaptureError.noPermission }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let id = Self.chooseDisplay(preferred: ChromeDisplay.displayIDShowingChrome(),
                                              main: CGMainDisplayID(), available: content.displays.map(\.displayID)),
                  let display = content.displays.first(where: { $0.displayID == id }) else { throw CaptureError.noDisplay }

            let ownApps = content.applications.filter { $0.bundleIdentifier == Bundle.main.bundleIdentifier }
            let filter = SCContentFilter(display: display, excludingApplications: ownApps, exceptingWindows: [])

            // Ask for at most 1920 px wide up front, so we never allocate a 5K frame.
            let pixelScale = max(1, CGFloat(filter.pointPixelScale))
            let pixels = CGSize(width: CGFloat(display.width) * pixelScale, height: CGFloat(display.height) * pixelScale)
            let size = FrameCheck.targetSize(for: pixels)
            let config = SCStreamConfiguration()
            config.width = Int(size.width)
            config.height = Int(size.height)
            config.showsCursor = false
            return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        } catch let error as CaptureError {
            throw error
        } catch {
            // The user switched the permission off after the preflight check, or declined the prompt.
            if (error as NSError).code == SCStreamError.userDeclined.rawValue { throw CaptureError.noPermission }
            throw CaptureError.failed
        }
    }

    /// Chrome's display when it is among the available ones, else the main display, else any display.
    static func chooseDisplay(preferred: CGDirectDisplayID?, main: CGDirectDisplayID,
                              available: [CGDirectDisplayID]) -> CGDirectDisplayID? {
        if let preferred, available.contains(preferred) { return preferred }
        if available.contains(main) { return main }
        return available.first
    }
}
