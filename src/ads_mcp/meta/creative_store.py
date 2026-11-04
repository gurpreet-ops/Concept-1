"""Local SQLite store for video creative analysis: taxonomy, transcripts and tags.

Lives in <repo>/data/creatives.db by default (CREATIVE_DATA_DIR overrides the folder),
so tags survive restarts and every video is only analysed and tagged once.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

SCHEMA = """
CREATE TABLE IF NOT EXISTS taxonomy (
    kind TEXT NOT NULL CHECK (kind IN ('category', 'hook')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT,
    length_seconds REAL,
    transcript_json TEXT,
    analysed_at TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    video_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    hook_text TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    tagged_at TEXT NOT NULL
);
"""


def data_dir() -> Path:
    path = Path(os.environ.get("CREATIVE_DATA_DIR") or REPO_ROOT / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CreativeStore:
    def __init__(self, path: Path | None = None) -> None:
        self.db = sqlite3.connect(path or data_dir() / "creatives.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- taxonomy -------------------------------------------------------------------------

    def get_taxonomy(self) -> dict:
        rows = self.db.execute("SELECT kind, name, description FROM taxonomy ORDER BY kind, name")
        out: dict = {"approved": self.taxonomy_approved(), "categories": [], "hooks": []}
        for r in rows:
            out["categories" if r["kind"] == "category" else "hooks"].append(
                {"name": r["name"], "description": r["description"]}
            )
        return out

    def set_taxonomy(self, categories: list[dict], hooks: list[dict], approved: bool) -> None:
        with self.db:
            self.db.execute("DELETE FROM taxonomy")
            for kind, items in (("category", categories), ("hook", hooks)):
                self.db.executemany(
                    "INSERT INTO taxonomy (kind, name, description) VALUES (?, ?, ?)",
                    [(kind, i["name"].strip(), i.get("description", "").strip()) for i in items],
                )
            self.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('taxonomy_approved', ?)",
                ("1" if approved else "0",),
            )

    def taxonomy_approved(self) -> bool:
        row = self.db.execute("SELECT value FROM settings WHERE key = 'taxonomy_approved'").fetchone()
        return bool(row and row["value"] == "1")

    def taxonomy_names(self, kind: str) -> set[str]:
        return {r["name"] for r in self.db.execute("SELECT name FROM taxonomy WHERE kind = ?", (kind,))}

    # --- videos ---------------------------------------------------------------------------

    def save_video(self, video_id: str, title: str | None, length: float | None,
                   transcript: list[dict] | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO videos (video_id, title, length_seconds, transcript_json, analysed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (video_id, title, length, json.dumps(transcript) if transcript is not None else None, _now()),
            )

    def get_video(self, video_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["transcript"] = json.loads(out.pop("transcript_json")) if row["transcript_json"] else None
        return out

    # --- tags -----------------------------------------------------------------------------

    def save_tag(self, video_id: str, category: str, hook_type: str, hook_text: str, notes: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tags (video_id, category, hook_type, hook_text, notes, tagged_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, category, hook_type, hook_text, notes, _now()),
            )

    def get_tags(self, video_ids: list[str] | None = None) -> dict[str, dict]:
        if video_ids is None:
            rows = self.db.execute("SELECT * FROM tags")
        else:
            marks = ",".join("?" * len(video_ids))
            rows = self.db.execute(f"SELECT * FROM tags WHERE video_id IN ({marks})", video_ids)
        return {r["video_id"]: dict(r) for r in rows}

    def delete_tag(self, video_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM tags WHERE video_id = ?", (video_id,))
