"""ML experiments on the frozen InsightFace buffalo_l backbone (see docs/ACCURACY.md, "ML experiments").

Uses the synthetic benchmark cache from scripts/bench_recognition.py (`embed` + `hard` stages).
Read-only on data/; writes only the cache files you name.

  venv/bin/python scripts/ml_experiments.py augment --cache C.pkl --aug A.pkl   # embeds degraded copies (needs the model)
  venv/bin/python scripts/ml_experiments.py gallery --cache C.pkl --aug A.pkl    # experiment A (numpy only)
  venv/bin/python scripts/ml_experiments.py calib   --cache C.pkl                # experiment B (numpy only)

Split BY SHOW (fixed before looking at any number): the calibration/selection shows and the held-out test
shows never share a show, a character or a photo.
"""
from __future__ import annotations

import argparse
import pickle
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

THRESHOLD = 0.5
NEG_MAX = 0.0
CALIB_SHOWS = ["beef", "breaking-bad", "castle-rock", "dark", "from", "below", "dhindora"]
TEST_SHOWS = ["friends", "the-sandman", "ozark", "the-chestnut-man"]


# ---------------------------------------------------------------- degraded copies

def augment(cache: Path, out: Path, copies: int) -> None:
    import cv2
    from insightface.app import FaceAnalysis
    from gallery_aug import degraded_face_image
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    data = pickle.loads(cache.read_bytes())
    rng = random.Random(23)
    aug: dict = {}
    for (show, cid), items in data["photos"].items():
        if len(items) < 3:
            continue
        for name, _ in items:
            img = cv2.imread(str(ROOT / "data" / "shows" / show / "raw" / cid / name))
            if img is None:
                continue
            faces = [f for f in app.get(img) if f.det_score >= 0.7]
            if len(faces) != 1:
                continue
            embs = []
            for _ in range(copies):
                deg = degraded_face_image(img, faces[0].bbox.tolist(), rng)
                det = [f for f in app.get(deg) if f.det_score >= 0.5]
                if det:
                    f = max(det, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    embs.append(f.embedding.copy())
            aug[(show, cid, name)] = embs
        print(show, cid, len(aug), flush=True)
    out.write_bytes(pickle.dumps(aug))


# ---------------------------------------------------------------- shared helpers

def unit(v):
    v = np.asarray(v, np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def build_gallery(items, aug, show, cid, held, mode):
    """-> list of prototype vectors for one character, leaving out the held-out photo and its degraded copies.
    mode: clean | mean_all | two_proto"""
    clean = [e for n, e in items if n != held]
    if len(clean) < 2:
        return None
    base = unit(np.mean(clean, 0))
    if mode == "clean":
        return [base]
    degraded = [e for n, _ in items if n != held for e in aug.get((show, cid, n), [])]
    if not degraded:
        return [base]
    if mode == "mean_all":
        return [unit(np.mean(clean + degraded, 0))]
    if mode == "two_proto":
        return [base, unit(np.mean(degraded, 0))]
    raise ValueError(mode)


def score(q, protos):
    q = unit(q)
    return max(float(q @ p) for p in protos)


def top2(q, G):
    ranked = sorted(((score(q, p), k) for k, p in G.items()), reverse=True)
    return ranked[0], ranked[1]


def queries(data, key, show, cid):
    v = data[key].get((show, cid), [])
    return v if isinstance(v, list) else [v]


def run_mode(data, aug, shows, mode, key):
    """Counts for one gallery mode on one query set: positives [correct, wrong, not_sure, n],
    negatives [false_match, n], undetected."""
    photos, actor = data["photos"], data["actor"]
    pos = [0, 0, 0, 0]
    neg = [0, 0]
    und = 0
    global NEG_MAX
    NEG_MAX = 0.0
    for show in shows:
        chars = [c for (s, c), it in photos.items() if s == show and len(it) >= 3]
        for c in chars:
            for held, q, _h in queries(data, key, show, c):
                if q is None:
                    und += 1
                    continue
                G = {k: build_gallery(photos[(show, k)], aug, show, k, held if k == c else None, mode) for k in chars}
                G = {k: v for k, v in G.items() if v is not None}
                if c not in G:
                    continue
                (s1, k1), (s2, _) = top2(q, G)
                matched = s1 >= THRESHOLD
                pos[0 if matched and k1 == c else 1 if matched else 2] += 1
                pos[3] += 1
        actors = {actor.get(c) for c in chars}
        G = {k: build_gallery(photos[(show, k)], aug, show, k, None, mode) for k in chars}
        G = {k: v for k, v in G.items() if v is not None}
        for (s_o, c_o) in list(data[key]):
            if s_o == show or actor.get(c_o) in actors:
                continue
            for _n, q, _h in queries(data, key, s_o, c_o):
                if q is None:
                    continue
                (s1, _), _ = top2(q, G)
                neg[1] += 1
                neg[0] += s1 >= THRESHOLD
                NEG_MAX = max(NEG_MAX, s1)
    return pos, neg, und


def pct(a, n):
    return 100.0 * a / n if n else float("nan")


def fmt(pos, neg):
    return (f"correct {pct(pos[0], pos[3]):6.2f}% ({pos[0]}/{pos[3]})  wrong {pct(pos[1], pos[3]):5.2f}% ({pos[1]})  "
            f"not-sure {pct(pos[2], pos[3]):6.2f}% ({pos[2]})  false-match {pct(neg[0], neg[1]):5.2f}% ({neg[0]}/{neg[1]})")


def experiment_a(cache: Path, aug_path: Path) -> None:
    data = pickle.loads(cache.read_bytes())
    aug = pickle.loads(aug_path.read_bytes())
    n_aug = sum(len(v) for v in aug.values())
    print(f"degraded embeddings: {n_aug} from {len(aug)} photos")
    for label, shows in (("CALIBRATION shows (selection only)", CALIB_SHOWS), ("HELD-OUT test shows", TEST_SHOWS)):
        print(f"\n== {label}: {', '.join(shows)}")
        for key, title in (("frames", "easy"), ("hard", "hard")):
            for mode in ("clean", "mean_all", "two_proto"):
                pos, neg, und = run_mode(data, aug, shows, mode, key)
                print(f"  {title:4s} {mode:9s} {fmt(pos, neg)}" + (f"  undetected {und}" if und else "") + f"  highest other-show score {NEG_MAX:.3f}")


# ---------------------------------------------------------------- experiment B: calibrated confidence

def collect(data, shows, neg_shows):
    """Rows (s1, margin, face_h, label, is_neg, kind) for every detected face. Gallery = current clean mean."""
    photos, actor = data["photos"], data["actor"]
    rows = []
    for show in shows:
        chars = [c for (s, c), it in photos.items() if s == show and len(it) >= 3]
        for key in ("frames", "hard"):
            for c in chars:
                for held, q, h in queries(data, key, show, c):
                    if q is None:
                        continue
                    G = {k: build_gallery(photos[(show, k)], {}, show, k, held if k == c else None, "clean") for k in chars}
                    G = {k: v for k, v in G.items() if v is not None}
                    if c not in G:
                        continue
                    (s1, k1), (s2, _) = top2(q, G)
                    rows.append((s1, s1 - s2, h, 1.0 if k1 == c else 0.0, 0, key))
        actors = {actor.get(c) for c in chars}
        G = {k: build_gallery(photos[(show, k)], {}, show, k, None, "clean") for k in chars}
        G = {k: v for k, v in G.items() if v is not None}
        for key in ("frames", "hard"):
            for (s_o, c_o) in list(data[key]):
                if s_o not in neg_shows or s_o == show or actor.get(c_o) in actors:
                    continue
                for _n, q, h in queries(data, key, s_o, c_o):
                    if q is None:
                        continue
                    (s1, _), (s2, _) = top2(q, G)
                    rows.append((s1, s1 - s2, h, 0.0, 1, key))
    return rows


def features(rows, cols):
    X = np.array([[r[0], r[1], np.log(max(r[2], 1.0))] for r in rows], np.float64)[:, cols]
    return X


def weights(rows):
    """Cast faces and non-cast faces count equally in total (50/50): an explicit assumption, not a measurement."""
    isneg = np.array([r[4] for r in rows])
    w = np.where(isneg == 1, 0.5 / max(isneg.sum(), 1), 0.5 / max((1 - isneg).sum(), 1))
    return w * len(rows)


def _sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -40, 40)))


