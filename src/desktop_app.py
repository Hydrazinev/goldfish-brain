from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
from pynput import keyboard, mouse as _pmouse

import objc
from AppKit import (
    NSAnimationContext,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageView,
    NSKernAttributeName,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSProgressIndicator,
    NSScreen,
    NSScrollView,
    NSStatusBar,
    NSTextField,
    NSTextView,
    NSView,
    NSWindow,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject
from PyObjCTools import AppHelper

try:
    from AppKit import NSAppearance, NSVisualEffectView
    _HAS_VIBRANCY = True
except ImportError:
    _HAS_VIBRANCY = False

try:
    # QuartzCore is already loaded by AppKit — no pyobjc-framework-Quartz needed
    _CAMediaTimingFunction = objc.lookUpClass("CAMediaTimingFunction")
    _EASE_OUT = _CAMediaTimingFunction.functionWithControlPoints____(
        0.23, 1.0, 0.32, 1.0)
except Exception:
    _EASE_OUT = None

_ROOT        = Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "data" / "config.json"

sys.path.insert(0, str(_ROOT / "src"))
import chrome_scripts
import config_store
import edit_menu
import keystore
import messages
import ui_window

_APP_STARTED = time.time()   # lets error text tell "still warming up" from "engine died"


def _save_config(changes: dict) -> None:
    """Atomically merge `changes` into config.json. Only the position keys
    (or an explicit "buddy" block) are written, so concurrent writers on the
    hotkey / buddy / menu threads cannot overwrite each other's keys."""
    try:
        config_store.update_config(_CONFIG_PATH, changes)
    except config_store.ConfigUnreadable as exc:
        print(f"[Goldfish Brain] config.json unreadable, not overwriting: {exc}")


def _save_position(cfg: dict) -> None:
    """Persist show/season/episode (and remember it for that show)."""
    try:
        if all(k in cfg for k in ("show_id", "season", "episode")):
            config_store.set_position(_CONFIG_PATH, cfg["show_id"],
                                      int(cfg["season"]), int(cfg["episode"]))
    except (config_store.ConfigUnreadable, ValueError) as exc:
        print(f"[Goldfish Brain] position not saved: {exc}")
_SERVER      = "http://localhost:8000"
_WIN_W       = 440
_PAD         = 18
_IW          = _WIN_W - 2 * _PAD   # inner width
_KW          = 100                  # key-column width in kv rows
_WORD_WRAP   = 0                    # NSLineBreakByWordWrapping


def _rgba(r, g, b, a=1.0):
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, a)

_C_BG       = _rgba(0.10, 0.10, 0.11, 0.97)   # solid fallback when vibrancy unavailable
_C_TINT     = _rgba(0.07, 0.07, 0.09, 0.55)   # dark tint laid over the blur material
_C_BORDER   = _rgba(1.00, 1.00, 1.00, 0.09)   # hairline card border
_C_TRACK    = _rgba(1.00, 1.00, 1.00, 0.12)   # confidence bar track
_C_WHITE    = NSColor.whiteColor()
_C_DIM      = _rgba(0.62, 0.62, 0.66)
_C_HINT     = _rgba(0.40, 0.40, 0.44)
_C_GREEN    = _rgba(0.20, 0.84, 0.41)
_C_AMBER    = _rgba(1.00, 0.80, 0.16)
_C_RED      = _rgba(1.00, 0.36, 0.34)
_C_SECTION  = _rgba(0.50, 0.50, 0.56)
_C_ARC_TEXT = _rgba(0.93, 0.93, 0.96)   # slightly brighter white for arc text
_C_LS_TEXT  = _rgba(0.74, 0.74, 0.78)   # brighter than _C_DIM for last-seen text

# Arc-type badges: label + accent colour  (rendered as a tinted pill next to
# the RIGHT NOW header — the pill background supplies the colour cue, so the
# label is just the word)
_ARC_BADGES = {
    "danger":        ("DANGER",     _C_RED),
    "conflict":      ("CONFLICT",   _rgba(1.00, 0.58, 0.22)),   # orange
    "alliance":      ("ALLIANCE",   _C_GREEN),
    "discovery":     ("DISCOVERY",  _rgba(0.35, 0.76, 1.00)),   # sky blue
    "deception":     ("DECEPTION",  _rgba(0.80, 0.50, 1.00)),   # purple
    "loss":          ("LOSS",       _rgba(0.64, 0.64, 0.92)),   # muted indigo
    "turning_point": ("TURNING PT", _C_AMBER),
}


def _detect_arc_type(text: str) -> "str | None":
    """Keyword-based arc type fallback for data populated before arc_type was added."""
    t = text.lower()
    if any(w in t for w in ("kill", "murder", "shot", "dead", "danger", "threat", "weapon", "gun", "bomb")):
        return "danger"
    if any(w in t for w in ("confront", "argument", "clash", "fight", "battle", "standoff", "arrested")):
        return "conflict"
    if any(w in t for w in ("alliance", "partner", "deal", "team", "agreement", "together", "trust")):
        return "alliance"
    if any(w in t for w in ("discover", "realize", "reveal", "secret", "truth", "identity", "learned")):
        return "discovery"
    if any(w in t for w in ("deceiv", "manipulat", "scheme", "plot", "betray", "hiding", "lied")):
        return "deception"
    if any(w in t for w in ("grief", "mourn", "loss", "death", "funeral", "miss", "died")):
        return "loss"
    if any(w in t for w in ("decision", "turning", "changed", "chose", "crossed", "finally", "irrevoc")):
        return "turning_point"
    return None


# ── config / capture / api ────────────────────────────────────────────────────

def _load_config() -> dict:
    cfg = config_store.read_config(_CONFIG_PATH)
    if cfg:
        return cfg
    return {"show_id": "breaking-bad", "season": 1, "episode": 1}


def _load_available_shows() -> list[dict]:
    """Return all fully-ingested shows from the database."""
    import sqlite3
    db_path = _ROOT / "data" / "cineid.db"
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT show_id, show_name FROM shows WHERE status = 'ready' ORDER BY show_name"
            ).fetchall()
        return [{"show_id": r[0], "show_name": r[1]} for r in rows]
    except Exception:
        return []


def _read_tmdb_key_safe() -> str:
    """Return TMDB API key from .env, or '' if not found."""
    return config_store.read_env_key(_ROOT / ".env", "TMDB_API_KEY")


def _load_char_avatar(character_id: str, show_id: str):
    """Return an NSImage for the character's reference photo, or None."""
    if not character_id or not show_id:
        return None
    raw_dir = _ROOT / "data" / "shows" / show_id / "raw" / character_id
    if not raw_dir.exists():
        return None
    # prefer TMDB profile pics (cleanest, no busy backgrounds)
    for name in ("tmdb_profile.jpg", "tmdb_0.jpg", "tmdb_1.jpg"):
        p = raw_dir / name
        if p.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(p))
            if img is not None:
                return img
    # fall back to first image found
    for pat in ("*.jpg", "*.jpeg", "*.png"):
        for p in raw_dir.glob(pat):
            img = NSImage.alloc().initWithContentsOfFile_(str(p))
            if img is not None:
                return img
    return None


def _chrome_running() -> bool:
    """`tell application "Google Chrome"` LAUNCHES Chrome if it is closed, so
    every AppleScript call must be gated on this."""
    try:
        return subprocess.run(["pgrep", "-x", "Google Chrome"],
                              capture_output=True, timeout=2).returncode == 0
    except Exception:
        return False


