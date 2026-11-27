#!/usr/bin/env python3
"""Freeze a split before tuning, then score labelled local frames. No network calls."""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import eval_store
import eval_metrics


def freeze(data_dir, output, seed=41, by='show'):
    frames = [eval_store.load_frame(data_dir, f['id']) for f in eval_store.list_frames(data_dir)]
    key = lambda f: f['show_id'] if by == 'show' else f"{f['show_id']}:S{f['season']}E{f['episode']}"
    groups = sorted({key(f) for f in frames})
    if len(groups) < 2: raise ValueError('Collect at least two independent shows/episodes before freezing a split')
    random.Random(seed).shuffle(groups)
    test = set(groups[:max(1, round(len(groups) / 3))])
    manifest = {'schema_version': 1, 'created_at': eval_store.now(), 'seed': seed, 'group_by': by,
                'frames': {f['id']: {'partition': 'test' if key(f) in test else 'tune',
                                      'image_sha256': f['image_sha256'], 'group': key(f)} for f in frames}}
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'xb') as stream: stream.write(eval_store.json_bytes(manifest))
    return manifest


def score(data_dir, split_path, final=False, replay=None, seed=41, repeats=2000):
    manifest = json.loads(Path(split_path).read_text())
    partition = 'test' if final else 'tune'
    inputs, skipped = [], 0
    predictions = None
    if replay:
        records = [json.loads(line) for line in Path(replay).read_text().splitlines() if line.strip()]
        predictions = {r['frame_id']: r for r in records}
        if len(predictions) != len(records): raise ValueError('Duplicate replay frame ids')
    for fid, entry in manifest['frames'].items():
        if entry['partition'] != partition: continue
        frame = eval_store.load_frame(data_dir, fid)
        if frame['image_sha256'] != entry['image_sha256']: raise ValueError('Frozen capture changed')
        if eval_store.digest(eval_store.frame_path(data_dir, fid, '.jpg').read_bytes()) != frame['image_sha256']:
            raise ValueError('Image bytes changed since capture')
        label = eval_store.latest_label(data_dir, fid)
        if not label or not label['complete']:
            skipped += 1; continue
        if predictions is not None:
            if fid not in predictions: raise ValueError('Replay does not cover every scored frame')
            if predictions[fid]['image_sha256'] != frame['image_sha256']: raise ValueError('Replay uses different image bytes')
        inputs.append((frame, label))
    if not inputs: raise ValueError('No completely labelled frames in this partition')
    result = eval_metrics.report(inputs, predictions, seed, repeats)
    result.update(partition=partition, final=final, split_sha256=eval_store.digest(Path(split_path).read_bytes()),
                  unlabelled_frames_excluded=skipped, generated_at=eval_store.now(),
                  prediction_source='replay' if replay else 'captured app responses')
    if replay:
        result['replay_sha256'] = eval_store.digest(Path(replay).read_bytes())
        result['replay_versions'] = [json.loads(v) for v in sorted({eval_store.json_bytes({k: predictions[f['id']][k]
            for k in ('model', 'gallery', 'thresholds', 'app')}).decode() for f, _ in inputs})]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['split', 'score'])
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=41)
    parser.add_argument('--by', choices=['show', 'episode'], default='show')
    parser.add_argument('--split', type=Path)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--final', action='store_true', help='Score the frozen test partition; otherwise tune only')
    parser.add_argument('--bootstrap', type=int, default=2000)
    args = parser.parse_args()
    try:
        if args.stage == 'split':
            result = freeze(args.data_dir, args.out, args.seed, args.by)
        else:
            if not args.split: parser.error('--split is required for scoring')
            if args.bootstrap < 200: parser.error('--bootstrap must be at least 200')
            result = score(args.data_dir, args.split, args.final, args.replay, args.seed, args.bootstrap)
            with open(args.out, 'x') as out: json.dump(result, out, indent=2)
        print(f'Wrote {args.out}')
    except (ValueError, OSError, KeyError) as e:
        parser.exit(1, f'{e}\n')


if __name__ == '__main__': main()
