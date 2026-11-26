"""A standard Edit menu for the app's main menu.

CineID runs as a menu-bar (accessory) app, which has no main menu. macOS routes
Cmd+V / Cmd+C / Cmd+X / Cmd+A / Cmd+Z to text fields THROUGH that menu, so
without it you cannot paste into the web UI's key fields.
"""
from __future__ import annotations


def install(app) -> None:
    from AppKit import NSMenu, NSMenuItem

    def item(menu, title, action, key):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        menu.addItem_(mi)      # no target: the action goes to the first responder
        return mi

    main = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    main.addItem_(app_item)
    app_item.setSubmenu_(NSMenu.alloc().init())

    edit_item = NSMenuItem.alloc().init()
    main.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    item(edit, "Undo", "undo:", "z")
    redo = item(edit, "Redo", "redo:", "z")
    redo.setKeyEquivalentModifierMask_((1 << 20) | (1 << 17))     # Cmd+Shift+Z
    edit.addItem_(NSMenuItem.separatorItem())
    item(edit, "Cut", "cut:", "x")
    item(edit, "Copy", "copy:", "c")
    item(edit, "Paste", "paste:", "v")
    item(edit, "Select All", "selectAll:", "a")
    edit_item.setSubmenu_(edit)
    app.setMainMenu_(main)
