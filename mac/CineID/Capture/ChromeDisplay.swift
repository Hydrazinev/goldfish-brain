import AppKit
import CoreGraphics

/// Finds which display shows Chrome. Used to place the popup and to pick what to capture.
/// Only window geometry is read (no titles), which needs no Screen Recording permission.
enum ChromeDisplay {
    /// Front-most ordinary Chrome window from a `CGWindowListCopyWindowInfo` list (already front to back).
    /// Skips off-screen windows, non-zero layers (menus, toolbars) and tiny helper windows.
    static func frontmostChromeWindowBounds(from windows: [[String: Any]]) -> CGRect? {
        for info in windows {
            guard (info["kCGWindowOwnerName"] as? String) == "Google Chrome",
                  (info["kCGWindowLayer"] as? Int) == 0,
                  (info["kCGWindowIsOnscreen"] as? Bool) ?? true,
                  ((info["kCGWindowAlpha"] as? Double) ?? 1) > 0,
                  let dict = info["kCGWindowBounds"] as? [String: Any],
                  let x = number(dict["X"]), let y = number(dict["Y"]),
                  let w = number(dict["Width"]), let h = number(dict["Height"]),
                  w >= 200, h >= 150
            else { continue }
            return CGRect(x: x, y: y, width: w, height: h)
        }
        return nil
    }

    /// CoreGraphics hands back numbers as NSNumber, but be lenient about the concrete type.
    private static func number(_ value: Any?) -> Double? {
        switch value {
        case let n as NSNumber: return n.doubleValue
        case let d as Double: return d
        case let f as CGFloat: return Double(f)
        case let i as Int: return Double(i)
        default: return nil
        }
    }

    /// The display (by id) that overlaps `bounds` the most; both in CoreGraphics global coordinates.
    static func bestDisplay(for bounds: CGRect, in displays: [(id: CGDirectDisplayID, bounds: CGRect)]) -> CGDirectDisplayID? {
        var best: (id: CGDirectDisplayID, area: CGFloat)?
        for display in displays {
            let overlap = display.bounds.intersection(bounds)
            guard !overlap.isNull else { continue }
            let area = overlap.width * overlap.height
            if area > 0, area > (best?.area ?? 0) { best = (display.id, area) }
        }
        return best?.id
    }

    /// Live: the display showing the front-most Chrome window, or nil when Chrome has no window on screen.
    static func displayIDShowingChrome() -> CGDirectDisplayID? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]],
              let bounds = frontmostChromeWindowBounds(from: list) else { return nil }
        var ids = [CGDirectDisplayID](repeating: 0, count: 16)
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(UInt32(ids.count), &ids, &count) == .success else { return nil }
        let displays = ids.prefix(Int(count)).map { (id: $0, bounds: CGDisplayBounds($0)) }
        return bestDisplay(for: bounds, in: displays)
    }

    /// Live: the NSScreen showing Chrome, else nil (callers fall back to the main screen).
    static func screenShowingChrome() -> NSScreen? {
        guard let id = displayIDShowingChrome() else { return nil }
        return NSScreen.screens.first { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id }
    }
}
