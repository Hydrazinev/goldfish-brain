import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import api_routes
import relationships
from database import CharacterDB


def _char(cid, name, summary, fs=1, fe=1, rank=1, actor="Actor"):
    return {"character_id": cid, "character_name": name, "actor_name": actor,
            "relationship_summary": summary, "first_season": fs, "first_episode": fe,
            "importance_rank": rank}


CAST = [
    _char("walter", "Walter White", "Partner of Jesse Pinkman;Father of Walter Jr.;Husband of Skyler White", rank=1),
    _char("jesse", "Jesse Pinkman", "Partner of Walter White;Friend of Badger", rank=2),
    _char("skyler", "Skyler White", "Wife of Walter White; sister of Marie Schrader", rank=3),
    _char("junior", "Walter White Jr.", "Son of Walter White", rank=4),
    _char("marie", "Marie Schrader", "Sister of Skyler White; wife of Hank Schrader.", fs=1, fe=3, rank=5),
    _char("hank", "Hank Schrader", "Brother-in-law of Walter White", fs=2, fe=1, rank=6),
    _char("badger", "Badger", "Friend of Jesse Pinkman", fs=1, fe=2, rank=7),
]


def _pairs(g):
    return {frozenset((e["a"], e["b"])): e["label"] for e in g["edges"]}


def test_edges_are_deduped_undirected_with_short_lowercase_labels():
    g = relationships.build_graph(CAST, 4, 1)
    pairs = _pairs(g)
    assert pairs[frozenset(("walter", "jesse"))] == "partner"
    assert pairs[frozenset(("walter", "skyler"))] in ("husband", "wife")
    assert len(g["edges"]) == len(pairs)                     # no duplicates
    assert pairs[frozenset(("hank", "walter"))] == "brother-in-law"
    assert all(e["label"] == e["label"].lower() for e in g["edges"])


def test_unique_partial_names_resolve_and_ambiguous_ones_do_not():
    g = relationships.build_graph(CAST, 4, 1)
    pairs = _pairs(g)
    assert frozenset(("walter", "junior")) in pairs          # "Walter Jr." -> Walter White Jr.
    assert frozenset(("jesse", "badger")) in pairs           # "Badger" exact
    # "Jesse" alone would be unique; "Walter" alone is ambiguous (Walter White / Jr.)
    cast = [_char("w", "Walter White", ""), _char("j", "Walter White Jr.", ""),
            _char("x", "Xena", "Friend of Walter"), _char("s", "Sam Jones", "Friend of Jones")]
    g2 = relationships.build_graph(cast, 5, 1)
    assert not any("x" in (e["a"], e["b"]) for e in g2["edges"])     # "Walter" is ambiguous
    assert not any("s" in (e["a"], e["b"]) for e in g2["edges"])     # "Jones" is just himself


def test_spoiler_shield_hides_unintroduced_characters_and_their_edges():
    g = relationships.build_graph(CAST, 1, 2)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"walter", "jesse", "skyler", "junior"}   # marie S1E3, hank S2E1 hidden; badger debuts S1E2 (this episode)
    for e in g["edges"]:
        assert e["a"] in ids and e["b"] in ids
    assert frozenset(("skyler", "marie")) not in _pairs(g)


def test_unknown_first_appearance_without_notes_is_not_shown():
    cast = CAST + [_char("ghost", "Ghost Person", "Friend of Walter White", fs=None, fe=None)]
    g = relationships.build_graph(cast, 5, 1)
    assert "ghost" not in {n["id"] for n in g["nodes"]}


def test_no_self_edges_and_empty_graph():
    g = relationships.build_graph([_char("a", "Ann Lee", "Friend of Ann Lee")], 2, 1)
    assert g["edges"] == [] and len(g["nodes"]) == 1


def test_label_from_possessive_form():
    cast = [_char("a", "Ann Lee", "Bob Ray's former student"), _char("b", "Bob Ray", "")]
    g = relationships.build_graph(cast, 2, 1)
    assert g["edges"][0]["label"] == "former student"


def test_node_shape():
    n = relationships.build_graph(CAST, 4, 1)["nodes"][0]
    assert set(n) == {"id", "name", "actor", "photo_url"}
    assert n["photo_url"] == "/api/characters/walter/photo"


# ── endpoint ────────────────────────────────────────────────────────────────

class _Keys:
    KEYS = ()

    def get_key(self, *a, **k):
        return ""


def _client(config=None):
    import json
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.json"
    if config is not None:
        cfg.write_text(json.dumps(config))
    db = CharacterDB(db_path=d / "t.db")
    db.add_show("bb", "Breaking Bad", tmdb_id=1)
    db.update_show_status("bb", "ready")
    db.add_show("half", "Half")
    for c in CAST:
        db.add_character(c["character_id"], "bb", c["character_name"], c["actor_name"],
                         first_season=c["first_season"], first_episode=c["first_episode"],
                         importance_rank=c["importance_rank"],
                         relationship_summary=c["relationship_summary"])
    deps = api_routes.ApiDeps(config_path=cfg, env_path=d / ".env", get_db=lambda: db,
                              keystore=_Keys(), key_check=None, runner=None,
                              tmdb_get=lambda p, q: {}, permissions=lambda: {})
    app = FastAPI()
    api_routes.install(app, deps)
    return TestClient(app)


def test_endpoint_with_explicit_position():
    r = _client().get("/api/shows/bb/relationships?season=1&episode=2")
    assert r.status_code == 200
    body = r.json()
    assert {n["id"] for n in body["nodes"]} == {"walter", "jesse", "skyler", "junior"}
    assert set(body) == {"nodes", "edges"}


def test_endpoint_defaults_to_stored_position():
    c = _client({"show_id": "bb", "positions": {"bb": {"season": 1, "episode": 2}}})
    ids = {n["id"] for n in c.get("/api/shows/bb/relationships").json()["nodes"]}
    assert "hank" not in ids and "walter" in ids


def test_endpoint_defaults_to_season_one_episode_one_without_config():
    # everyone here debuts in S1E1, i.e. the current episode, so the map shows nobody yet
    ids = {n["id"] for n in _client().get("/api/shows/bb/relationships").json()["nodes"]}
    assert ids == set()


def test_endpoint_unknown_or_unready_show_404():
    c = _client()
    assert c.get("/api/shows/nope/relationships").status_code == 404
    assert c.get("/api/shows/half/relationships").status_code == 404


def test_and_list_shares_the_label():
    cast = [_char("m", "Marty Byrde", "Father of Charlotte and Jonah"),
            _char("c", "Charlotte Byrde", ""), _char("j", "Jonah Byrde", "")]
    pairs = _pairs(relationships.build_graph(cast, 2, 1))
    assert pairs == {frozenset(("m", "c")): "father", frozenset(("m", "j")): "father"}


def test_titles_with_a_period_do_not_split_the_clause():
    cast = [_char("t", "Dr. Tara", ""), _char("b", "Bhuvan", "Love interest of Dr. Tara")]
    assert _pairs(relationships.build_graph(cast, 2, 1)) == {frozenset(("t", "b")): "love interest"}
