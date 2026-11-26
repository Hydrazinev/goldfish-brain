import socket
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from messages import friendly_error


def _http(code):
    return urllib.error.HTTPError("http://x", code, "boom", {}, None)


def test_drm_block_explains_the_fix():
    msg = friendly_error(RuntimeError("drm_blocked"), server_age_s=500)
    assert "hardware acceleration" in msg


def test_connection_refused_just_after_launch_says_warming_up():
    msg = friendly_error(urllib.error.URLError(ConnectionRefusedError()), server_age_s=12)
    assert msg == "Goldfish Brain is warming up — try again in a few seconds."


def test_connection_refused_long_after_launch_says_restart():
    msg = friendly_error(urllib.error.URLError(ConnectionRefusedError()), server_age_s=300)
    assert "isn't responding" in msg and "reopen" in msg


def test_404_names_the_show_and_the_next_step():
    msg = friendly_error(_http(404), server_age_s=300, show="Breaking Bad")
    assert msg == "Breaking Bad hasn't been set up yet — open Goldfish Brain → Add a Show."


def test_404_without_show_name_is_still_clear():
    assert "hasn't been set up yet" in friendly_error(_http(404), server_age_s=300)


@pytest.mark.parametrize("code", [500, 502, 503])
def test_server_errors_are_apologetic_not_technical(code):
    msg = friendly_error(_http(code), server_age_s=300)
    assert msg == "Something went wrong on our side. Try again."


@pytest.mark.parametrize("make", [lambda: subprocess.TimeoutExpired("x", 3),
                                  lambda: socket.timeout(), lambda: TimeoutError()])
def test_timeouts(make):
    assert friendly_error(make(), server_age_s=300) == "Taking too long — try again."


def test_unknown_errors_never_leak_internals():
    msg = friendly_error(ValueError("secret path /Users/x/.env KeyError 'foo'"), server_age_s=300)
    assert msg == "Something went wrong. Try again."


@pytest.mark.parametrize("make", [lambda: RuntimeError("drm_blocked"), lambda: _http(404),
                                  lambda: _http(500), lambda: ValueError("x"),
                                  lambda: urllib.error.URLError("x")])
def test_never_contains_raw_error_markers(make):
    msg = friendly_error(make(), server_age_s=5)
    assert "Traceback" not in msg and "Error:" not in msg and "Exception" not in msg


def test_ui_url_building():
    import ui_window
    assert ui_window.ui_url() == "http://127.0.0.1:8000/ui/"
    assert ui_window.ui_url("add") == "http://127.0.0.1:8000/ui/#/add"
    assert ui_window.ui_url("#/settings") == "http://127.0.0.1:8000/ui/#/settings"


def test_browser_mode_opens_default_browser(monkeypatch):
    import ui_window
    opened = []
    monkeypatch.setattr(ui_window.webbrowser, "open", opened.append)
    ui_window.open_ui("add", mode="browser")
    assert opened == ["http://127.0.0.1:8000/ui/#/add"]


def test_window_failure_falls_back_to_browser(monkeypatch):
    import ui_window
    opened = []
    monkeypatch.setattr(ui_window.webbrowser, "open", opened.append)
    monkeypatch.setattr(ui_window, "_open_window",
                        lambda url: (_ for _ in ()).throw(RuntimeError("no display")))
    ui_window.open_ui("settings", mode="window")
    assert opened == ["http://127.0.0.1:8000/ui/#/settings"]


def test_edit_menu_has_paste_and_friends_with_cmd_shortcuts():
    """Menu-bar apps have no Edit menu, so Cmd+V did nothing in the web UI's
    key fields. The Edit menu must provide the standard shortcuts."""
    import edit_menu
    from AppKit import NSApplication
    app = NSApplication.sharedApplication()
    edit_menu.install(app)
    edit = app.mainMenu().itemAtIndex_(1).submenu()
    keys = {edit.itemAtIndex_(i).title(): (edit.itemAtIndex_(i).action(), edit.itemAtIndex_(i).keyEquivalent())
            for i in range(edit.numberOfItems()) if edit.itemAtIndex_(i).title()}
    assert keys["Paste"] == ("paste:", "v")
    assert keys["Copy"] == ("copy:", "c")
    assert keys["Cut"] == ("cut:", "x")
    assert keys["Select All"] == ("selectAll:", "a")
    assert keys["Undo"] == ("undo:", "z")
    assert edit.itemWithTitle_("Paste").target() is None      # first responder, not a delegate
