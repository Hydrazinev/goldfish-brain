<img src="assets/app-icon-256.png" width="96" align="right" alt="Goldfish Brain icon">

# Goldfish Brain

**Forgot who that character is? Press a key.** A Mac app that recognises the TV character on your screen and tells you who they are, who plays them and where they stand in the story, **only up to the episode you have reached**.

Website (interactive demo, no install needed): https://hydrazinev.github.io/goldfish-brain/

> Built with AI assistance (Claude Code) under my direction. The design decisions below are mine to explain; the code was written with help, reviewed, and covered by tests.

## What it does

- **⌘⇧X, Who is that?** One screenshot, faces matched on your Mac, a paper "dossier" card: name, actor, what is happening to them right now, last scene, and the actor's other work.
- **⌘⇧E, Ask.** Type a question about the scene; get a short answer limited to what you have already watched.
- **⌘⇧R, React.** Your chosen "watch buddy" personality gives a one-line take on the scene. On demand only, never on a timer.
- **Spoiler shield.** Characters who first appear later are hidden; notes describe each person as of the start of your episode.
- Reads your episode from your Netflix tab in Chrome, per-show library, relationship map, your own editable notes, add any show.

## Install

You need: macOS 14+, Chrome, [Xcode](https://apps.apple.com/app/xcode/id497799835) (free), Homebrew, and a free [TMDB API key](https://www.themoviedb.org/settings/api). An [Anthropic API key](https://console.anthropic.com/) is optional (notes, Ask, React).

```bash
git clone https://github.com/Hydrazinev/goldfish-brain.git && cd goldfish-brain && ./install.sh
```

The script installs the build tool, sets up the Python engine, downloads the face model, builds the app and puts it in Applications. Keep the folder where it is: the app uses it as its engine. ⌘Q stops everything.

## Architecture

```
mac/      Swift app (SwiftUI/AppKit): global hotkeys, ScreenCaptureKit capture, Chrome episode detection
          (AppleScript), popup cards, engine supervisor. Registers shortcuts, never runs in the background after quit.
src/      Python engine (FastAPI, localhost:8000): InsightFace buffalo_l + FAISS face matching, SQLite,
          TMDB client, Claude calls (one module, src/llm.py), spoiler logic (src/spoiler.py, server.py)
src/ui/   Web UI shown inside the app (library, add a show, settings, relationship map)
site/     The website (static, GitHub Pages)
tests/    pytest (407) · mac/CineIDTests (392 Swift tests)
```

**Flow for ⌘⇧X:** hotkey → capture → engine `/identify` (detect faces, FAISS search, one-to-one assignment so two faces cannot claim one character) → spoiler filter → card.

## Decisions worth discussing

- **Native shell + Python engine.** The OS-level parts (hotkeys, screen capture, non-activating panels over full-screen video) need Swift; the ML stack is Python. The app supervises the engine and reclaims orphans after crashes.
- **Spoilers are a boundary, not a filter on output.** Every text the model or the viewer sees is built only from data at or before the viewer's position: notes "as of the start of the episode", TMDB summaries of earlier episodes only, bios redacted of later characters, current-episode debuts hidden until late playback. A separate audit found and fixed three leaks in this design.
- **Privacy by construction.** Face matching never leaves the Mac. Ask and React send only your **Chrome window** (not the whole screen) after explicit consent, and only when you press the key. Keys live in the macOS Keychain. Quitting stops everything.
- **Thread safety.** SQLite connections are per-thread after a shared connection crashed the engine under concurrent requests.
- **Small details that matter at 3 a.m. on the sofa.** Instant "Looking…" card, press-again/Esc/click-away to dismiss, auto-fade that pauses on hover, a "Spoiler-safe up to S2E4" caption.

## Accuracy, honestly

Measured on synthetic frames (cast photos pasted into 1920×1080 frames, the test photo left out of the gallery), **not** real streaming frames. Details and method: [docs/ACCURACY.md](docs/ACCURACY.md).

| Set | Correct | Wrong name | Not sure | False match on other-show faces |
|---|---|---|---|---|
| Easy (faces 120–400 px) | 100% | 0% | 0% | 0% of 1,380 |
| Hard (35–110 px, blur, JPEG), of the 508 detected faces | 78% (396 of 508) | 0% | 22% | 0% of 5,080 |
| Hard, counting all 570 frames (62 with no face found count as misses) | ≈69.5% (396 of 570) | 0% | 30.5% | n/a |

The percentage on a card is a similarity score, labelled "match strength", not the probability of being right. Real-world accuracy is not yet measured: the app logs (locally, no images) each identification and the "Wrong?" / "Missed someone" buttons save frames so it can be.

## Known limits

- Mac only; episode auto-detection is Netflix-in-Chrome only; small, dark or turned faces get missed.
- Notes and Ask answers come from an AI model and can be wrong; Ask and React got a small text-only sanity check (19 questions, one run), see [docs/EVAL_ASK.md](docs/EVAL_ASK.md): 19 of 19 judged correct, 0 spoiler leaks flagged; not a benchmark, and React and screenshots were not scored.
- With player controls hidden the episode can't be read, so it falls back to the saved position.
- Playback progress is read from the Netflix tab and passed on to Ask, React and the card, but only when it provably belongs to the screenshot (same Netflix title, episode read from Chrome); otherwise it is left out. The old Python Watch Buddy and `setup_show.py` are legacy.
- Needs Xcode to build; no pre-built download.

## Privacy

Local: face matching, library, notes, positions, accuracy log. Network: TMDB (cast and episode info); episode summaries to Claude once per added show (to write notes); for Ask/React, your question and a screenshot of your Chrome window to Claude, only after you tick the privacy box. No analytics, no accounts, no server run by this project.

## Credits and notices

- Cast data and photos: [TMDB](https://www.themoviedb.org). Used under its API terms; not endorsed or certified by TMDB.
- Face recognition: [InsightFace](https://github.com/deepinsight/insightface) `buffalo_l`, downloaded on your machine under its own (non-commercial research) license.
- Not affiliated with Netflix. Show names, characters and images belong to their owners.

## License

Code: [MIT](LICENSE).
