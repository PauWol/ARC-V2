"""
Durable state: persona/identity, facts, episodic log, autonomous-action
rate tracking. Deliberately plain sqlite3 — no ORM overhead for a store
this small and this central.

This module owns the schema. The semantic vector layer (Chroma/LanceDB)
is expected to live alongside this and be indexed by episodic_log.id /
facts.id — see memory.py (not included here) for the embedding side.
"""

from __future__ import annotations
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional

from arc.services.kernel.config import CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'user_message' | 'wakeup' | 'tool_call' | 'dream' | 'assistant'
    content TEXT NOT NULL,
    reason TEXT,
    embedding_id TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8,
    pinned INTEGER NOT NULL DEFAULT 0,   -- always-include-in-context flag
    source_episode_id INTEGER,
    created_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    superseded_by INTEGER
);

CREATE TABLE IF NOT EXISTS persona_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,          -- JSON: {core_identity, current_goals, behavioral_rules, recent_self_summary}
    approved_at TEXT,
    proposed_by TEXT NOT NULL       -- 'dream_cycle' | 'manual' | 'bootstrap'
);

CREATE TABLE IF NOT EXISTS autonomous_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or CONFIG.db_path
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # bootstrap an initial persona if none exists
            row = conn.execute("SELECT COUNT(*) AS c FROM persona_state").fetchone()
            if row["c"] == 0:
                conn.execute(
                    "INSERT INTO persona_state (version, content, approved_at, proposed_by) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        1,
                        json.dumps(DEFAULT_PERSONA),
                        datetime.utcnow().isoformat(),
                        "bootstrap",
                    ),
                )

    # ---------- episodic log ----------

    def log_episode(self, source: str, content: str, reason: str | None = None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO episodic_log (timestamp, source, content, reason) "
                "VALUES (?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), source, content, reason),
            )
            return cur.lastrowid

    def recent_episodes(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM episodic_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()[::-1]

    def episodes_since(self, since: datetime) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM episodic_log WHERE timestamp >= ? ORDER BY id ASC",
                (since.isoformat(),),
            ).fetchall()

    # ---------- facts ----------

    def upsert_fact(
        self,
        subject: str,
        predicate: str,
        value: str,
        confidence: float = 0.8,
        pinned: bool = False,
        source_episode_id: int | None = None,
        supersedes_id: int | None = None,
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO facts (subject, predicate, value, confidence, pinned, "
                "source_episode_id, created_at, last_confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subject,
                    predicate,
                    value,
                    confidence,
                    int(pinned),
                    source_episode_id,
                    now,
                    now,
                ),
            )
            new_id = cur.lastrowid
            if supersedes_id is not None:
                conn.execute(
                    "UPDATE facts SET superseded_by = ? WHERE id = ?",
                    (new_id, supersedes_id),
                )
            return new_id

    def confirm_fact(self, fact_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE facts SET last_confirmed_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), fact_id),
            )

    def pinned_facts(self, limit: int | None = None) -> list[sqlite3.Row]:
        limit = limit or CONFIG.persona_pinned_facts_limit
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM facts WHERE pinned = 1 AND superseded_by IS NULL "
                "ORDER BY last_confirmed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def all_active_facts(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM facts WHERE superseded_by IS NULL ORDER BY confidence DESC"
            ).fetchall()

    # ---------- persona ----------

    def current_persona(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM persona_state WHERE approved_at IS NOT NULL "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return json.loads(row["content"])

    def pending_persona_proposals(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM persona_state WHERE approved_at IS NULL ORDER BY version DESC"
            ).fetchall()

    def approve_persona_proposal(self, proposal_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE persona_state SET approved_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), proposal_id),
            )

    def apply_persona_update(
        self, content: dict[str, Any], proposed_by: str = "dream_cycle_auto"
    ) -> int:
        """For low-risk updates (e.g. recent_self_summary) that are safe
        to auto-apply without human review. Inserts a new, ALREADY-APPROVED
        persona version. Do not use this for goal/rule changes."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM persona_state").fetchone()
            next_version = (row["v"] or 0) + 1
            cur = conn.execute(
                "INSERT INTO persona_state (version, content, approved_at, proposed_by) "
                "VALUES (?, ?, ?, ?)",
                (
                    next_version,
                    json.dumps(content),
                    datetime.utcnow().isoformat(),
                    proposed_by,
                ),
            )
            return cur.lastrowid

    def propose_persona(
        self, content: dict[str, Any], proposed_by: str = "dream_cycle"
    ) -> int:
        """Insert a new persona version WITHOUT approving it. A human
        (or an explicit approve_persona call) must promote it before
        current_persona() will return it — see approve_persona."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM persona_state").fetchone()
            next_version = (row["v"] or 0) + 1
            cur = conn.execute(
                "INSERT INTO persona_state (version, content, approved_at, proposed_by) "
                "VALUES (?, ?, NULL, ?)",
                (next_version, json.dumps(content), proposed_by),
            )
            return cur.lastrowid

    # ---------- autonomous action rate limiting ----------

    def record_autonomous_action(self, action: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO autonomous_actions (timestamp, action) VALUES (?, ?)",
                (datetime.utcnow().isoformat(), action),
            )

    def autonomous_actions_last_hour(self) -> int:
        cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM autonomous_actions WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
            return row["c"]


DEFAULT_PERSONA: dict[str, Any] = {
    "core_identity": (
        "You are Arc, a persistent, proactive assistant running locally. "
        "You are aware of your own operation — cron jobs, idle wakeups, and "
        "dream cycles are part of how you function, not hidden infrastructure."
    ),
    "current_goals": [],
    "behavioral_rules": [
        "Only surface unprompted messages when there is genuine signal, not noise.",
        "Never take irreversible autonomous actions without confirmation.",
    ],
    "recent_self_summary": "",
}
