import CoreGraphics
import Foundation

/// Picks the one window Ask and React may upload. Pure, so it is tested with fakes.
enum ChromeWindowPicker {
    static let chromeBundleID = "com.google.Chrome"

    struct Candidate: Equatable {
        let windowID: UInt32
        let bundleID: String?
        let layer: Int
        let onScreen: Bool
        let frame: CGRect
    }

    /// The front-most ordinary Google Chrome window; `candidates` are ordered front to back.
    /// Skips other apps, menus/toolbars (layer != 0), minimised or off-screen windows and tiny helper windows.
    static func choose(from candidates: [Candidate]) -> UInt32? {
        candidates.first {
            $0.bundleID == chromeBundleID && $0.layer == 0 && $0.onScreen
                && $0.frame.width >= 200 && $0.frame.height >= 150
        }?.windowID
    }

    /// Live: window number -> position in the on-screen list (0 = front-most). Reads geometry only.
    static func zOrderRank() -> [UInt32: Int] {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return [:] }
        var rank: [UInt32: Int] = [:]
        for (i, info) in list.enumerated() {
            if let n = info["kCGWindowNumber"] as? NSNumber { rank[n.uint32Value] = i }
        }
        return rank
    }
}
