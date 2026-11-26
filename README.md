<img src="assets/app-icon-256.png" width="96" align="right" alt="Goldfish Brain icon">

# Goldfish Brain

**Forgot who that character is? Press a key.**

Goldfish Brain is a Mac app that recognises the TV character on your screen and tells you who they are, who plays them and where they stand in the story — **only up to the episode you've reached**. No pausing, no googling, no spoilers.

Website: https://hydrazinev.github.io/goldfish-brain/

## What it does

- **⌘⇧X — Who's that?** One screenshot, faces matched on your Mac, a paper "dossier" card pops up: name, actor, biography, what's happening to them *right now*, last scene, and the actor's other movies and series.
- **⌘⇧E — Ask.** Type a question about the scene ("why is she upset?"). You get a short answer that never goes beyond your current episode, and the video never pauses.
- **Spoiler shield.** Characters who first appear later are hidden. Notes are written "as of the start of this episode".
- **Knows your episode.** Reads it from your Netflix tab in Chrome (falls back to a position you set).
- **Relationship map** for every show — only people you've already met.
- **Your own notes.** Write or correct any character's notes, per episode.
- **Add any show.** Search, pick the poster, wait a few minutes.
- **"Wrong?" / "Missed someone"** buttons save the frame locally so accuracy can be measured.

## Install (one command)

You need: a Mac on macOS 14+, Chrome, [Xcode](https://apps.apple.com/app/xcode/id497799835) (free, App Store), and a free [TMDB API key](https://www.themoviedb.org/settings/api). An [Anthropic API key](https://console.anthropic.com/) is optional (needed for character notes and Ask).

```bash
git clone https://github.com/Hydrazinev/goldfish-brain.git && cd goldfish-brain && ./install.sh
```

The script installs the build tool, sets up the Python engine, downloads the face model, builds the app and installs it into Applications. Then follow the first-run screen: allow Screen Recording and Chrome access, paste your keys, add a show, open Netflix in Chrome and press ⌘⇧X.

Keep the cloned folder where it is — the app uses it as its engine. Quit (⌘Q) fully stops everything.

## Privacy

- Face matching, your library, notes and viewing position stay on your Mac.
- API keys are stored in the macOS Keychain, never in the repo.
- Nothing runs while the app is quit. Screenshots are taken only when you press a shortcut.
- Sent over the network: TMDB requests (cast/episode info), episode summaries to Claude once when you add a show (to write notes), and — only for Ask, only after you tick a privacy box — your question and a screenshot to Claude.
- No analytics, no accounts, no server operated by this project.

## How it's built

```
mac/        Swift app (SwiftUI/AppKit): hotkeys, screen capture, Chrome episode detection, popup cards, engine supervisor
src/        Python engine (FastAPI): InsightFace face recognition + FAISS, SQLite, TMDB, Claude via src/llm.py
src/ui/     Web UI (library, add a show, settings, relationship map) shown inside the app
site/       The website (static HTML, deployed with GitHub Pages)
tests/      pytest suite (venv/bin/pytest tests -q)
```

Develop: `./scripts/run-mac.sh` builds and opens a debug copy; `cd mac && xcodegen && xcodebuild test -scheme CineID -destination 'platform=macOS' CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES` runs the Swift tests. (The internal Xcode target keeps its original name, CineID.) More in [CLAUDE.md](CLAUDE.md) and [docs/HOW_TO_TRY_MAC.md](docs/HOW_TO_TRY_MAC.md).

## Credits and notices

- Character and actor data and photos: [TMDB](https://www.themoviedb.org). This product uses the TMDB API but is not endorsed or certified by TMDB.
- Face recognition: [InsightFace](https://github.com/deepinsight/insightface) `buffalo_l`. The model is downloaded on your machine and has its own license (non-commercial research use) — check it before using this commercially.
- Not affiliated with Netflix. Show names, characters and images belong to their owners.

## License

Code: [MIT](LICENSE).