def _netflix_js(js: str) -> str:
    """Run JS in the Netflix watch tab (any Chrome window). Returns the JS result,
    '' if Chrome/Netflix isn't there. Never launches Chrome."""
    if not _chrome_running():
        return ""
    scr = chrome_scripts.find_netflix_tab_script(chrome_scripts.js_action(js))
    try:
        return subprocess.run(["osascript", "-e", scr], timeout=5,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _playback_progress() -> "float | None":
    """Share (0..1) of the current Netflix episode played so far, or None."""
    return chrome_scripts.parse_progress(_netflix_js(chrome_scripts.PROGRESS_JS))


def _show_netflix_controls() -> "tuple[float, float] | None":
    """Move mouse to center of Chrome window so Netflix player controls appear.

    Controls auto-hide after ~3 s; this ensures the [data-uia=video-title]
    element is in the DOM and the episode info is visible for both OCR and
    the DOM title calls.

    Returns the original mouse position so callers can restore it, or None
    if Chrome bounds couldn't be read.
    """
    import subprocess, time

    if not _chrome_running():
        return None

    script = '''tell application "Google Chrome"
    if (URL of active tab of front window) does not contain "netflix.com/watch" then return ""
    set b to bounds of front window
    return (item 1 of b) & " " & (item 2 of b) & " " & (item 3 of b) & " " & (item 4 of b)
end tell'''
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        parts = out.split()
        if len(parts) != 4:
            return None
        x1, y1, x2, y2 = (float(p) for p in parts)
        ctrl   = _pmouse.Controller()
        orig   = ctrl.position
        center = (int((x1 + x2) / 2), int((y1 + y2) / 2))
        ctrl.position = center
        time.sleep(0.45)        # wait for Netflix controls to animate in
        print(f"[Goldfish Brain] mouse → {center} (was {orig})")
        return orig
    except Exception as exc:
        print(f"[Goldfish Brain] mouse trigger failed: {exc}")
        return None


def _tmdb_find_season(show_id: str, episode_num: int, episode_title: str,
                      current_season: int = 1) -> "int | None":
    """Query TMDB to find which season contains episode_num with the given title.

    Tries seasons starting at current_season so the common case (same season)
    resolves in 1 request.  Returns None on any failure.
    """
    import sqlite3

    # Read API key from .env
    api_key = config_store.read_env_key(_ROOT / ".env", "TMDB_API_KEY")
    if not api_key:
        return None

    # Look up TMDB ID from database
    db_path = _ROOT / "data" / "cineid.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT tmdb_id FROM shows WHERE show_id = ?", (show_id,)
            ).fetchone()
        if not row:
            return None
        tmdb_id = row[0]
    except Exception:
        return None

    # Search order: current season first (fast-path), then 1..N for all others.
    # Never break on 404 alone — lower season numbers must still be checked.
    # Stop only after 3 consecutive 404s (we've gone past the last season).
    max_try = 15
    tried: set = set()
    order = [current_season] + list(range(1, max_try + 1))
    title_lower = episode_title.lower()
    consecutive_404 = 0

    for season_num in order:
        if season_num in tried:
            continue
        tried.add(season_num)
        try:
            url = (f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_num}"
                   f"?api_key={api_key}")
            req = urllib.request.Request(url, headers={"User-Agent": "CineID/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            consecutive_404 = 0
            for ep in data.get("episodes", []):
                if ep.get("episode_number") == episode_num:
                    ep_name = ep.get("name", "").lower()
                    if not title_lower or title_lower in ep_name or ep_name in title_lower:
                        print(f"[Goldfish Brain] TMDB: S{season_num:02d}E{episode_num:02d} = {ep.get('name')!r}")
                        return season_num
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                consecutive_404 += 1
                if consecutive_404 >= 3:
                    break   # well past the last season
                continue
        except Exception as exc:
            print(f"[Goldfish Brain] TMDB season {season_num} error: {exc}")
            break

    return None


def _detect_episode() -> dict:
    """Read current Netflix episode from Chrome DOM via AppleScript.

    Tries four strategies in priority order:
      1. window.netflix.falkorCache  — instant, no network, always accurate
      2. Netflix Shakti internal metadata API — reliable, uses in-page cookies
      3. [data-uia=video-title] child elements — works during playback
      4. aria-label / class-based DOM scan — last resort

    Updates config.json on success, then returns config.
    """
    import subprocess, re

    if not _chrome_running():
        print("[Goldfish Brain] Chrome not running — using config.json")
        return _load_config()

    # Step 1: get URL + title without JS (always works if Chrome is running)
    script_basic = chrome_scripts.find_netflix_tab_script(chrome_scripts.TAB_INFO_ACTION)
    try:
        basic = subprocess.run(["osascript", "-e", script_basic],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        print(f"[Goldfish Brain] chrome tab: {basic!r:.160}")
    except Exception as e:
        print(f"[Goldfish Brain] chrome tab error: {e}")
        return _load_config()

    if "netflix.com/watch" not in basic:
        print("[Goldfish Brain] Netflix/watch not active in Chrome — using config.json")
        return _load_config()

    # Extract the Netflix video ID from the URL (digits after /watch/)
    vid_match = re.search(r"netflix\.com/watch/(\d+)", basic)
    video_id  = vid_match.group(1) if vid_match else ""

    cfg = _load_config()

    def _as_js(js: str) -> str:
        """Run JS in the active Chrome tab, return stdout stripped."""
        scr = chrome_scripts.find_netflix_tab_script(chrome_scripts.js_action(js))
        try:
            return subprocess.run(["osascript", "-e", scr],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""

    # ── Call 1: find the Netflix build ID from inline <script> tags ───────────
    # The build ID is required for the Shakti metadata API path.
    # e.g.  BUILD_IDENTIFIER":"Akira"  →  build_id = "Akira"
    r_bid = _as_js(
        "(function(){"
        "var ss=document.querySelectorAll('script');"
        "for(var i=0;i<ss.length;i++){"
        "var idx=ss[i].textContent.indexOf('BUILD_IDENTIFIER');"
        "if(idx>=0)return ss[i].textContent.substring(idx,idx+55);"
        "}"
        "return 'nobid';"
        "})()"
    )
    print(f"[Goldfish Brain] build_id raw: {r_bid!r:.100}")
    build_id = ""
    if r_bid and not r_bid.startswith("jserr"):
        m = re.search(r'BUILD_IDENTIFIER[^A-Za-z0-9]*([A-Za-z0-9_-]+)', r_bid)
        if m:
            build_id = m.group(1)
    print(f"[Goldfish Brain] build_id: {build_id!r}")

    # ── Call 2: Netflix React context (always loaded, no network needed) ────────
    # window.netflix.reactContext.models holds all video state including
    # season + episode numbers for whatever is currently playing.
    if video_id:
        r_rc = _as_js(
            "(function(){"
            f"var vid='{video_id}';"
            "try{"
            "var rc=window.netflix&&window.netflix.reactContext;"
            "if(rc&&rc.models){"
            "var mk=Object.keys(rc.models);"
            "for(var i=0;i<mk.length;i++){"
            "var m=rc.models[mk[i]];"
            "var d=m&&(m.data||m);"
            "if(d&&typeof d==='object'){"
            "var s=d.seasonNumber||d.season||0;"
            "var e=d.episodeNumber||d.episode||0;"
            "if(s&&e)return 'rc:S'+s+'E'+e;"
            "}"
            "}"
            "return 'rc_models:'+mk.slice(0,12).join(',');"
            "}"
            "}catch(ex){return 'rc_err:'+ex;}"
            "return 'no_rc|nkeys:'+Object.keys(window.netflix||{}).slice(0,8).join(',');"
            "})()"
        )
        print(f"[Goldfish Brain] reactContext: {r_rc!r:.200}")
        if r_rc.startswith("rc:"):
            m = re.match(r"rc:S(\d+)E(\d+)", r_rc)
            if m:
                cfg["season"]  = int(m.group(1))
                cfg["episode"] = int(m.group(2))
                _save_position(cfg)
                print(f"[Goldfish Brain] detected (reactContext): S{cfg['season']:02d}E{cfg['episode']:02d}")
                return cfg

    # ── Call 3: Shakti metadata API (with Accept header, build ID from Call 1) ─
    if build_id and video_id:
        shakti_url = f"/api/shakti/{build_id}/metadata?movieid={video_id}"
        r_shakti = _as_js(
            "(function(){"
            f"var u='{shakti_url}';"
            "try{"
            "var x=new XMLHttpRequest();"
            "x.open('GET',u,false);x.withCredentials=true;"
            "x.setRequestHeader('Accept','application/json');"
            "x.send(null);"
            "if(x.status===200)return 'ok:'+x.responseText.substring(0,1500);"
            "return 'status:'+x.status;"
            "}catch(e){return 'err:'+e;}"
            "})()"
        )
        print(f"[Goldfish Brain] shakti: {r_shakti!r:.200}")
        if r_shakti.startswith("ok:"):
            raw = r_shakti[3:]
            try:
                data  = json.loads(raw)
                video = data.get("video") or data.get("episodeVideo") or data
                s = (video.get("season") or video.get("seasonNumber")
                     or video.get("seasonSequenceNumber"))
                e = (video.get("episode") or video.get("episodeNumber")
                     or video.get("sequenceNumber"))
                if s and e:
                    cfg["season"]  = int(s)
                    cfg["episode"] = int(e)
                    _save_position(cfg)
                    print(f"[Goldfish Brain] detected (shakti): S{cfg['season']:02d}E{cfg['episode']:02d}")
                    return cfg
                print(f"[Goldfish Brain] shakti keys: {list(video.keys())[:10]} sample: {raw[:150]!r}")
            except Exception as je:
                print(f"[Goldfish Brain] shakti parse: {je} — {raw[:150]!r}")

    # ── Call 4: player title children (splits "Breaking Bad|||E2|||Madrigal") ──
    # Uses t.children so each child span is a separate ||| part rather than
    # all being concatenated into one string like "Breaking BadE2Madrigal".
    r_dom = _as_js(
        "(function(){"
        "var t=document.querySelector('[data-uia=video-title]');"
        "if(!t)return document.title||'empty';"
        "var cs=t.children;"
        "if(cs.length>0){"
        "var parts=[];"
        "for(var i=0;i<cs.length;i++){"
        "var tx=cs[i].innerText.trim();"
        "if(tx&&parts.indexOf(tx)<0)parts.push(tx);"
        "}"
        "if(parts.length)return parts.join('|||');"
        "}"
        "return t.innerText.trim()||document.title||'empty';"
        "})()"
    )
    print(f"[Goldfish Brain] dom title: {r_dom!r:.150}")

    # ── Call 5: season text scan ("Season 5" anywhere on the page) ───────────
    r_season = _as_js(
        "(function(){"
        "var all=document.querySelectorAll('*');"
        "for(var i=0;i<all.length;i++){"
        "var kk=all[i].childNodes;"
        "for(var j=0;j<kk.length;j++){"
        "if(kk[j].nodeType===3){"
        "var tx=kk[j].textContent.trim();"
        "if(/^[Ss]eason\\s+\\d+$/.test(tx))return tx;"
        "}"
        "}"
        "}"
        "return '';"
        "})()"
    )
    print(f"[Goldfish Brain] season scan: {r_season!r:.60}")

    # Parse season from scan
    det_s = None
    if r_season:
        ms = re.match(r"^[Ss]eason\s+(\d+)$", r_season.strip(), re.IGNORECASE)
        if ms:
            det_s = int(ms.group(1))

    # Try full S/E patterns first
    for pattern in [
        r"[Ss](\d+)[:\s.·•xX]*[Ee](\d+)",
        r"[Ss]eason\s+(\d+)[,\s]+[Ee]pisode\s+(\d+)",
        r"[Ss]eason\s+(\d+)[^0-9]+(\d+)",
    ]:
        m = re.search(pattern, r_dom)
        if m:
            cfg["season"]  = int(m.group(1))
            cfg["episode"] = int(m.group(2))
            _save_position(cfg)
            print(f"[Goldfish Brain] detected (dom SE): S{cfg['season']:02d}E{cfg['episode']:02d}")
            return cfg

    # Parts split: look for isolated "E2" token
    parts = [p.strip() for p in r_dom.split("|||")] if "|||" in r_dom else []
    det_e = None
    for part in parts:
        m = re.match(r"^[Ee](\d{1,2})$", part)
        if m:
            det_e = int(m.group(1))
        if not det_s:
            ms2 = re.match(r"^[Ss]eason\s+(\d+)$", part, re.IGNORECASE)
            if ms2:
                det_s = int(ms2.group(1))

    # Fallback: search for uppercase E\d in raw string (e.g. "Breaking BadE2Madrigal")
    # Netflix always uses uppercase E for episode numbers so this is safe.
    if det_e is None:
        m = re.search(r'E(\d{1,2})(?!\d)', r_dom)
        if m:
            det_e = int(m.group(1))

    if det_e is not None:
        cfg["episode"] = det_e
        if det_s is not None:
            cfg["season"] = det_s
        else:
            # Season not in DOM — ask TMDB using the episode title
            ep_title = ""
            if "|||" in r_dom:
                parts_all = [p.strip() for p in r_dom.split("|||")]
                # Title part = anything that isn't the E\d token
                ep_title = " ".join(p for p in parts_all
                                    if not re.match(r"^[Ee]\d{1,2}$", p)
                                    and p.lower() not in (cfg.get("show_id", "").replace("-", " "), "breaking bad"))
                ep_title = ep_title.strip()
            print(f"[Goldfish Brain] asking TMDB for season (ep_title={ep_title!r})")
            found_s = _tmdb_find_season(
                cfg["show_id"], det_e, ep_title,
                current_season=cfg.get("season", 1)
            )
            if found_s:
                cfg["season"] = found_s
        _save_position(cfg)
        print(f"[Goldfish Brain] detected S{cfg['season']:02d}E{det_e:02d}")
        return cfg

    print("[Goldfish Brain] no S/E found — using config.json")
    return cfg


def _capture() -> "np.ndarray":
    import subprocess, tempfile, os
    import numpy as np
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(["screencapture", "-x", "-t", "jpg", tmp],
                       check=True, timeout=5)
        img_bgr = cv2.imread(tmp)
    finally:
        os.unlink(tmp)

    if img_bgr is None:
        raise RuntimeError("screencapture returned no image")
    if img_bgr.mean() < 8:
        raise RuntimeError("drm_blocked")

    h, w = img_bgr.shape[:2]
    if w > 1920:
        scale   = 1920 / w
        img_bgr = cv2.resize(img_bgr, (1920, int(h * scale)),
                             interpolation=cv2.INTER_AREA)
    return img_bgr


def _to_b64(img_bgr: "np.ndarray") -> str:
    _, jpeg = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(jpeg.tobytes()).decode()


def _ocr_episode(img_bgr: "np.ndarray") -> "tuple[int,int] | None":
    """macOS Vision OCR on the screenshot to find an S/E episode number.

    Uses PyObjC's runtime class lookup so pyobjc-framework-Vision is not needed.
    """
    import re, os, tempfile, ctypes
    import numpy as np
    try:
        import objc
        from Foundation import NSURL

        # Load Vision.framework into the ObjC runtime (idempotent if already loaded)
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/Vision.framework/Vision"
        )
        VNRecognizeTextRequest = objc.lookUpClass("VNRecognizeTextRequest")
        VNImageRequestHandler  = objc.lookUpClass("VNImageRequestHandler")

        h, w = img_bgr.shape[:2]
        # Scan only top 15 % + bottom 25 % — where Netflix shows episode overlays
        top    = img_bgr[:int(h * 0.15), :]
        bottom = img_bgr[int(h * 0.75):,  :]
        crop   = np.vstack([top, bottom])

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        try:
            cv2.imwrite(tmp, crop)
            url     = NSURL.fileURLWithPath_(tmp)
            req     = VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(1)   # fast mode
            handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})
            ret = handler.performRequests_error_([req], None)
            ok  = ret[0] if isinstance(ret, (tuple, list)) else bool(ret)
        finally:
            os.unlink(tmp)

        if not ok:
            return None

        texts = [obs.topCandidates_(1)[0].string()
                 for obs in (req.results() or [])
                 if obs.topCandidates_(1)]
        full = " ".join(texts)
        print(f"[Goldfish Brain] OCR ({len(texts)} segs): {full[:200]!r}")

        for pat in [
            r"[Ss](\d+)[:\s.·•×xX]*[Ee](\d+)",
            r"[Ss]eason\s+(\d+)[,\s]+[Ee]pisode\s+(\d+)",
        ]:
            m = re.search(pat, full)
            if m:
                return int(m.group(1)), int(m.group(2))
        print("[Goldfish Brain] OCR: no S/E pattern found")
    except Exception as exc:
        print(f"[Goldfish Brain] OCR error: {exc}")
    return None


def _load_show_index(show_id: str) -> None:
    """Ask the server to load a show's FAISS index if it isn't in memory yet."""
    if not show_id:
        return
    try:
        payload = json.dumps({"show_id": show_id}).encode()
        req = urllib.request.Request(
            f"{_SERVER}/load-show",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[Goldfish Brain] loaded index for {show_id}: {resp.read()}")
    except Exception as exc:
        print(f"[Goldfish Brain] load-show failed: {exc}")


def _call_identify(b64: str, cfg: dict, progress: "float | None" = None) -> dict:
    body = {
        "image":   b64,
        "show_id": cfg["show_id"],
        "season":  cfg["season"],
        "episode": cfg["episode"],
    }
    if progress is not None:        # how far into the episode: keeps notes spoiler-safe
        body["progress"] = progress
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{_SERVER}/identify",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ── layout helpers ────────────────────────────────────────────────────────────

def _split_lines(text: str, width: int, font_size: float) -> list[str]:
    chars_per_line = max(1, int(width / (font_size * 0.58)))
    words = text.split()
    lines, cur = [], ""
    for word in words:
        candidate = (cur + " " + word).strip()
        if len(candidate) <= chars_per_line:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [text]


def _wrap_h(text: str, width: int, font_size: float) -> int:
    lines = max(1, len(_split_lines(text, width, font_size)))
    return int(lines * font_size * 1.5) + 2


def _parse_rels(summary: str) -> list[str]:
    if not summary:
        return []
    for sep in (";", "\n", ","):
        if sep in summary:
            parts = [p.strip() for p in summary.split(sep) if p.strip()]
            if len(parts) > 1:
                return parts
    return [summary.strip()] if summary.strip() else []


def _font(size, bold=False, mono_digits=False):
    if mono_digits:
        try:
            # weight 0.0 = regular — keeps percentages from jittering in width
            return NSFont.monospacedDigitSystemFontOfSize_weight_(size, 0.0)
        except Exception:
            pass
    return NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)


def _tf(view, text, x, y, w, h, size, bold=False, color=None, wrap=False,
        kern=0.0, mono_digits=False):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    font = _font(size, bold=bold, mono_digits=mono_digits)
    if kern:
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: color or _C_WHITE,
            NSKernAttributeName: kern,
        }
        f.setAttributedStringValue_(
            NSAttributedString.alloc().initWithString_attributes_(text, attrs))
    else:
        f.setStringValue_(text)
        f.setTextColor_(color or _C_WHITE)
        f.setFont_(font)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setBordered_(False)
    f.setBackgroundColor_(NSColor.clearColor())
    view.addSubview_(f)


