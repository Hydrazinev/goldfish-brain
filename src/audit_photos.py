"""Read-only audit: which reference photos of a show look like a different person?

    venv/bin/python src/audit_photos.py --show breaking-bad [--cutoff 0.25]

Lists suspects (similarity to the mean of the character's other photos below the
cutoff). Deletes nothing, rebuilds nothing, writes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import photo_clean

_ROOT = Path(__file__).parent.parent


def audit_show(show_dir: Path, embed_photos, cutoff: float = photo_clean.OUTLIER_CUTOFF) -> list[dict]:
    """embed_photos(list[Path]) -> [(path, embedding)]. Returns one dict per suspect photo."""
    raw = Path(show_dir) / "raw"
    suspects: list[dict] = []
    if not raw.is_dir():
        return suspects
    for cdir in sorted(p for p in raw.iterdir() if p.is_dir()):
        images = sorted(p for p in cdir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        pairs = embed_photos(images)
        if len(pairs) < photo_clean.MIN_PHOTOS_TO_CHECK:
            continue
        sims = photo_clean.similarities([e for _, e in pairs])
        keep = set(photo_clean.select_keep([e for _, e in pairs], cutoff, keep_min=2))
        for i, ((path, _), sim) in enumerate(zip(pairs, sims)):
            if i not in keep:
                suspects.append({"character_id": cdir.name, "photo": Path(path).name,
                                 "similarity": round(sim, 3), "photos": len(pairs)})
    return suspects


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--show", required=True)
    ap.add_argument("--cutoff", type=float, default=photo_clean.OUTLIER_CUTOFF)
    ap.add_argument("--data-dir", default=str(_ROOT / "data"))
    a = ap.parse_args(argv)
    show_dir = Path(a.data_dir) / "shows" / a.show
    if not (show_dir / "raw").is_dir():
        print(f"No reference photos for '{a.show}' under {show_dir}")
        return 1
    from recogniser import FaceRecogniser
    suspects = audit_show(show_dir, FaceRecogniser().photo_embeddings, a.cutoff)
    for s in suspects:
        print(f"{s['character_id']}/{s['photo']}  similarity {s['similarity']}  ({s['photos']} photos)")
    print(f"{len(suspects)} suspect photo(s) in '{a.show}' (cutoff {a.cutoff}). Nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
