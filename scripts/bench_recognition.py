"""Synthetic recognition benchmark (NOT real streaming frames).

Stage `embed`: embed every reference photo under data/shows/*/raw (cached), then
paste one held-out photo per character into a 1920x1080 frame, run the real
detector + embedder on the frame (cached). Read-only on data/; writes only the cache file.
Stage `eval`: sweep match margins using the cache (see docs/ACCURACY.md).

  venv/bin/python scripts/bench_recognition.py embed --cache /path/cache.pkl
  venv/bin/python scripts/bench_recognition.py eval  --cache /path/cache.pkl
"""
from __future__ import annotations

import argparse
import pickle
import random
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
DET_MIN = 0.7
THRESHOLD = 0.5


def _frame(photo: np.ndarray, bbox, rng: random.Random, hard: bool = False):
    """Paste a crop of the photo (face at 120-400 px tall) onto a noisy 1920x1080 frame."""
    x1, y1, x2, y2 = bbox
    fh = max(y2 - y1, 1)
    target = rng.randint(35, 110) if hard else rng.randint(120, 400)
    cx, cy, half = (x1 + x2) / 2, (y1 + y2) / 2, fh * 2.2
    h, w = photo.shape[:2]
    a, b = int(max(0, cx - half)), int(max(0, cy - half))
    c, d = int(min(w, cx + half)), int(min(h, cy + half))
    crop = photo[b:d, a:c]
    s = target / fh
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
    bg = np.full((1080, 1920, 3), rng.randint(20, 90), np.uint8)
    bg = cv2.add(bg, np.random.RandomState(rng.randint(0, 9999)).randint(0, 25, bg.shape, dtype=np.uint8))
    ch, cw = crop.shape[:2]
    crop = crop[:min(ch, 1080), :min(cw, 1920)]
    ch, cw = crop.shape[:2]
    ox, oy = rng.randint(0, 1920 - cw), rng.randint(0, 1080 - ch)
    bg[oy:oy + ch, ox:ox + cw] = crop
    if hard:   # small face in a wide shot: soft focus, low contrast, heavier compression
        k = rng.choice([3, 5])
        bg = cv2.GaussianBlur(bg, (k, k), rng.uniform(0.6, 1.4))
        bg = cv2.convertScaleAbs(bg, alpha=rng.uniform(0.55, 0.9), beta=rng.uniform(-15, 5))
    ok, buf = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, 50 if hard else 75])
    return cv2.imdecode(buf, 1)


