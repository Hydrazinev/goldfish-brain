# Watch Buddy — Design Spec
_Date: 2026-05-27_

## Overview

A "watch buddy" persona that chimes in between scenes while the user watches a show. It detects audio silence, takes a screenshot for context, pulls character arc data for the current episode, and delivers a friend-like reaction + question via a floating chat bubble and macOS TTS voice.

---

## Goals

- Feel like a real friend watching with you — spontaneous, contextual, not robotic
- Never spoil past the user's current episode
- Zero friction: no manual triggers, no setup beyond persona config
- Reuse existing infrastructure (screenshot, OpenAI, SQLite, PyObjC overlay)

---

## Architecture & Flow

A new background thread `BuddyListener` runs alongside the existing `pynput` hotkey listener.

**Trigger conditions (all must be true):**
1. System audio has been below silence threshold for ≥1.5 seconds
2. At least N minutes have passed since last trigger (N = user-configured cooldown: 2/3/5 min)
3. Server is running (`/health` check passes on startup)

**On trigger:**
1. Take a screenshot (same `mss` capture as existing hotkey flow)
2. Read `data/config.json` → `show_id`, `season`, `episode`
3. Query `character_arcs` table for top 5 characters by `importance_rank` where `first_episode <= current_episode` (spoiler shield)
4. Build prompt (see Prompt Design below)
5. Call `gpt-4o` with screenshot + prompt (single API call)
6. Display `BuddyBubble` overlay with response text
7. Speak response via `subprocess.Popen(["say", "-v", persona_voice, response])`

---

## New Components

### `src/buddy.py` — BuddyListener

- Runs as a daemon thread started in `AppDelegate.applicationDidFinishLaunching_`
- Uses `sounddevice` to monitor system audio in real time (input stream, RMS level check)
- Silence threshold: RMS < 0.01 for ≥1.5 seconds
- Cooldown enforced via `time.time()` comparison
- On trigger: calls `_generate_buddy_message()` which does screenshot + OpenAI call
- Posts result back to main thread via `AppHelper.callAfter()` to show `BuddyBubble`

### `BuddyBubble` class in `desktop_app.py`

- New `NSWindow` subclass, similar to existing `Overlay`
- Positioned bottom-right corner of screen (20px margin)
- iMessage-style rounded rect bubble, dark background matching app theme
- Shows buddy name + message text
- Auto-dismisses after 8 seconds, or immediately on click
- Slides in from bottom (NSAnimationContext fade + translate)

### Persona settings in `data/config.json`

New keys added alongside existing `show_id`/`season`/`episode`:
```json
{
  "show_id": "breaking-bad",
  "season": 3,
  "episode": 7,
  "buddy": {
    "name": "Alex",
    "personality": "hype",
    "voice": "Samantha",
    "cooldown_minutes": 3,
    "enabled": true
  }
}
```

Personalities: `hype` / `film_nerd` / `chill`

### Persona setup UI

Accessible from menu bar → "Watch Buddy Settings…" — opens a small `NSPanel`:
- Buddy name: `NSTextField`
- Personality: `NSPopUpButton` (Hype Friend / Film Nerd / Chill Vibes)
- Voice: `NSPopUpButton` populated from `subprocess.check_output(["say", "-v", "?"])`
- Cooldown: `NSPopUpButton` (2 min / 3 min / 5 min)
- Enable/disable toggle: `NSButton` checkbox
- Save button: writes to `data/config.json`

---

## Prompt Design

```
You are {name}, a {personality_description} friend watching {show_title} with the user.
They are currently on Season {season}, Episode {episode}.
IMPORTANT: Do NOT reference or hint at anything that happens after this episode.

Here is what's happening with the main characters right now:
{for each character: "- {character_name} ({arc_type}): {current_arc}"}

[Screenshot of the current scene attached]

React to what you see on screen and ask ONE short question like a friend would.
Keep your response under 40 words. Be natural, not formal.
```

Personality descriptions:
- `hype`: "enthusiastic and excitable, uses casual slang, reacts loudly to drama"
- `film_nerd`: "thoughtful and analytical, notices themes and character motivations, warm but smart"
- `chill`: "laid-back and funny, low-key reactions, dry humor"

---

## Spoiler Safety

Two-layer enforcement:
1. **Data layer**: only characters where `first_season < season OR (first_season = season AND first_episode <= episode)` are included in the prompt — same logic as existing overlay spoiler shield
2. **Prompt layer**: explicit instruction in system prompt not to reference past current episode

---

## Dependencies

- `sounddevice` — add to `requirements.txt` (audio monitoring)
- `gpt-4o` vision — already used in project via OpenAI API
- macOS `say` — built-in, no install needed
- `mss` — already in requirements (screenshots)

**macOS audio capture note:** `sounddevice` reads from the default input device. To capture system audio (TV show playback) rather than the microphone, the user needs a virtual audio loopback driver such as [BlackHole](https://existential.audio/blackhole/) (free) set as the system output. The setup wizard should include a one-time prompt explaining this and linking to BlackHole if the user hasn't configured it.

---

## What This Does NOT Include

- ElevenLabs or third-party TTS (can be added later)
- Streaming/typewriter text effect (can be added later)
- Per-scene question bank (all questions are GPT-generated, not pre-written)
- Pause/resume of video playback (buddy chimes in, doesn't control the player)