def _sep(view, x, y, w):
    line = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, 1))
    line.setWantsLayer_(True)
    line.layer().setBackgroundColor_(_rgba(1.0, 1.0, 1.0, 0.07).CGColor)
    view.addSubview_(line)


def _rect(view, x, y, w, h, color, radius=0.0):
    """Solid rounded rect — used for badge pills and the confidence capsule."""
    v = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.setWantsLayer_(True)
    v.layer().setBackgroundColor_(color.CGColor)
    if radius:
        v.layer().setCornerRadius_(radius)
    view.addSubview_(v)
    return v


# ── dismiss view ──────────────────────────────────────────────────────────────

class _DismissView(NSView):
    def drawRect_(self, rect):
        """Solid dark background — only when the blur material is unavailable
        (with vibrancy, the NSVisualEffectView + tint subviews supply the bg)."""
        if not _HAS_VIBRANCY:
            _C_BG.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                self.bounds(), 16.0, 16.0
            ).fill()

    def mouseDown_(self, event):
        self.window().orderOut_(None)

    def dismissWindow_(self, sender):
        self.window().orderOut_(None)

    def acceptsFirstResponder(self):
        return True

    def keyDown_(self, event):
        self.window().orderOut_(None)


# ── overlay ───────────────────────────────────────────────────────────────────

