"""Shared recognition decision rules for live Identify and offline replay."""

def candidates(matches, threshold, min_margin):
    first = matches[0][1] if matches else 0.0
    second = matches[1][1] if len(matches) > 1 else 0.0
    if first - second < min_margin: return []
    return [(cid, score) for cid, score in matches if score >= threshold]


def assign_one_to_one(candidates):
    pairs = sorted(((score, i, cid) for i, hits in enumerate(candidates) for cid, score in hits),
                   key=lambda p: p[0], reverse=True)
    out, taken = [None] * len(candidates), set()
    for score, i, cid in pairs:
        if out[i] is None and cid not in taken:
            out[i] = (cid, score)
            taken.add(cid)
    return out
