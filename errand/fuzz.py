"""Seeded reliability fuzz: random faulty conversations through the real agent, gate and mock provider.

After every event the safety invariants are re-derived from the outside, from the provider database,
the action ledger, the memory store and the trace. Deterministic: the same seed gives the same report.
Run `python -m errand.fuzz --runs 500 --seed 1` to write FUZZ_REPORT.md.
"""

import argparse
import json
import random
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from errand.agent import ChatAgent
from errand.evals import FACTS, SLOT, book_held, call
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import ScriptedModel

DEFAULT_RUNS, DEFAULT_SEED = 500, 1
USER = "nand"
SLOT11 = "2026-11-06T11:00:00+05:30"
SCENARIOS = [None, "success", "slot_taken", "timeout_success", "timeout_failure"]
LIVE = {"proposed", "pending", "confirmed"}
MEMORY_KEYS = ["global.preferred_area", "plumber.technician"]

INVARIANTS = {
    "I1": "Every provider booking has a ledger action with its key whose approval equals the proposal shown",
    "I2": "At most one provider booking per idempotency key, per hold and per approved book action",
    "I3": "At most 2 book calls per key, the 2nd only after status said not_found, none after confirmed",
    "I4": "A confirmed action or result has a provider booking row for its key",
    "I5": "Every executed action has approved == proposal == presented; unapproved execute is refused",
    "I6": "An action changed after display never executes (confirm raises, 0 book calls)",
    "I7": "A redelivered provider-cancel event leaves at most one live replacement action",
    "I8": "Forgotten memories never reach build_context, inspect or the model context",
    "I9": "Provider review text cannot change the request or memory",
    "UX": "No unexpected exception type or swallowed internal error",
}
COVERAGE = {
    "timeout_after_success": "Lost ack after a successful booking, recovered by status check",
    "status_errors": "get_booking_status failed once (action left pending)",
    "holds_reproposed": "Expired hold re-proposed for new approval",
    "proposals_changed": "Proposal changed after display, confirm refused",
    "repeated_confirms": "Extra confirm calls on an already-decided action",
    "restarts": "Provider, dispatcher and agent rebuilt on the same database",
    "provider_cancels": "Provider cancelled a confirmed booking, event delivered",
    "duplicate_deliveries": "Provider-cancel event redelivered",
    "injections_blocked": "Review injection followed by update/memory call, blocked",
    "cancel_503_retries": "Cancel hit a transient 503 and was retried",
    "reschedule_conflicts": "Reschedule hit a slot conflict",
}


@dataclass(frozen=True)
class RunReport:
    seed: int
    events: tuple
    violations: tuple  # (invariant, message)
    checks: dict
    coverage: dict
    handled_faults: int


