import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import spoiler


class _DB:
    def __init__(self):
        self.calls = []

    def get_character_arc_at_or_before(self, cid, s, e):
        self.calls.append((s, e))
        return {"season": s, "episode": e}


def test_pick_arc_start_uses_previous_episode_until_90_percent():
    db = _DB()
    assert spoiler.pick_arc(db, "c", 2, 5, None)[1] == "start"
    assert spoiler.pick_arc(db, "c", 2, 5, 0.89)[1] == "start"
    assert db.calls == [(2, 4), (2, 4)]
    assert spoiler.pick_arc(db, "c", 2, 5, 0.9)[1] == "end"
    assert db.calls[-1] == (2, 5)


def test_same_answers_as_server():
    """The server copy is the reference; both must agree on every case."""
    import server
    firsts = [(None, None), (1, None), (1, 3), (2, 1), (2, 5), (3, 1)]
    arcs = [None, {"status": "unknown"}, {"status": "alive"}, {"current_arc": "hiding"}]
    for (fs, fe), arc, (s, e) in itertools.product(firsts, arcs, [(1, 1), (2, 3), (2, 5), (4, 1)]):
        char = {"first_season": fs, "first_episode": fe}
        assert spoiler.passes_shield(char, s, e, arc) == server._passes_spoiler_shield(char, s, e, arc=arc)
        assert spoiler.visibility(char, s, e, arc) == server._visibility(char, s, e, arc)
    assert spoiler.REVEAL_AT == server._REVEAL_AT
