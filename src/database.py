from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_DB = Path("data/cineid.db")


class CharacterDB:
    def __init__(self, db_path: str | Path = _DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # One connection PER THREAD. A single connection shared by FastAPI's worker threads
        # (check_same_thread=False) corrupted memory and segfaulted the engine inside SQLite.
        self._local = threading.local()
        self._all_conns: list = []
        self._conns_lock = threading.Lock()
        self._create_tables()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._path), timeout=15)
            conn.row_factory = sqlite3.Row
            # WAL mode allows concurrent reads alongside a write without blocking
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS shows (
                show_id             TEXT PRIMARY KEY,
                show_name           TEXT NOT NULL,
                tmdb_id             INTEGER,
                total_characters    INTEGER DEFAULT 0,
                ingested_at         TIMESTAMP,
                status              TEXT DEFAULT 'ingesting'
            );

            CREATE TABLE IF NOT EXISTS characters (
                character_id         TEXT PRIMARY KEY,
                show_id              TEXT NOT NULL REFERENCES shows(show_id),
                character_name       TEXT NOT NULL,
                actor_name           TEXT,
                role                 TEXT,
                first_episode        INTEGER,
                last_episode         INTEGER,
                first_season         INTEGER,
                last_season          INTEGER,
                importance_rank      INTEGER,
                relationship_summary TEXT,
                image_count          INTEGER DEFAULT 0,
                confidence_level     TEXT
            );

            CREATE TABLE IF NOT EXISTS character_arcs (
                character_id          TEXT NOT NULL REFERENCES characters(character_id),
                season                INTEGER NOT NULL,
                episode               INTEGER NOT NULL,
                status                TEXT,
                current_arc           TEXT,
                last_seen_description TEXT,
                PRIMARY KEY (character_id, season, episode)
            );

            CREATE TABLE IF NOT EXISTS actor_credits (
                actor_tmdb_id INTEGER PRIMARY KEY,
                credits_json  TEXT,
                fetched_at    TEXT
            );
        """)
        self._conn.commit()
        # Migrate: add columns introduced after the initial schema
        existing_chars = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(characters)").fetchall()
        }
        for col, defn in [
            ("status",                "TEXT"),
            ("current_arc",           "TEXT"),
            ("last_seen_description", "TEXT"),
            ("arc_type",              "TEXT"),
            ("actor_tmdb_id",         "INTEGER"),
        ]:
            if col not in existing_chars:
                self._conn.execute(f"ALTER TABLE characters ADD COLUMN {col} {defn}")

        existing_shows = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(shows)").fetchall()
        }
        if "poster_path" not in existing_shows:
            self._conn.execute("ALTER TABLE shows ADD COLUMN poster_path TEXT")

        existing_arcs = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(character_arcs)").fetchall()
        }
        for col, defn in [
            ("arc_type", "TEXT"),
            ("source",   "TEXT DEFAULT 'ai'"),     # 'user' rows are written by the owner and never regenerated
        ]:
            if col not in existing_arcs:
                self._conn.execute(f"ALTER TABLE character_arcs ADD COLUMN {col} {defn}")

        self._conn.commit()

    # ------------------------------------------------------------------
    # Shows
    # ------------------------------------------------------------------

    def add_show(
        self,
        show_id: str,
        show_name: str,
        tmdb_id: Optional[int] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO shows (show_id, show_name, tmdb_id, ingested_at, status)
            VALUES (?, ?, ?, ?, 'ingesting')
            ON CONFLICT(show_id) DO UPDATE SET
                show_name   = excluded.show_name,
                tmdb_id     = COALESCE(excluded.tmdb_id, shows.tmdb_id),
                ingested_at = excluded.ingested_at,
                status      = 'ingesting'
            """,
            (show_id, show_name, tmdb_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def update_show_status(self, show_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE shows SET status = ? WHERE show_id = ?",
            (status, show_id),
        )
        self._conn.commit()

    def update_character_first_seen(
        self, character_id: str, season: int, episode: int
    ) -> None:
        self._conn.execute(
            "UPDATE characters SET first_season = ?, first_episode = ? "
            "WHERE character_id = ?",
            (season, episode, character_id),
        )
        self._conn.commit()

    def delete_character_arcs(self, character_id: str) -> None:
        """Remove the generated (AI) notes only; the owner's own notes survive a regenerate."""
        self._conn.execute(
            "DELETE FROM character_arcs WHERE character_id = ? AND COALESCE(source, 'ai') <> 'user'",
            (character_id,),
        )
        self._conn.commit()

    def get_user_arcs(self, character_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM character_arcs WHERE character_id = ? AND source = 'user' "
            "ORDER BY season, episode", (character_id,)).fetchall()
        return [dict(r) for r in rows]

    def count_ai_arcs(self, character_id: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM character_arcs WHERE character_id = ? "
            "AND COALESCE(source, 'ai') <> 'user'", (character_id,)).fetchone()[0]

    def replace_user_arcs(self, character_id: str, entries: list[dict],
                          bio: Optional[str] = None) -> None:
        """Make `entries` the character's complete set of user notes (and the bio, when given),
        all or nothing. A user entry replaces the generated row for the same episode."""
        conn = self._conn
        try:
            if bio is not None:
                conn.execute("UPDATE characters SET relationship_summary = ? WHERE character_id = ?",
                             (bio, character_id))
            conn.execute("DELETE FROM character_arcs WHERE character_id = ? AND source = 'user'",
                         (character_id,))
            for e in entries:
                conn.execute("DELETE FROM character_arcs WHERE character_id = ? "
                             "AND season = ? AND episode = ? AND COALESCE(source, 'ai') <> 'user'",
                             (character_id, e["season"], e["episode"]))
                conn.execute(
                    "INSERT INTO character_arcs (character_id, season, episode, status, current_arc, "
                    "last_seen_description, arc_type, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'user')",
                    (character_id, e["season"], e["episode"], e.get("status"), e.get("current_arc"),
                     e.get("last_seen_description"), e.get("arc_type")))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def derive_first_seen_for_character(self, character_id: str) -> None:
        """Like derive_first_seen_from_arcs for one character, but never moves an existing
        EARLIER first appearance later."""
        row = self._conn.execute(
            """
            SELECT season, episode FROM character_arcs
            WHERE character_id = ?
              AND (LOWER(COALESCE(status, 'unknown')) != 'unknown'
                   OR COALESCE(current_arc, '') != '')
            ORDER BY season, episode LIMIT 1
            """, (character_id,)).fetchone()
        char = self.get_character(character_id)
        if not row or not char:
            return
        cur = (char.get("first_season"), char.get("first_episode"))
        if cur[0] is not None and (cur[0], cur[1] or 0) <= (row[0], row[1]):
            return
        self.update_character_first_seen(character_id, row[0], row[1])

    # ------------------------------------------------------------------
    # Actor credits cache + TMDB person id
    # ------------------------------------------------------------------

    def set_actor_tmdb_id(self, character_id: str, actor_tmdb_id: int) -> None:
        self._conn.execute("UPDATE characters SET actor_tmdb_id = ? WHERE character_id = ?",
                           (actor_tmdb_id, character_id))
        self._conn.commit()

    def get_cached_credits(self, actor_tmdb_id: int) -> Optional[dict]:
        """{"credits": [...], "fetched_at": iso} or None when never fetched."""
        import json
        row = self._conn.execute(
            "SELECT credits_json, fetched_at FROM actor_credits WHERE actor_tmdb_id = ?",
            (actor_tmdb_id,)).fetchone()
        if not row:
            return None
        try:
            credits = json.loads(row["credits_json"] or "[]")
        except ValueError:
            credits = []
        return {"credits": credits, "fetched_at": row["fetched_at"]}

    def set_cached_credits(self, actor_tmdb_id: int, credits: list,
                           fetched_at: Optional[str] = None) -> None:
        import json
        self._conn.execute(
            "INSERT OR REPLACE INTO actor_credits (actor_tmdb_id, credits_json, fetched_at) "
            "VALUES (?, ?, ?)",
            (actor_tmdb_id, json.dumps(credits),
             fetched_at or datetime.now(timezone.utc).isoformat()))
        self._conn.commit()

    def update_character_bio(self, character_id: str, summary: str) -> None:
        self._conn.execute(
            "UPDATE characters SET relationship_summary = ? WHERE character_id = ?",
            (summary, character_id),
        )
        self._conn.commit()

    def derive_first_seen_from_arcs(self, show_id: str) -> int:
        """Set first_season/first_episode from the earliest arc row that shows
        the character as present (status != unknown, or a non-empty arc).

        The spoiler shield depends on these columns; ingest cannot fill them
        (TMDB has no per-character first-appearance data), but the generated
        arc rows encode it. Returns the number of characters updated.
        """
        updated = 0
        for char in self.get_show_characters(show_id):
            row = self._conn.execute(
                """
                SELECT season, episode FROM character_arcs
                WHERE character_id = ?
                  AND (LOWER(COALESCE(status, 'unknown')) != 'unknown'
                       OR COALESCE(current_arc, '') != '')
                ORDER BY season, episode
                LIMIT 1
                """,
                (char["character_id"],),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE characters SET first_season = ?, first_episode = ? "
                    "WHERE character_id = ?",
                    (row[0], row[1], char["character_id"]),
                )
                updated += 1
        self._conn.commit()
        return updated

    def refresh_show_character_count(self, show_id: str) -> int:
        """Set shows.total_characters to the actual character count."""
        count = self._conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id = ?", (show_id,)
        ).fetchone()[0]
        self._conn.execute(
            "UPDATE shows SET total_characters = ? WHERE show_id = ?",
            (count, show_id),
        )
        self._conn.commit()
        return count

    def show_exists(self, show_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM shows WHERE show_id = ?", (show_id,)
        ).fetchone()
        return row is not None

    def set_show_poster(self, show_id: str, poster_path: Optional[str]) -> None:
        self._conn.execute("UPDATE shows SET poster_path = ? WHERE show_id = ?",
                           (poster_path, show_id))
        self._conn.commit()

    def fail_stale_ingests(self) -> int:
        """Mark shows left 'ingesting' by a crashed/killed run as failed.
        Call at startup, when no setup job can possibly be running."""
        cur = self._conn.execute(
            "UPDATE shows SET status = 'failed' WHERE status = 'ingesting'")
        self._conn.commit()
        return cur.rowcount

    def get_ready_shows(self) -> list[dict]:
        """Shows whose ingest finished; the only ones safe to offer in the UI."""
        rows = self._conn.execute(
            "SELECT * FROM shows WHERE status = 'ready' ORDER BY show_name"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_show(self, show_id: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM shows WHERE show_id = ?", (show_id,)).fetchone()
        return dict(row) if row else None

    def get_all_shows(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM shows ORDER BY ingested_at DESC").fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Characters
    # ------------------------------------------------------------------

    def add_character(
        self,
        character_id: str,
        show_id: str,
        character_name: str,
        actor_name: Optional[str] = None,
        role: Optional[str] = None,
        first_episode: Optional[int] = None,
        last_episode: Optional[int] = None,
        first_season: Optional[int] = None,
        last_season: Optional[int] = None,
        importance_rank: Optional[int] = None,
        relationship_summary: Optional[str] = None,
        image_count: int = 0,
        confidence_level: Optional[str] = None,
        status: Optional[str] = None,
        current_arc: Optional[str] = None,
        last_seen_description: Optional[str] = None,
        actor_tmdb_id: Optional[int] = None,
    ) -> None:
        # Upsert, not INSERT OR REPLACE: re-ingesting a show must not wipe the
        # fields populate_metadata derived (first_season, arc_type, ...).
        self._conn.execute(
            """
            INSERT INTO characters (
                character_id, show_id, character_name, actor_name,
                role, first_episode, last_episode, first_season, last_season,
                importance_rank, relationship_summary, image_count, confidence_level,
                status, current_arc, last_seen_description, actor_tmdb_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                show_id              = excluded.show_id,
                character_name       = excluded.character_name,
                actor_name           = excluded.actor_name,
                role                 = excluded.role,
                importance_rank      = excluded.importance_rank,
                image_count          = excluded.image_count,
                actor_tmdb_id        = COALESCE(excluded.actor_tmdb_id, characters.actor_tmdb_id),
                confidence_level     = excluded.confidence_level,
                first_episode        = COALESCE(excluded.first_episode, characters.first_episode),
                last_episode         = COALESCE(excluded.last_episode, characters.last_episode),
                first_season         = COALESCE(excluded.first_season, characters.first_season),
                last_season          = COALESCE(excluded.last_season, characters.last_season),
                relationship_summary = COALESCE(excluded.relationship_summary, characters.relationship_summary),
                status               = COALESCE(excluded.status, characters.status),
                current_arc          = COALESCE(excluded.current_arc, characters.current_arc),
                last_seen_description = COALESCE(excluded.last_seen_description, characters.last_seen_description)
            """,
            (
                character_id, show_id, character_name, actor_name,
                role, first_episode, last_episode, first_season, last_season,
                importance_rank, relationship_summary, image_count, confidence_level,
                status, current_arc, last_seen_description, actor_tmdb_id,
            ),
        )
        self._conn.commit()

    def get_character(self, character_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM characters WHERE character_id = ?", (character_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_show_characters_overview(self, show_id: str) -> list[dict]:
        """Every character of a show, most important first, with `has_notes`: True when at least
        one per-episode row has real story text (what the library's character list shows)."""
        rows = self._conn.execute(
            """
            SELECT c.character_id, c.character_name, c.actor_name, c.role, c.importance_rank,
                   EXISTS(SELECT 1 FROM character_arcs a
                          WHERE a.character_id = c.character_id
                            AND (COALESCE(a.current_arc, '') <> '' OR a.source = 'user')) AS has_notes
            FROM characters c
            WHERE c.show_id = ?
            ORDER BY c.importance_rank ASC NULLS LAST, c.character_name
            """,
            (show_id,),
        ).fetchall()
        return [{**dict(r), "has_notes": bool(r["has_notes"])} for r in rows]

    def get_show_characters(self, show_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM characters
            WHERE show_id = ?
            ORDER BY importance_rank ASC NULLS LAST
            """,
            (show_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Character arcs (per-episode dynamic data)
    # ------------------------------------------------------------------

    def add_character_arc(
        self,
        character_id: str,
        season: int,
        episode: int,
        status: Optional[str] = None,
        current_arc: Optional[str] = None,
        last_seen_description: Optional[str] = None,
        arc_type: Optional[str] = None,
    ) -> None:
        # Generated notes never overwrite one the owner wrote for the same episode.
        self._conn.execute(
            """
            INSERT INTO character_arcs
            (character_id, season, episode, status, current_arc, last_seen_description, arc_type, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ai')
            ON CONFLICT(character_id, season, episode) DO UPDATE SET
                status = excluded.status, current_arc = excluded.current_arc,
                last_seen_description = excluded.last_seen_description,
                arc_type = excluded.arc_type, source = 'ai'
            WHERE COALESCE(character_arcs.source, 'ai') <> 'user'
            """,
            (character_id, season, episode, status, current_arc, last_seen_description, arc_type),
        )
        self._conn.commit()

    def get_character_arc(
        self, character_id: str, season: int, episode: int
    ) -> Optional[dict]:
        row = self._conn.execute(
            """
            SELECT * FROM character_arcs
            WHERE character_id = ? AND season = ? AND episode = ?
            """,
            (character_id, season, episode),
        ).fetchone()
        return dict(row) if row else None

    def get_character_arc_at_or_before(
        self, character_id: str, season: int, episode: int
    ) -> Optional[dict]:
        """Latest arc row at or before the given episode.

        Spoiler-safe fallback when the exact episode has no arc row — never
        returns data from a later point in the show.
        """
        row = self._conn.execute(
            """
            SELECT * FROM character_arcs
            WHERE character_id = ?
              AND (season < ? OR (season = ? AND episode <= ?))
            ORDER BY season DESC, episode DESC
            LIMIT 1
            """,
            (character_id, season, season, episode),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()


if __name__ == "__main__":
    test_path = Path("data/test_cineid.db")
    db = CharacterDB(db_path=test_path)

    db.add_show("breaking-bad", "Breaking Bad", tmdb_id=1396)
    db.add_character(
        character_id="breaking-bad-walter-white",
        show_id="breaking-bad",
        character_name="Walter White",
        actor_name="Bryan Cranston",
        role="main",
        first_episode=1,
        last_episode=62,
        first_season=1,
        last_season=5,
        importance_rank=1,
        relationship_summary="Chemistry teacher turned drug kingpin",
        image_count=0,
        confidence_level="high",
    )

    result = db.get_character("breaking-bad-walter-white")
    print(result)

    db.close()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(test_path) + suffix)
        p.unlink(missing_ok=True)
    print("test DB cleaned up")
