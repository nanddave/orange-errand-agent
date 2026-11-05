"""First-gate proof of approval, durable action identity, and recovery."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict


class GateState(TypedDict, total=False):
    action_id: str
    outcome: str


class MockBookingProvider:
    def __init__(self, db: Path, *, timeout_after_success=False, status_error_once=False):
        self.db = db
        self.timeout_after_success = timeout_after_success
        self.status_error_once = status_error_once
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS provider_calls (key TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS provider_bookings (key TEXT PRIMARY KEY, hold_id TEXT)")

    def book(self, hold_id: str, key: str) -> str:
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO provider_calls VALUES (?)", (key,))
            conn.execute("INSERT OR IGNORE INTO provider_bookings VALUES (?, ?)", (key, hold_id))
        if self.timeout_after_success:
            self.timeout_after_success = False
            raise TimeoutError("acknowledgement lost after booking")
        return "confirmed"

    def get_booking_status(self, key: str) -> str:
        if self.status_error_once:
            self.status_error_once = False
            raise RuntimeError("status temporarily unavailable")
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT 1 FROM provider_bookings WHERE key = ?", (key,)).fetchone()
        return "confirmed" if row else "not_found"

    def booking_count(self) -> int:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM provider_bookings").fetchone()[0]

    def book_call_count(self) -> int:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM provider_calls").fetchone()[0]


class ActionDispatcher:
    def __init__(self, db: Path, provider: MockBookingProvider, trace: Path):
        self.db = db
        self.provider = provider
        self.trace = trace
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "id TEXT PRIMARY KEY, proposal TEXT NOT NULL, approved TEXT, "
                "status TEXT NOT NULL, key TEXT NOT NULL)"
            )

    def _log(self, event: str, action_id: str, **details) -> None:
        with self.trace.open("a") as file:
            file.write(json.dumps({"event": event, "action_id": action_id, **details}) + "\n")

    def propose(self, proposal: dict) -> str:
        action_id = str(uuid4())
        frozen = json.dumps(proposal, sort_keys=True)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO actions VALUES (?, ?, NULL, 'proposed', ?)",
                (action_id, frozen, f"book:{action_id}"),
            )
        self._log("proposed", action_id, proposal=proposal)
        return action_id

    def current_proposal(self, action_id: str) -> dict:
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT proposal FROM actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("unknown action")
        return json.loads(row[0])

    def approve(self, action_id: str, proposal: dict) -> None:
        frozen = json.dumps(proposal, sort_keys=True)
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT proposal FROM actions WHERE id = ?", (action_id,)).fetchone()
            if row is None or row[0] != frozen:
                raise PermissionError("approval does not match current proposal")
            conn.execute("UPDATE actions SET approved = ? WHERE id = ?", (frozen, action_id))
        self._log("approved", action_id)

    def change(self, action_id: str, proposal: dict) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE actions SET proposal = ?, approved = NULL WHERE id = ?",
                (json.dumps(proposal, sort_keys=True), action_id),
            )
        self._log("changed", action_id)

    def execute(self, action_id: str) -> str:
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT proposal, approved, status, key FROM actions WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None or row[0] != row[1]:
                raise PermissionError("valid approval required")
            proposal, _, status, key = row
            if status == "confirmed":
                return "confirmed"
            if status == "proposed":
                conn.execute("UPDATE actions SET status = 'pending' WHERE id = ?", (action_id,))

        if status == "pending":
            self._log("recovery_started", action_id)
            outcome = self._check_status(action_id, key)
            if outcome == "confirmed":
                self._confirm(action_id)
                return outcome

        try:
            self._log("book_call", action_id, idempotency_key=key)
            self.provider.book(json.loads(proposal)["hold_id"], key)
        except TimeoutError:
            self._log("booking_timeout", action_id, idempotency_key=key)
        outcome = self._check_status(action_id, key)
        if outcome == "confirmed":
            self._confirm(action_id)
        return outcome

    def _check_status(self, action_id: str, key: str) -> str:
        try:
            outcome = self.provider.get_booking_status(key)
        except Exception as error:
            self._log("status_error", action_id, error=type(error).__name__)
            raise
        self._log("status_checked", action_id, idempotency_key=key, outcome=outcome)
        return outcome

    def _confirm(self, action_id: str) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE actions SET status = 'confirmed' WHERE id = ?", (action_id,))
        self._log("confirmed", action_id, message="Booking confirmed.")


def build_graph(dispatcher: ActionDispatcher, checkpointer):
    def approval(state: GateState) -> GateState:
        proposal = dispatcher.current_proposal(state["action_id"])
        answer = interrupt({"action": "book", "proposal": proposal})
        if not answer.get("approved"):
            return {"outcome": "not_approved"}
        dispatcher.approve(state["action_id"], answer["proposal"])
        return {}

    def execute(state: GateState) -> GateState:
        if state.get("outcome") == "not_approved":
            return {}
        return {"outcome": dispatcher.execute(state["action_id"])}

    graph = StateGraph(GateState)
    graph.add_node("approval", approval)
    graph.add_node("execute", execute)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=checkpointer)
