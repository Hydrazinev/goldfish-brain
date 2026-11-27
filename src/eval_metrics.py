"""Identity-aware evaluation with geometric matching and clustered uncertainty."""
from collections import Counter, defaultdict

import numpy as np
from scipy.optimize import linear_sum_assignment

import eval_store

METRICS = {'precision': ('correct_names', 'shown_names'),
           'identification_recall': ('correct_names', 'known_cast_faces'),
           'detection_recall': ('detected_faces', 'faces'),
           'wrong_name_rate': ('wrong_names', 'known_cast_faces'),
           'extra_false_match_rate': ('extras_named', 'extras'),
           'not_in_gallery_false_match_rate': ('not_in_gallery_named', 'not_in_gallery_faces'),
           'not_sure_rate': ('not_sure_known', 'detected_known')}


def iou(a, b):
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, right-left) * max(0, bottom-top)
    area = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / area if area > 0 else 0


def match_boxes(predicted, labelled, threshold=0.5):
    if not predicted or not labelled: return {}
    overlaps = np.array([[iou(p['bbox'], t['bbox']) for t in labelled] for p in predicted])
    # Maximum cardinality first, then overlap. Identity never influences localisation matching.
    benefit = np.where(overlaps >= threshold, 1000 + overlaps, 0)
    pi, ti = linear_sum_assignment(-benefit)
    return {int(t): int(p) for p, t in zip(pi, ti) if overlaps[p, t] >= threshold}


def size_key(box):
    height = box[3] - box[1]
    return 'small_<50px' if height < 50 else 'medium_50-109px' if height < 110 else 'large_110px+'


def frame_counts(frame, label, predictions=None):
    predicted = [p for p in (predictions if predictions is not None else frame['faces'])
                 if p.get('bbox') and p.get('detector_confidence', 1) >= frame['thresholds']['detector']]
    truth = label['faces']
    matches = match_boxes(predicted, truth)
    total, strata, calibration = Counter(), defaultdict(Counter), []
    used = set(matches.values())
    for t, target in enumerate(truth):
        identity = target['identity']
        pred = predicted[matches[t]] if t in matches else None
        name = pred.get('displayed_character_id') if pred else None
        c = Counter()
        if identity == 'false_detection':
            c['false_detections'] += int(pred is not None)
            c['shown_names'] += int(name is not None)
        else:
            c['faces'] += 1
            c['detected_faces'] += int(pred is not None)
            if identity == 'cant_determine':
                c['excluded_uncertain'] += 1
            else:
                c['shown_names'] += int(name is not None)
                if identity in ('extra', 'not_in_gallery'):
                    key = 'extras' if identity == 'extra' else 'not_in_gallery_faces'
                    named = 'extras_named' if identity == 'extra' else 'not_in_gallery_named'
                    c[key] += 1; c[named] += int(name is not None)
                else:
                    c['known_cast_faces'] += 1
                    c['detected_known'] += int(pred is not None)
                    c['correct_names'] += int(name == identity)
                    c['wrong_names'] += int(name is not None and name != identity)
                    c['not_sure_known'] += int(pred is not None and name is None)
                probability = pred.get('calibrated_confidence') if pred else None
                if probability is not None and name is not None:
                    calibration.append((float(probability), int(name == identity)))
        total.update(c)
        strata['size:' + size_key(target['bbox'])].update(c)
        for tag in target.get('tags', []): strata['tag:' + tag].update(c)
    for i, pred in enumerate(predicted):
        if i not in used:
            c = Counter(false_detections=1, shown_names=int(pred.get('displayed_character_id') is not None))
            total.update(c); strata['size:' + size_key(pred['bbox'])].update(c)
    return total, strata, calibration


def estimates(counts):
    return {name: (counts[a] / counts[b] if counts[b] else None) for name, (a, b) in METRICS.items()}


def aggregate(rows, seed=41, repeats=2000):
    totals = sum((r[1] for r in rows), Counter())
    groups = defaultdict(Counter)
    for frame, counts in rows:
        groups[(frame['show_id'], frame['season'], frame['episode'])].update(counts)
    unit = 'episode'
    if len(groups) < 2:
        unit = 'frame'
        groups = {r[0]['id']: r[1] for r in rows}
    result = {'counts': dict(totals), 'metrics': estimates(totals), 'ci95': {},
              'cluster_unit': unit, 'clusters': len(groups)}
    if len(groups) < 2:
        result['warning'] = 'Too few independent clusters for a confidence interval.'
        return result
    keys = sorted({k for g in groups.values() for k in g})
    values = np.array([[g[k] for k in keys] for g in groups.values()], dtype=np.int64)
    draws = np.random.default_rng(seed).multinomial(len(groups), np.ones(len(groups)) / len(groups), repeats)
    samples = draws @ values
    for metric, (a, b) in METRICS.items():
        if a not in keys or b not in keys: continue
        num, den = samples[:, keys.index(a)], samples[:, keys.index(b)]
        valid = den > 0
        if valid.sum() < repeats * 0.8: continue
        result['ci95'][metric] = np.percentile(num[valid] / den[valid], [2.5, 97.5]).tolist()
    result['warning'] = ('Small samples and correlated viewing sessions can give unstable or zero-width intervals. '
                         'No observed errors does not establish a zero error rate; subgroup intervals may be wide.')
    return result


def report(frames_and_labels, predictions=None, seed=41, repeats=2000):
    rows, groups, calibrated = [], defaultdict(list), []
    revisions = []
    for frame, label in frames_and_labels:
        if not label or not label.get('complete'): continue
        pred = predictions[frame['id']]['faces'] if predictions is not None else None
        counts, strata, probs = frame_counts(frame, label, pred)
        rows.append((frame, counts)); groups['show:' + frame['show_id']].append((frame, counts))
        for name, c in strata.items(): groups[name].append((frame, c))
        calibrated.extend(probs)
        revisions.append({'frame_id': frame['id'], 'label_revision': label['revision'],
                          'label_sha256': eval_store.digest(eval_store.json_bytes(label)),
                          'prediction_revealed_before_save': label['prediction_revealed']})
    out = {'frames': len(rows), 'overall': aggregate(rows, seed, repeats),
           'strata': {k: aggregate(v, seed, repeats) for k, v in sorted(groups.items())},
           'labels': revisions, 'bootstrap_seed': seed, 'bootstrap_repeats': repeats,
           'iou_threshold': 0.5, 'calibration': {'n': len(calibrated)},
           'limits': ['One viewer and selected scenes; manual sampling can bias results.',
                      'Precision uses names actually shown. Recall includes missed enrolled cast faces.',
                      'Uncertain identities are excluded from identity metrics, but included in detection recall.',
                      'Extra and not-in-gallery labels have separate false-match denominators.']}
    if calibrated:
        p, y = np.array(calibrated).T
        ece, bins = 0, []
        for i in range(10):
            m = (p >= i/10) & ((p < (i+1)/10) if i < 9 else (p <= 1))
            if m.any():
                predicted, observed = float(p[m].mean()), float(y[m].mean())
                ece += m.sum()/len(p) * abs(predicted-observed)
                bins.append({'n': int(m.sum()), 'predicted': predicted, 'observed': observed})
        out['calibration'].update(ece=ece, brier=float(((p-y)**2).mean()), bins=bins,
                                  scope='Scored shown matches with an applicable stored calibration only.')
    return out