def fit_lr(X, y, w, l2=1.0, iters=50):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    beta = np.zeros(Z.shape[1])
    reg = np.eye(Z.shape[1]) * l2
    reg[-1, -1] = 0
    for _ in range(iters):
        p = _sigmoid(Z @ beta)
        g = Z.T @ (w * (p - y)) + reg @ beta
        H = (Z * (w * p * (1 - p))[:, None]).T @ Z + reg + 1e-9 * np.eye(Z.shape[1])
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-9:
            break
    return mu, sd, beta


def predict(X, params):
    mu, sd, beta = params
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    return _sigmoid(Z @ beta)


def metrics(p, y, w, bins=10):
    p = np.clip(p, 0, 1)
    brier = float(np.sum(w * (p - y) ** 2) / w.sum())
    ece, table = 0.0, []
    edges = np.linspace(0, 1, bins + 1)
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        wm = w[m].sum()
        if m.sum() == 0 or wm <= 0:
            continue
        conf, acc = float(np.sum(w[m] * p[m]) / wm), float(np.sum(w[m] * y[m]) / wm)
        ece += wm / w.sum() * abs(conf - acc)
        table.append((edges[i], edges[i + 1], int(m.sum()), conf, acc))
    return ece, brier, table


def experiment_b(cache: Path) -> dict:
    data = pickle.loads(cache.read_bytes())
    train = collect(data, CALIB_SHOWS, set(CALIB_SHOWS))
    test = collect(data, TEST_SHOWS, set(TEST_SHOWS))
    ytr, yte = np.array([r[3] for r in train]), np.array([r[3] for r in test])
    wtr = weights(train)
    print(f"calibration rows {len(train)} (cast {sum(1 for r in train if not r[4])}, non-cast {sum(r[4] for r in train)}); "
          f"test rows {len(test)} (cast {sum(1 for r in test if not r[4])}, non-cast {sum(r[4] for r in test)})")
    print(f"top-1 correct: calibration cast {np.mean([r[3] for r in train if not r[4]]):.3f}, test cast {np.mean([r[3] for r in test if not r[4]]):.3f}")
    out = {}
    views = {"cast + non-cast faces (50/50)": (np.ones(len(test)) * 0 + weights(test)),
             "cast faces only": np.array([0.0 if r[4] else 1.0 for r in test]),
             "faces shown (raw sim >= 0.5), 50/50": weights(test) * np.array([1.0 if r[0] >= THRESHOLD else 0.0 for r in test])}
    models = {"raw similarity": None, "LR(sim)": [0], "LR(sim, margin)": [0, 1],
              "LR(sim, margin, log h)": [0, 1, 2]}
    for mname, cols in models.items():
        if cols is None:
            ptest = np.array([r[0] for r in test])
            params = None
        else:
            params = fit_lr(features(train, cols), ytr, wtr)
            ptest = predict(features(test, cols), params)
        for vname, w in views.items():
            ece, brier, table = metrics(ptest, yte, w)
            out[(mname, vname)] = (ece, brier, table)
        out[(mname, "params")] = params
    for vname in views:
        print(f"\n--- test view: {vname}")
        for mname in models:
            ece, brier, _ = out[(mname, vname)]
            print(f"  {mname:24s} ECE {ece:.4f}  Brier {brier:.4f}")
    print("\nreliability bins (test, 50/50 view) per model: [lo,hi) n  mean-predicted  observed-correct")
    for mname in models:
        print(" ", mname)
        for lo, hi, n, conf, acc in out[(mname, "cast + non-cast faces (50/50)")][2]:
            print(f"    [{lo:.1f},{hi:.1f}) n={n:4d}  pred {conf:.3f}  obs {acc:.3f}")
    for mname, cols in models.items():
        if cols:
            f = out[(mname, "params")]
            print(f"\n{mname} (standardised features {cols}): mean {f[0].round(6).tolist()} sd {f[1].round(6).tolist()} beta {f[2].round(6).tolist()}")
    out["full"] = out[("LR(sim, margin)", "params")]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["augment", "gallery", "calib"])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--aug")
    ap.add_argument("--copies", type=int, default=4)
    a = ap.parse_args()
    if a.stage == "augment":
        augment(Path(a.cache), Path(a.aug), a.copies)
    elif a.stage == "gallery":
        experiment_a(Path(a.cache), Path(a.aug))
    else:
        experiment_b(Path(a.cache))


if __name__ == "__main__":
    main()
