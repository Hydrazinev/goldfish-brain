#!/usr/bin/env python3
"""Replay saved frames locally against an alternative gallery. Never mutates captures or active indexes."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import eval_store
import match_policy
import calibration


def replay_frame(frame, image, recogniser, index, thresholds, augmented=False):
    rows, eligible, indices = [], [], []
    for _, box, face in recogniser.detect_faces(image):
        row = {'bbox': [float(v) for v in box], 'detector_confidence': float(face.det_score),
               'candidates': [], 'eligible': False, 'assigned_character_id': None,
               'displayed_character_id': None}
        rows.append(row)
        if face.det_score < thresholds['detector']: continue
        embedding = recogniser.get_embedding(image, face)
        if embedding is None: continue
        matches = index.search(embedding, top_k=5)
        row.update(candidates=[{'character_id': cid, 'score': float(s)} for cid, s in matches], eligible=True)
        eligible.append(match_policy.candidates(matches, thresholds['similarity'], thresholds['margin']))
        indices.append(len(rows)-1)
    visible = set(frame.get('visible_character_ids', frame['gallery']['character_ids']))
    for i, assigned in enumerate(match_policy.assign_one_to_one(eligible)):
        if assigned is None: continue
        cid, score = assigned
        row = rows[indices[i]]
        row['assigned_character_id'] = cid
        row['displayed_character_id'] = cid if cid in visible else None
        candidates = row['candidates']
        if not augmented and candidates and candidates[0]['character_id'] == cid:
            margin = candidates[0]['score'] - (candidates[1]['score'] if len(candidates) > 1 else 0)
            row['calibrated_confidence'] = calibration.calibrated_confidence(score, margin)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=ROOT/'data')
    p.add_argument('--gallery-root', type=Path, required=True, help='Contains SHOW/embeddings.faiss and .meta')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--threshold', type=float, default=0.5)
    p.add_argument('--margin', type=float, default=0)
    args = p.parse_args()
    if not (0 <= args.threshold <= 1 and 0 <= args.margin <= 2): p.error('Invalid threshold or margin')
    if not (Path.home()/'.insightface/models/buffalo_l').is_dir():
        p.error('The face model must already be installed; replay does not download models')
    os.environ.setdefault('NO_ALBUMENTATIONS_UPDATE', '1')
    import cv2
    from recogniser import FaceIndex, FaceRecogniser
    recogniser, loaded = FaceRecogniser(), {}
    thresholds = {'similarity': args.threshold, 'margin': args.margin, 'detector': 0.5}
    # Exclusive output: never overwrite a prior replay or a captured frame.
    with open(args.out, 'x') as output:
        for item in eval_store.list_frames(args.data_dir):
            frame = eval_store.load_frame(args.data_dir, item['id'])
            sid = frame['show_id']
            base = args.gallery_root.resolve()
            show = (base/sid).resolve()
            if base not in show.parents: raise ValueError('Invalid show id')
            if sid not in loaded:
                index = FaceIndex(); index.load(show/'embeddings.faiss')
                loaded[sid] = index
            index = loaded[sid]
            raw = eval_store.frame_path(args.data_dir, frame['id'], '.jpg').read_bytes()
            if eval_store.digest(raw) != frame['image_sha256']: raise ValueError('Captured bytes changed')
            image = cv2.imread(str(eval_store.frame_path(args.data_dir, frame['id'], '.jpg')))
            if image is None: raise ValueError('Unreadable captured image')
            gallery = eval_store.gallery_version(args.gallery_root.parent, sid, index) if args.gallery_root.name == 'shows' else {
                'augmented': (show/'gallery_aug.flag').exists(),
                'index_file_sha256': eval_store.digest((show/'embeddings.faiss').read_bytes()),
                'character_ids': list(index._ids),
                'character_ids_sha256': eval_store.digest(eval_store.json_bytes(index._ids))}
            if set(index._ids) != set(frame['gallery']['character_ids']):
                raise ValueError('Comparison gallery must contain the same identities as the captured gallery')
            record = {'schema_version': 1, 'frame_id': frame['id'], 'image_sha256': frame['image_sha256'],
                      'replayed_at': eval_store.now(), 'model': {'name': 'buffalo_l', 'det_size': [640,640]},
                      'gallery': gallery, 'thresholds': thresholds, 'app': eval_store.app_version(),
                      'calibration_version': eval_store.digest(Path(calibration.__file__).read_bytes()),
                      'faces': replay_frame(frame, image, recogniser, index, thresholds, gallery['augmented'])}
            output.write(json.dumps(record, allow_nan=False)+'\n'); output.flush()
    print(f'Wrote {args.out}; active indexes and saved labels were unchanged')


if __name__ == '__main__': main()
