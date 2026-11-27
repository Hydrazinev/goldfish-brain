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

The hard-set figure above is **of the 508 detected faces** (396 correct). Counting all 570 hard frames, where the 62 with no face found are misses, correct is 396 / 570 = **about 69.5%** (the other 30.5% are 62 undetected plus 112 "not sure"). Quote both.

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

## ML experiments (frozen buffalo_l backbone)

Two experiments, both on the **synthetic** benchmark above (cast photos pasted into noisy frames, other-show faces as negatives). The InsightFace model is not fine-tuned. Code: `scripts/ml_experiments.py`, `src/gallery_aug.py`, `src/calibration.py`. All numbers are from one run with fixed seeds; the same benchmark cache as above (311 characters, 879 photos in characters with 3+ photos).

**Split by show, fixed before any number was seen.** Calibration / selection shows: beef, breaking-bad, castle-rock, dark, from, below, dhindora (109 characters). Held-out test shows: friends, the-sandman, ozark, the-chestnut-man (81 characters; 56 easy and 214 hard detected frames, 29 more hard frames had no face detected). A held-out photo is never in the gallery it is tested against (leave-one-photo-out, and its degraded copies are left out too). Nothing was tuned on the test shows.

### A. Gallery built from clean plus degraded copies of each photo

Each reference photo was also embedded as 4 degraded copies: face shrunk to 35-110 px tall, pasted into a noisy canvas, Gaussian blur (85% of copies), brightness/contrast drop, JPEG quality 40-70 (3,515 degraded embeddings from 879 photos). Compared: **clean** (today: mean of clean photos), **mean_all** (one vector: mean of clean + degraded), **two_proto** (a clean and a degraded vector, best similarity wins). Threshold 0.5, margin off, as in production. Negatives: every face from the other shows (all queries, nothing learned from them).

| Held-out test shows | Correct | Wrong name | Not sure | False match on other-show faces | Highest other-show score |
|---|---|---|---|---|---|
| Easy, clean (n=56) | 100% | 0% | 0% | 0 of 496 | |
| Easy, mean_all / two_proto | 100% / 100% | 0% | 0% | 0 of 496 | |
| Hard, clean (n=214 detected) | 78.04% (167) | 0% | 21.96% (47) | 0 of 1,818 | 0.330 |
| **Hard, mean_all** | **86.92% (186)** | **0%** | **13.08% (28)** | **0 of 1,818** | 0.402 |
| Hard, two_proto | 87.85% (188) | 0% | 12.15% (26) | 0 of 1,818 | 0.409 |

On the calibration shows (used to choose): hard clean 77.89%, mean_all 88.10%, two_proto 87.76% (n=294); easy 100% for all; wrong and false match 0 for all. mean_all was chosen there (marginally higher, and a single vector per character fits the existing index with no code change); two_proto scored 0.9 point higher on the test shows, which was not used to choose.

Counting the 29 undetected hard test frames as misses (243 frames): 68.7% clean vs 76.5% mean_all.

**Verdict: kept (mean_all).** Hard set improved by about 9 points, wrong-name and false-match rates stayed 0, easy set unchanged (100%, no drop). Caveats that matter:
- **The degradations in the gallery come from the same family as the degradations in the test frames** (same shrink, blur, JPEG idea, different random draws and ranges). That favours the method. Real stream degradation (motion blur, compression artefacts, lighting, angle) is different, so expect a smaller gain.
- **The safety gap narrowed**: the highest score an other-show face got rose from 0.330 to 0.402 on the test shows (threshold 0.5). Still no false match in 1,818 comparisons, but the margin is thinner, and same-show extras are not in this test.
- Detection of small faces is untouched: faces the detector misses stay missed.
- Small sample (214 hard frames over 81 characters): a 9-point gain is well outside noise on these frames, but the exact figure is not precise.

