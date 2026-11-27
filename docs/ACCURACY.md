# Recognition accuracy: what we measured, and what we can't yet

Short version: on **synthetic** frames the face matcher never gave a wrong name and never matched a face from another show. It does miss small, soft faces. We have **no measurement on real streaming frames yet**; the local log and the "Wrong?" button are how that will change.

## How a face becomes a name

1. InsightFace `buffalo_l` finds faces and makes a 512-number embedding per face.
2. Each character has one reference embedding (the mean of their cleaned TMDB photos).
3. A face is matched only when its best similarity is at least **0.5** (`_SIMILARITY_THRESHOLD`) and, if `CINEID_MIN_MARGIN` is set above 0, its lead over the runner-up is at least that margin. Each character is claimed by at most one face.
4. Otherwise the card says "not sure / not recognised". It never guesses a name.

The percentage on the card is **match strength**: how similar the face is to the reference photos. It is not the probability that the name is right.

## Method (synthetic benchmark)

`scripts/bench_recognition.py` (read-only on `data/`, writes only a cache file you name). It uses the 11 shows' existing reference photos (`data/shows/*/raw`), 879 usable photos across characters that have at least 3.

- **Held-out test:** a photo is pasted into a 1920x1080 noisy frame; the real detector and embedder run on the frame; the character's reference is rebuilt **without** that photo.
- **Easy set (138 frames):** face 120-400 px tall, JPEG 75.
- **Hard set (570 frames, 62 where no face was found):** face 35-110 px tall, blurred, low contrast, JPEG 50. 508 detected faces are scored.
- **Negatives:** every pasted face from the *other* shows (1,380 easy / 5,080 hard comparisons), leaving out any actor who also appears in the show being tested.
- **Similar-looking cast:** counted as frames where the best *other* character scores at least 0.35.

These are cast photos, not Netflix frames. Real frames have motion blur, odd angles, makeup, crowds and extras who are in the show but not in the gallery. None of that is here.

## Numbers (threshold 0.5)

Margin sweep. Every row is identical across margins in both sets:

| Margin | Easy: correct / wrong name / not sure | Easy: false match | Hard: correct / wrong name / not sure | Hard: false match |
|---|---|---|---|---|
| 0, .02, .05, .08, .10 | 100% / 0% / 0% (n=138) | 0% (n=1,380) | 77.95% / 0% / 22.05% (n=508) | 0% (n=5,080) |

- Similar-looking cases: **0** frames. The highest score any rival character got on a true face was **0.29**; the highest score any other-show face got was **0.33**. Both are far below 0.5.
- Median true-face score: 0.83 (easy), 0.64 (hard).
- Side check at looser thresholds on the hard set: 0.35 gives 94.7% correct, 0% wrong name, 0% false match; 0.25 gives 98.0% correct, 0% wrong, 0.2% false match. **The threshold was not changed**: the negatives here exclude same-show extras, which is where a looser threshold would most likely go wrong.

## Decisions

- **Margin rule: built, defaulted to 0 (off).** The rule only helps when a wrong name is otherwise possible, and the benchmark produced none, so it had nothing to reduce. It also cost nothing here, but a rule that cannot be shown to help does not ship switched on. Set `CINEID_MIN_MARGIN=0.05` to try it. Revisit with real frames from the feedback log.
- **Reference-photo clean-up: on, cut-off 0.3.** Each photo is compared with the mean of the character's other photos. Over 879 photos, same-person scores start at 0.34 and the median is 0.83; one photo scored 0.07 (`beef-naomi/tmdb_2.jpg`). At any cut-off from 0.2 to 0.4 exactly **1 photo** is flagged in total (0 in the other 10 shows). 0.3 sits in the gap. Ingest leaves a flagged photo out of the average (the file stays on disk) and never goes below 3 photos so the gallery still builds. Existing shows and indexes were **not** touched. To see suspects: `venv/bin/python src/audit_photos.py --show <id>` (read-only).
- **Popup label:** "Match" is now "Match strength".

## What will measure real accuracy

- `data/accuracy_log.jsonl` (local only, git-ignored): one line per detected face with time, show, season/episode, best score, margin to the runner-up, face height in px, matched yes/no, character id when matched, and whether you were told "not sure". No images, no names.
- "Wrong?" feedback (`data/feedback/`) keeps the frame for wrong and missed reports.
- `GET /api/accuracy/summary` gives counts, median score and margin, the feedback counts, and a **reported wrong rate** = wrong reports / matched faces. This is a **lower bound**: a wrong name you did not notice or report is invisible, and wrong reports are not matched to specific log lines.

## Known limits

- No real-frame measurement. The synthetic numbers are an upper-ish bound for clean cases, not a promise.
- Small faces are the main gap: about 1 in 5 hard-set faces came back "not sure" at 0.5, and 62 of 570 were not detected at all.
- Twins, siblings, the same actor in several roles, and extras not in the gallery are untested.
- Characters with fewer than 3 photos on disk (121 of the 311 folders) are not in the benchmark.
- Photos are mostly TMDB profile shots, which are cleaner than screen grabs; real scenes score lower.