class Overlay:
    def __init__(self):
        self._win = None

    def _open(self, height: int):
        if self._win:
            self._win.orderOut_(None)

        sf      = NSScreen.mainScreen().visibleFrame()   # excludes menu bar + dock
        print(f"[Goldfish Brain] screen visible: {sf.size.width:.0f}x{sf.size.height:.0f}  origin=({sf.origin.x:.0f},{sf.origin.y:.0f})  card={height}px")
        max_h   = int(sf.size.height) - 40
        height  = min(height, max_h)
        xf      = sf.origin.x + sf.size.width - _WIN_W - 20
        y       = sf.origin.y + sf.size.height - height - 20

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(xf, y, _WIN_W, height),
            0, NSBackingStoreBuffered, False,
        )
        # Clear window bg so the rounded-corner view shows through at corners
        win.setBackgroundColor_(NSColor.clearColor())
        win.setOpaque_(False)
        win.setHasShadow_(True)
        win.setLevel_(3)            # NSFloatingWindowLevel
        win.setCollectionBehavior_(1)  # NSWindowCollectionBehaviorCanJoinAllSpaces

        view = _DismissView.alloc().initWithFrame_(NSMakeRect(0, 0, _WIN_W, height))
        # Layer-based clipping ensures all subviews also respect the rounded corners
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(16.0)
        view.layer().setMasksToBounds_(True)
        view.layer().setBorderWidth_(1.0)
        view.layer().setBorderColor_(_C_BORDER.CGColor)
        win.setContentView_(view)

        if _HAS_VIBRANCY:
            win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
            blur = NSVisualEffectView.alloc().initWithFrame_(
                NSMakeRect(0, 0, _WIN_W, height))
            blur.setMaterial_(13)        # NSVisualEffectMaterialHUDWindow
            blur.setBlendingMode_(0)     # behind-window
            blur.setState_(1)            # active
            view.addSubview_(blur)
            # dark tint over the blur so content keeps contrast on bright video
            _rect(view, 0, 0, _WIN_W, height, _C_TINT)

        # ✕ close button — top-right corner
        cb = NSButton.alloc().initWithFrame_(
            NSMakeRect(_WIN_W - 34, height - 30, 22, 22))
        cb.setBezelStyle_(0)
        cb.setBordered_(False)
        cb.setTitle_("")
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(12),
            NSForegroundColorAttributeName: _C_HINT,
        }
        cb.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_("✕", attrs))
        cb.setTarget_(view)
        cb.setAction_("dismissWindow:")
        view.addSubview_(cb)

        # Enter: short slide + fade (24px travel, strong ease-out) — less
        # distance than the old full off-screen slide, so it feels faster.
        win.setFrame_display_(NSMakeRect(xf + 24, y, _WIN_W, height), False)
        win.setAlphaValue_(0.0)
        win.makeKeyAndOrderFront_(None)
        win.makeFirstResponder_(view)
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(0.18)
        if _EASE_OUT is not None:
            ctx.setTimingFunction_(_EASE_OUT)
        win.animator().setFrame_display_(NSMakeRect(xf, y, _WIN_W, height), True)
        win.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

        self._win = win
        return view

    def _render(self, items, seps, total_h, avatar=None, bars=None, rects=None):
        """Render text items, separators, avatar thumbnail, accent bars, pills.

        avatar = (NSImage, av_x, av_yt, av_sz, border_color)
        bars   = list of (color, bar_yt, bar_h) — 3px colored left-edge bars
        rects  = list of (x, yt, w, h, color, radius) — badge pills / capsules,
          drawn *under* the text items.
          All y values measured from the top (same convention as items).
        """
        view = self._open(total_h)
        # The window is capped to the screen height. Anchor everything to the TOP of the
        # window, so a tall card loses its bottom (dismiss hint) instead of its header.
        total_h = view.frame().size.height
        if rects:
            for rx, ryt, rw, rh, rcol, rrad in rects:
                try:
                    _rect(view, rx, total_h - ryt - rh, rw, rh, rcol, radius=rrad)
                except Exception as exc:
                    print(f"[Goldfish Brain] rect failed: {exc}")
        for i, item in enumerate(items):
            x, yt, w, h, t, sz, bold, col, wrap = item[:9]
            extra = item[9] if len(item) > 9 else {}
            try:
                _tf(view, t, x, total_h - yt - h, w, h, sz,
                    bold=bold, color=col, wrap=wrap, **extra)
            except Exception as exc:
                print(f"[Goldfish Brain] item[{i}] FAILED text={t!r:.30} err={exc}")
        for yt in seps:
            try:
                _sep(view, _PAD, total_h - yt - 1, _IW)
            except Exception as exc:
                print(f"[Goldfish Brain] sep at yt={yt} FAILED: {exc}")
        if bars:
            for bar_col, bar_yt, bar_h in bars:
                if bar_h <= 0:
                    continue
                try:
                    bv = NSView.alloc().initWithFrame_(
                        NSMakeRect(_PAD - 7, total_h - bar_yt - bar_h, 3, bar_h))
                    bv.setWantsLayer_(True)
                    bv.layer().setBackgroundColor_(bar_col.CGColor)
                    view.addSubview_(bv)
                except Exception as exc:
                    print(f"[Goldfish Brain] accent bar failed: {exc}")
        if avatar is not None:
            try:
                av_img, av_x, av_yt, av_sz, av_border = avatar
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(av_x, total_h - av_yt - av_sz, av_sz, av_sz))
                iv.setImage_(av_img)
                iv.setImageScaling_(3)   # NSImageScaleAxesIndependently (fill)
                iv.setWantsLayer_(True)
                iv.layer().setCornerRadius_(av_sz / 2.0)
                iv.layer().setMasksToBounds_(True)
                if av_border is not None:
                    iv.layer().setBorderWidth_(2.5)
                    iv.layer().setBorderColor_(av_border.CGColor)
                view.addSubview_(iv)
            except Exception as exc:
                print(f"[Goldfish Brain] avatar render failed: {exc}")

    def show_message(self, text: str):
        items, seps, y = [], [], [0]

        def sp(h): y[0] += h
        def add(t, sz, h, col=None):
            items.append((_PAD, y[0], _IW, h, t, sz, False, col, False))
            y[0] += h

        sp(16)
        add(text, 13, 22, col=_C_DIM)
        sp(10)
        seps.append(y[0]); y[0] += 1
        sp(8)
        add("click anywhere to dismiss", 10, 13, col=_C_HINT)
        sp(10)
        self._render(items, seps, y[0])

    def show_result(self, result: dict):
        chars = result.get("characters", [])
        if not chars:
            if result.get("hidden_by_spoiler_shield"):
                msg = ("Recognised someone you haven't met yet —\n"
                       "hidden to avoid spoilers.")
            elif result.get("low_confidence"):
                msg = "Face detected — not recognised"
            else:
                msg = "No face detected — try a clearer scene"
            self.show_message(msg)
            return

        c          = chars[0]
        confidence = c.get("confidence", 0)
        name       = c.get("name", "Unknown")
        actor      = c.get("actor") or ""
        role       = c.get("role") or ""
        status     = c.get("status") or ""
        first_s    = c.get("first_seen_season")
        first_e    = c.get("first_seen_episode")
        last_s     = c.get("last_seen_season")
        last_e     = c.get("last_seen_episode")
        summary    = c.get("relationship_summary") or ""
        arc        = c.get("current_arc") or ""
        last_desc  = c.get("last_seen_description") or ""
        rels       = _parse_rels(summary)
        cfg_ep     = result.get("_cfg", {})
        cur_s      = cfg_ep.get("season")
        cur_e      = cfg_ep.get("episode")
        print(f"[Goldfish Brain] name={name}  status={status!r}  rels={rels}  arc={arc[:40]!r}  last_desc={last_desc[:40]!r}")

        dot_col = (_C_GREEN if confidence >= 0.65
                   else _C_AMBER if confidence >= 0.45 else _C_RED)
        role_map = {
            "main":      "Main character",
            "recurring": "Recurring character",
            "guest":     "Guest",
        }

        items, seps, bars, rects, y = [], [], [], [], [0]
        LH = 19   # line height for 12px font
        AV_SZ = 54  # avatar circle diameter

        def sp(h): y[0] += h
        def add(t, sz, h, bold=False, col=None, x=_PAD, w=_IW, **extra):
            items.append((x, y[0], w, h, t, sz, bold, col, False, extra))
            y[0] += h
        def section(title):
            add(title, 10, 13, bold=True, col=_C_SECTION, kern=1.4)
        def multiline(t, sz, col):
            for ln in _split_lines(t, _IW, sz):
                items.append((_PAD, y[0], _IW, LH, ln, sz, False, col, False))
                y[0] += LH
        def kv(key, val, h=17, vc=None):
            items.append((_PAD,       y[0], _KW,      h, key, 12, False, _C_DIM, False))
            items.append((_PAD + _KW, y[0], _IW - _KW, h, val, 12, False, vc or _C_WHITE, False))
            y[0] += h
        def pill(text, color, header_y, char_w=7.2):
            """Right-aligned tinted capsule badge on a section-header row."""
            pw = int(len(text) * char_w) + 20
            px = _PAD + _IW - pw
            rects.append((px, header_y - 2, pw, 17,
                          color.colorWithAlphaComponent_(0.16), 8.5))
            items.append((px + 10, header_y, pw - 10, 13, text, 9, True, color,
                          False, {"kern": 0.8}))
        def sep():
            seps.append(y[0]); y[0] += 1

        # ── Load actor avatar (right-side of header) ─────────────────────
        char_id    = c.get("character_id", "")
        show_id_v  = result.get("show_id", "")
        av_img     = _load_char_avatar(char_id, show_id_v) if char_id else None
        av_x       = _WIN_W - _PAD - AV_SZ          # right-aligned
        name_w     = _IW - 22 - (AV_SZ + 10) if av_img else _IW - 22

        # ── HEADER ──────────────────────────────────────────────────────
        sp(16)
        header_yt = y[0]   # remember for avatar placement

        items.append((_PAD,      y[0], 22,       30, "●",  16, False, dot_col,  False))
        items.append((_PAD + 22, y[0], name_w,   30, name, 22, True,  _C_WHITE, False))
        y[0] += 30
        sp(2)
        if actor:
            add(f"played by  {actor}", 12, 17, col=_C_DIM, w=name_w + 22)
        # Ensure header block is tall enough for the avatar
        min_after = header_yt + AV_SZ + 8
        y[0] = max(y[0], min_after)
        sp(10)

        # ── IDENTITY ────────────────────────────────────────────────────
        sep()
        sp(9)
        section("IDENTITY")
        sp(4)
        if cur_s is not None and cur_e is not None:
            kv("Watching", f"S{cur_s:02d}E{cur_e:02d}", vc=_C_AMBER)
        if role:
            kv("Role", role_map.get(role.lower(), role.title()))
        if status:
            sl = status.lower()
            sc = _C_GREEN if sl == "alive" else (_C_RED if sl == "dead" else _C_DIM)
            kv("Status", status.title(), vc=sc)
        if first_s is not None:
            ep = f"S{first_s:02d}E{first_e:02d}" if first_e is not None else f"Season {first_s}"
            kv("First seen", ep)
        if last_s is not None:
            ep = f"S{last_s:02d}E{last_e:02d}" if last_e is not None else f"Season {last_s}"
            kv("Last seen", ep)
        # Confidence: rounded capsule track + colored fill + monospaced %
        items.append((_PAD, y[0], _KW, 17, "Confidence", 12, False, _C_DIM, False))
        bar_w = 110
        rects.append((_PAD + _KW, y[0] + 5, bar_w, 6, _C_TRACK, 3.0))
        rects.append((_PAD + _KW, y[0] + 5,
                      max(6, int(bar_w * max(0.0, min(1.0, confidence)))),
                      6, dot_col, 3.0))
        items.append((_PAD + _KW + bar_w + 10, y[0], _IW - _KW - bar_w - 10, 17,
                      f"{confidence * 100:.0f}%", 12, False, dot_col, False,
                      {"mono_digits": True}))
        y[0] += 17
        sp(12)

        # ── NO STORY NOTES YET ───────────────────────────────────────────
        if c.get("limited"):
            sep()
            sp(10)
            multiline("Story notes aren't available for this show yet. "
                      "Open Goldfish Brain → Add a Show → Update to generate them.", 12, _C_DIM)
            sp(10)

        # ── RELATIONSHIPS ────────────────────────────────────────────────
        if rels:
            sep()
            sp(9)
            section("RELATIONSHIPS")
            sp(4)
            for rel in rels[:4]:
                multiline(f"• {rel}", 12, _C_WHITE)
                sp(2)
            sp(11)

        # ── RIGHT NOW ───────────────────────────────────────────────────
        if arc:
            arc_type_val = c.get("arc_type") or _detect_arc_type(arc)
            badge        = _ARC_BADGES.get(arc_type_val or "")

            sep()
            sp(10)
            arc_header_y = y[0]
            section("AS OF START OF EPISODE" if c.get("as_of") == "start" else "RIGHT NOW")
            if badge:
                badge_txt, badge_col = badge
                pill(badge_txt, badge_col, arc_header_y)
            sp(5)
            arc_content_start = y[0]
            multiline(arc, 13, _C_ARC_TEXT)   # 13px — punchier than 12
            arc_content_end = y[0]
            if badge:
                bars.append((badge[1], arc_content_start,
                              arc_content_end - arc_content_start))
            sp(12)

        # ── LAST SCENE ──────────────────────────────────────────────────
        if last_desc:
            sep()
            sp(10)
            ls_header_y = y[0]
            section("LAST SCENE")
            # Episode badge right-aligned (uses last_seen fields already in response)
            if last_s is not None:
                ep_badge = (f"S{last_s:02d}E{last_e:02d}"
                            if last_e is not None else f"S{last_s:02d}")
                pill(ep_badge, _C_AMBER, ls_header_y)
            sp(5)
            multiline(last_desc, 12, _C_LS_TEXT)
            sp(12)

        # ── ALSO DETECTED ────────────────────────────────────────────────
        if len(chars) > 1:
            sep()
            sp(9)
            section("ALSO DETECTED")
            sp(6)
            role_map2 = {"main": "Main", "recurring": "Recurring", "guest": "Guest"}
            for other in chars[1:4]:
                o_conf  = other.get("confidence", 0)
                o_name  = other.get("name", "Unknown")
                o_role  = other.get("role") or ""
                o_stat  = other.get("status") or ""
                o_dot   = (_C_GREEN if o_conf >= 0.65
                           else _C_AMBER if o_conf >= 0.45 else _C_RED)
                # dot + name on same row
                items.append((_PAD,      y[0], 16, 17, "●", 11, False, o_dot,   False))
                items.append((_PAD + 16, y[0], _IW - 16 - 60, 17, o_name, 12, True, _C_WHITE, False))
                # confidence right-aligned in same row
                conf_str = f"{o_conf * 100:.0f}%"
                items.append((_PAD + _IW - 44, y[0], 44, 17, conf_str, 11, False,
                              _C_DIM, False, {"mono_digits": True}))
                y[0] += 17
                # role · status subtitle
                sub_parts = []
                if o_role:
                    sub_parts.append(role_map2.get(o_role.lower(), o_role.title()))
                if o_stat:
                    sub_parts.append(o_stat.title())
                if sub_parts:
                    add("  " + "  ·  ".join(sub_parts), 11, 15, col=_C_DIM)
                sp(4)
            sp(7)

        # ── DISMISS HINT ─────────────────────────────────────────────────
        sep()
        sp(8)
        add("click anywhere to dismiss", 10, 13, col=_C_HINT)
        sp(10)

        avatar = None
        if av_img is not None:
            avatar = (av_img, av_x, header_yt, AV_SZ, dot_col)

        print(f"[Goldfish Brain] total_h={y[0]}  items={len(items)}  seps={len(seps)}  bars={len(bars)}  rects={len(rects)}  avatar={'yes' if avatar else 'no'}")
        self._render(items, seps, y[0], avatar=avatar, bars=bars, rects=rects)