def embed(cache_path: Path, limit: int | None):
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    rng = random.Random(7)
    db = sqlite3.connect(f"file:{ROOT/'data'/'cineid.db'}?mode=ro", uri=True)
    actor = {r[0]: r[1] for r in db.execute("select character_id, actor_name from characters")}
    out = {"photos": {}, "frames": {}, "actor": actor}   # photos[(show,cid)] = [(name, emb)], frames[(show,cid)] = (name, emb, face_h) | None
    for show_dir in sorted((ROOT / "data" / "shows").iterdir()):
        raw = show_dir / "raw"
        if not raw.is_dir():
            continue
        for cdir in sorted(raw.iterdir())[:limit]:
            items, held = [], []
            for p in sorted(cdir.glob("*.[jp][pn]*g")):
                img = cv2.imread(str(p))
                if img is None:
                    continue
                faces = [f for f in app.get(img) if f.det_score >= DET_MIN]
                if len(faces) == 1:
                    items.append((p.name, faces[0].embedding.copy()))
                    held.append((p.name, img, faces[0].bbox.tolist()))
            out["photos"][(show_dir.name, cdir.name)] = items
            if len(held) >= 4:   # one held-out photo per character, pasted into a frame
                name, img, bbox = rng.choice(held)
                fr = _frame(img, bbox, rng)
                fs = app.get(fr)
                fs = [f for f in fs if f.det_score >= 0.5]
                if fs:
                    f = max(fs, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    out["frames"][(show_dir.name, cdir.name)] = (name, f.embedding.copy(), float(f.bbox[3] - f.bbox[1]))
                else:
                    out["frames"][(show_dir.name, cdir.name)] = (name, None, 0.0)
        print(show_dir.name, len(out["photos"]), len(out["frames"]), flush=True)
    cache_path.write_bytes(pickle.dumps(out))


def hard_frames(cache_path: Path, per_char: int = 3):
    """Re-detect on reference photos and paste up to per_char of them per character as small,
    degraded faces. Adds data['hard'][(show,cid)] = [(photo, emb|None, face_h)]."""
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    data = pickle.loads(cache_path.read_bytes())
    rng = random.Random(11)
    hard = {}
    for (show, cid), items in data["photos"].items():
        if len(items) < 3:
            continue
        names = [n for n, _ in items]
        rng.shuffle(names)
        for name in names[:per_char]:
            img = cv2.imread(str(ROOT / "data" / "shows" / show / "raw" / cid / name))
            fs = [f for f in app.get(img) if f.det_score >= DET_MIN]
            if len(fs) != 1:
                continue
            fr = _frame(img, fs[0].bbox.tolist(), rng, hard=True)
            det = [f for f in app.get(fr) if f.det_score >= 0.5]
            if det:
                f = max(det, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                hard.setdefault((show, cid), []).append((name, f.embedding.copy(), float(f.bbox[3] - f.bbox[1])))
            else:
                hard.setdefault((show, cid), []).append((name, None, 0.0))
        print(show, cid, flush=True)
    data["hard"] = hard
    cache_path.write_bytes(pickle.dumps(data))


def unit(v):
    v = np.asarray(v, np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def clean_mask(embs, cutoff, keep_min=3):
    """Indices kept: similarity of each photo to the mean of the others >= cutoff (keep >= keep_min)."""
    n = len(embs)
    if n < 3 or cutoff is None:
        return list(range(n))
    E = np.stack([unit(e) for e in embs])
    sims = []
    for i in range(n):
        others = np.delete(E, i, 0).mean(0)
        sims.append(float(E[i] @ unit(others)))
    keep = [i for i in range(n) if sims[i] >= cutoff]
    if len(keep) < keep_min:
        keep = sorted(np.argsort(sims)[::-1][:keep_min].tolist())
    return keep


def leave_one_sims(embs):
    E = np.stack([unit(e) for e in embs])
    return [float(E[i] @ unit(np.delete(E, i, 0).mean(0))) for i in range(len(E))]


def evaluate(data, margins, cutoff=None, close_second=0.35, key="frames"):
    photos, actor = data["photos"], data["actor"]
    frames = {k: (v if isinstance(v, list) else [v]) for k, v in data[key].items()}
    undetected = sum(1 for v in frames.values() for f in v if f[1] is None)
    print(f"[{key}] frames: {sum(len(v) for v in frames.values())}, face not detected: {undetected}")
    stats = {"neg_max": 0.0, "rival_max": 0.0, "pos_med": []}
    data["_stats"] = stats
    res = {m: {"pos": [0, 0, 0, 0], "neg": [0, 0], "close": [0, 0, 0, 0]} for m in margins}

    def rank(q, G):
        return sorted(((float(unit(q) @ v), k) for k, v in G.items()), reverse=True)

    for show in sorted({s for s, _ in photos}):
        chars = [c for (s, c), it in photos.items() if s == show and len(it) >= 3]
        base = {}
        for c in chars:
            items = photos[(show, c)]
            keep = clean_mask([e for _, e in items], cutoff)
            base[c] = [items[i] for i in keep]

        def gal(c, held=None):
            es = [e for n, e in base[c] if n != held]
            return unit(np.mean(es, 0)) if len(es) >= 2 else None

        for c in chars:
            for held_name, q, _h in frames.get((show, c), []):
                if q is None:
                    continue
                G = {k: gal(k, held_name if k == c else None) for k in chars}
                G = {k: v for k, v in G.items() if v is not None}
                if c not in G:
                    continue
                ranked = rank(q, G)
                (s1, k1), (s2, _k2) = ranked[0], ranked[1]
                # similar-looking case: the best *other* character scores >= close_second
                close = (s2 if k1 == c else s1) >= close_second
                stats["rival_max"] = max(stats["rival_max"], s2 if k1 == c else s1)
                stats["pos_med"].append(s1)
                for m in margins:
                    matched = s1 >= THRESHOLD and (s1 - s2) >= m
                    idx = 0 if (matched and k1 == c) else 1 if (matched and k1 != c) else 2
                    r = res[m]
                    r["pos"][idx] += 1
                    r["pos"][3] += 1
                    if close:
                        r["close"][idx] += 1
                        r["close"][3] += 1
        # negatives: frames of characters from OTHER shows, excluding actors that appear in this show
        actors = {actor.get(c) for c in chars}
        G = {k: gal(k) for k in chars}
        G = {k: v for k, v in G.items() if v is not None}
        for (s_o, c_o), frs in frames.items():
            if s_o == show or actor.get(c_o) in actors:
                continue
            for _n, q, _h in frs:
                if q is None:
                    continue
                ranked = rank(q, G)
                s1, s2 = ranked[0][0], ranked[1][0]
                stats["neg_max"] = max(stats["neg_max"], s1)
                for m in margins:
                    res[m]["neg"][1] += 1
                    if s1 >= THRESHOLD and (s1 - s2) >= m:
                        res[m]["neg"][0] += 1
    return res


def show_table(res, title):
    print(f"\n{title}")
    print("margin | positives n | correct% wrong-name% not-sure% | close-second n correct% wrong% notsure% | negatives n false-match%")
    for m, r in res.items():
        p, c, n = r["pos"], r["close"], r["neg"]
        pc = lambda a, t: 100 * a / t if t else float('nan')
        print(f"{m:5.2f}  | {p[3]:5d} | {pc(p[0],p[3]):6.2f} {pc(p[1],p[3]):6.2f} {pc(p[2],p[3]):6.2f} | "
              f"{c[3]:4d} {pc(c[0],c[3]):6.2f} {pc(c[1],c[3]):6.2f} {pc(c[2],c[3]):6.2f} | {n[1]:5d} {pc(n[0],n[1]):6.2f}")


def main():
    global THRESHOLD
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["embed", "hard", "eval", "photos"])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--cutoff", type=float)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    a = ap.parse_args()
    THRESHOLD = a.threshold
    cache = Path(a.cache)
    if a.stage == "embed":
        embed(cache, a.limit)
        return
    if a.stage == "hard":
        hard_frames(cache)
        return
    data = pickle.loads(cache.read_bytes())
    margins = [0.0, 0.02, 0.05, 0.08, 0.10]
    if a.stage == "eval":
        for key, title in (("frames", "Easy synthetic: face 120-400 px, JPEG 75"),
                           ("hard", "Hard synthetic: face 35-110 px, blur, low contrast, JPEG 50")):
            if key not in data:
                continue
            show_table(evaluate(data, margins, None, key=key), f"{title}, no photo clean-up (threshold {THRESHOLD})")
            st = data["_stats"]
            print(f"   highest other-show face score {st['neg_max']:.3f}; highest rival-character score on a true face {st['rival_max']:.3f}; median true score {np.median(st['pos_med']):.3f}")
            if a.cutoff is not None:
                show_table(evaluate(data, margins, a.cutoff, key=key), f"{title}, photo clean-up cutoff {a.cutoff}")
    else:
        allsims = []
        for (s, c), it in data["photos"].items():
            if len(it) >= 3:
                allsims += leave_one_sims([e for _, e in it])
        allsims = np.array(allsims)
        print("photos in chars with >=3:", len(allsims), "percentiles 0.5/1/2/5/10/50:",
              np.percentile(allsims, [0.5, 1, 2, 5, 10, 50]).round(3))
        for cut in (0.2, 0.25, 0.3, 0.35, 0.4):
            per = {}
            for (s, c), it in data["photos"].items():
                if len(it) >= 3:
                    k = clean_mask([e for _, e in it], cut)
                    per[s] = per.get(s, 0) + len(it) - len(k)
            print("cutoff", cut, "dropped per show:", per, "total", sum(per.values()))


if __name__ == "__main__":
    main()
