"""Local evaluation endpoints. Images are only served by UUID; no uploads or outbound calls."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import config_store
import eval_store as store


class Settings(BaseModel):
    eval_mode: bool
    max_mb: int = Field(default=500, ge=10, le=5000)
    acknowledge_full_display: bool = False


class Deletion(BaseModel):
    confirmation: str


def install_eval_routes(parent, deps, hidden_visible):
    def local_only(request: Request):
        if request.url.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise HTTPException(403, 'Evaluation is only available on loopback')
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
            raise HTTPException(403, 'Evaluation requests must come from the local app')
    router = APIRouter(prefix='/eval', dependencies=[Depends(local_only)])
    def root():
        value = deps.eval_data_dir() if deps.eval_data_dir else deps.data_dir
        if value is None: raise HTTPException(503, 'Evaluation storage is unavailable')
        return Path(value)

    def frame(fid):
        try: return store.load_frame(root(), fid)
        except (ValueError, OSError): raise HTTPException(404, 'Frame not found')

    @router.get('/status')
    def status():
        cfg = config_store.read_config(deps.config_path)
        return dict(store.usage(root()), eval_mode=cfg.get('eval_mode') is True,
                    max_mb=store.limit_bytes(cfg) // (1024 * 1024))

    @router.post('/settings')
    def settings(body: Settings):
        if body.eval_mode and not body.acknowledge_full_display:
            raise HTTPException(400, 'Acknowledge that entire-display captures can contain private windows')
        try:
            config_store.update_config(deps.config_path, {'eval_mode': body.eval_mode, 'eval_max_mb': body.max_mb})
        except config_store.ConfigUnreadable:
            raise HTTPException(409, 'Settings file is unreadable and was left untouched')
        return status()

    @router.post('/delete')
    def delete(body: Deletion):
        if body.confirmation != 'DELETE': raise HTTPException(400, 'Deletion confirmation is required')
        try: store.delete_data(root(), deps.config_path)
        except config_store.ConfigUnreadable: raise HTTPException(409, 'Cannot safely turn evaluation mode off')
        return status()

    @router.get('/frames')
    def frames():
        return {'frames': store.list_frames(root())}

    @router.get('/frames/{fid}/image')
    def image(fid: str):
        info = frame(fid)
        try: raw = store.frame_path(root(), fid, '.jpg').read_bytes()
        except OSError: raise HTTPException(404, 'Frame not found')
        return Response(content=raw, media_type=info['media_type'],
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    def visible_choices(info, all_names=False):
        # Names of future characters require a separate explicit spoiler opt-in.
        db = deps.get_db()
        chars = db.get_show_characters(info['show_id'])
        anchor = {'show_id': info['show_id']}
        hidden, _ = hidden_visible(db, anchor)
        ids = set(info['gallery']['character_ids'])
        return [{'id': c['character_id'], 'name': c['character_name']} for c in chars
                if c['character_id'] in ids and (all_names or c['character_name'] not in hidden)]

    @router.get('/frames/{fid}')
    def detail(fid: str, all: bool = False):
        info = frame(fid)
        history = store.events(root(), fid)
        revealed = any(e['event'] == 'reveal' for e in history)
        result = {k: info[k] for k in ('id', 'captured_at', 'show_id', 'season', 'episode', 'width', 'height')}
        result.update(choices=visible_choices(info, all), revision=len(history),
                      prediction_revealed=revealed, label=store.latest_label(root(), fid))
        if revealed:
            # No biographies, actor identities, or story notes are exposed by the labelling page.
            names = {c['id']: c['name'] for c in result['choices']}
            result['predictions'] = [dict(bbox=f['bbox'], detector_confidence=f['detector_confidence'],
                character_id=f['displayed_character_id'] if f['displayed_character_id'] in names else None,
                name=names.get(f['displayed_character_id'], 'Withheld / not recognised')) for f in info['faces']]
        return result

    @router.post('/frames/{fid}/labels')
    def labels(fid: str, body: dict):
        frame(fid)
        try: event = store.append_event(root(), deps.config_path, fid, body)
        except OSError: raise HTTPException(404, 'Frame no longer exists')
        except RuntimeError as e: raise HTTPException(409, str(e))
        except ValueError as e: raise HTTPException(400, str(e))
        return {'ok': True, 'revision': event['revision'], 'label': event}

    @router.post('/frames/{fid}/reveal')
    def reveal(fid: str, body: dict, all: bool = False):
        frame(fid)
        try: store.append_event(root(), deps.config_path, fid, body, reveal=True)
        except OSError: raise HTTPException(404, 'Frame no longer exists')
        except RuntimeError as e: raise HTTPException(409, str(e))
        except ValueError as e: raise HTTPException(400, str(e))
        return detail(fid, all)

    parent.include_router(router)