**Integration (new shows only).** `src/ingest.py` now calls `gallery_aug.build_gallery`, which averages clean photos plus 4 degraded copies each and falls back to the plain gallery on any error; `CINEID_GALLERY_AUG=0` switches it off. **Existing shows and indexes were not touched.** `scripts/rebuild_gallery.py --show <id>` prints what it would change and writes nothing unless `--apply` (it then backs up `embeddings.faiss`/`.meta` as `.bak`). It was not run on any real show. Tests use a fake recogniser (`tests/test_gallery_aug.py`).

### B. Calibrated confidence

A logistic regression (numpy IRLS, L2 = 1, standardised features) maps match numbers to P(top-1 identity is right). Fitted on the calibration shows (2,632 faces: 376 cast faces from easy + hard frames, 2,256 other-show faces labelled "wrong"), evaluated on the held-out shows (1,080 faces: 270 cast, 810 non-cast). **Assumption, not a measurement:** cast and non-cast faces are weighted 50/50 in both fitting and scoring. Raw baseline: top-1 similarity used directly as a probability. ECE uses 10 equal-width bins.

| Held-out test view | Model | ECE | Brier |
|---|---|---|---|
| Cast + non-cast (50/50) | raw similarity | 0.2199 | 0.0754 |
| | LR(sim) | 0.0156 | 0.0028 |
| | **LR(sim, margin)** (shipped) | 0.0135 | 0.0025 |
| | LR(sim, margin, log face height) | 0.0085 | 0.0014 |
| Cast faces only | raw similarity | 0.3356 | 0.1357 |
| | LR(sim, margin) | 0.0043 | 0.0012 |
| Faces the app would show (raw sim >= 0.5) | raw similarity | 0.2845 | 0.0931 |
| | LR(sim, margin) | 0.0000 | 0.0000 |

Reliability (LR(sim, margin), test, all faces; predicted vs observed correct): bin 0.0-0.1: n=769, 0.015 vs 0.000; 0.1-0.2: n=28, 0.144 vs 0.000; 0.2-0.3: n=5, 0.246 vs 0.000; 0.3-0.4: n=5, 0.342 vs 0.000; 0.4-0.5: n=2, 0.470 vs 0.750; 0.6-0.7: n=1; 0.7-0.8: n=2, 0.779 vs 0.750; 0.8-0.9: n=1; 0.9-1.0: n=267, 0.999 vs 1.000. The raw similarity is badly off: a face at 0.5-0.9 is right every time here, yet reads as 50-90%.

**Verdict: beats raw similarity on ECE and Brier in every view, so it is integrated, with strong reservations.**
- On this data every cast face got the right name (no wrong-name cases exist), so "P(correct)" is really "is this a cast face". The 50/50 prior is invented. The middle of the reliability curve has about 15 faces in total: it is not resolved. **Do not read it as real-world accuracy;** refit once `data/feedback/` holds real wrong/right frames.
- The face-height feature improved the synthetic numbers a little (ECE 0.0085) but its coefficient is negative (bigger face, lower probability), the opposite of what is plausible. It comes from how the synthetic negatives are sized, not from the faces. **It was dropped; the shipped model uses similarity and margin only.** Face height is still logged.
- Integration, without touching the card or any threshold: `/identify` characters carry an extra `calibrated_confidence` (the existing `confidence` key, the match strength, is unchanged; the name differs for that reason) when the character is the face's top-1, and each line of `data/accuracy_log.jsonl` has `p_correct`. Parameters are constants in `src/calibration.py`. Tests: `tests/test_calibration.py`, `tests/test_accuracy.py`.

### Limits of both experiments

Synthetic data only; no real streaming frame was measured. 4 held-out shows, 81 characters, a few hundred frames, one run, no confidence intervals. Same-show extras, twins, makeup and angle changes are absent. The 62 / 29 faces the detector never finds are outside both experiments.

### Calibration scope (correction)

`calibrated_confidence` was fitted on galleries built from the **clean** reference photos. New shows now use the
**augmented** gallery, whose similarity and margin distributions differ, so the ECE above does **not** validate that
combination. The engine therefore emits `calibrated_confidence` (and `p_correct` in the accuracy log) only for shows
without a `gallery_aug.flag` file in their data folder, and leaves it empty for augmented shows until the model is
refitted on real frames.
