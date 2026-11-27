from __future__ import annotations

import argparse
import logging
import os
import re
import unicodedata
import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests
from dotenv import load_dotenv
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from recogniser import FaceRecogniser, FaceIndex
from database import CharacterDB
from job_events import emit
import photo_clean

_ROOT = Path(__file__).parent.parent
_DATA_DIR = _ROOT / "data"

_TMDB_BASE = "https://api.themoviedb.org/3"
_TMDB_IMG_BASE = "https://image.tmdb.org/t/p/original"
_TMDB_EXTRA_PROFILE_LIMIT = 5   # person/images results to download per actor
_TMDB_RATE_SLEEP = 0.25         # seconds between API calls

_TOP_CAST_LIMIT = 30
_GOOGLE_MAX_IMAGES = 15
_DET_SCORE_MIN = 0.7
_MIN_CLEAN_IMAGES = 3


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _slugify(text: str) -> str:
    """ASCII-only, filesystem-safe slug (show names become directory names)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _show_id(show_name: str, tmdb_id: Optional[int] = None) -> str:
    """Slug of the show name; falls back to show-<tmdb_id> when the name has no
    ASCII letters (e.g. a title in another script), never to an empty id."""
    return _slugify(show_name) or (f"show-{tmdb_id}" if tmdb_id else "show")


def _unique_character_id(show_id: str, character_name: str, seen: set) -> str:
    """show_id-<slug>, with -2, -3... appended on collision (e.g. several cast
    members credited as the same character), and recorded in `seen`."""
    base = f"{show_id}-{_slugify(character_name)}"
    cid, n = base, 2
    while cid in seen:
        cid = f"{base}-{n}"
        n += 1
    seen.add(cid)
    return cid


def _confidence_level(count: int) -> str:
    if count >= 15:
        return "high"
    if count >= 5:
        return "medium"
    return "low"


def _role_from_episode_count(count: int) -> str:
    if count >= 20:
        return "main"
    if count >= 5:
        return "recurring"
    return "guest"


# ------------------------------------------------------------------
# TMDB helpers
# ------------------------------------------------------------------

def _search_tmdb(show_name: str, api_key: str) -> tuple[int, str, str]:
    """Return (tmdb_id, official_title, show_id)."""
    resp = requests.get(
        f"{_TMDB_BASE}/search/tv",
        params={"api_key": api_key, "query": show_name},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise ValueError(f"No TMDB results for '{show_name}'")
    top = results[0]
    title = top["name"]
    return top["id"], title, _slugify(title)


def _get_cast(tmdb_id: int, api_key: str) -> list[dict]:
    """Return top _TOP_CAST_LIMIT cast members sorted by episode count desc."""
    resp = requests.get(
        f"{_TMDB_BASE}/tv/{tmdb_id}/aggregate_credits",
        params={"api_key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    cast = resp.json().get("cast", [])
    cast.sort(key=lambda c: c.get("total_episode_count", 0), reverse=True)
    return cast[:_TOP_CAST_LIMIT]


def _download_tmdb_images(
    actor_id: int,
    profile_path: Optional[str],
    char_dir: Path,
    api_key: str,
) -> None:
    """Download actor profile images from TMDB into char_dir."""
    if profile_path:
        try:
            r = requests.get(f"{_TMDB_IMG_BASE}{profile_path}", timeout=10)
            if r.ok:
                (char_dir / "tmdb_profile.jpg").write_bytes(r.content)
        except requests.RequestException:
            pass

    try:
        resp = requests.get(
            f"{_TMDB_BASE}/person/{actor_id}/images",
            params={"api_key": api_key},
            timeout=10,
        )
        if not resp.ok:
            return
        for i, img in enumerate(resp.json().get("profiles", [])[:_TMDB_EXTRA_PROFILE_LIMIT]):
            try:
                r = requests.get(f"{_TMDB_IMG_BASE}{img['file_path']}", timeout=10)
                if r.ok:
                    (char_dir / f"tmdb_{i}.jpg").write_bytes(r.content)
            except requests.RequestException:
                continue
    except requests.RequestException:
        pass


def _download_google_images(
    actor_name: str, show_name: str, char_dir: Path
) -> None:
    """Crawl Google Images for photos of actor in show context."""
    try:
        from icrawler.builtin import GoogleImageCrawler
    except ImportError:
        return

    # icrawler's worker threads crash internally when Google changes its
    # results HTML (TypeError: 'NoneType' object is not iterable). Those
    # exceptions bypass our try/except and dump raw tracebacks into the
    # setup wizard log, so suppress the default traceback printer for its
    # threads — a failed crawl just means fewer extra photos.
    import threading
    orig_hook = threading.excepthook

    def _quiet_hook(args):
        name = getattr(args.thread, "name", "") or ""
        if name.startswith(("parser", "feeder", "downloader")):
            return
        orig_hook(args)

    threading.excepthook = _quiet_hook
    try:
        crawler = GoogleImageCrawler(
            storage={"root_dir": str(char_dir)},
            log_level=logging.CRITICAL,
        )
        crawler.crawl(
            keyword=f"{actor_name} {show_name}",
            max_num=_GOOGLE_MAX_IMAGES,
            file_idx_offset="auto",
        )
    except Exception:
        pass
    finally:
        threading.excepthook = orig_hook


# ------------------------------------------------------------------
# Image filtering
# ------------------------------------------------------------------

def _filter_images(char_dir: Path, recogniser: FaceRecogniser) -> list[Path]:
    """Keep images with exactly one face at det_score >= 0.7; delete the rest.

    Accesses recogniser._app directly to retrieve det_score alongside
    detections, which detect_faces() does not expose.
    """
    all_images = (
        list(char_dir.glob("*.jpg"))
        + list(char_dir.glob("*.jpeg"))
        + list(char_dir.glob("*.png"))
    )
    clean: list[Path] = []
    for img_path in all_images:
        image = cv2.imread(str(img_path))
        if image is None:
            img_path.unlink(missing_ok=True)
            continue
        faces = recogniser._app.get(image)
        qualified = [f for f in faces if f.det_score >= _DET_SCORE_MIN]
        if len(qualified) == 1:
            clean.append(img_path)
        else:
            img_path.unlink(missing_ok=True)
    return clean


def _drop_outlier_photos(clean_images: list[Path], recogniser, character_name: str = "") -> list[Path]:
    """With >= 3 usable photos, leave out those clearly not the same person as
    the rest (similarity to the mean of the others below photo_clean.OUTLIER_CUTOFF).
    Files stay on disk; always keeps >= _MIN_CLEAN_IMAGES so the gallery still builds."""
    if len(clean_images) < photo_clean.MIN_PHOTOS_TO_CHECK:
        return clean_images
    try:
        pairs = recogniser.photo_embeddings(clean_images)
        if len(pairs) < photo_clean.MIN_PHOTOS_TO_CHECK:
            return clean_images
        keep = photo_clean.select_keep([e for _, e in pairs], keep_min=_MIN_CLEAN_IMAGES)
    except Exception as exc:        # clean-up is an extra; never block ingest
        logging.getLogger(__name__).warning("photo clean-up skipped for %s: %s", character_name, exc)
        return clean_images
    kept = [pairs[i][0] for i in keep]
    if len(kept) < len(pairs):
        tqdm.write(f"  {character_name}: left out {len(pairs) - len(kept)} photo(s) that don't match the rest")
    return kept


# ------------------------------------------------------------------
# Main pipeline
# ------------------------------------------------------------------

def ingest_show(show_name: str, tmdb_id: Optional[int] = None) -> None:
    load_dotenv()
    api_key = os.getenv("TMDB_API_KEY")
    if not api_key:
        raise RuntimeError("TMDB_API_KEY not set — add it to .env")

    # Step 1 — search TMDB (or use provided ID directly)
    if tmdb_id is not None:
        print(f"Using provided TMDB ID {tmdb_id} for '{show_name}' …")
        official_title = show_name
        show_id = _show_id(show_name, tmdb_id)
    else:
        print(f"Searching TMDB for '{show_name}' …")
        tmdb_id, official_title, show_id = _search_tmdb(show_name, api_key)
        print(f"Found: {official_title}  (tmdb_id={tmdb_id}, show_id='{show_id}')")

    # Step 2 — fetch full cast
    cast = _get_cast(tmdb_id, api_key)
    _max_cast = os.environ.get("CINEID_MAX_CAST")        # developer/test knob
    if _max_cast and _max_cast.isdigit():
        cast = cast[: int(_max_cast)]
    print(f"Cast fetched: {len(cast)} characters (top {_TOP_CAST_LIMIT})")
    emit("cast", total=len(cast))

    show_dir = _DATA_DIR / "shows" / show_id
    show_dir.mkdir(parents=True, exist_ok=True)

    db = CharacterDB()
    db.add_show(show_id, official_title, tmdb_id=tmdb_id)

    try:
        recogniser = FaceRecogniser()
        index = FaceIndex()

        ingested = 0
        skipped = 0
        total_images = 0
        seen_ids: set = set()

        with tqdm(cast, desc=official_title, unit="char", dynamic_ncols=True) as pbar:
            for rank, member in enumerate(pbar, start=1):
                actor_name: str = member.get("name", "Unknown")
                actor_id: int = member["id"]
                profile_path: Optional[str] = member.get("profile_path")
                episode_count: int = member.get("total_episode_count", 0)

                roles = member.get("roles", [])
                character_name: str = roles[0]["character"] if roles else actor_name
                character_id = _unique_character_id(show_id, character_name, seen_ids)

                pbar.set_postfix_str(character_name[:24])
                emit("faces", current=rank - 1, total=len(cast), name=character_name)

                char_dir = show_dir / "raw" / character_id
                char_dir.mkdir(parents=True, exist_ok=True)

                # Step 3 — download images
                try:
                    _download_tmdb_images(actor_id, profile_path, char_dir, api_key)
                    time.sleep(_TMDB_RATE_SLEEP)
                    _download_google_images(actor_name, official_title, char_dir)
                except Exception as exc:
                    tqdm.write(f"  [skip] {character_name}: download error — {exc}")
                    skipped += 1
                    continue

                # Step 4 — filter: exactly 1 face, det_score >= 0.7
                clean_images = _filter_images(char_dir, recogniser)
                tqdm.write(f"  {character_name}: {len(clean_images)} clean image(s)")

                if len(clean_images) < _MIN_CLEAN_IMAGES:
                    tqdm.write(f"  [skip] {character_name}: only {len(clean_images)} clean image(s), need {_MIN_CLEAN_IMAGES}")
                    skipped += 1
                    continue

                # Step 4b — drop reference photos that clearly are not this actor
                clean_images = _drop_outlier_photos(clean_images, recogniser, character_name)

                # Step 5 — build mean embedding gallery
                gallery = recogniser.build_gallery(clean_images)
                if gallery is None:
                    tqdm.write(f"  [skip] {character_name}: build_gallery returned None")
                    skipped += 1
                    continue
                mean_vec, image_count = gallery

                # Step 6 — save embedding + add to FAISS index
                np.save(str(show_dir / f"{character_id}.npy"), mean_vec)
                index.add_character(character_id, mean_vec)

                # Step 7 — persist to database
                db.add_character(
                    character_id=character_id,
                    show_id=show_id,
                    character_name=character_name,
                    actor_name=actor_name,
                    role=_role_from_episode_count(episode_count),
                    importance_rank=rank,
                    actor_tmdb_id=actor_id,
                    image_count=image_count,
                    confidence_level=_confidence_level(image_count),
                )

                ingested += 1
                total_images += image_count

    except BaseException:
        # Never leave the show stuck in 'ingesting' (the UI treats it as set up)
        db.update_show_status(show_id, "failed")
        db.close()
        raise

    emit("faces", current=len(cast), total=len(cast), name="")
    emit("faces_done", ingested=ingested, skipped=skipped)

    # Save FAISS index
    index_path = show_dir / "embeddings.faiss"
    index.save(index_path)

    # Mark show ready
    db.update_show_status(show_id, "ready" if ingested > 0 else "failed")
    db.refresh_show_character_count(show_id)
    db.close()

    # Step 8 — summary
    avg = total_images / ingested if ingested else 0.0
    print(
        f"\n{'─' * 48}\n"
        f"  Show             : {official_title}\n"
        f"  Characters ready : {ingested}\n"
        f"  Characters skipped: {skipped}\n"
        f"  Avg images/char  : {avg:.1f}\n"
        f"  Index saved      : {index_path}\n"
        f"{'─' * 48}"
    )


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest a TV show into CineID")
    parser.add_argument("--show", required=True, metavar="NAME",
                        help='Show name, e.g. "Breaking Bad"')
    parser.add_argument("--tmdb-id", type=int, default=None, metavar="ID",
                        help="TMDB show ID (skips search, uses this ID directly)")
    args = parser.parse_args()
    ingest_show(args.show, tmdb_id=args.tmdb_id)