# ── watch buddy bubble ────────────────────────────────────────────────────────

class BuddyBubble:
    """Floating iMessage-style chat bubble shown when the buddy speaks."""

    _WIN_W = 360
    _MARGIN = 20
    _AUTO_DISMISS = 8.0

    def show(self, text: str, buddy_name: str = "Alex") -> None:
        """Create and display the bubble. Call from main thread only."""
        self._dismiss_existing()

        pad    = 16
        inner_w = self._WIN_W - pad * 2

        chars_per_line = max(1, int(inner_w / (14 * 0.58)))
        words = text.split()
        lines, cur = [], ""
        for w in words:
            cand = (cur + " " + w).strip()
            if len(cand) <= chars_per_line:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        text_h = max(1, len(lines)) * int(14 * 1.5) + 4

        win_h = pad + 20 + 6 + text_h + pad

        sf = NSScreen.mainScreen().visibleFrame()
        x  = sf.origin.x + sf.size.width - self._WIN_W - self._MARGIN
        y  = sf.origin.y + self._MARGIN

        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, self._WIN_W, win_h),
            0,
            NSBackingStoreBuffered,
            False,
        )
        self._win.setLevel_(3)
        self._win.setOpaque_(False)
        self._win.setBackgroundColor_(NSColor.clearColor())
        self._win.setHasShadow_(True)
        self._win.setIgnoresMouseEvents_(False)

        cv = self._win.contentView()

        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self._WIN_W, win_h))
        bg.setWantsLayer_(True)
        bg.layer().setCornerRadius_(18)
        bg.layer().setMasksToBounds_(True)
        bg.layer().setBorderWidth_(1.0)
        bg.layer().setBorderColor_(_C_BORDER.CGColor)
        if _HAS_VIBRANCY:
            self._win.setAppearance_(
                NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
            blur = NSVisualEffectView.alloc().initWithFrame_(
                NSMakeRect(0, 0, self._WIN_W, win_h))
            blur.setMaterial_(13)        # NSVisualEffectMaterialHUDWindow
            blur.setBlendingMode_(0)     # behind-window
            blur.setState_(1)            # active
            bg.addSubview_(blur)
            _rect(bg, 0, 0, self._WIN_W, win_h, _C_TINT)
        else:
            bg.layer().setBackgroundColor_(_rgba(0.12, 0.12, 0.14, 0.96).CGColor)
        cv.addSubview_(bg)

        name_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(pad, win_h - pad - 20, inner_w, 18))
        name_lbl.setStringValue_(f"\U0001f4ac {buddy_name}")
        name_lbl.setFont_(NSFont.boldSystemFontOfSize_(12))
        name_lbl.setTextColor_(_rgba(0.55, 0.85, 1.0))
        name_lbl.setEditable_(False)
        name_lbl.setBordered_(False)
        name_lbl.setBackgroundColor_(NSColor.clearColor())
        cv.addSubview_(name_lbl)

        msg_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(pad, pad, inner_w, text_h))
        msg_lbl.setStringValue_(text)
        msg_lbl.setFont_(NSFont.systemFontOfSize_(14))
        msg_lbl.setTextColor_(NSColor.whiteColor())
        msg_lbl.setEditable_(False)
        msg_lbl.setBordered_(False)
        msg_lbl.setBackgroundColor_(NSColor.clearColor())
        msg_lbl.setLineBreakMode_(0)
        msg_lbl.setMaximumNumberOfLines_(0)
        cv.addSubview_(msg_lbl)

        # Enter: rise 12px + fade (200ms ease-out)
        self._win.setFrame_display_(
            NSMakeRect(x, y - 12, self._WIN_W, win_h), False)
        self._win.setAlphaValue_(0.0)
        self._win.makeKeyAndOrderFront_(None)
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(0.20)
        if _EASE_OUT is not None:
            ctx.setTimingFunction_(_EASE_OUT)
        self._win.animator().setFrame_display_(
            NSMakeRect(x, y, self._WIN_W, win_h), True)
        self._win.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

        self._dismiss_timer = threading.Timer(
            self._AUTO_DISMISS,
            lambda: AppHelper.callAfter(self._dismiss_existing, True),
        )
        self._dismiss_timer.daemon = True
        self._dismiss_timer.start()

    def _dismiss_existing(self, animated: bool = False) -> None:
        if hasattr(self, "_dismiss_timer"):
            self._dismiss_timer.cancel()
            del self._dismiss_timer
        if hasattr(self, "_win"):
            win = self._win
            del self._win
            try:
                if animated:
                    # Exit faster than enter — the system responding, not arriving
                    def _fade():
                        NSAnimationContext.currentContext().setDuration_(0.15)
                        win.animator().setAlphaValue_(0.0)
                    NSAnimationContext.runAnimationGroup_completionHandler_(
                        lambda ctx: _fade(), lambda: win.orderOut_(None))
                else:
                    win.orderOut_(None)
            except Exception:
                try:
                    win.orderOut_(None)
                except Exception:
                    pass


# ── buddy settings window ─────────────────────────────────────────────────────

