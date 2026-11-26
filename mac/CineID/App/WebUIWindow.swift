import AppKit
import WebKit

/// The engine's web UI (library, add a show, settings) in a plain window.
@MainActor
final class WebUIWindow: NSObject, WKNavigationDelegate {
    nonisolated static let base = "http://127.0.0.1:8000/ui/"

    nonisolated static func url(route: String) -> URL {
        // `base` is a constant, valid URL string.
        URL(string: route.isEmpty ? base : base + "#/" + route) ?? URL(fileURLWithPath: "/")
    }

    private var window: NSWindow?
    private var webView: WKWebView?

    func open(route: String) {
        EditMenu.install()   // ⌘V etc. only reach the web view through a main menu
        let target = Self.url(route: route)
        if let window, let webView {
            // Same page, new route: changing only the hash keeps typed text; otherwise load.
            if webView.url?.absoluteString.hasPrefix(Self.base) == true {
                webView.evaluateJavaScript("location.hash = '#/\(route)'", completionHandler: nil)
            } else {
                webView.load(URLRequest(url: target))
            }
            show(window)
            return
        }
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 980, height: 760), configuration: WKWebViewConfiguration())
        web.navigationDelegate = self
        // The web view kept an old copy of the UI after it was rebuilt: start from a clean cache.
        WKWebsiteDataStore.default().removeData(
            ofTypes: [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache],
            modifiedSince: .distantPast) { web.load(URLRequest(url: target)) }
        let win = NSWindow(contentRect: web.frame, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "Goldfish Brain"
        win.contentView = web
        win.isReleasedWhenClosed = false
        win.center()
        webView = web
        window = win
        show(win)
    }

    private func show(_ window: NSWindow) {
        // A deliberate click on a menu item: fine to bring Goldfish Brain forward (unlike the popup).
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let html = "<body style='font:16px -apple-system;padding:40px'>Goldfish Brain is still starting. Close this window and open it again in a few seconds.</body>"
        MainActor.assumeIsolated { webView.loadHTMLString(html, baseURL: nil) }
    }
}
