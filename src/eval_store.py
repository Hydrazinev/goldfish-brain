"""Opt-in, local-only recognition corpus. Never consumed by feedback or Claude calls."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import config_store

SCHEMA_VERSION = 1
DEFAULT_MAX_MB = 500
KINDS = {'not_in_gallery', 'extra', 'cant_determine', 'false_detection'}
TAGS = {'dark', 'profile', 'small', 'occluded'}
_LOCK = threading.RLock()
_LAST = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def uuid_id(value):
    if str(UUID(str(value))) != value:
        raise ValueError('Invalid frame id')
    return value


def corpus(data_dir):
    root = Path(data_dir) / 'eval'
    if root.is_symlink():
        raise ValueError('Evaluation directory must not be a symlink')
    return root


def folder(data_dir, name):
    path = corpus(data_dir) / name
    if path.is_symlink():
        raise ValueError('Evaluation directories must not be symlinks')
    return path


def frame_path(data_dir, frame_id, suffix='.json'):
    path = folder(data_dir, 'frames') / (uuid_id(frame_id) + suffix)
    if path.is_symlink():
        raise ValueError('Evaluation files must not be symlinks')
    return path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@lru_cache(maxsize=1)
def app_version():
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, timeout=1,
                                         stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        commit = 'unknown'
    return {'commit': commit, 'recognition_source_sha256': digest(
        b''.join((root / 'src' / name).read_bytes() for name in ('server.py', 'recogniser.py', 'match_policy.py')))}


def gallery_version(data_dir, show_id, index):
    # UUID capture paths never use show_id. Gallery paths must still remain inside shows/.
    base = (Path(data_dir) / 'shows').resolve()
    path = (base / show_id).resolve()
    if base not in path.parents:
        raise ValueError('Invalid show id')
    ids = list(getattr(index, '_ids', []))
    loaded_hash = None
    if hasattr(index, '_index'):
        import faiss
        loaded_hash = digest(faiss.serialize_index(index._index).tobytes())
    file = path / 'embeddings.faiss'
    return {'augmented': (path / 'gallery_aug.flag').exists(),
            'index_file_sha256': digest(file.read_bytes()) if file.is_file() else None,
            'loaded_index_sha256': loaded_hash, 'character_ids': ids,
            'character_ids_sha256': digest(json_bytes(ids))}


def _usage(data_dir):
    total = count = labelled = 0
    for name in ('frames', 'labels', 'splits', 'replays'):
        path = folder(data_dir, name)
        if path.exists():
            for file in path.rglob('*'):
                if file.is_symlink():
                    continue
                if file.is_file():
                    total += file.stat().st_size
                    if name == 'frames' and file.suffix == '.json': count += 1
                    if name == 'labels' and file.suffix == '.jsonl': labelled += 1
    return {'bytes': total, 'frames': count, 'labelled_files': labelled,
            'last_capture': _LAST.get(str(Path(data_dir)), None)}


def usage(data_dir):
    with _LOCK:
        return _usage(data_dir)


def note_failure(data_dir):
    _LAST[str(Path(data_dir))] = 'A capture could not be saved. Check free space and file permissions.'


def limit_bytes(config):
    try: value = int(config.get('eval_max_mb', DEFAULT_MAX_MB))
    except (ValueError, TypeError): value = DEFAULT_MAX_MB
    return max(10, min(5000, value)) * 1024 * 1024


def save_capture(data_dir, config_path, raw, width, height, metadata):
    """Called AFTER an identify response exists. Exceptions are caught by the caller."""
    with _LOCK:
        config = config_store.read_config(config_path)
        if config.get('eval_mode') is not True:
            return None
        fid = str(uuid4())
        record = dict(metadata, schema_version=SCHEMA_VERSION, id=fid, captured_at=now(),
                      width=width, height=height, image_sha256=digest(raw),
                      media_type='image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg')
        payload = json_bytes(record)
        if usage(data_dir)['bytes'] + len(raw) + len(payload) > limit_bytes(config):
            _LAST[str(Path(data_dir))] = 'Storage cap reached. Delete captures or increase the cap.'
            return None
        image_path = frame_path(data_dir, fid, '.jpg')
        try:
            atomic_write(image_path, raw)  # exact received bytes, including PNG requests; never re-encode
            atomic_write(frame_path(data_dir, fid), payload)
        except Exception:
            image_path.unlink(missing_ok=True)
            raise
        _LAST[str(Path(data_dir))] = 'Saved locally'
        return fid


def load_frame(data_dir, fid):
    return json.loads(frame_path(data_dir, fid).read_text())


def events(data_dir, fid):
    uuid_id(fid)
    path = folder(data_dir, 'labels') / (fid + '.jsonl')
    if path.is_symlink(): raise ValueError('Invalid labels file')
    if not path.exists(): return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def latest_label(data_dir, fid):
    return next((e for e in reversed(events(data_dir, fid)) if e['event'] == 'label'), None)


def append_event(data_dir, config_path, fid, body, reveal=False):
    with _LOCK:
        frame = load_frame(data_dir, fid)
        history = events(data_dir, fid)
        if body.get('base_revision') != len(history):
            raise RuntimeError('Labels changed. Reload this frame before saving.')
        revealed = reveal or any(e['event'] == 'reveal' for e in history)
        event = {'schema_version': SCHEMA_VERSION, 'frame_id': fid, 'revision': len(history) + 1,
                 'timestamp': now(), 'event': 'reveal' if reveal else 'label',
                 'prediction_revealed': revealed}
        if not reveal:
            faces = body.get('faces', [])
            if not isinstance(faces, list) or len(faces) > 100: raise ValueError('Invalid faces')
            clean = []
            allowed = set(frame['gallery']['character_ids']) | KINDS
            w, h = frame['width'], frame['height']
            for face in faces:
                box = face.get('bbox')
                if not isinstance(box, list) or len(box) != 4 or not all(
                        isinstance(x, (int, float)) and math.isfinite(x) for x in box):
                    raise ValueError('Draw a valid face box')
                x1, y1, x2, y2 = box
                if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h): raise ValueError('Face box is outside the frame')
                if face.get('identity') not in allowed: raise ValueError('Choose an identity for every box')
                tags = face.get('tags', [])
                if not isinstance(tags, list) or not set(tags) <= TAGS: raise ValueError('Invalid difficulty tags')
                clean.append({'bbox': box, 'bbox_normalized': [x1/w, y1/h, x2/w, y2/h],
                              'identity': face['identity'], 'tags': sorted(set(tags))})
            no_faces = body.get('no_faces') is True
            if no_faces and clean: raise ValueError('A no-faces frame cannot contain labelled boxes')
            complete = body.get('complete') is True
            if complete and not clean and not no_faces: raise ValueError('Label every face or mark no faces')
            event.update(faces=clean, no_faces=no_faces, complete=complete)
        raw = json_bytes(event) + b'\n'
        if usage(data_dir)['bytes'] + len(raw) > limit_bytes(config_store.read_config(config_path)):
            raise ValueError('Storage cap reached. Increase the cap before saving labels.')
        path = folder(data_dir, 'labels') / (fid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, 'ab') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        return event


def list_frames(data_dir):
    out = []
    for file in sorted(folder(data_dir, 'frames').glob('*.json'), reverse=True):
        try:
            frame = load_frame(data_dir, file.stem)
            label = latest_label(data_dir, file.stem)
            out.append({k: frame[k] for k in ('id', 'captured_at', 'show_id', 'season', 'episode')} | {
                'complete': bool(label and label.get('complete'))})
        except (ValueError, OSError, KeyError):
            continue
    return sorted(out, key=lambda r: r['captured_at'])


def delete_data(data_dir, config_path):
    with _LOCK:
        config_store.update_config(config_path, {'eval_mode': False})
        for name in ('frames', 'labels', 'splits', 'replays'):
            path = folder(data_dir, name)
            if path.exists(): shutil.rmtree(path)
        _LAST.pop(str(Path(data_dir)), None)