class BuddySettingsWindow:
    """NSPanel for configuring the Watch Buddy persona."""

    _PERSONALITIES = ["Mac Style", "Hype Friend", "Film Nerd", "Chill Vibes"]
    _PERSONALITY_KEYS = ["mac", "hype", "film_nerd", "chill"]
    _COOLDOWNS = ["Off (every pause)", "2 minutes", "3 minutes", "5 minutes"]
    _COOLDOWN_VALUES = [0, 2, 3, 5]
    _TRIGGER_MODES = ["Timer (no mic)", "Audio silence"]
    _TRIGGER_KEYS = ["timer", "audio"]

    def __init__(self):
        self._win = None

    def show(self) -> None:
        if self._win is not None:
            self._win.makeKeyAndOrderFront_(None)
            return
        self._build()
        self._win.makeKeyAndOrderFront_(None)

    def _build(self) -> None:
        W, H = 340, 345
        sf = NSScreen.mainScreen().visibleFrame()
        x  = (sf.size.width  - W) / 2
        y  = (sf.size.height - H) / 2

        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H),
            15,
            NSBackingStoreBuffered,
            False,
        )
        self._win.setTitle_("Watch Buddy Settings")
        self._win.setLevel_(3)

        cfg   = _load_config()
        buddy = cfg.get("buddy", {
            "name": "Alex", "personality": "mac",
            "voice": "Samantha", "cooldown_minutes": 0, "enabled": True,
            "trigger_mode": "audio",
        })

        cv  = self._win.contentView()
        pad = 20
        row = H - 50

        def _label(text, y):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(pad, y, 120, 20))
            lbl.setStringValue_(text)
            lbl.setEditable_(False)
            lbl.setBordered_(False)
            lbl.setBackgroundColor_(NSColor.clearColor())
            cv.addSubview_(lbl)

        self._enabled_btn = NSButton.alloc().initWithFrame_(NSMakeRect(pad, row, W - pad*2, 24))
        self._enabled_btn.setButtonType_(3)
        self._enabled_btn.setTitle_("Enable Watch Buddy")
        self._enabled_btn.setState_(1 if buddy.get("enabled", True) else 0)
        cv.addSubview_(self._enabled_btn)
        row -= 44

        _label("Buddy Name:", row)
        self._name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 24))
        self._name_field.setStringValue_(buddy.get("name", "Alex"))
        cv.addSubview_(self._name_field)
        row -= 40

        _label("Personality:", row)
        self._personality_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        for p in self._PERSONALITIES:
            self._personality_btn.addItemWithTitle_(p)
        cur_p = buddy.get("personality", "hype")
        if cur_p in self._PERSONALITY_KEYS:
            self._personality_btn.selectItemAtIndex_(self._PERSONALITY_KEYS.index(cur_p))
        cv.addSubview_(self._personality_btn)
        row -= 40

        _label("Voice:", row)
        self._voice_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        voices = self._available_voices()
        for v in voices:
            self._voice_btn.addItemWithTitle_(v)
        cur_v = buddy.get("voice", "Samantha")
        if cur_v in voices:
            self._voice_btn.selectItemAtIndex_(voices.index(cur_v))
        cv.addSubview_(self._voice_btn)
        self._voices = voices
        row -= 40

        _label("Cooldown:", row)
        self._cooldown_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        for c in self._COOLDOWNS:
            self._cooldown_btn.addItemWithTitle_(c)
        cur_c = buddy.get("cooldown_minutes", 3)
        if cur_c in self._COOLDOWN_VALUES:
            self._cooldown_btn.selectItemAtIndex_(self._COOLDOWN_VALUES.index(cur_c))
        cv.addSubview_(self._cooldown_btn)
        row -= 40

        _label("Trigger:", row)
        self._trigger_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        for t in self._TRIGGER_MODES:
            self._trigger_btn.addItemWithTitle_(t)
        cur_t = buddy.get("trigger_mode", "timer")
        if cur_t in self._TRIGGER_KEYS:
            self._trigger_btn.selectItemAtIndex_(self._TRIGGER_KEYS.index(cur_t))
        cv.addSubview_(self._trigger_btn)
        row -= 50

        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 100 - pad, row, 100, 30))
        save_btn.setTitle_("Save")
        save_btn.setTarget_(self)
        save_btn.setAction_("onSave:")
        cv.addSubview_(save_btn)

    def _available_voices(self) -> list:
        import re
        import subprocess
        try:
            out = subprocess.check_output(["say", "-v", "?"], text=True, timeout=3)
            voices = []
            for line in out.splitlines():
                # Each line: "Voice Name          en_US    # description"
                m = re.match(r'^(.+?)\s{2,}(en_US|en_GB|en_AU|en_IE|en_ZA)', line)
                if m:
                    voices.append(m.group(1).strip())
            return voices or ["Samantha", "Tom", "Karen"]
        except Exception:
            return ["Samantha", "Tom", "Karen"]

    def onSave_(self, _sender):
        cfg   = _load_config()
        p_idx = self._personality_btn.indexOfSelectedItem()
        c_idx = self._cooldown_btn.indexOfSelectedItem()
        v_idx = self._voice_btn.indexOfSelectedItem()
        t_idx = self._trigger_btn.indexOfSelectedItem()
        if p_idx < 0 or p_idx >= len(self._PERSONALITY_KEYS):
            p_idx = 0
        if c_idx < 0 or c_idx >= len(self._COOLDOWN_VALUES):
            c_idx = 1
        if v_idx < 0 or v_idx >= len(self._voices):
            v_idx = 0
        if t_idx < 0 or t_idx >= len(self._TRIGGER_KEYS):
            t_idx = 0
        cfg["buddy"] = {
            "name":             self._name_field.stringValue(),
            "personality":      self._PERSONALITY_KEYS[p_idx],
            "voice":            self._voices[v_idx],
            "cooldown_minutes": self._COOLDOWN_VALUES[c_idx],
            "enabled":          bool(self._enabled_btn.state()),
            "trigger_mode":     self._TRIGGER_KEYS[t_idx],
        }
        _save_config({"buddy": cfg["buddy"]})
        self._win.orderOut_(None)
        self._win = None


# ── setup wizard ─────────────────────────────────────────────────────────────