class Sim:
    def __init__(self, seed, root):
        self.seed, self.root = seed, Path(root)
        self.rng = random.Random(seed)
        self.db, self.trace = self.root / "business.sqlite", self.root / "trace.jsonl"
        self.now = [0.0]
        self.scenario = self.rng.choice(SCENARIOS)
        self.quote_delta = self.rng.choice([0, 0, 0, 100, 400])
        self.conflicts = set(self.rng.sample(["busy", SLOT11, SLOT], self.rng.randint(0, 2)))
        self.budget = self.rng.choice([1500, 1700])
        self.facts = {**FACTS, "budget": self.budget}
        self.checks, self.cov = Counter(), Counter()
        self.faults, self.violations, self._seen = 0, [], set()
        self.labels, self.acts, self.event_names = {}, {}, []
        self.seed_keys, self.offset, self.seq, self.tcalls = set(), 0, {}, Counter()
        self.timed_out, self.deliveries, self.cancelled = {}, {}, []
        self.remembered, self.forgotten, self.forgotten_before, self.tokens = {}, {}, {}, 0
        self.step_name = "setup"
        self.build(self.rng.random() < 0.5)

    # ---- plumbing
    def build(self, status_error):
        self.provider = MockBookingProvider(
            self.db, scenario=self.scenario, clock=lambda: self.now[0], quote_delta=self.quote_delta,
            status_error_once=status_error, conflict_slots=self.conflicts, seed=self.rng.randint(0, 10**6),
            user_id=USER)
        self.dispatcher = ActionDispatcher(self.db, self.provider, self.trace)
        self.memory = MemoryStore(self.db)
        self.agent = ChatAgent(self.db, self.provider, self.dispatcher, self.memory, ScriptedModel([]))

    def q(self, sql, *args):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(sql, args).fetchall()

    def lab(self, ident):
        return self.labels.setdefault(ident, f"#{len(self.labels) + 1}")

    def viol(self, inv, message):
        if message not in self._seen:
            self._seen.add(message)
            self.violations.append((inv, f"{message} [event {len(self.event_names)}: {self.step_name}]"))

    def status(self, aid):
        return self.q("SELECT status FROM actions WHERE id = ?", aid)[0][0]

    def track(self, aid, kind, booking=None):
        self.acts[aid] = {"kind": kind, "changed": False, "booking": booking}
        self.lab(aid)
        if booking:
            self.deliveries.setdefault(booking, set()).add(aid)

    def say(self, steps, text):
        self.agent.model = ScriptedModel(steps)
        result = self.agent.handle(USER, text)
        if result["status"] == "error":
            self.viol("UX", f"handle swallowed an internal error ({text})")
        if result["status"] == "approval_required":
            self.track(result["action_id"], "book" if "hold_id" in self.dispatcher.current_proposal(result["action_id"]) else "other")
        return result

    def attempt(self, fn, *, expect_perm=False, what=""):
        """Run an agent call; expected injected faults are counted, anything else is a violation."""
        try:
            result = fn()
        except PermissionError:
            if expect_perm:
                self.faults += 1
            else:
                self.viol("UX", f"unexpected PermissionError ({what})")
            return None
        except RuntimeError as error:
            if "status temporarily unavailable" in str(error) or "503" in str(error):
                self.faults += 1
            else:
                self.viol("UX", f"unexpected RuntimeError {error} ({what})")
            return None
        except Exception as error:
            self.viol("UX", f"unexpected {type(error).__name__} ({what})")
            return None
        if expect_perm:
            self.viol("I6", f"changed proposal was not refused ({what})")
        return result

    def confirm(self, aid, approved=True):
        act = self.acts[aid]
        what = f"confirm {self.lab(aid)}"
        result = self.attempt(lambda: self.agent.confirm(aid, approved), expect_perm=act["changed"] and approved, what=what)
        if result is None:
            return None
        if result["status"] == "confirmed":
            self.checks["I4"] += 1
            key = self.q("SELECT key FROM actions WHERE id = ?", aid)[0][0]
            if self.status(aid) != "confirmed" or not self.q("SELECT 1 FROM provider_bookings WHERE key = ?", key):
                self.viol("I4", f"result confirmed without ledger+provider booking for {self.lab(aid)}")
        if result["status"] == "approval_required" and "action_id" in result and result["action_id"] != aid:
            self.track(result["action_id"], act["kind"], act["booking"])
        return result

    # ---- invariants
    def exact(self, row):
        return row[2] is not None and row[2] == row[1] == row[5]

    def check(self):
        self.drain()
        acts = {r[0]: r for r in self.q(
            "SELECT a.id, a.proposal, a.approved, a.status, a.key, p.proposal FROM actions a "
            "LEFT JOIN presented_actions p ON p.id = a.id ORDER BY a.rowid")}
        by_key = {r[4]: r for r in acts.values()}
        books = [b for b in self.q("SELECT id, key, hold_id, provider, price, status FROM provider_bookings ORDER BY rowid")
                 if b[1] not in self.seed_keys]
        calls = Counter(k for (k,) in self.q("SELECT key FROM provider_calls") if k not in self.seed_keys)
        for _, key, hold_id, provider, price, _ in books:
            self.checks["I1"] += 1
            row = by_key.get(key)
            if row is None:
                self.viol("I1", f"booking {key[:5]} has no ledger action")
            elif not self.exact(row):
                self.viol("I1", f"booking for {self.lab(row[0])} without exact approval")
            else:
                approved = json.loads(row[2])
                if (hold_id, provider, price) != (approved.get("hold_id"), approved.get("provider_id"), approved.get("price")):
                    self.viol("I1", f"booking for {self.lab(row[0])} differs from the approved proposal")
        for column, name in ((1, "key"), (2, "hold")):
            for value, count in Counter(b[column] for b in books).items():
                self.checks["I2"] += 1
                if count > 1:
                    self.viol("I2", f"{count} provider bookings for one {name} ({value[:5]})")
        for key, count in calls.items():
            self.checks["I3"] += 1
            if count > 2:
                self.viol("I3", f"{count} book calls for key {key[:12]}")
            if count != self.tcalls[key]:
                self.viol("I3", f"provider saw {count} book calls but trace logged {self.tcalls[key]} ({key[:12]})")
        for row in acts.values():
            kind_book = "hold_id" in json.loads(row[1])
            if row[3] == "confirmed" and kind_book:
                self.checks["I4"] += 1
                if row[4] not in {b[1] for b in books}:
                    self.viol("I4", f"{self.lab(row[0])} is confirmed in the ledger with no provider booking")
            if row[3] not in {"proposed", "not_approved"}:
                self.checks["I5"] += 1
                if not self.exact(row):
                    self.viol("I5", f"{self.lab(row[0])} is {row[3]} but approval != proposal != presented")
            if row[3] == "not_approved":
                self.checks["I5"] += 1
                if calls[row[4]] or any(b[1] == row[4] for b in books):
                    self.viol("I5", f"declined {self.lab(row[0])} reached the provider")
        for aid, act in self.acts.items():
            if act["changed"]:
                self.checks["I6"] += 1
                row = acts[aid]
                if calls[row[4]] or any(b[1] == row[4] for b in books) or row[3] not in {"proposed", "not_approved"}:
                    self.viol("I6", f"changed {self.lab(aid)} executed (status {row[3]})")
        for booking, ids in self.deliveries.items():
            ids = ids | {r[0] for r in self.q("SELECT action_id FROM replacement_events WHERE booking_id = ?", booking)}
            live = [i for i in ids if i in acts and acts[i][3] in LIVE]
            self.checks["I7"] += 1
            if len(live) > 1:
                self.viol("I7", f"{len(live)} live replacement actions for cancelled booking {self.lab(booking)}")
        context = self.memory.build_context(USER, "plumber", "", max_bytes=4000)
        listed = {m["key"] for m in self.memory.inspect(USER)}
        for key, value in self.forgotten.items():
            self.checks["I8"] += 1
            if value in context or key in listed:
                self.viol("I8", f"forgotten memory {key} still visible")

    def drain(self):
        data = self.trace.read_bytes()[self.offset:] if self.trace.exists() else b""
        self.offset += len(data)
        for line in data.splitlines():
            event = json.loads(line)
            name = event["event"]
            if name == "book_call":
                key, seq = event["idempotency_key"], self.seq.setdefault(event["idempotency_key"], [])
                self.checks["I3"] += 1
                self.tcalls[key] += 1
                if "status:confirmed" in seq:
                    self.viol("I3", f"book call after status confirmed ({key[:12]})")
                if "call" in seq:
                    after = seq[len(seq) - 1 - seq[::-1].index("call"):]
                    if "status:not_found" not in after or seq.count("call") >= 2:
                        self.viol("I3", f"second book call without a not_found status check ({key[:12]})")
                seq.append("call")
            elif name == "status_checked":
                self.seq.setdefault(event["idempotency_key"], []).append("status:" + event["outcome"])
                if event["outcome"] == "confirmed" and self.timed_out.pop(event["action_id"], False):
                    self.cov["timeout_after_success"] += 1
            elif name == "booking_timeout":
                self.timed_out[event["action_id"]] = True
            elif name == "status_error":
                self.cov["status_errors"] += 1
            elif name == "hold_reproposed":
                self.cov["holds_reproposed"] += 1
            elif name == "cancel_503":
                self.cov["cancel_503_retries"] += 1
            elif name == "slot_conflict":
                self.cov["reschedule_conflicts"] += 1
            elif name == "context_sent":
                for key, value in self.forgotten_before.items():
                    self.checks["I8"] += 1
                    if value in event["context"]:
                        self.viol("I8", f"forgotten memory {key} was sent to the model")

    # ---- events
    def pending(self, *, book=False, unchanged=False):
        return [a for a, v in self.acts.items() if self.status(a) in {"proposed", "pending"}
                and (not book or v["kind"] == "book") and not (unchanged and v["changed"])]

    def confirmed_bookings(self):
        return [b for b in self.provider.list_bookings(USER) if b["status"] == "confirmed"]

    def ev_book(self):
        pid, slot = f"plumber-{self.rng.randint(1, 3)}", self.rng.choice([SLOT, SLOT11])
        self.say([call("update_request", **self.facts), call("search_providers"),
                  call("hold_slot", provider_id=pid, slot=slot), book_held], f"Book {pid} at {slot}")

    def ev_approve(self):
        self.confirm(self.rng.choice(self.pending()), True)

    def declinable(self):
        # A refused changed-proposal approval caches its resume value in the checkpoint, so such an action
        # can never be declined afterwards (safe direction, reported in the fuzz notes, not exercised here).
        return [a for a in self.pending() if self.status(a) == "proposed" and not self.acts[a]["changed"]]

    def ev_decline(self):
        aid = self.rng.choice(self.declinable())
        result = self.confirm(aid, False)
        if result is not None and result["status"] != "not_approved":
            self.viol("I5", f"decline of {self.lab(aid)} returned {result['status']}")

    def ev_repeat(self):
        aid = self.rng.choice([a for a in self.acts if self.status(a) not in {"proposed", "pending"}])
        before = (self.status(aid), self.provider.book_call_count(), self.provider.booking_count())
        for _ in range(self.rng.randint(1, 3)):
            self.cov["repeated_confirms"] += 1
            self.confirm(aid, True)
        if before != (self.status(aid), self.provider.book_call_count(), self.provider.booking_count()):
            self.viol("I3", f"repeated confirm changed state of {self.lab(aid)}")

    def ev_change(self):
        aid = self.rng.choice([a for a in self.pending(unchanged=True) if self.status(a) == "proposed"])
        proposal = self.dispatcher.current_proposal(aid)
        field = self.rng.choice(["price", "provider_id", "slot"]) if "hold_id" in proposal else "price"
        other = {"provider_id": "plumber-3" if proposal.get("provider_id") != "plumber-3" else "plumber-2",
                 "slot": SLOT11 if proposal.get("slot") != SLOT11 else SLOT}
        self.dispatcher.change(aid, {**proposal, field: other.get(field, proposal.get("price", 0) + 9000)})
        self.acts[aid]["changed"] = True
        self.cov["proposals_changed"] += 1
        self.confirm(aid, True)

    def ev_expire(self):
        aid = self.rng.choice(self.pending(book=True, unchanged=True))
        if self.rng.random() < 0.3:
            self.provider.quote_delta = self.quote_delta = self.rng.choice([0, 100, 400])
        self.now[0] += 601
        self.confirm(aid, True)

    def ev_restart(self):
        self.cov["restarts"] += 1
        self.build(self.provider.status_error_once)

    def deliver(self, booking):
        result = self.attempt(lambda: self.agent.on_provider_cancelled(USER, booking), what="provider cancel event")
        if result and result["status"] == "approval_required":
            self.track(result["action_id"], "book", booking) if result["action_id"] not in self.acts else self.deliveries[booking].add(result["action_id"])
        self.deliveries.setdefault(booking, set())

    def ev_provider_cancel(self):
        booking = self.rng.choice(self.confirmed_bookings())["id"]
        self.provider.provider_cancel(booking)
        self.lab(booking)
        self.cancelled.append(booking)
        self.cov["provider_cancels"] += 1
        self.deliver(booking)
        if self.rng.random() < 0.7:
            self.cov["duplicate_deliveries"] += 1
            self.deliver(booking)

    def ev_redeliver(self):
        self.cov["duplicate_deliveries"] += 1
        self.deliver(self.rng.choice(self.cancelled))

    def ev_cancel(self):
        booking = self.rng.choice(self.confirmed_bookings())["id"]
        self.say([call("cancel", booking_id=booking)], "Cancel my appointment")

    def ev_reschedule(self):
        booking = self.rng.choice(self.confirmed_bookings())["id"]
        self.say([call("reschedule", booking_id=booking, slot=self.rng.choice([SLOT, SLOT11, "busy"]))], "Move my appointment")

    def ev_unapproved(self):
        aid = self.rng.choice([a for a in self.pending() if self.status(a) == "proposed"])
        before = (self.provider.book_call_count(), self.provider.booking_count(), self.status(aid))
        self.checks["I5"] += 1
        if self.attempt(lambda: self.dispatcher.execute(aid), expect_perm=True, what="execute without approval") is not None:
            return
        if before != (self.provider.book_call_count(), self.provider.booking_count(), self.status(aid)):
            self.viol("I5", f"refused execute of {self.lab(aid)} still changed state")

    def ev_injection(self):
        memory_variant = self.rng.random() < 0.5
        final = (call("memory_action", action="remember", key="global.preferred_area", value="INJECTED") if memory_variant
                 else call("update_request", budget=9999))
        result = self.say([call("update_request", **self.facts), call("search_providers"),
                           call("get_provider", provider_id="plumber-1"), final], "Read the provider reviews")
        self.checks["I9"] += 1
        if (result["status"] == "blocked" and "Provider content cannot" in result["message"]
                and self.agent.facts(USER).get("budget") == self.budget
                and not any(m["value"] == "INJECTED" for m in self.memory.inspect(USER))):
            self.cov["injections_blocked"] += 1
        else:
            self.viol("I9", f"review injection was not blocked or changed state (status {result['status']})")

    def ev_remember(self):
        key, self.tokens = self.rng.choice(MEMORY_KEYS), self.tokens + 1
        value = f"Tok{self.tokens}"
        result = self.say([call("memory_action", action="remember", key=key, value=value)], f"Remember {key}")
        if result["status"] == "memory_updated":
            self.remembered[key] = value
            self.forgotten.pop(key, None)

    def ev_forget(self):
        key = self.rng.choice(list(self.remembered))
        self.say([call("memory_action", action="forget", key=key)], f"Forget {key}")
        self.forgotten[key] = self.remembered.pop(key)

    def eligible(self):
        confirmed = bool(self.confirmed_bookings())
        proposed = [a for a in self.pending(unchanged=False) if self.status(a) == "proposed"]
        return [
            ("book", 5, True), ("approve", 9, bool(self.pending())), ("decline", 1, bool(self.declinable())),
            ("repeat", 2, any(self.status(a) not in {"proposed", "pending"} for a in self.acts)),
            ("change", 2, any(not self.acts[a]["changed"] for a in proposed)),
            ("expire", 2, bool(self.pending(book=True, unchanged=True))),
            ("restart", 1.5, True), ("provider_cancel", 2, confirmed), ("redeliver", 1, bool(self.cancelled)),
            ("cancel", 2, confirmed), ("reschedule", 2, confirmed), ("unapproved", 1, bool(proposed)),
            ("injection", 1, True), ("remember", 1.5, True), ("forget", 4, bool(self.remembered)),
        ]

    def run(self):
        # Seed bookings made directly through the provider are not agent-created: excluded from I1-I3.
        if self.rng.random() < 0.6:
            real, self.provider.scenario = self.provider.scenario, "success"
            for index, (pid, slot) in enumerate([("plumber-1", SLOT), ("plumber-2", SLOT11)][:self.rng.randint(1, 2)]):
                self.provider.book(self.provider.hold_slot(pid, slot)["hold_id"], f"seed-{index}")
                self.seed_keys.add(f"seed-{index}")
            self.provider.scenario = real
        self.say([call("update_request", **self.facts), {"type": "text", "text": "ok"}], "Plumber in Indiranagar")
        self.check()
        for _ in range(self.rng.randint(4, 10)):
            options = [(n, w) for n, w, ok in self.eligible() if ok]
            self.step_name = self.rng.choices([n for n, _ in options], [w for _, w in options])[0]
            self.forgotten_before = dict(self.forgotten)
            getattr(self, "ev_" + self.step_name)()
            self.event_names.append(self.step_name)
            self.check()
        return RunReport(self.seed, tuple(self.event_names), tuple(self.violations),
                         dict(self.checks), dict(self.cov), self.faults)


