# Trying the native Mac app (Stage 1)

The Python app (`./start.sh`) still works and is unchanged. The Mac app is a separate build in `mac/`.
Quit the Python app first: both want port 8000 and the same hotkey.

## Build and run
```bash
./scripts/run-mac.sh            # xcodegen + xcodebuild + open the app
./scripts/run-mac.sh -demoPopup front   # preview a popup state (front back stack limited hidden identifying error)
```
- Needs Xcode, `brew install xcodegen`, and the Python `venv/` from the main README.
- Signing: copy `mac/Config/Local.xcconfig.example` to `mac/Config/Local.xcconfig` and set your
  `DEVELOPMENT_TEAM` so macOS keeps your permissions across rebuilds. Without it the script signs ad hoc
  and prints a warning; you then have to re-grant permissions after every rebuild.
- A film icon appears in the menu bar. Its first line says "Warming up…" for up to ~90 s, then "Ready · <show> · S04E04".
- Logs: `~/Library/Logs/CineID/engine.log`. Quit from the menu (it stops the engine).

## Permissions to grant (System Settings → Privacy & Security)
1. **Screen Recording** → Goldfish Brain (needed to take the screenshot). The popup tells you if it is missing.
2. **Automation** → Goldfish Brain → Google Chrome (asked the first time; lets Goldfish Brain read the Netflix episode).
3. In Chrome: View → Developer → **Allow JavaScript from Apple Events** (optional; without it Goldfish Brain reads the
   episode from the screen text, then falls back to your stored position).
No Accessibility permission is needed for the hotkey.

## Checklist to run on a real screen (none of this is covered by automated tests)
1. Netflix full screen in Chrome, playing. Press **⌘⇧X**. Expect: a "Looking…" card within a moment, then character
   cards top-right, on the display showing Chrome. No Space switch, video keeps playing and stays full screen.
2. Press **Space**: Netflix still pauses/resumes (the popup did not take the keyboard). Click the card: it flips;
   then Esc closes it; ✕ closes it; otherwise it fades after ~12 s (not while the mouse is over it).
3. Two displays: the popup and the screenshot are from the display showing Chrome.
4. Press ⌘⇧X from Terminal and from Chrome: works both times, with no Accessibility prompt.
5. Another Chrome window in front of the Netflix one: the episode is still detected (stamp matches Netflix).
6. Chrome quit: press ⌘⇧X; Chrome must NOT launch. Deny the Automation prompt: still identifies (OCR/stored).
7. Turn Screen Recording off for Goldfish Brain: clear message, no crash. Chrome hardware acceleration on: the
   "graphics acceleration" message appears instead of a wrong answer.
8. Press ⌘⇧X twice quickly: one popup, one request. Stop the engine (`pkill -f src/server.py`): it restarts itself.
9. Start the Python app first, then the Mac app: the menu's first line and the ⌘⇧X popup say port 8000 is in use.
10. Menu: Open Goldfish Brain…, Add a Show… (paste an API key with ⌘V), Switch show; Quit leaves no `src/server.py` running.

## Shortcuts
- **⌘⇧X** identify who is on screen. Pressed again while a result card is up, it closes the card.
- **⌘⇧E** ask a question about the scene (needs the Claude key and the Watch Buddy privacy box ticked: Settings → Watch Buddy).
  Type, **Return** sends, **Esc** cancels, **↑** recalls the last question (kept in memory only). Same key again closes it.
- On a character card: "Wrong?" / "Missed someone" send a correction to the engine. The card also says "Spoiler-safe up to S3E7".

## Checklist for ⌘⇧E, Esc, click-away and corrections (real screen only)
11. Netflix full screen in Chrome, playing. Press **⌘⇧E**: the question card appears top-right, you can type at once,
    Netflix stays full screen, no Space switch, video does not pause by itself. Type, Return: "Thinking…", then an answer.
12. Esc cancels the Ask card; empty Return does nothing; ↑ recalls the last question; ⌘⇧E again closes the card.
13. After closing the card, press Space: Netflix pauses/resumes (keyboard went back to Chrome). If it does not, tell me.
14. Identify card: click anywhere in Chrome (outside the card) closes it; clicking the card does not. Hover keeps it
    open; leaving starts the 12 s countdown again. Ask answers fade after 20 s.
15. While a card is up, Esc closes it (Goldfish Brain claims Esc only while a card is visible, so Chrome does not see it then).
16. "Wrong?" shows "Thanks — noted" for ~1.5 s (needs the engine's /api/feedback).
17. ⌘⇧E without the Watch Buddy privacy box ticked: friendly message pointing to Settings → Watch Buddy.

## Not verified / not built yet
- Everything in the checklist above (full-screen, Spaces, focus, permission prompts, status item, ⌘V in the web window).
- No Watch Buddy yet.
- The "how far into the episode" progress hook exists in `IdentifyFlow` but is unused.
