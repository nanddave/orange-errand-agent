"""Durable, inspectable preferences and bounded prompt context."""

import json
import sqlite3
from pathlib import Path


class MemoryStore:
    def __init__(self, db: Path):
        self.db = db
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "user_id TEXT, key TEXT, value TEXT, source TEXT, confirmed INTEGER, "
                "forgotten INTEGER DEFAULT 0, PRIMARY KEY (user_id, key))"
            )

    def put(self, user_id: str, key: str, value: str, *, source: str) -> None:
        if source not in {"explicit", "inferred"}:
            raise ValueError("source must be explicit or inferred")
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?, 0)",
                (user_id, key, value, source, int(source == "explicit")),
            )

    def exists(self, user_id: str, key: str) -> bool:
        """True if a row exists in any state, including forgotten."""
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT 1 FROM memories WHERE user_id = ? AND key = ?", (user_id, key)
            ).fetchone() is not None

    def inspect(self, user_id: str) -> list[dict]:
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                "SELECT key, value, source, confirmed FROM memories "
                "WHERE user_id = ? AND forgotten = 0 ORDER BY key", (user_id,)
            ).fetchall()
        return [dict(zip(("key", "value", "source", "confirmed"), row)) for row in rows]

    def correct(self, user_id: str, key: str, value: str) -> None:
        with sqlite3.connect(self.db) as conn:
            result = conn.execute(
                "UPDATE memories SET value = ?, source = 'explicit', confirmed = 1, forgotten = 0 "
                "WHERE user_id = ? AND key = ?", (value, user_id, key),
            )
            if result.rowcount != 1:
                raise KeyError(key)

    def forget(self, user_id: str, key: str) -> None:
        with sqlite3.connect(self.db) as conn:
            result = conn.execute(
                "UPDATE memories SET forgotten = 1 WHERE user_id = ? AND key = ?", (user_id, key)
            )
            if result.rowcount == 0:  # tombstone, so a later inference cannot create what the user already refused
                conn.execute("INSERT INTO memories VALUES (?, ?, '', 'explicit', 0, 1)", (user_id, key))

    def confirm(self, user_id: str, key: str) -> None:
        with sqlite3.connect(self.db) as conn:
            result = conn.execute(
                "UPDATE memories SET confirmed = 1 WHERE user_id = ? AND key = ? AND forgotten = 0",
                (user_id, key),
            )
            if result.rowcount != 1:
                raise KeyError(key)

    def build_context(
        self, user_id: str, service: str, prior_chat: str, *, max_bytes: int,
        trace: Path | None = None,
    ) -> str:
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                "SELECT key, value FROM memories WHERE user_id = ? AND confirmed = 1 "
                "AND forgotten = 0 ORDER BY key", (user_id,),
            ).fetchall()
        lines = []
        for key, value in rows:
            if not (key.startswith("global.") or key.startswith(f"{service}.")):
                continue
            line = f"{key.split('.', 1)[1]}: {value}\n"
            if len(("".join(lines) + line).encode()) > max_bytes:
                continue
            lines.append(line)
        context = "".join(lines)
        # shortcut: structured facts carry forward; old chat is excluded so forgotten facts cannot leak back.
        if trace:
            with trace.open("a") as file:
                file.write(json.dumps({"event": "context_sent", "context": context, "bytes": len(context.encode())}) + "\n")
        return context
