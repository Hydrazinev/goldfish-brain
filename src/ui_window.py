"""Shows the CineID web UI (served by the local engine) in a native window.

Call open_ui() from the main thread only (menu actions already are).
CINEID_UI=browser opens the default browser instead; any failure creating the
window also falls back to the browser, so the UI is always reachable.
"""
from __future__ import annotations

import os
import webbrowser
from typing import Optional

_BASE = "http://127.0.0.1:8000/ui/"
_win = None
_web = None


def ui_url(route: str = "") -> str:
    route = route.strip("/#")
    return f"{_BASE}#/{route}" if route else _BASE


def _open_window(url: str) -> None:
    global _win, _web
    from AppKit import (NSApplication, NSBackingStoreBuffered, NSWindow)
    from Foundation import NSMakeRect, NSURL, NSURLRequest
    from WebKit import WKWebView, WKWebViewConfiguration

    request = NSURLRequest.requestWithURL_(NSURL.URLWithString_(url))
    if _win is None:
        # 1|2|4|8 = titled | closable | miniaturizable | resizable
        _win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 980, 760), 15, NSBackingStoreBuffered, False)
        _win.setTitle_("CineID")
        _win.setReleasedWhenClosed_(False)      # reopen the same window later
        _win.setMinSize_((420, 520))
        _web = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, 980, 760), WKWebViewConfiguration.alloc().init())
        _win.setContentView_(_web)
        _win.center()
    _web.loadRequest_(request)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _win.makeKeyAndOrderFront_(None)


def open_ui(route: str = "", mode: Optional[str] = None) -> None:
    url = ui_url(route)
    mode = mode or os.environ.get("CINEID_UI", "window")
    if mode == "browser":
        webbrowser.open(url)
        return
    try:
        _open_window(url)
    except Exception as exc:                      # never leave the user with nothing
        print(f"[CineID] window unavailable ({exc}); opening the browser instead")
        webbrowser.open(url)
