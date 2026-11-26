import AppKit

/// A standard Edit menu for the app's main menu.
///
/// Goldfish Brain needs a main menu, and macOS routes ⌘V/⌘C/⌘X/⌘A/⌘Z to text
/// fields THROUGH that menu. Without it you cannot paste into the web UI's key fields.
/// (Same approach as src/edit_menu.py in the Python app.)
enum EditMenu {
    static func makeMainMenu() -> NSMenu {
        let main = NSMenu()
        // App menu: a normal Mac app can be hidden and QUIT (⌘Q). Quitting stops the engine and the
        // ⌘⇧X shortcut — a quit Goldfish Brain must never keep listening (privacy).
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Goldfish Brain")
        appMenu.addItem(NSMenuItem(title: "Hide Goldfish Brain", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit Goldfish Brain", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        func add(_ title: String, _ action: Selector, _ key: String, shift: Bool = false) {
            // No target: the action goes to the first responder (the focused web view / text field).
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            if shift { item.keyEquivalentModifierMask = [.command, .shift] }
            edit.addItem(item)
        }
        add("Undo", Selector(("undo:")), "z")
        add("Redo", Selector(("redo:")), "z", shift: true)
        edit.addItem(.separator())
        add("Cut", #selector(NSText.cut(_:)), "x")
        add("Copy", #selector(NSText.copy(_:)), "c")
        add("Paste", #selector(NSText.paste(_:)), "v")
        add("Select All", #selector(NSText.selectAll(_:)), "a")
        editItem.submenu = edit
        main.addItem(editItem)
        return main
    }

    static func install(on app: NSApplication = .shared) {
        app.mainMenu = makeMainMenu()
    }
}
