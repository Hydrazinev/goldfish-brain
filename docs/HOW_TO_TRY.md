# How to try Goldfish Brain (first-run, add a show, Claude)

## 1. Start it
```bash
cd ~/Projects/cineid
./start.sh          # run from Terminal.app, not VSCode
```
A film icon appears in the menu bar. (`CineID.app` in this folder runs the same
script on double-click — untested for macOS permissions; use `./start.sh` first.)

If nothing is set up yet (no TMDB key, or no ready show), the Goldfish Brain window opens
by itself and walks you through it. Otherwise open it any time: menu bar icon →
**Open Goldfish Brain…**

Allow **Screen Recording** for the app that started it (Terminal if you used
`./start.sh`): System Settings → Privacy & Security → Screen Recording, then quit
and reopen Goldfish Brain. The first-run screen and Settings show a live ✓ for this.

## 2. Put your Claude key in
Menu bar icon → **Open Goldfish Brain…** → **Settings** → **Keys** → *Anthropic (Claude) key*
→ **Replace/Add** → paste → **Check & save**. It's checked with Anthropic first, then
stored in your Mac's Keychain (not in a file). Get one at
https://console.anthropic.com/settings/keys

Alternative: put `ANTHROPIC_API_KEY=...` in `.env` in the project folder.

Prove it works (costs a fraction of a cent):
```bash
venv/bin/python scripts/check_claude.py
```
Both Watch Buddy and character notes now use **Claude Haiku 5.5**
(`claude-haiku-5-5`, the cheapest model). To try another model, set
`CINEID_MODEL=claude-sonnet-5-5` before `./start.sh`.

## 3. Add a show
Menu bar icon → **Add a Show…** → type the title → click the right poster →
leave **Write character notes** ticked → **Add this show**.
You'll see "Learning faces — 12 of 30" then "Writing character notes — 5 of 30".
You can close the window; setup continues. When it says **Ready**, click
**Use this show now**, then set your Season/Episode in **Library** (spoiler safety
depends on it). Press **⌘⇧X** while watching.

- No Anthropic key (or notes failed): faces still work; the popup shows name, actor
  and confidence only, with a note that story notes aren't available. Re-run via
  Add a Show → search the same show → **Update this show**.
- Adding a show that already exists asks you to use **Update**; it never silently
  overwrites.
- **Cancel** stops cleanly and marks the show failed (it won't appear in the library).

## 4. Watch Buddy
Settings → Watch Buddy. It stays **paused until you tick the privacy box** (each
reaction uploads a screenshot to Anthropic). "Share only the browser window" is on
by default. For "react at quiet moments" with the show's own audio you still need
BlackHole (see ONBOARDING.md); "every few minutes" mode needs no microphone.

## Tests
```bash
venv/bin/pytest tests/ -q
```

## What was NOT verified end to end (be aware)
- A live Claude call: no Anthropic key was available while building. The request is
  unit-tested against the documented Haiku 5.5 settings (effort low, thinking off, no
  sampling params). Run `scripts/check_claude.py` first; if it fails, the message says why.
- Watch Buddy's window-only screenshot and its spoken reaction; the popup's look and
  the new "Identifying…" state; the `CineID.app` launcher's permission behaviour.
- Esc to dismiss: the popup is a borderless window that likely can't receive key
  presses, so the hint now says "click anywhere to dismiss".
- Recognition accuracy on real on-screen frames (only reference-photo tests exist).
- Time to write notes for a full 30-character show (faces: ~9 s per character measured).