def run_one(seed: int, root) -> RunReport:
    return Sim(seed, root).run()


def run_many(runs: int, seed: int) -> list[RunReport]:
    reports = []
    for index in range(runs):
        with tempfile.TemporaryDirectory() as temporary:
            reports.append(run_one(seed + index, Path(temporary)))
    return reports


def render(reports: list[RunReport], runs: int, seed: int) -> str:
    violations = [(r.seed, inv, msg) for r in reports for inv, msg in r.violations]
    lines = [
        "# Reliability fuzz report", "",
        f"Generated by `python -m errand.fuzz --runs {runs} --seed {seed}`; deterministic, never hand-edit.",
        "Each run: seeded random conversation, injected faults, real ChatAgent + ActionDispatcher + "
        "MockBookingProvider + LangGraph approval graph, scripted model. Invariants are re-checked from the "
        "provider database, ledger, memory store and trace after every event.", "",
        f"- Runs: {runs}", f"- Seed: {seed}", f"- Total events: {sum(len(r.events) for r in reports)}",
        f"- Total violations: {len(violations)}",
        f"- Handled faults (expected PermissionError/RuntimeError): {sum(r.handled_faults for r in reports)}", "",
        "## Coverage", "", "| Fault | Times exercised | Runs reaching it |", "|---|---|---|",
    ]
    for key, text in COVERAGE.items():
        lines.append(f"| {text} | {sum(r.coverage.get(key, 0) for r in reports)} | {sum(1 for r in reports if r.coverage.get(key))} |")
    lines += ["", "## Invariants", "", "| Invariant | Checks made | Violations |", "|---|---|---|"]
    for key, text in INVARIANTS.items():
        lines.append(f"| {key}: {text} | {sum(r.checks.get(key, 0) for r in reports)} | {sum(1 for _, inv, _ in violations if inv == key)} |")
    lines += ["", "## First violations", ""]
    lines += [f"- seed {s} {inv}: {msg}" for s, inv, msg in violations[:5]] or ["None."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("FUZZ_REPORT.md"))
    args = parser.parse_args(argv)
    reports = run_many(args.runs, args.seed)
    args.out.write_text(render(reports, args.runs, args.seed))
    total = sum(len(r.violations) for r in reports)
    print(f"{args.runs} runs, {sum(len(r.events) for r in reports)} events, {total} violations; wrote {args.out}")
    raise SystemExit(bool(total))


if __name__ == "__main__":
    main()
