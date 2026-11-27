#!/usr/bin/env python3
"""Rebuild one show's face gallery with degraded copies of the reference photos (see docs/ACCURACY.md).

NOT run on existing shows automatically. Without --apply it only prints what it would do and writes nothing.
With --apply it backs up embeddings.faiss(+.meta) to *.bak and writes the new index; restart the app
(or call /load-show) afterwards. Characters keep their place: only the vector changes.

  venv/bin/python scripts/rebuild_gallery.py --show breaking-bad            # dry run
  venv/bin/python scripts/rebuild_gallery.py --show breaking-bad --apply
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gallery_aug  # noqa: E402


def _cos(a, b) -> float:
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def rebuild(show_dir: Path, recogniser, index_cls, apply: bool, copies: int = gallery_aug.COPIES, out=print, out_dir=None, mode="augmented") -> dict:
    """Plan (and with apply=True, write) a new index for show_dir. Returns a summary dict."""
    index_path = show_dir / "embeddings.faiss"
    if not index_path.exists():
        raise SystemExit(f"No index at {index_path}")
    destination = Path(out_dir) if out_dir is not None else None
    if destination is not None:
        if destination.resolve() == show_dir.resolve() or destination.exists():
            raise ValueError('--out must be a new directory outside the active show gallery')
    old = index_cls()
    old.load(index_path)
    new = index_cls()
    changed = kept = 0
    for cid in old._ids:
        vec_old = old._index.reconstruct(old._ids.index(cid))
        photos = sorted(p for p in (show_dir / "raw" / cid).glob("*.[jp][pn]*g")) if (show_dir / "raw" / cid).is_dir() else []
        gallery = (recogniser.build_gallery(photos) if mode == "clean" else gallery_aug.build_gallery(recogniser, photos, copies)) if photos else None
        if gallery is None:
            new.add_character(cid, vec_old)
            kept += 1
            out(f"  {cid}: keep old vector (no usable photos on disk)")
            continue
        new.add_character(cid, gallery[0])
        changed += 1
        out(f"  {cid}: {gallery[1]} clean photos + up to {gallery[1] * copies} degraded copies; similarity old vs new {_cos(vec_old, gallery[0]):.3f}")
    summary = {"changed": changed, "kept": kept, "applied": False}
    if not apply and destination is None:
        out(f"\nDry run: {changed} character(s) would change, {kept} keep their vector. Nothing was written. Use --apply.")
        return summary
    if destination is not None:
        destination.mkdir(parents=True)
        index_path = destination / 'embeddings.faiss'
    for suffix in (".faiss", ".meta"):
        src = index_path.with_suffix(suffix)
        if src.exists():
            shutil.copy2(src, src.with_name(src.name + ".bak"))
    new.save(index_path)
    flag = index_path.parent / "gallery_aug.flag"
    if (mode == "augmented" and gallery_aug.enabled() and copies > 0) or (kept and (show_dir / "gallery_aug.flag").exists()):
        flag.write_text("1")   # Never apply clean-gallery calibration to mixed / augmented vectors.
    else:
        flag.unlink(missing_ok=True)
    summary["applied"] = True
    out(f"\nWrote {index_path} (backup: {index_path.name}.bak). Restart the app to load it.")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--copies", type=int, default=gallery_aug.COPIES)
    ap.add_argument("--out", type=Path, help="Write an alternative gallery to a NEW directory; active index is unchanged")
    ap.add_argument("--mode", choices=["clean", "augmented"], default="augmented")
    a = ap.parse_args()
    from recogniser import FaceIndex, FaceRecogniser
    rebuild(ROOT / "data" / "shows" / a.show, FaceRecogniser(), FaceIndex, a.apply, a.copies, out_dir=a.out, mode=a.mode)


if __name__ == "__main__":
    main()
