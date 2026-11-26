"""Spike: does a WKWebView window load and run JS alongside the hotkey thread?

Prints `SPIKE_RESULT <document.title>` and exits 0 on success.
Run: timeout 20 venv/bin/python scripts/spike_webview.py
"""
import sys
import threading

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                    NSBackingStoreBuffered, NSWindow)
from Foundation import NSMakeRect, NSURL
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration
from pynput import keyboard


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # mirrors the real app: a global hotkey listener thread is alive
    keyboard.GlobalHotKeys({"<cmd>+<shift>+9": lambda: None}).start()

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(200, 200, 480, 320), 15, NSBackingStoreBuffered, False)
    web = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, 480, 320), WKWebViewConfiguration.alloc().init())
    win.setContentView_(web)
    win.makeKeyAndOrderFront_(None)
    web.loadHTMLString_baseURL_("<html><head><title>ok</title></head><body>hi</body></html>",
                                NSURL.URLWithString_("http://127.0.0.1/"))

    def ask():
        def done(result, error):
            print(f"SPIKE_RESULT {result}" if error is None else f"SPIKE_ERROR {error}")
            sys.stdout.flush()
            AppHelper.stopEventLoop()
        web.evaluateJavaScript_completionHandler_("document.title", done)

    threading.Timer(2.0, lambda: AppHelper.callAfter(ask)).start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