class SetupWindow(NSObject):
    """Floating GUI panel — lets non-technical users add a new show.

    No terminal needed.  The window:
      1. Asks for a show name → searches TMDB
      2. Shows up to 6 results in a dropdown (pick the right one)
      3. "Set Up" button runs ingest + populate in a background thread
      4. An animated progress bar + live log keep the user informed
      5. Writes config.json and prints a "Ready" banner when done

    Setup is one-time per show; afterwards the show appears in the
    menu bar dropdown for instant switching.
    """

    @objc.python_method
    def init_window(self):
        """Call instead of init — returns self for chaining."""
        self._results  = []   # TMDB search results
        self._selected = None # currently selected result dict
        self._win      = None
        self._build()
        return self

    # ── window construction ───────────────────────────────────────────

    @objc.python_method
    def _build(self):
        W, H, M = 460, 420, 18

        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, W, H),
            1 | 2,                    # Titled | Closable
            NSBackingStoreBuffered,
            False,
        )
        self._win.setTitle_("Add a New Show — Goldfish Brain")
        self._win.setLevel_(3)        # NSFloatingWindowLevel — stays above Netflix
        cv = self._win.contentView()

        # helper: plain read-only label
        def plain_lbl(text, x, y, w, h, size=13, color=None, align=0):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            f.setStringValue_(text)
            f.setEditable_(False)
            f.setBordered_(False)
            f.setDrawsBackground_(False)
            f.setFont_(NSFont.systemFontOfSize_(size))
            f.setAlignment_(align)
            if color:
                f.setTextColor_(color)
            cv.addSubview_(f)
            return f

        # ── Row 1: show name input ────────────────────────────────────────
        plain_lbl("Show name:", M, H - 48, 96, 22)
        self._tf = NSTextField.alloc().initWithFrame_(NSMakeRect(M + 100, H - 50, W - M*2 - 104, 28))
        self._tf.setPlaceholderString_("e.g.  Better Call Saul")
        self._tf.setFont_(NSFont.systemFontOfSize_(13))
        cv.addSubview_(self._tf)

        # ── Row 2: search button ──────────────────────────────────────────
        self._btn_search = NSButton.alloc().initWithFrame_(
            NSMakeRect((W - 170)//2, H - 96, 170, 32))
        self._btn_search.setTitle_("🔍  Search TMDB")
        self._btn_search.setBezelStyle_(1)   # NSBezelStyleRounded
        self._btn_search.setTarget_(self)
        self._btn_search.setAction_("onSearch:")
        cv.addSubview_(self._btn_search)

        # ── Row 3: result picker (hidden until search completes) ──────────
        self._lbl_pick = plain_lbl("Select the right show:", M, H - 140, W - M*2, 18,
                                   size=11, color=_C_DIM)
        self._lbl_pick.setHidden_(True)

        self._popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(M, H - 170, W - M*2, 26))
        self._popup.setTarget_(self)
        self._popup.setAction_("onPickResult:")
        self._popup.setHidden_(True)
        cv.addSubview_(self._popup)

        # ── Row 4: setup button (hidden until result selected) ────────────
        self._btn_setup = NSButton.alloc().initWithFrame_(
            NSMakeRect((W - 220)//2, H - 222, 220, 36))
        self._btn_setup.setTitle_("▶  Set Up This Show")
        self._btn_setup.setBezelStyle_(1)
        self._btn_setup.setTarget_(self)
        self._btn_setup.setAction_("onSetup:")
        self._btn_setup.setHidden_(True)
        cv.addSubview_(self._btn_setup)

        # ── Progress bar (hidden until setup starts) ──────────────────────
        self._progress = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(M, H - 246, W - M*2, 12))
        self._progress.setStyle_(0)          # bar style
        self._progress.setIndeterminate_(True)
        self._progress.setMinValue_(0.0)
        self._progress.setMaxValue_(1.0)
        self._progress.setDoubleValue_(0.0)
        self._progress.setHidden_(True)
        cv.addSubview_(self._progress)

        # ── Live log scroll view (hidden until setup starts) ──────────────
        self._scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(M, 58, W - M*2, 162))  # taller log area
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setBorderType_(2)       # NSBezelBorder
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W - M*2 - 15, 400))
        tv.setEditable_(False)
        tv.setFont_(NSFont.monospacedSystemFontOfSize_weight_(10, 0.0))
        tv.setTextColor_(_C_DIM)
        self._scroll.setDocumentView_(tv)
        self._tv = tv
        self._scroll.setHidden_(True)
        cv.addSubview_(self._scroll)

        # ── Cancel button (bottom-left, hidden until setup starts) ────────
        self._btn_cancel = NSButton.alloc().initWithFrame_(NSMakeRect(M, 16, 110, 28))
        self._btn_cancel.setTitle_("✖  Cancel")
        self._btn_cancel.setBezelStyle_(1)
        self._btn_cancel.setTarget_(self)
        self._btn_cancel.setAction_("onCancel:")
        self._btn_cancel.setHidden_(True)
        cv.addSubview_(self._btn_cancel)

        # ── Status / hint text at the very bottom (full-width, centered) ──
        self._status = plain_lbl("", M, 14, W - M*2, 26, size=12, align=2)

    # ── public ────────────────────────────────────────────────────────

    @objc.python_method
    def show(self):
        """(Re-)open the window, resetting to the initial search state."""
        self._tf.setStringValue_("")
        self._lbl_pick.setHidden_(True)
        self._popup.setHidden_(True)
        self._btn_setup.setHidden_(True)
        self._progress.setHidden_(True)
        self._progress.setIndeterminate_(True)
        self._scroll.setHidden_(True)
        self._tv.setString_("")
        self._btn_cancel.setHidden_(True)
        self._status.setStringValue_("One-time setup per show — enter a name to get started.")
        self._btn_search.setEnabled_(True)
        self._btn_setup.setEnabled_(True)
        self._popup.setEnabled_(True)
        self._results    = []
        self._selected   = None
        self._cancelled  = False
        self._proc       = None
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._win.makeKeyAndOrderFront_(None)
        self._win.center()

    # ── ObjC actions (called by buttons / popup) ──────────────────────

    def onSearch_(self, _sender):
        name = self._tf.stringValue().strip()
        if not name:
            self._status.setStringValue_("Please enter a show name.")
            return
        self._status.setStringValue_("Searching TMDB…")
        self._btn_search.setEnabled_(False)
        self._lbl_pick.setHidden_(True)
        self._popup.setHidden_(True)
        self._btn_setup.setHidden_(True)
        threading.Thread(target=self._do_search, args=(name,), daemon=True).start()

    def onPickResult_(self, _sender):
        idx = self._popup.indexOfSelectedItem()
        if 0 <= idx < len(self._results):
            self._selected = self._results[idx]

    def onSetup_(self, _sender):
        if not self._selected:
            return
        self._cancelled = False
        self._proc      = None
        self._btn_search.setEnabled_(False)
        self._btn_setup.setEnabled_(False)
        self._popup.setEnabled_(False)
        self._btn_setup.setHidden_(True)
        self._lbl_pick.setHidden_(True)
        self._popup.setHidden_(True)
        self._scroll.setHidden_(False)
        self._progress.setHidden_(False)
        self._progress.setIndeterminate_(True)
        self._progress.startAnimation_(None)
        self._btn_cancel.setHidden_(False)
        self._status.setStringValue_("Step 1 of 2 — downloading cast images…")
        threading.Thread(target=self._do_setup, args=(self._selected,), daemon=True).start()

    def onCancel_(self, _sender):
        """Stop the running ingest/populate process and reset the wizard."""
        self._cancelled = True
        proc = getattr(self, "_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        AppHelper.callAfter(self._on_cancel_done)

    @objc.python_method
    def _on_cancel_done(self):
        self._btn_cancel.setHidden_(True)
        self._progress.stopAnimation_(None)
        self._progress.setHidden_(True)
        self._scroll.setHidden_(True)
        self._btn_search.setEnabled_(True)
        self._btn_setup.setEnabled_(True)
        self._btn_setup.setHidden_(False)
        self._popup.setEnabled_(True)
        self._popup.setHidden_(False)
        self._lbl_pick.setHidden_(False)
        self._status.setStringValue_("Setup cancelled — you can try again.")

    # ── background workers (python-only, not ObjC selectors) ──────────

    @objc.python_method
    def _do_search(self, name: str):
        key = _read_tmdb_key_safe()
        if not key:
            AppHelper.callAfter(self._on_search_done, [], "TMDB_API_KEY not found — add it to .env")
            return
        try:
            q   = urllib.parse.urlencode({"api_key": key, "query": name, "language": "en-US"})
            req = urllib.request.Request(
                f"https://api.themoviedb.org/3/search/tv?{q}",
                headers={"User-Agent": "CineID/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            AppHelper.callAfter(self._on_search_done, data.get("results", [])[:6], "")
        except Exception as exc:
            AppHelper.callAfter(self._on_search_done, [], f"Search failed: {exc}")

    @objc.python_method
    def _on_search_done(self, results: list, err: str):
        self._btn_search.setEnabled_(True)
        if err:
            self._status.setStringValue_(err)
            return
        if not results:
            self._status.setStringValue_("No results — try a different spelling.")
            return
        self._results = results
        self._popup.removeAllItems()
        for r in results:
            year = r.get("first_air_date", "????")[:4]
            self._popup.addItemWithTitle_(f"{r['name']}  ({year})")
        self._selected = results[0]
        self._lbl_pick.setHidden_(False)
        self._popup.setHidden_(False)
        self._btn_setup.setHidden_(False)
        self._status.setStringValue_("Setup is one-time only — pick the right show, then tap Set Up.")

    @objc.python_method
    def _do_setup(self, show: dict):
        import re, subprocess, sqlite3, os
        tmdb_id      = show["id"]
        show_name    = show["name"]
        show_id      = re.sub(r"[^a-z0-9]+", "-", show_name.lower()).strip("-")
        python       = str(_ROOT / "venv" / "bin" / "python")
        # Disable tqdm animation so output is clean line-by-line text
        env = {**os.environ, "TQDM_DISABLE": "1", "PYTHONUNBUFFERED": "1"}

        def log(msg: str):
            AppHelper.callAfter(self._append_log, msg + "\n")

        # Lines we never want to show in the wizard log
        _NOISE = ("NotOpenSSLWarning", "only supports OpenSSL", "warnings.warn",
                  "site-packages/", 'File "/', "Traceback (most recent",
                  "Exception in thread")
        def _keep(ln: str) -> bool:
            if not ln.strip():
                return False
            if any(s in ln for s in _NOISE):
                return False
            if ln.startswith("    ") and ('"' in ln or "^" in ln or "self." in ln):
                return False
            # bare exception lines, e.g. "TypeError: 'NoneType' object …"
            if re.match(r"^[A-Za-z_]\w*(Error|Exception|Warning):", ln):
                return False
            return True

        log(f"Setting up  {show_name}  (TMDB #{tmdb_id})")
        log("─" * 46)

        # After step 1 we read the real show_id back from the DB
        # (ingest slugifies the official title, which may differ from our slug)
        actual_show_id = show_id

        for step_idx in range(2):
            if step_idx == 0:
                status_msg = f"Step 1 of 2 — downloading cast images…"
                cmd = [python, str(_ROOT / "src" / "ingest.py"),
                       "--show", show_name, "--tmdb-id", str(tmdb_id)]
            else:
                # Fetch the show_id that ingest actually stored
                try:
                    with sqlite3.connect(str(_ROOT / "data" / "cineid.db")) as conn:
                        row = conn.execute(
                            "SELECT show_id FROM shows WHERE tmdb_id = ?", (tmdb_id,)
                        ).fetchone()
                    if row:
                        actual_show_id = row[0]
                except Exception:
                    pass
                status_msg = f"Step 2 of 2 — generating episode arc data…"
                cmd = [python, str(_ROOT / "src" / "populate_metadata.py"),
                       "--show", actual_show_id, "--all-episodes"]

            AppHelper.callAfter(self._set_status, status_msg)
            log(status_msg)

            total   = 0    # total characters (parsed from output)
            seen    = set() # character names already counted (deduplication)
            base    = step_idx * 0.5   # bar offset: 0.0 for step 1, 0.5 for step 2

            AppHelper.callAfter(self._set_progress_mode, True, 0.0)

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        bufsize=1, env=env)
                self._proc = proc
                for line in proc.stdout:
                    if getattr(self, "_cancelled", False):
                        proc.terminate()
                        return
                    ln = line.rstrip()

                    # ── step 1: ingest ───────────────────────────────────
                    # ingest.py prints per character (any of these 4):
                    #   "  Name: N clean image(s)"              ← always printed
                    #   "  [skip] Name: only N clean …"         ← also printed if N < min
                    #   "  [skip] Name: download error — …"     ← printed on error
                    #   "  [skip] Name: build_gallery returned None"
                    # A character may produce TWO lines (lines 1+2 above).
                    # We deduplicate via `seen` so each name is counted once.
                    if step_idx == 0:
                        m = re.search(r"Cast fetched: (\d+)", ln)
                        if m:
                            total = int(m.group(1))
                            AppHelper.callAfter(self._set_progress_mode, False, base)
                            AppHelper.callAfter(self._set_status,
                                f"Step 1 of 2 — 0 / {total} cast members")

                        char_name = None
                        # "  [skip] Name: reason"
                        ms = re.match(r"  \[skip\] (.+?): (only |download |build_gallery)", ln)
                        if ms:
                            char_name = ms.group(1)
                        else:
                            # "  Name: N clean image(s)"  — must end with clean image(s)
                            mc = re.match(r"  (.+?): \d+ clean image\(s\)$", ln)
                            if mc:
                                char_name = mc.group(1)

                        if char_name and char_name not in seen:
                            seen.add(char_name)
                            done = len(seen)
                            if total > 0:
                                pct = base + min(done / total, 1.0) * 0.5
                                AppHelper.callAfter(self._set_progress_mode, False, pct)
                                AppHelper.callAfter(self._set_status,
                                    f"Step 1 of 2 — {done} / {total} cast members")

                    # ── step 2: populate ─────────────────────────────────
                    # populate_metadata.py prints:
                    #   "Generating arc data for N characters × M episodes …"
                    #   "  Name … done (N episodes saved)"
                    #   "  Name … FAILED — reason"
                    elif step_idx == 1:
                        m = re.search(r"Generating arc data for (\d+) characters", ln)
                        if m:
                            total = int(m.group(1))
                            seen.clear()
                            AppHelper.callAfter(self._set_progress_mode, False, base)
                            AppHelper.callAfter(self._set_status,
                                f"Step 2 of 2 — 0 / {total} characters")

                        mp = re.match(r"  (.+?) …", ln)
                        if mp and (re.search(r"done \(\d+ episodes", ln)
                                   or "FAILED" in ln):
                            char_name = mp.group(1)
                            if char_name not in seen:
                                seen.add(char_name)
                                done = len(seen)
                                if total > 0:
                                    pct = base + min(done / total, 1.0) * 0.5
                                    AppHelper.callAfter(self._set_progress_mode, False, pct)
                                    AppHelper.callAfter(self._set_status,
                                        f"Step 2 of 2 — {done} / {total} characters")

                    if _keep(ln):
                        log(ln)

                proc.wait()
                self._proc = None
                if getattr(self, "_cancelled", False):
                    return
                if proc.returncode != 0:
                    log(f"✗  Failed (exit code {proc.returncode})")
                    AppHelper.callAfter(self._on_setup_done, False, actual_show_id, show_name)
                    return
            except Exception as exc:
                self._proc = None
                if getattr(self, "_cancelled", False):
                    return
                log(f"✗  {exc}")
                AppHelper.callAfter(self._on_setup_done, False, actual_show_id, show_name)
                return
            log("✓  Done")

        # Update config.json to point at the new show
        try:
            _save_config({"show_id": actual_show_id, "season": 1, "episode": 1})
            log(f"✓  config.json → {actual_show_id}  S01E01")
        except Exception as exc:
            log(f"⚠  config update failed: {exc}")

        AppHelper.callAfter(self._on_setup_done, True, actual_show_id, show_name)

    @objc.python_method
    def _append_log(self, text: str):
        cur  = self._tv.string() or ""
        self._tv.setString_(cur + text)
        end  = len(self._tv.string())
        self._tv.scrollRangeToVisible_((end, 0))

    @objc.python_method
    def _set_status(self, msg: str):
        self._status.setStringValue_(msg)

    @objc.python_method
    def _set_progress_mode(self, indeterminate: bool, value: float = 0.0):
        """Switch between spinning (indeterminate) and filled (determinate) bar.
        value is 0.0–1.0 (matches setMaxValue_(1.0) from _build)."""
        if indeterminate:
            self._progress.setIndeterminate_(True)
            self._progress.startAnimation_(None)
        else:
            self._progress.stopAnimation_(None)
            self._progress.setIndeterminate_(False)
            self._progress.setDoubleValue_(value)

    @objc.python_method
    def _on_setup_done(self, success: bool, show_id: str, show_name: str):
        self._progress.stopAnimation_(None)
        self._progress.setIndeterminate_(False)
        self._progress.setDoubleValue_(1.0 if success else 0.0)
        self._btn_cancel.setHidden_(True)
        if success:
            self._status.setStringValue_(
                f"✅  {show_name} is ready!  Press Cmd+Shift+X while watching."
            )
            # Refresh the menu bar so the new show appears immediately
            delegate = getattr(self, "_delegate_ref", None)
            if delegate is not None:
                try:
                    AppHelper.callAfter(delegate._rebuild_menu)
                except Exception:
                    pass
        else:
            self._status.setStringValue_("✗  Setup failed — see the log above.")
            self._btn_search.setEnabled_(True)
            self._btn_setup.setEnabled_(True)
            self._btn_setup.setHidden_(False)
            self._popup.setEnabled_(True)
            self._popup.setHidden_(False)
            self._lbl_pick.setHidden_(False)


# ── app delegate ──────────────────────────────────────────────────────────────

class _Delegate(NSObject):
    def applicationDidFinishLaunching_(self, _notif):
        # Without an Edit menu, Cmd+V/C/X/A do nothing in the web UI's text fields
        edit_menu.install(NSApplication.sharedApplication())
        self._overlay            = Overlay()
        self._busy               = threading.Lock()
        self._setup_win          = None
        self._buddy_bubble       = BuddyBubble()
        self._buddy_settings_win = BuddySettingsWindow()

        import sys as _sys
        _sys.path.insert(0, str(_ROOT / "src"))
        from buddy import BuddyListener, ensure_buddy_defaults
        # Defaults first: the listener reads trigger_mode as soon as it starts.
        ensure_buddy_defaults(_CONFIG_PATH)
        self._buddy_listener = BuddyListener(
            config_path=_CONFIG_PATH,
            db_path=_ROOT / "data" / "cineid.db",
            on_message=self._on_buddy_message,
        )
        self._buddy_listener.start()

        # ── menu bar icon ─────────────────────────────────────────────
        # Build menu statically at startup (no delegate = no runtime callbacks
        # that can race with the pynput thread on macOS 26).
        try:
            sb = NSStatusBar.systemStatusBar()
            self._status_item = sb.statusItemWithLength_(-1.0)
            try:
                btn = self._status_item.button()
                if btn is not None:
                    # Try SF Symbol "film.fill" first (macOS 11+)
                    icon_set = False
                    try:
                        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                            "film.fill", "Goldfish Brain")
                        if img is not None:
                            img.setSize_(NSMakeSize(16, 16))
                            btn.setImage_(img)
                            btn.setTitle_("")
                            icon_set = True
                    except Exception:
                        pass
                    if not icon_set:
                        btn.setTitle_("CID")
            except Exception:
                pass
            self._menu = NSMenu.alloc().init()
            self._rebuild_menu()
            self._status_item.setMenu_(self._menu)
        except Exception as e:
            print(f"[Goldfish Brain] menu bar unavailable: {e}")
            self._status_item = None
            self._menu = None
        # ─────────────────────────────────────────────────────────────

        listener = keyboard.GlobalHotKeys({"<cmd>+<shift>+x": self._on_hotkey})
        listener.start()
        self._listener = listener
        print("Goldfish Brain  —  Cmd+Shift+X to identify  ·  CID in menu bar")
        AppHelper.callLater(1.5, self._maybe_open_first_run)

    def _maybe_open_first_run(self):
        """Nothing set up yet (no TMDB key, or no ready show): open the guided UI."""
        try:
            has_key = bool(keystore.get_key("TMDB_API_KEY", _ROOT / ".env"))
        except Exception:
            has_key = False
        if not has_key or not _load_available_shows():
            ui_window.open_ui("")

    def _rebuild_menu(self):
        """Replace all menu items with the current shows list.
        Safe to call any time from the main thread."""
        if self._menu is None:
            return
        self._menu.removeAllItems()
        self._populate_menu(self._menu)

    def _populate_menu(self, menu):
        def add(title, action, target=None):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(target or self)
            menu.addItem_(item)
            return item

        # Where we are right now (disabled line)
        cfg = _load_config()
        shows = _load_available_shows()
        names = {x["show_id"]: x["show_name"] for x in shows}
        cur = cfg.get("show_id", "")
        if cur in names:
            try:
                where = f"{names[cur]} · S{int(cfg['season']):02d}E{int(cfg['episode']):02d}"
            except Exception:
                where = names[cur]
            hdr = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(where, "", "")
            hdr.setEnabled_(False)
            menu.addItem_(hdr)
            menu.addItem_(NSMenuItem.separatorItem())

        add("Open Goldfish Brain…", "onOpenUI:")
        add("Add a Show…", "onAddShow:")

        if shows:
            menu.addItem_(NSMenuItem.separatorItem())
            hdr = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Switch show", "", "")
            hdr.setEnabled_(False)
            menu.addItem_(hdr)
            for sh in shows:
                mark = "✓  " if sh["show_id"] == cur else "    "
                item = add(mark + sh["show_name"], "onSwitchShow:")
                item.setRepresentedObject_(sh["show_id"])

        menu.addItem_(NSMenuItem.separatorItem())
        add("Watch Buddy Settings…", "onBuddySettings:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Quit Goldfish Brain", "terminate:", NSApplication.sharedApplication())

    def onOpenUI_(self, _sender):
        ui_window.open_ui("")

    def onAddShow_(self, _sender):
        ui_window.open_ui("add")

    def onSwitchShow_(self, sender):
        show_id = sender.representedObject()
        try:
            config_store.select_show(_CONFIG_PATH, show_id)   # restores that show's own position
        except config_store.ConfigUnreadable as exc:
            print(f"[Goldfish Brain] could not switch show: {exc}")
            return
        self._rebuild_menu()           # move the ✓ to the chosen show
        threading.Thread(target=_load_show_index, args=(show_id,), daemon=True).start()
        print(f"[Goldfish Brain] switched to {show_id}")

    def _on_buddy_message(self, text: str, voice: str) -> None:
        cfg        = _load_config()
        buddy      = cfg.get("buddy", {})
        buddy_name = buddy.get("name", "Alex")
        self._buddy_bubble.show(text, buddy_name)
        threading.Thread(target=self._speak_and_resume,
                         args=(voice, text), daemon=True).start()

    def _speak_and_resume(self, voice: str, text: str) -> None:
        # Only resume what we paused: if the viewer had already paused (or
        # there is no video), leave playback alone.
        was_playing = _netflix_js(
            "(function(){var v=document.querySelector('video');"
            "if(v&&!v.paused){v.pause();return 'yes';}return 'no';})()"
        ) == "yes"
        try:
            from buddy import _safe_say_text
            subprocess.run(["say", "-v", voice, _safe_say_text(text)])
        finally:
            if was_playing:
                _netflix_js("document.querySelector('video') && document.querySelector('video').play()")

    def onBuddySettings_(self, _sender):
        ui_window.open_ui("settings")

    def _on_hotkey(self):
        if self._busy.locked():
            return
        # Instant feedback: the identify below takes a second or two
        AppHelper.callAfter(self._overlay.show_message, "Identifying…")
        threading.Thread(target=self._identify, daemon=True).start()

    def _identify(self):
        with self._busy:
            try:
                # Move mouse over Netflix to show player controls BEFORE screenshot.
                # Controls auto-hide; this ensures the episode title is visible for
                # both OCR (screenshot) and DOM reading (_detect_episode).
                orig_mouse = _show_netflix_controls()
                try:
                    img = _capture()
                finally:
                    # Always give the viewer their mouse back, even on errors
                    if orig_mouse:
                        try:
                            _pmouse.Controller().position = orig_mouse
                        except Exception:
                            pass
                    orig_mouse = None

                b64 = _to_b64(img)

                # Try OCR on the screenshot first (reads what's actually on screen)
                ocr = _ocr_episode(img)
                if ocr:
                    s, e = ocr
                    cfg  = _load_config()
                    cfg["season"]  = s
                    cfg["episode"] = e
                    _save_position(cfg)
                    print(f"[Goldfish Brain] episode via OCR: S{s:02d}E{e:02d}")
                else:
                    # Fall back to Chrome DOM / config.json
                    cfg = _detect_episode()

                progress = _playback_progress()   # None if unknown -> server stays spoiler-safe

                # If the show isn't loaded yet, ask the server to load it first
                try:
                    result = _call_identify(b64, cfg, progress)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        _load_show_index(cfg.get("show_id", ""))
                        result = _call_identify(b64, cfg, progress)   # retry once
                    else:
                        raise
                result["_cfg"] = cfg   # carry season/episode into the overlay

                AppHelper.callAfter(self._overlay.show_result, result)
            except Exception as exc:
                print(f"[Goldfish Brain] identify failed: {exc!r}")
                cfg = _load_config()
                name = {x["show_id"]: x["show_name"]
                        for x in _load_available_shows()}.get(cfg.get("show_id", ""))
                AppHelper.callAfter(
                    self._overlay.show_message,
                    messages.friendly_error(exc, server_age_s=time.time() - _APP_STARTED,
                                            show=name))


# ── embedded server ───────────────────────────────────────────────────────────

def _start_server() -> None:
    import uvicorn
    sys.path.insert(0, str(_ROOT / "src"))
    import server as _srv
    uvicorn.Server(uvicorn.Config(
        _srv.app, host="127.0.0.1", port=8000, log_level="error"
    )).run()


# ── entry ─────────────────────────────────────────────────────────────────────

def main():
    threading.Thread(target=_start_server, daemon=True).start()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = _Delegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
