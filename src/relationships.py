"""Who is related to whom, read from each character's relationship_summary.

"Wife of Walter White; sister of Marie Schrader" becomes edges
(this character, Walter White, "wife") and (this character, Marie Schrader,
"sister"). Pure functions; the spoiler shield is applied to the nodes first, so
a character the viewer hasn't met never appears, nor do edges to them.
"""
from __future__ import annotations

import re
from typing import Optional

import spoiler

_CLAUSE = re.compile(r"[;,\n]|(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bJr)(?<!\bSr)(?<!\bSt)\.(?:\s|$)")
_PHRASE = re.compile(r"[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*)*")
_TRAILING = {"of", "to", "for", "with", "and", "the", "a", "an", "in", "at", "from", "by", "on"}
_MAX_WORDS = 3


def _tokens(name: str) -> list[str]:
    out = []
    for t in re.split(r"\s+", (name or "").strip()):
        t = re.sub(r"(?:'s|’s)$", "", t).strip(".,;:()\"'").lower()
        if t:
            out.append(t)
    return out


def _resolve(phrase: str, index: list[tuple[str, list[str]]], self_id: str) -> Optional[str]:
    """The one character a mention refers to, or None when none/several do."""
    toks = _tokens(phrase)
    if not toks:
        return None
    exact = [cid for cid, ct in index if ct == toks]
    if len(exact) == 1:
        return None if exact[0] == self_id else exact[0]
    if exact:
        return None
    partial = [cid for cid, ct in index if set(toks) <= set(ct)]
    if len(partial) == 1 and partial[0] != self_id:
        return partial[0]
    return None


def _label(clause: str, start: int, end: int) -> str:
    before = clause[:start].strip().lower()
    words = [w for w in re.split(r"\s+", before) if w]
    while words and words[-1].strip(",") in _TRAILING:
        words.pop()
    if not words:
        # "Bob Ray's former student": the label follows the name
        after = re.sub(r"^(?:'s|’s)\s*", "", clause[end:].strip().lower())
        words = [w for w in re.split(r"\s+", after) if w][:_MAX_WORDS]
        while words and words[-1] in _TRAILING:
            words.pop()
        return " ".join(words) or "connected"
    return " ".join(words[-_MAX_WORDS:])


def edges_from_summary(char: dict, index: list[tuple[str, list[str]]]) -> list[dict]:
    out = []
    for clause in _CLAUSE.split(char.get("relationship_summary") or ""):
        clause = clause.strip()
        prev_end, prev_label = 0, None
        for m in _PHRASE.finditer(clause):
            other = _resolve(m.group(0), index, char["character_id"])
            if not other:
                continue
            gap = clause[prev_end:m.start()].strip().lower()
            # "Father of Charlotte and Jonah": the second name shares the first's label
            label = prev_label if (prev_label and gap in ("and", "&")) \
                else _label(clause, m.start(), m.end())
            out.append({"a": char["character_id"], "b": other, "label": label})
            prev_end, prev_label = m.end(), label
    return out


def build_graph(chars: list[dict], season: int, episode: int, db=None,
                progress: Optional[float] = None) -> dict:
    """{nodes, edges} for the characters introduced before the position. Anyone
    first seen in this very episode is left out until `progress` reaches
    spoiler.REVEAL_AT (the map passes no progress, so it never shows them)."""
    visible = []
    for c in chars:
        arc = None
        if c.get("first_season") is None and db is not None:
            arc, _ = spoiler.pick_arc(db, c["character_id"], season, episode, None)
        if spoiler.is_met(c, season, episode, arc, progress):
            visible.append(c)
    index = [(c["character_id"], _tokens(c["character_name"])) for c in visible]
    seen: dict[frozenset, dict] = {}
    for c in visible:
        for e in edges_from_summary(c, index):
            key = frozenset((e["a"], e["b"]))
            if key not in seen:
                seen[key] = e
    nodes = [{"id": c["character_id"], "name": c["character_name"],
              "actor": c.get("actor_name"),
              "photo_url": f'/api/characters/{c["character_id"]}/photo'} for c in visible]
    return {"nodes": nodes, "edges": list(seen.values())}
