# Measure recognition on real viewing frames

This workflow evaluates faces only. It never calls Claude or uploads evaluation files. It does not measure Ask/React quality, and the tests supplied with this feature are synthetic fixtures, not real accuracy results.

## Collect

Rebuild the native app after updating its source, and restart its Python engine. In Settings → Recognition evaluation:

1. Read the **whole-display warning**. Identify captures the display, so other windows or private content may be present. This feature preserves exactly the bytes Identify received; it does not crop or change identification.
2. Tick the local capture switch and the privacy acknowledgement, choose a storage cap (default 500 MB), and save. Evaluation is off by default. The native menu displays an evaluation warning after fetching fresh state when opened.
3. Press ⌘⇧X during ordinary viewing, including dark scenes, profiles, small faces, multiple people, and moments where recognition might fail. Collect across at least three shows. Do not select only successful examples.
4. Turn evaluation off when finished. Reaching the cap stops new captures. Settings shows disk usage and the latest capture status.

The corpus uses the engine's data directory:

- `data/eval/frames/<uuid>.jpg`: exact request bytes (native requests are JPEG; PNG API requests remain PNG bytes with recorded MIME type).
- `data/eval/frames/<uuid>.json`: immutable versioned capture, boxes, detector confidence, top-five candidates, assignments, displayed response, position/progress, thresholds, model/detector size, gallery/index/ID hashes, calibration hash, app commit and recognition source hash.
- `data/eval/labels/<uuid>.jsonl`: append-only label and prediction-reveal events, timestamps and revisions.
- `data/eval/splits/` and `data/eval/replays/`: suggested locations for frozen splits and replay outputs.

All `data/eval/` content is git-ignored. The corpus is never read by feedback or upload code. Existing “Wrong?” feedback may independently save the usual `last_capture.jpg`; it does not read evaluation files. The delete button requires confirmation, disables evaluation first, and deletes only frames, labels, splits and replays. It preserves your library, notes and the separate `data/eval/ask_results.json`.

## Freeze the split before tuning

After collection, freeze a split by show (recommended) or episode. Split files cannot be overwritten by this command. New frames require a new manifest; do not add frames to a frozen final test after seeing its results.

```bash
venv/bin/python scripts/score_recognition.py split --by show --seed 41 --out data/eval/splits/viewing-v1.json
```

Roughly one third of the groups become the test partition. Two groups are the technical minimum; three or more shows are a better starting point. A fixed seed is reproducible, not a guarantee that a small sample is representative.

## Label

Open the **Evaluate** tab in the local app (`/ui/#/eval`). Guesses start hidden, including detection boxes, to reduce anchoring bias.

- Draw a box around **every visible face**, including faces the detector missed. Alternatively use Add face box and edit the pixel coordinates with the keyboard.
- Choose a gallery character, **Cast member without a gallery reference**, **Extra / not a cast member**, **Cannot determine identity**, or **False face detection**. Do not guess an unclear identity.
- Mark empty frames **No faces here**. Use optional dark/profile/small/occluded tags.
- Tick **I labelled every visible face** only when complete. Incomplete drafts can be saved, but are excluded from scoring.
- Save, reload, and correct labels as needed. Every save appends a revision. The server rejects a stale revision instead of overwriting another editor's work.
- Reveal predictions when useful; the exposure is recorded permanently for subsequent saves. Reports retain the scored revision and whether the predictions were exposed before that save. Future character names require an explicit spoiler opt-in.

Boxes are stored in source-image pixels plus derived normalised coordinates. Scoring matches boxes at IoU ≥ 0.5, maximising the number of geometric matches before overlap. Identity does not influence box matching.

## Score the captured app

```bash
# Tuning partition only; the default never reports final-test numbers.
venv/bin/python scripts/score_recognition.py score --split data/eval/splits/viewing-v1.json --out data/eval/tune-v1.json

# Final test: only after settings/experiments have been chosen using the tuning partition.
venv/bin/python scripts/score_recognition.py score --split data/eval/splits/viewing-v1.json --final --out data/eval/test-v1.json
```

Metrics include precision of names **actually shown**, correct-identification recall over all enrolled cast faces (including missed detections), detection recall, wrong-name rate on enrolled cast, separate false-match rates for extras and unenrolled cast, and not-sure rate among detected enrolled cast faces. Reports include raw denominators, per-show/face-size/tag strata, excluded uncertain identities, and label revision hashes. Uncertain identities do not enter identity precision/recall but still enter detection recall.

Confidence intervals use a seeded cluster bootstrap over episodes, falling back to frames when there is only one episode. With fewer than two independent clusters no interval is reported. At 200–300 faces, subgroup intervals may be wide or unstable; zero observed errors can produce zero-width bootstrap intervals and **does not establish a zero population error rate**. One viewer and deliberate keypresses remain sources of sampling bias. Applicable recorded confidence values also receive a descriptive reliability/Brier/ECE summary; it is not a newly fitted calibration model.

## Compare galleries without changing active indexes

Build each comparison gallery in a **new** directory. `--out` writes that alternative directly, even without `--apply`; it rejects an existing destination or the active gallery directory. The ordinary command without `--out` remains a dry run unless `--apply` is supplied.

```bash
venv/bin/python scripts/rebuild_gallery.py --show SHOW_ID --mode clean --out /private/tmp/goldfish-clean/SHOW_ID
venv/bin/python scripts/rebuild_gallery.py --show SHOW_ID --mode augmented --out /private/tmp/goldfish-augmented/SHOW_ID
```

Repeat for every show in the manifest. Replay expects a root containing `SHOW_ID/embeddings.faiss` and `.meta`, with the same character IDs as the captured gallery. The face model must already be installed; replay does not download it. Do not use evaluation frames to build either reference gallery.

```bash
venv/bin/python scripts/replay_recognition.py --gallery-root /private/tmp/goldfish-clean --out data/eval/replays/clean-v1.jsonl
venv/bin/python scripts/replay_recognition.py --gallery-root /private/tmp/goldfish-augmented --out data/eval/replays/augmented-v1.jsonl
venv/bin/python scripts/score_recognition.py score --split data/eval/splits/viewing-v1.json --replay data/eval/replays/augmented-v1.jsonl --out data/eval/tune-augmented-v1.json
```

Replay uses the same assignment/margin policy as Identify and the captured spoiler-visibility boundary. It reads only saved frames and alternative indexes, writes a new predictions file, records provenance, and never replaces captures, labels or active indexes. Change `--threshold` or `--margin` using tuning data only. Add `--final` to score the chosen replay on the test partition. Keep all outputs local.

## Verification

`venv/bin/pytest tests/ -q` exercises capture isolation, exact bytes, quota, deletion boundaries, UUID/origin checks, append-only revisions, prediction exposure, replay policy, geometric scoring and frozen partitions.

`tests/browser/evaluation.cjs` drives the real Settings and Evaluate UI against an isolated temporary API/database fixture. Install Playwright in a separate tooling environment, set `PLAYWRIGHT_MODULE` if needed, and optionally set `CINEID_BROWSER_EXECUTABLE`. **Close regular Chrome first.** The script refuses to launch if it is running, uses a separate temporary browser profile, and closes the browser and fixture server in `finally`.
