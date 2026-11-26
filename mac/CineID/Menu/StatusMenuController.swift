import AppKit

/// The menu bar item. Thin: all wording comes from `StatusMenuModel`.
@MainActor
final class StatusMenuController: NSObject, NSMenuDelegate {
    struct Actions {
        var identify: () -> Void
        var ask: () -> Void
        var openUI: (String) -> Void
        var selectShow: (String) -> Void
        var quit: () -> Void
        /// Called when the menu opens; fetch fresh state and call `update(state:)`.
        var refresh: () -> Void
    }

    private let item: NSStatusItem
    private let menu = NSMenu()
    private let actions: Actions
    private var model: StatusMenuModel

    init(model: StatusMenuModel, actions: Actions) {
        self.model = model
        self.actions = actions
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()
        let image = NSImage(systemSymbolName: "film.fill", accessibilityDescription: "Goldfish Brain")
        image?.isTemplate = true
        item.button?.image = image
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        rebuild()
    }

    func update(_ model: StatusMenuModel) {
        self.model = model
        rebuild()
    }

    func menuWillOpen(_ menu: NSMenu) { actions.refresh() }

    private func rebuild() {
        menu.removeAllItems()
        let first = NSMenuItem(title: model.firstLine, action: nil, keyEquivalent: "")
        first.isEnabled = false
        menu.addItem(first)
        menu.addItem(.separator())
        menu.addItem(shortcut(StatusMenuModel.identifyTitle, #selector(identify), key: "x"))
        menu.addItem(shortcut(StatusMenuModel.askTitle, #selector(ask), key: "e"))
        menu.addItem(.separator())
        menu.addItem(action("Open Goldfish Brain…", #selector(openLibrary)))
        menu.addItem(action("Add a Show…", #selector(openAdd)))

        let switchItem = NSMenuItem(title: model.switchShowTitle, action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false
        for choice in model.showChoices {
            let entry = NSMenuItem(title: choice.title, action: #selector(selectShow(_:)), keyEquivalent: "")
            entry.target = self
            entry.representedObject = choice.showId
            entry.state = choice.isCurrent ? .on : .off
            entry.isEnabled = model.canSwitchShow
            sub.addItem(entry)
        }
        switchItem.submenu = model.showChoices.isEmpty ? nil : sub
        switchItem.isEnabled = !model.showChoices.isEmpty
        menu.addItem(switchItem)

        menu.addItem(.separator())
        menu.addItem(action("Quit Goldfish Brain", #selector(quit), key: "q"))
    }

    private func action(_ title: String, _ selector: Selector, key: String = "") -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        entry.target = self
        return entry
    }

    /// Shows the ⇧⌘ shortcut next to the title. It only works while the menu is open; the global hotkey is Carbon's.
    private func shortcut(_ title: String, _ selector: Selector, key: String) -> NSMenuItem {
        let entry = action(title, selector, key: key)
        entry.keyEquivalentModifierMask = [.command, .shift]
        return entry
    }

    @objc private func identify() { actions.identify() }
    @objc private func ask() { actions.ask() }
    @objc private func openLibrary() { actions.openUI("") }
    @objc private func openAdd() { actions.openUI("add") }
    @objc private func selectShow(_ sender: NSMenuItem) {
        if let id = sender.representedObject as? String { actions.selectShow(id) }
    }
    @objc private func quit() { actions.quit() }
}
